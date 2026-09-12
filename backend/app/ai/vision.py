"""Cloud vision fallback for crop disease photos, using Gemini multimodal.

Reached only when the on-device gate refuses: an uncovered crop, low confidence,
a split decision, or a diffuse distribution. A large multimodal model generalises
to leaves and field conditions the small CNN never saw, which is exactly what
those cases need.

Ordered like every other provider chain here, for the same reason: one vendor
outage must not take crop diagnosis off a farmer's phone. The provider that
actually answered is recorded on the diagnosis, so provenance survives the
fallback instead of being averaged away.

Two deliberate constraints:

1. The on-device guess is NOT sent. Telling the model "the CNN thought this was
   blast but wasn't sure" anchors it toward that answer, and the whole reason we
   are here is that the CNN's answer is untrustworthy. It judges the photo cold.

2. The model chooses from a closed enum of taxonomy classes for that crop, plus
   "unknown". It cannot invent a disease name, and it is never asked for a
   chemical -- those come only from the verified allowlist in
   engine/treatments.py, keyed on whatever class it returns.
"""

import base64
import io
import json
import logging
from dataclasses import dataclass, field as dc_field

from app.ai.disease import GateDecision, Route, build_diagnosis
from app.ai.disease_taxonomy import DISEASES, classes_for_crop, get_disease
from app.ai import gemini
from app.config import get_settings

logger = logging.getLogger(__name__)

# Matches the app's capture size. A larger image costs proportionally more
# image tokens -- (w*h)/750 -- without helping a leaf fill the frame, and a
# gallery pick could otherwise be 1024px and triple the per-photo cost.
MAX_EDGE_PX = 640
JPEG_QUALITY = 80

SYSTEM_PROMPT = """\
You are a plant pathologist looking at a photograph of a crop leaf sent by a \
smallholder farmer.

Identify the disease from the supplied list of possibilities for this crop, or \
answer "unknown".

Rules:
- Choose only from the listed disease codes, or "unknown".
- Answer "unknown" when the photo is blurred, too dark, too far away, shows no \
leaf, or shows symptoms that do not match any listed option. A wrong \
confident answer costs a farmer a season; "unknown" costs them a second photo.
- Never name a pesticide, fungicide or any chemical product.
- Give your confidence honestly. Field photos are harder than textbook images.
- Describe only symptoms you can actually see in this image.
"""

USER_PROMPT = """\
Crop: {crop_label}

Possible diseases for this crop:
{options}

Identify what you see in the photograph."""


def _schema(crop_code: str) -> dict:
    codes = [d.code for d in classes_for_crop(crop_code)] + ["unknown"]
    return {
        "type": "object",
        "properties": {
            "disease_code": {"type": "string", "enum": codes},
            # No minimum/maximum: structured outputs reject range keywords on a
            # number and return 400. The bound is enforced below instead, where
            # it has to be anyway -- a schema the model satisfies is not a
            # promise about a value this code then divides by.
            "confidence": {"type": "number"},
            "visible_symptoms": {"type": "string"},
            "image_quality_issue": {"type": ["string", "null"]},
        },
        "required": [
            "disease_code",
            "confidence",
            "visible_symptoms",
            "image_quality_issue",
        ],
        "additionalProperties": False,
    }


def _clamped_confidence(value: object) -> float:
    """Confidence into 0..1, whatever the model returned.

    It is rendered as a percentage and compared against thresholds, so a value
    outside the range would show a farmer something like "140% confident".
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


OPEN_ENDED_SYSTEM_PROMPT = """\
You are a plant pathologist looking at a photograph of a crop leaf sent by a \
smallholder farmer.

This node has no verified disease list for this crop, so there is no list to \
choose from. Name what you see, or answer "unknown".

Rules:
- Give the common English name of the disease or pest, as plainly as you can. \
A farmer will repeat this name to an extension officer.
- Answer "unknown" when the photo is blurred, too dark, too far away, shows no \
leaf, or shows nothing you can identify. A wrong confident answer costs a \
farmer a season; "unknown" costs them a second photo.
- Never name a pesticide, fungicide or any chemical product, and never suggest \
a treatment. This node cannot check a treatment for this crop, so naming one \
would be worse than saying nothing.
- Give your confidence honestly. Field photos are harder than textbook images, \
and you are working without a list of the diseases known to occur here.
- Describe only symptoms you can actually see in this image.
"""

OPEN_ENDED_USER_PROMPT = """\
Crop: {crop_label}

There is no verified disease list for this crop on this node.

