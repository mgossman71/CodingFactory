"""Structured output extraction/validation/repair."""

import pytest
from pydantic import BaseModel

from app.llm.gateway import LLMError
from app.llm.mock import MockLLM
from app.llm.structured import chat_json, extract_json


class Doc(BaseModel):
    markdown: str


def test_extract_plain():
    assert extract_json('{"markdown": "x"}') == '{"markdown": "x"}'


def test_extract_fenced():
    text = 'Sure! Here you go:\n```json\n{"markdown": "x"}\n```\nEnjoy.'
    assert extract_json(text) == '{"markdown": "x"}'


def test_extract_prose_wrapped():
    text = 'Result: {"markdown": "x"} trailing words'
    assert extract_json(text) == '{"markdown": "x"}'


def test_extract_array():
    text = 'blah [1, 2, {"a": "b"}] blah'
    assert extract_json(text) == '[1, 2, {"a": "b"}]'


def test_extract_none_found():
    with pytest.raises(ValueError):
        extract_json("no json here")


async def test_chat_json_valid_first_try():
    llm = MockLLM(replies=['{"markdown": "hello"}'])
    doc = await chat_json(llm, [{"role": "user", "content": "go"}], Doc)
    assert doc.markdown == "hello"


async def test_chat_json_repair_once():
    llm = MockLLM(replies=["I am not JSON", '{"markdown": "fixed"}'])
    doc = await chat_json(llm, [{"role": "user", "content": "go"}], Doc, repair_attempts=1)
    assert doc.markdown == "fixed"
    # second call must include the repair instruction
    assert "invalid" in llm.calls[1][-1]["content"].lower()


async def test_chat_json_exhausted_repair_raises():
    llm = MockLLM(replies=["junk one", "junk two"])
    with pytest.raises(LLMError) as exc:
        await chat_json(llm, [{"role": "user", "content": "go"}], Doc, repair_attempts=1)
    assert exc.value.kind == "invalid_output"


async def test_chat_json_missing_field_repair():
    llm = MockLLM(replies=['{"notmarkdown": 1}', '{"markdown": "ok"}'])
    doc = await chat_json(llm, [{"role": "user", "content": "go"}], Doc, repair_attempts=1)
    assert doc.markdown == "ok"
