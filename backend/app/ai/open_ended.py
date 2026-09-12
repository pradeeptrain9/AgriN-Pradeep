"""Naming a disease on a crop this node has no verified list for.

Nine of the fourteen crops in the registry have no disease taxonomy, and until
now a photograph of one of them could not be checked at all -- the structured
output enum in `vision._schema` was literally `["unknown"]`, so the model was
forbidden from saying what it could plainly see.

Claude's vision capability was never the constraint. The enum was, and it was
there for a good reason: a `disease_code` is the key into IPM actions and into
the verified pesticide allowlist, so a code the model invented would key into
nothing, or worse, into the wrong row.

This module takes the other road. It asks for a **name and nothing else**, and
the name keys into nothing by construction:

    identification -> a name a farmer can repeat to an extension officer
                   -> no disease_code
                   -> no ipm_actions
                   -> no chemical_options

The safety property is unchanged: a chemical is still only ever served from a
row somebody verified against a national register. What changes is that a
farmer with a diseased cotton plant now gets a name to take to a human, instead
of silence.
"""

from __future__ import annotations

import base64
import logging

from app.ai.vision import (
    OPEN_ENDED_SYSTEM_PROMPT,
    OPEN_ENDED_USER_PROMPT,
    VisionIdentification,
    _open_ended_schema,
    prepare_image,
)
from app.ai import capabilities
from app.config import get_settings

logger = logging.getLogger(__name__)

# A name longer than this is a sentence, not a disease name, and a farmer
# cannot repeat it. Truncating is better than rendering a paragraph as a title.
MAX_NAME_CHARS = 80


def identify_open_ended(
    image_bytes: bytes,
    *,
    crop_label: str,
    client=None,
) -> VisionIdentification:
    """Name what the photograph shows, for a crop with no verified list.

    Returns a VisionIdentification whose `disease_code` is always None and
    whose `provisional_name` carries the free-text name. Every failure mode --
    no key, an unreachable model, a refusal, an unreadable answer, "unknown" --
    comes back the same way the coded path does: unidentified, with a reason.
    """
    settings = get_settings()
    notes: list[str] = []

    def unidentified(reason: str, **billed) -> VisionIdentification:
        notes.append(reason)
        return VisionIdentification(
            disease_code=None, confidence=0.0, notes=notes, **billed
        )

    if client is None:
        if not settings.anthropic_api_key:
            return unidentified(
                "This crop has no disease list on this node, and no cloud "
                "diagnosis is configured. Show the plant to your extension "
                "officer."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        import anthropic
    except ImportError:  # pragma: no cover - dependency is pinned
        return unidentified("Cloud diagnosis is unavailable.")

    prepared, media_type = prepare_image(image_bytes)
    encoded = base64.standard_b64encode(prepared).decode("utf-8")

    try:
        response = client.beta.messages.create(
            model=settings.claude_vision_model,
            max_tokens=16000,
            **capabilities.request_kwargs(
                settings.claude_vision_model,
                effort=settings.claude_vision_effort,
                schema=_open_ended_schema(),
            ),
            system=OPEN_ENDED_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {
                            "type": "text",
                            "text": OPEN_ENDED_USER_PROMPT.format(
                                crop_label=crop_label
                            ),
                        },
                    ],
                }
            ],
        )
    except (
        anthropic.BadRequestError,
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        anthropic.NotFoundError,
        anthropic.RateLimitError,
        anthropic.APIStatusError,
        anthropic.APIConnectionError,
    ) as exc:
        logger.warning("open-ended diagnosis unavailable (%s)", exc)
        return unidentified("Cloud diagnosis could not be reached.")

    # Billed from here whatever the outcome, so every return below carries it.
    billed = {
        "usage": getattr(response, "usage", None),
        "model": settings.claude_vision_model,
    }

    if getattr(response, "stop_reason", None) == "refusal":
        return unidentified("Cloud diagnosis declined to answer.", **billed)

    from app.ai.vision import _extract_json, _clamped_confidence

    data = _extract_json(response)
    if data is None:
        return unidentified(
            "Cloud diagnosis returned an unreadable answer.", **billed
        )

    quality_issue = data.get("image_quality_issue")
    if quality_issue:
        notes.append(f"Photo quality: {quality_issue}")

    raw_name = data.get("disease_name")
    name = str(raw_name).strip() if raw_name else ""
    if not name or name.lower() in {"unknown", "none", "null"}:
        return unidentified(
            "The photograph could not be matched to a disease.", **billed
        )
    name = name[:MAX_NAME_CHARS]

    symptoms = data.get("visible_symptoms")
    if symptoms:
        notes.append(f"What the image shows: {symptoms}")

    # The caveat is not decoration. This name was produced without a list of
    # the diseases known to occur in this crop here, nothing downstream checked
    # it, and no treatment is attached to it.
    notes.append(
        f"This node has no verified disease list for {crop_label}, so this "
        "name is a suggestion from a photograph alone and has not been checked "
        "against anything."
    )
    notes.append(
        "No treatment is given for this crop. Take this name to your extension "
        "officer before buying or spraying anything."
    )

    return VisionIdentification(
        disease_code=None,
        confidence=_clamped_confidence(data.get("confidence")),
        notes=notes,
        provisional_name=name,
        **billed,
    )
