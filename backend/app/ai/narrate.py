"""Turn a deterministic advisory payload into plain language.

The model rephrases; it never calculates. Every figure in the output must
already exist in the engine payload, enforced mechanically by `ai/guard.py`
rather than by trusting the prompt.

Three layers, in order of preference:

  1. Gemini, schema-constrained, output checked by the guard
  2. Gemini again, once, with the violations fed back
  3. the deterministic English template, which needs no API key and no network

Layer 3 is not a degraded mode to apologise for. It is what runs offline, what
runs when the key is missing, and what runs when the model writes a number it
should not have. The app is fully usable on it.

There is no second model. That is a deliberate narrowing: the guard, the
corrective round and the template do not care which model was asked, so a
fallback provider bought resilience rather than safety, and resilience is
already covered by layer 3.
"""

import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Any

from app.ai.guard import check_narration
from app.ai import gemini
from app.config import get_settings

logger = logging.getLogger(__name__)

# Farmer-facing languages. English is the template's own language; the rest
# require the model.
LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "pa": "Punjabi",
    "bn": "Bengali",
    "mr": "Marathi",
    "te": "Telugu",
    "pt": "Brazilian Portuguese",
    "ru": "Russian",
    "zh": "Simplified Chinese",
    "zu": "isiZulu",
}

NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two short sentences on the state of the field.",
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "urgency": {
                        "type": "string",
                        "enum": ["now", "this_week", "this_season", "info"],
                    },
                },
                "required": ["title", "detail", "urgency"],
                "additionalProperties": False,
            },
        },
        "explanation": {
            "type": "string",
            "description": "Why the advice is what it is, in plain words.",
        },
    },
    "required": ["summary", "actions", "explanation"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You write farm advisories for small and marginal farmers. Many have limited \
formal schooling and are reading on a small phone screen.

You are given a JSON advisory that has already been calculated by an agronomic \
engine. Your only job is to express it in clear, plain {language}.

Absolute rules:
- NEVER write a number that does not appear in the JSON. Not a dose, not a \
depth, not a date, not a percentage. If you want to state a quantity, copy it \
from the JSON.
- Never invent an agronomic recommendation that is not in the JSON. If the JSON \
says crop health is unknown, say it is unknown; do not guess a cause.
- If the JSON reports a gap or stale data, tell the farmer plainly.
- Do not add pesticide or chemical product names.

Style:
- Short sentences. Everyday words. Speak directly to the farmer as "you".
- Lead with what to do today, then what to watch.
- No greetings, no sign-off, no markdown formatting.
"""

USER_TEMPLATE = """\
Here is the advisory data for the field.

{payload}

Write the narration in {language}."""


@dataclass
class NarrationResult:
    summary: str
    actions: list[dict]
    explanation: str
    lang: str
    source: str                       # gemini | template
    model: str | None = None
    guard_violations: list[str] = dc_field(default_factory=list)
    retried: bool = False
    translated: bool = True
    notes: list[str] = dc_field(default_factory=list)
    # One entry per billed request. The guard's corrective round makes a second
    # call, and a retry that is not recorded is spend that does not exist as far
    # as the budget is concerned.
    usages: list[object] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "actions": self.actions,
            "explanation": self.explanation,
            "lang": self.lang,
            "source": self.source,
            "model": self.model,
            "guard_violations": self.guard_violations,
            "retried": self.retried,
            "translated": self.translated,
            "notes": self.notes,
        }


# --------------------------------------------------------------- the template
def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "not known"
    if isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return f"{text}{suffix}"


def build_template_narration(payload: dict) -> NarrationResult:
    """Deterministic English narration. No model, no network, no API key."""
    actions: list[dict] = []
    notes: list[str] = []

    if payload.get("status") == "no_crop":
        return NarrationResult(
            summary="No crop is recorded for this field yet.",
            actions=[
                {
                    "title": "Add your crop",
                    "detail": "Tell the app which crop you sowed and the date you "
                    "sowed it. Advice cannot start without it.",
                    "urgency": "now",
                }
            ],
            explanation="Every recommendation depends on the crop and how many "
            "days it has been growing.",
            lang="en",
            source="template",
            translated=True,
        )

    crop = payload.get("crop") or {}
    health = payload.get("health") or {}
    irrigation = payload.get("irrigation") or {}
    nutrients = payload.get("nutrients") or {}

    # ---- irrigation
    if irrigation:
        if irrigation.get("irrigate_now"):
            actions.append(
                {
                    "title": "Irrigate today",
                    "detail": (
                        f"Apply about {_fmt(irrigation.get('gross_depth_mm'), ' mm')} "
                        f"of water, which delivers {_fmt(irrigation.get('recommended_depth_mm'), ' mm')} "
                        "to the crop after losses."
                    ),
                    "urgency": "now",
                }
            )
        elif irrigation.get("forecast_irrigation_date"):
            actions.append(
                {
                    "title": "Next irrigation",
                    "detail": (
                        f"Plan to irrigate on {irrigation['forecast_irrigation_date']}, "
                        f"in {_fmt(irrigation.get('days_until_irrigation'))} days. "
                        f"Apply about {_fmt(irrigation.get('gross_depth_mm'), ' mm')}."
                    ),
                    "urgency": "this_week",
                }
            )
        else:
            actions.append(
                {
                    "title": "No irrigation needed now",
                    "detail": "The field has enough water for the days ahead.",
                    "urgency": "info",
                }
            )
        for note in irrigation.get("notes") or []:
            actions.append({"title": "Water note", "detail": note, "urgency": "info"})

    # ---- health
    severity = health.get("severity")
    if severity == "alert":
        actions.append(
            {
                "title": "Check the crop on foot",
                "detail": (
                    "Satellite greenness is well below what this crop should show "
                    f"at {_fmt(crop.get('days_after_sowing'))} days after sowing. "
                    "Walk the field and look for pests, disease or waterlogging."
                ),
                "urgency": "now",
            }
        )
    elif severity == "watch":
        actions.append(
            {
                "title": "Keep an eye on the crop",
                "detail": "Greenness is a little below normal for this stage.",
                "urgency": "this_week",
            }
        )
    elif severity == "unknown":
        actions.append(
            {
                "title": "Crop health not available",
                "detail": "There is no recent clear satellite image of this field, "
                "so health cannot be scored yet.",
                "urgency": "info",
            }
        )
    for note in health.get("notes") or []:
        actions.append({"title": "Health note", "detail": note, "urgency": "info"})

    # ---- nutrients
    if nutrients:
        n, p, k = nutrients.get("n") or {}, nutrients.get("p2o5") or {}, nutrients.get("k2o") or {}
        products = nutrients.get("products_kg_ha") or {}
        actions.append(
            {
                "title": "Fertiliser for the season",
                "detail": (
                    f"Nitrogen {_fmt(n.get('low_kg_ha'))} to {_fmt(n.get('high_kg_ha'))} kg per hectare, "
                    f"phosphate {_fmt(p.get('low_kg_ha'))} to {_fmt(p.get('high_kg_ha'))}, "
                    f"potash {_fmt(k.get('low_kg_ha'))} to {_fmt(k.get('high_kg_ha'))}. "
                    f"That is about {_fmt(products.get('urea'))} kg urea, "
                    f"{_fmt(products.get('dap'))} kg DAP and {_fmt(products.get('mop'))} kg MOP per hectare."
                ),
                "urgency": "this_season",
            }
        )
        for split in nutrients.get("splits") or []:
            if split.get("n_kg_ha"):
                actions.append(
                    {
                        "title": f"Nitrogen dose: {split.get('when')}",
                        "detail": (
                            f"Apply {_fmt(split.get('n_kg_ha'))} kg N per hectare at "
                            f"{_fmt(split.get('days_after_sowing'))} days after sowing."
                        ),
                        "urgency": "this_season",
                    }
                )
        for action in nutrients.get("regenerative_actions") or []:
            actions.append(
                {"title": "Soil health", "detail": action, "urgency": "this_season"}
            )

    # ---- rotation
    rotation = payload.get("rotation") or []
    if rotation:
        best = rotation[0]
        reasons = "; ".join(best.get("reasons") or [])
        actions.append(
            {
                "title": f"Next season: consider {best.get('label')}",
                "detail": reasons or "Suited to your soil, rainfall and last crop.",
                "urgency": "this_season",
            }
        )

    for gap in payload.get("gaps") or []:
        notes.append(gap)

    stage = crop.get("stage")
    summary = (
        f"Your {crop.get('label', 'crop')} is at the {stage} stage, "
        f"{_fmt(crop.get('days_after_sowing'))} days after sowing."
    )
    if irrigation.get("irrigate_now"):
        summary += " It needs water today."
    elif severity == "alert":
        summary += " The crop needs checking on foot."

    soil = payload.get("soil") or {}
    explanation = (
        "This advice comes from satellite images of your field, local weather, "
        f"and your soil information (source: {soil.get('source', 'unknown')}, "
        f"confidence {soil.get('confidence', 'unknown')}). "
        "Every quantity here is calculated, not guessed."
    )

    return NarrationResult(
        summary=summary,
        actions=actions,
        explanation=explanation,
        lang="en",
        source="template",
        translated=True,
        notes=notes,
    )


# ------------------------------------------------------------------ the model
def _extract_json(response: Any) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                continue
    return None


def _call_gemini(api_key: str, model: str, language: str, messages: list[dict]):
    return gemini.generate(
        api_key=api_key,
        model=model,
        system=SYSTEM_PROMPT.format(language=language),
        messages=messages,
        schema=NARRATION_SCHEMA,
        max_output_tokens=8192,
    )


def _guarded(
    call,
    *,
    source: str,
    model: str,
    payload: dict,
    lang: str,
    messages: list[dict],
    billed: list,
    errors: tuple,
) -> NarrationResult | None:
    """Run one provider through the guard. None means "try the next thing".

    This function is the reason a second provider was cheap to add, and the
    reason adding it could not weaken anything: the rule that every figure must
    trace to the engine is enforced here, once, for whoever is answering. The
    corrective round is here too, so a model that invents a number gets exactly
    one chance to withdraw it regardless of who made it.
    """
    for attempt in (1, 2):
        try:
            response = call(messages)
        except errors as exc:
            logger.warning("%s narration unavailable (%s)", source, exc)
            return None

        usage = getattr(response, "usage", None)
        if usage is not None:
            billed.append(usage)

        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning("%s narration refused by the model", source)
            return None

        data = _extract_json(response)
        if data is None:
            logger.warning("%s narration was not valid JSON", source)
            return None

        violations = check_narration(data, payload)
        if not violations:
            return NarrationResult(
                summary=data.get("summary", ""),
                actions=data.get("actions", []),
                explanation=data.get("explanation", ""),
                lang=lang,
                source=source,
                model=getattr(response, "model", model),
                retried=attempt == 2,
                translated=True,
                usages=billed,
            )

        if attempt == 1:
            # One corrective round: name the offending figures and try again.
            logger.info("%s narration guard rejected output: %s", source, violations)
            messages = messages + [
                {"role": "assistant", "content": json.dumps(data)},
                {
                    "role": "user",
                    "content": (
                        "That draft used numbers that are not in the advisory data:\n"
                        + "\n".join(f"- {v}" for v in violations)
                        + "\n\nRewrite it using only figures that appear in the JSON. "
                        "If you cannot support a quantity, describe it in words "
                        "instead of giving a number."
                    ),
                },
            ]
            continue

        logger.warning("%s narration still unsupported after retry", source)
        _LAST_VIOLATIONS.clear()
        _LAST_VIOLATIONS.extend(violations)
        return None

    return None


# Carries the guard's complaint out of _guarded so the template can report it.
# A list rather than a return value because a guard failure and a transport
# failure both mean "try the next provider", and collapsing them into one
# signal keeps the fallback logic readable.
_LAST_VIOLATIONS: list[str] = []


def narrate(payload: dict, *, lang: str = "en", client=None) -> NarrationResult:
    """Narrate an advisory payload, falling back to the template on any problem.

    Gemini, then the deterministic template. Every attempt passes through the
    same guard, and `source` records which one actually produced the words the
    farmer read.
    """
    settings = get_settings()
    language = LANGUAGES.get(lang, LANGUAGES["en"])
    template = build_template_narration(payload)
    _LAST_VIOLATIONS.clear()

    compact = json.dumps(payload, sort_keys=True, default=str)
    messages: list[dict] = [
        {"role": "user", "content": USER_TEMPLATE.format(payload=compact, language=language)}
    ]

    billed: list[object] = []
    template.usages = billed

    # --- Gemini first.
    if client is None and gemini.available(settings):
        model = settings.gemini_narrate_model
        result = _guarded(
            lambda msgs: _call_gemini(settings.gemini_api_key, model, language, msgs),
            source="gemini",
            model=model,
            payload=payload,
            lang=lang,
            messages=messages,
            billed=billed,
            errors=(gemini.GeminiUnavailable, gemini.GeminiRejected),
        )
        if result is not None:
            result.notes = list(template.notes)
            return result

    # No second provider. Gemini or the deterministic template -- and the
    # template is not a failure mode, it is the same advice in the same plain
    # language, because the advice was never the model's to make.
    if _LAST_VIOLATIONS:
        template.guard_violations = list(_LAST_VIOLATIONS)
        template.notes.append(
            "The generated wording used figures that are not in the calculated "
            "advice, so the built-in template was used instead."
        )
    elif client is None and not gemini.available(settings):
        # No key at all is a different situation from a model that failed, and
        # the difference matters to whoever is reading it: one is a
        # configuration the operator can fix, the other is weather. It also has
        # to say the text is English, because the template only exists in
        # English -- rendering it under a Hindi heading with no warning would
        # leave a farmer assuming the app simply does not work in their
        # language.
        template.notes.append(
            "Narration used the built-in template because no language model "
            "key is configured."
            + ("" if lang == "en" else f" Text is in English, not {language}.")
        )
        template.translated = lang == "en"
        template.lang = "en"
    else:
        template.notes.append("Narration fell back to the built-in template.")
    return template
