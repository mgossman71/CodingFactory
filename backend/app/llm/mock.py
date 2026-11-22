"""Scripted mock LLM for tests. Never hits the network."""

import json
from dataclasses import dataclass, field

from .gateway import LLMError, LLMReply


@dataclass
class MockLLM:
    """Pops scripted replies in order. Records every call for assertions.

    Replies may be strings/dicts (dicts are JSON-encoded), or
    callables(messages) -> str. A callable may raise to simulate failures.
    """

    replies: list[object] = field(default_factory=list)
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    async def chat(
        self, messages: list[dict[str, str]], *, timeout: int | None = None
    ) -> LLMReply:
        self.calls.append(messages)
        if not self.replies:
            raise LLMError("mock_exhausted", "MockLLM has no scripted replies left")
        item = self.replies.pop(0)
        if callable(item):
            content = item(messages)
        elif isinstance(item, str):
            content = item
        else:
            content = json.dumps(item)
        return LLMReply(content=str(content), prompt_tokens=10, completion_tokens=5)
