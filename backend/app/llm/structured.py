"""Structured JSON output: request, parse, validate, bounded repair retries.

The model's tool/JSON calling is not assumed reliable — everything is
validated against a Pydantic schema and repaired at most
`max_repair_attempts` times.
"""

import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .gateway import LLMClient, LLMError

logger = logging.getLogger("cf.llm.structured")

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull a JSON document out of a possibly chatty LLM reply."""
    fenced = _FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    candidate = candidate.strip()
    if not candidate.startswith(("{", "[")):
        # Find the first brace/bracket that closes.
        starts = [i for i in (candidate.find("{"), candidate.find("[")) if i >= 0]
        if not starts:
            raise ValueError("no JSON object found in response")
        start = min(starts)
        opener = candidate[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        for i in range(start, len(candidate)):
            if candidate[i] == opener:
                depth += 1
            elif candidate[i] == closer:
                depth -= 1
                if depth == 0:
                    return candidate[start : i + 1]
        raise ValueError("unbalanced JSON in response")
    return candidate


async def chat_json(
    client: LLMClient,
    messages: list[dict[str, str]],
    schema: type[T],
    *,
    repair_attempts: int = 1,
    timeout: int | None = None,
) -> T:
    """Ask for JSON matching `schema`, validate, and repair bounded times."""
    schema_hint = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    convo = list(messages)
    convo.append(
        {
            "role": "user",
            "content": (
                "Respond with a single JSON document and nothing else. "
                f"It MUST validate against this JSON Schema:\n{schema_hint}"
            ),
        }
    )

    last_error = ""
    for attempt in range(repair_attempts + 1):
        reply = await client.chat(convo, timeout=timeout)
        try:
            return schema.model_validate_json(extract_json(reply.content))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:1000]
            logger.warning("structured output invalid (attempt %d): %s", attempt + 1, last_error)
            if attempt < repair_attempts:
                convo.append({"role": "assistant", "content": reply.content})
                convo.append(
                    {
                        "role": "user",
                        "content": (
                            "That response was invalid. Error:\n"
                            f"{last_error}\n"
                            "Return ONLY a corrected JSON document matching the schema."
                        ),
                    }
                )

    raise LLMError("invalid_output", f"Model output failed schema validation: {last_error}")