Name what you see in the photograph, or answer "unknown"."""


def _open_ended_schema() -> dict:
    """No enum: the point of this path is that there is no list to pick from.

    `disease_name` is free text and therefore keys into nothing -- not a
    treatment, not an IPM action, not a pesticide row. The caller must present
    it as an unconfirmed name and attach no advice to it.
    """
    return {
        "type": "object",
        "properties": {
            "disease_name": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "visible_symptoms": {"type": "string"},
            "image_quality_issue": {"type": ["string", "null"]},
        },
        "required": [
            "disease_name",
            "confidence",
            "visible_symptoms",
            "image_quality_issue",
        ],
        "additionalProperties": False,
    }


def prepare_image(raw: bytes) -> tuple[bytes, str]:
    """Downscale, re-encode as JPEG, and strip metadata.

    Re-encoding through a decode/encode cycle drops EXIF, which matters: phone
    photos routinely carry GPS coordinates, and a diagnosis request must not
    quietly ship a farmer's precise location to a third party.
    """
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        image = image.convert("RGB")
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
        buffer = io.BytesIO()
        # A fresh Image built from the pixel data carries no EXIF forward.
        clean = Image.new("RGB", image.size)
        clean.putdata(list(image.getdata()))
        clean.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue(), "image/jpeg"


def _extract_json(response) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                continue
    return None


@dataclass
class VisionIdentification:
    """What the model saw. Identification only -- no treatment attached yet.

    Kept separate from the Diagnosis so the caller can look up verified
    chemicals for the identified disease BEFORE the diagnosis is assembled.
    Building the diagnosis first and attaching chemicals afterwards produced a
    contradiction: the diagnosis would carry a "no chemical treatment is shown"
    note alongside a chemical.
    """

    disease_code: str | None
    confidence: float
    notes: list[str] = dc_field(default_factory=list)
    # Set only on the open-ended path, for crops this node has no verified
    # disease list for. It is a name and nothing more: it keys into no
    # treatment, no IPM action and no pesticide row, and must never be
    # presented as though it did.
    provisional_name: str | None = None
    # What this call cost, for the ledger. None when no call was made -- an
    # unconfigured key, a cap already reached, a refusal before billing.
    usage: object | None = None
    model: str | None = None

    @property
    def identified(self) -> bool:
        return self.disease_code is not None


def _identification_from(
    data: dict, *, notes: list[str], usage: object, model: str | None
) -> VisionIdentification:
    """Turn a parsed cloud answer into an identification, provider-agnostic.

    Shared so that both providers are held to the same refusals: an unknown
    code, or one outside this node's disease list, is not an identification no
    matter which model produced it. `model` carries the provenance -- which is
    where it belongs, rather than in the route, since the route records that the
    photograph was escalated at all.
    """
    billed = {"usage": usage, "model": model}

    quality_issue = data.get("image_quality_issue")
    if quality_issue:
        notes.append(f"Photo quality: {quality_issue}")

    code = data.get("disease_code")
    if code == "unknown" or code not in DISEASES:
        notes.append(
            "The photograph could not be matched to a known disease for this crop."
        )
        return VisionIdentification(
            disease_code=None, confidence=0.0, notes=notes, **billed
        )

    symptoms = data.get("visible_symptoms")
    if symptoms:
        notes.append(f"What the image shows: {symptoms}")
    notes.append(
        "This identification came from a photograph alone. Confirm it with your "
        "extension officer before spending money on treatment."
    )
    return VisionIdentification(
        disease_code=code,
        confidence=_clamped_confidence(data.get("confidence")),
        notes=notes,
        **billed,
    )


def identify_with_vision(
    image_bytes: bytes,
    *,
    crop_code: str,
    crop_label: str,
    client=None,
) -> VisionIdentification:
    """Ask the cloud model what disease the photo shows. Never returns a guess.

    An unavailable model, a refusal, an unreadable answer or "unknown" all come
    back as an unidentified result, which the caller renders as inconclusive.
    """
    settings = get_settings()
    notes: list[str] = []

    def unidentified(reason: str, **billed) -> VisionIdentification:
        notes.append(reason)
        return VisionIdentification(
            disease_code=None, confidence=0.0, notes=notes, **billed
        )

    if not gemini.available(settings) and client is None:
        return unidentified(
            "The on-device model was not confident enough, and no cloud "
            "diagnosis is configured on this node. Show the plant to your "
            "extension officer."
        )

    prepared, media_type = prepare_image(image_bytes)

    options = "\n".join(
        f"- {d.code}: {d.label_en}" + (f" ({d.notes})" if d.notes else "")
        for d in classes_for_crop(crop_code)
    )

    # The escalation only happens when the on-device model already declined to
    # answer, so this is the harder half of the photographs by construction --
    # which is the half where abstaining correctly matters most.
    try:
        response = gemini.generate(
            api_key=settings.gemini_api_key,
            model=settings.gemini_vision_model,
            system=SYSTEM_PROMPT,
            messages=[
                gemini.image_message(
                    prepared,
                    media_type,
                    USER_PROMPT.format(crop_label=crop_label, options=options),
                )
            ],
            schema=_schema(crop_code),
        )
    except (gemini.GeminiUnavailable, gemini.GeminiRejected) as exc:
        logger.warning("cloud diagnosis unavailable (%s)", exc)
        return unidentified("Cloud diagnosis could not be reached.")

    # Billed from here whatever the outcome. A refused or unreadable answer
    # costs the same as a good one and has to reach the ledger -- otherwise the
    # cheapest way to look under budget is to fail.
    billed = {"usage": response.usage, "model": response.model}

    if response.stop_reason == "refusal":
        return unidentified("Cloud diagnosis declined to answer.", **billed)

    data = _extract_json(response)
    if data is None:
        return unidentified("Cloud diagnosis returned an unreadable answer.", **billed)

    return _identification_from(
        data, notes=notes, usage=billed["usage"], model=billed["model"]
    )


def diagnose_with_vision(
    image_bytes: bytes,
    *,
    crop_code: str,
    crop_label: str,
    decision: GateDecision,
    chemical_options: list[dict] | None = None,
    client=None,
):
    """Identify, then assemble a Diagnosis. Chemicals must be supplied up front."""
    identification = identify_with_vision(
        image_bytes, crop_code=crop_code, crop_label=crop_label, client=client
    )
    disease = get_disease(identification.disease_code) if identification.identified else None
    return build_diagnosis(
        disease=disease,
        crop_code=crop_code,
        confidence=identification.confidence,
        resolved_by=(
            Route.CLOUD_VISION.value if identification.identified
            else Route.INCONCLUSIVE.value
        ),
        decision=decision,
        chemical_options=chemical_options or [],
        extra_notes=identification.notes,
    )
