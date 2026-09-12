"""Google Gemini: the one cloud model this node calls, for narration and vision.

Why this module looks like an adapter. The valuable thing in this codebase is
not which model writes the sentences -- it is `guard.py`, which refuses any
narration containing a figure the deterministic engine did not produce. That
guard never inspects the provider, and swapping the model out underneath it
required changing none of it: the same guard runs, the same corrective retry
runs, the same ledger bills it, and the same template catches it when it fails.

That migration is the evidence for the claim. If the safety property had been a
property of one vendor integration rather than of the system, replacing the
vendor would have broken it.

REST rather than the Python SDK, deliberately. `httpx` is already a dependency
and already carries this node's timeout and retry conventions; adding a vendor
SDK to the advisory path would put a second release cadence between a farmer and
their irrigation figure, in a project other countries are meant to deploy and
maintain themselves.

Two shapes have to be translated:

  * **Roles.** Messages are built with `assistant`, Gemini wants `model`. The
    guard's corrective round replays the rejected draft as a prior turn, so this
    matters on exactly the path where numbers are being argued about.

  * **Token accounting.** `promptTokenCount` already includes cached tokens, so
    billing both would double-count the cheap half. Thinking tokens bill as
    output and are added there. An unpriced model bills zero in `budget.py`,
    which would mean Gemini spend never reached the monthly cap -- so the rates
    live in the same table as the others.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Keys the JSON Schema drafts allow and Gemini's OpenAPI subset rejects. Sending
# one produces a 400 for the whole request, so an unknown key is dropped rather
# than forwarded.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "$schema", "additionalProperties", "definitions", "$defs", "$ref",
    "patternProperties", "const", "examples", "default",
})


class GeminiUnavailable(RuntimeError):
    """Transport, auth or quota. The caller should fall back."""


class GeminiRejected(RuntimeError):
    """The model refused, or returned something unusable."""


# --------------------------------------------------------------- response shim


@dataclass
class _Usage:
    """Named to match what `budget.price` reads, so the ledger is untouched."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class GeminiResponse:
    """The response surface the rest of the AI layer reads.

    `_extract_json` in both narrate.py and vision.py walks `.content` looking for
    blocks whose `.type` is "text". Matching that here is what lets the guard
    loop stay provider-blind.
    """

    content: list[_Block]
    usage: _Usage
    model: str
    stop_reason: str | None = None
    notes: list[str] = dc_field(default_factory=list)


# -------------------------------------------------------------------- helpers


def available(settings) -> bool:
    return bool(getattr(settings, "gemini_api_key", ""))


def sanitise_schema(schema: Any) -> Any:
    """Strip what Gemini's schema dialect will reject, recursively.

    Kept permissive on purpose: the schema is a convenience that makes parsing
    reliable, not a safety boundary. The safety boundary is the guard, which
    runs on the parsed result regardless of how well the schema was honoured.
    """
    if isinstance(schema, dict):
        return {
            key: sanitise_schema(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [sanitise_schema(item) for item in schema]
    return schema


def _role(role: str) -> str:
    # Anthropic's "assistant" is Gemini's "model". Everything else passes through
    # as "user", because a role Gemini does not know is a 400.
    return "model" if role == "assistant" else "user"


def _to_contents(messages: list[dict]) -> list[dict]:
    contents: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts: list[dict] = [{"text": content}]
        elif isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append({"text": block.get("text", "")})
                elif block.get("type") == "image":
                    source = block.get("source", {})
                    parts.append({
                        "inlineData": {
                            "mimeType": source.get("media_type", "image/jpeg"),
                            "data": source.get("data", ""),
                        }
                    })
        else:
            continue
        if parts:
            contents.append({"role": _role(message.get("role", "user")), "parts": parts})
    return contents


def _usage_from(payload: dict) -> _Usage:
    meta = payload.get("usageMetadata", {}) or {}

    def count(name: str) -> int:
        try:
            return int(meta.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    cached = count("cachedContentTokenCount")
    prompt = count("promptTokenCount")
    return _Usage(
        # promptTokenCount already includes the cached tokens. Billing both
        # would charge the cheap half twice and overstate the month.
        input_tokens=max(0, prompt - cached),
        cache_read_input_tokens=cached,
        # Thinking tokens are billed as output and are reported separately.
        output_tokens=count("candidatesTokenCount") + count("thoughtsTokenCount"),
    )


def _text_from(payload: dict) -> tuple[str, str | None]:
    candidates = payload.get("candidates") or []
    if not candidates:
        # An empty candidate list with a promptFeedback block means the *input*
        # was blocked, which is a different failure from a refused answer and
        # worth saying so in the log.
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise GeminiRejected(f"no candidates returned (blockReason={blocked})")

    candidate = candidates[0]
    finish = candidate.get("finishReason")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))

    if not text.strip():
        raise GeminiRejected(f"empty completion (finishReason={finish})")
    return text, finish


# ----------------------------------------------------------------------- call


def generate(
    *,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    schema: dict | None = None,
    max_output_tokens: int = 8192,
    temperature: float = 0.2,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
) -> GeminiResponse:
    """One completion, returned in the shape the rest of the AI layer expects."""
    body: dict[str, Any] = {
        "contents": _to_contents(messages),
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {
            # Low but not zero. The task is rephrasing fixed numbers, where
            # variety buys nothing and drift costs correctness.
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    if schema is not None:
        body["generationConfig"]["responseSchema"] = sanitise_schema(schema)

    owns = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            f"{BASE_URL}/{model}:generateContent",
            params={"key": api_key},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise GeminiUnavailable(f"Gemini unreachable: {exc}") from exc
    finally:
        if owns:
            client.close()

    if response.status_code != 200:
        # 400 is usually a schema this dialect will not take, 429 is quota, 403
        # is a key without the API enabled. All three mean "use the next
        # provider", and the body is the only thing that says which.
        raise GeminiUnavailable(
            f"Gemini request failed ({response.status_code}): {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise GeminiUnavailable(f"Gemini returned non-JSON: {exc}") from exc

    text, finish = _text_from(payload)

    return GeminiResponse(
        content=[_Block(text=text)],
        usage=_usage_from(payload),
        model=payload.get("modelVersion") or model,
        # The guard loop already treats "refusal" as fall-back-to-template.
        stop_reason="refusal" if finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"} else None,
    )


def image_message(image_bytes: bytes, media_type: str, text: str) -> dict:
    """A user turn carrying one photograph, in the provider-neutral block form."""
    return {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            },
            {"type": "text", "text": text},
        ],
    }


def json_from(response: GeminiResponse) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                continue
    return None
