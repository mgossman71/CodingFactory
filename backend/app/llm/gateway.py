"""Shared, concurrency-limited OpenAI-compatible chat gateway.

Every agent in the system calls the model through LLMGateway.chat().
Retries are bounded; errors are structured; no secrets are ever logged.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..config import Settings

logger = logging.getLogger("cf.llm")

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(Exception):
    """Structured LLM failure."""

    def __init__(self, kind: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass
class LLMReply:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1


class LLMClient(Protocol):
    """Anything with .chat(...) — real gateway or mock — satisfies this."""

    async def chat(
        self, messages: list[dict[str, str]], *, timeout: int | None = None
    ) -> LLMReply: ...


@dataclass
class LLMGateway:
    settings: Settings
    _sem: asyncio.Semaphore = field(init=False)
    _client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.settings.llm_max_concurrent_requests)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.settings.llm_request_timeout))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        return headers

    async def chat(
        self, messages: list[dict[str, str]], *, timeout: int | None = None
    ) -> LLMReply:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
        }
        request_timeout = timeout or self.settings.llm_request_timeout
        last_error: LLMError | None = None

        for attempt in range(1, self.settings.llm_max_retries + 2):
            try:
                async with self._sem:
                    resp = await self._http().post(
                        f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                        json=payload,
                        headers=self._headers(),
                        timeout=httpx.Timeout(request_timeout),
                    )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.TransportError) as exc:
                last_error = LLMError("transport", f"LLM request failed: {exc.__class__.__name__}: {exc}")
            else:
                if resp.status_code in RETRYABLE_STATUS:
                    last_error = LLMError(
                        "http", f"LLM returned HTTP {resp.status_code}", status=resp.status_code
                    )
                elif resp.status_code >= 400:
                    # Non-retryable: surface body snippet for diagnosis.
                    detail = resp.text[:500]
                    raise LLMError(
                        "http", f"LLM returned HTTP {resp.status_code}: {detail}", status=resp.status_code
                    )
                else:
                    try:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        usage = data.get("usage") or {}
                    except (ValueError, KeyError, TypeError) as exc:
                        raise LLMError("bad_response", f"Malformed LLM response: {exc}") from exc
                    if not isinstance(content, str):
                        raise LLMError("bad_response", "LLM response content was not a string")
                    return LLMReply(
                        content=content,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        attempts=attempt,
                    )

            if attempt <= self.settings.llm_max_retries:
                backoff = min(2 ** (attempt - 1), 10)
                logger.warning("LLM attempt %d failed (%s), retrying in %ss", attempt, last_error.kind, backoff)
                await asyncio.sleep(backoff)

        assert last_error is not None
        last_error.args = (f"{last_error.args[0]} (after {attempt} attempts)",)
        raise last_error

    async def test_connection(self) -> dict[str, Any]:
        """Connectivity probe used by the settings/health endpoint."""
        try:
            resp = await self._http().get(
                f"{self.settings.llm_base_url.rstrip('/')}/models",
                headers=self._headers(),
                timeout=httpx.Timeout(15),
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.TransportError) as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        return {
            "ok": resp.status_code < 400,
            "status": resp.status_code,
            "base_url": self.settings.llm_base_url,
            "model": self.settings.llm_model,
        }
