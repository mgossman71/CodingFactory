"""Scripted mock LLM for tests. Never hits the network."""

import inspect
import json
from dataclasses import dataclass, field

from .gateway import LLMError, LLMReply


@dataclass
class MockLLM:
    """Pops scripted replies in order. Records every call for assertions.

    Replies may be strings/dicts (dicts are JSON-encoded), or
    callables(messages) -> str. A callable may raise to simulate failures.
    A callable may also be a coroutine function — tests use that to hold a
    call "in flight" while the rest of the system acts around it (D-026).
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
            if inspect.isawaitable(content):
                content = await content
        else:
            content = item
        if not isinstance(content, str):
            content = json.dumps(content)
        return LLMReply(content=str(content), prompt_tokens=10, completion_tokens=5)
