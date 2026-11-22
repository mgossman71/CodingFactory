"""LLM gateway: retries, non-retryable errors, structured errors, mock transport."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm.gateway import LLMError, LLMGateway


def make_gateway(handler, retries: int = 2) -> LLMGateway:
    settings = Settings(
        _env_file=None,
        llm_base_url="http://mock-llm/v1",
        llm_max_retries=retries,
        llm_request_timeout=10,
    )
    gw = LLMGateway(settings)
    gw._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return gw


async def test_success_first_try():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "qwen3.8-flash-next-iq3_s"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    gw = make_gateway(handler=handler)
    reply = await gw.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "hello"
    assert reply.prompt_tokens == 3
    await gw.aclose()


async def test_retries_500_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    gw = make_gateway(retries=2, handler=handler)
    reply = await gw.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "ok"
    assert reply.attempts == 3
    await gw.aclose()


async def test_4xx_not_retried_and_structured():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    gw = make_gateway(retries=3, handler=handler)
    with pytest.raises(LLMError) as exc:
        await gw.chat([{"role": "user", "content": "hi"}])
    assert exc.value.kind == "http"
    assert exc.value.status == 400
    assert calls["n"] == 1
    await gw.aclose()


async def test_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    gw = make_gateway(retries=1, handler=handler)
    with pytest.raises(LLMError) as exc:
        await gw.chat([{"role": "user", "content": "hi"}])
    assert exc.value.kind == "http"
    assert "after 2 attempts" in str(exc.value)
    await gw.aclose()


async def test_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    gw = make_gateway(handler=handler)
    with pytest.raises(LLMError) as exc:
        await gw.chat([{"role": "user", "content": "hi"}])
    assert exc.value.kind == "bad_response"
    await gw.aclose()


async def test_api_key_header_only_when_set():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    settings = Settings(_env_file=None, llm_base_url="http://m/v1", llm_api_key="sekret")
    gw = LLMGateway(settings)
    gw._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await gw.chat([{"role": "user", "content": "hi"}])
    assert seen_headers["authorization"] == "Bearer sekret"
    await gw.aclose()
