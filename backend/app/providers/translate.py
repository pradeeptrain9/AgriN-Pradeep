"""Google Cloud Translation, for the advice a language model never touched.

I argued this was redundant because Gemini already narrates in Hindi and
Punjabi. Reading the fallback path properly shows it is not, and the gap is on
the worst possible path.

`build_template_narration` produces the deterministic wording used whenever the
model is unavailable, over budget, or caught inventing a figure. That template
is **English only**. So the farmer most likely to get English is the one whose
node ran out of credit or whose narration was rejected by the guard -- and the
note they receive politely explains, in English, that the text is in English.

Translating the template is a different job from narrating. There is no
generation here and nothing to guard against: the sentences were produced by the
engine, the numbers inside them are already the engine's own, and a translator
that changes a number is a defect rather than a plausible output. Every string
is still checked after translation for exactly that reason -- if the digits move,
the English is kept.

Not used on the model path. A narration Gemini already wrote in Hindi must not
be round-tripped through a second machine; that would add error and cost to text
that is already correct.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

URL = "https://translation.googleapis.com/language/translate/v2"

# Google's codes for the languages the narration layer already lists. Absent
# means the English template is returned unchanged, which is honest, rather than
# guessing at a near-neighbour language.
TARGETS: dict[str, str] = {
    "hi": "hi",
    "pa": "pa",
    "bn": "bn",
    "mr": "mr",
    "te": "te",
}

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class TranslationUnavailable(RuntimeError):
    pass


def _digits(text: str) -> list[str]:
    """Numbers as written, with separators stripped so 1,250 == 1250."""
    return [m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text)]


def numbers_survived(source: str, translated: str) -> bool:
    """Every figure in the English must appear, unchanged, in the translation.

    Translation engines localise digits and separators -- Devanagari numerals,
    the Indian grouping system, a decimal comma. Any of those changes the string
    while meaning the same thing, and this check would reject it. That is the
    intended direction: a farmer reading correct English beats a farmer reading
    a dose this node cannot prove is the dose the engine computed.
    """
    return _digits(source) == _digits(translated)


async def translate(
    strings: list[str],
    *,
    api_key: str,
    lang: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Translate template strings, falling back to English per string.

    Never raises for a bad translation -- only for a missing key or an
    unreachable service, which the caller reports as "shown in English".
    """
    if not api_key:
        raise TranslationUnavailable("No translation key is configured on this node.")

    target = TARGETS.get(lang)
    if target is None:
        raise TranslationUnavailable(f"No translation target for {lang}.")

    payload = [s for s in strings if s and s.strip()]
    if not payload:
        return list(strings)

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            URL,
            params={"key": api_key},
            json={"q": payload, "target": target, "source": "en", "format": "text"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise TranslationUnavailable(f"Translation unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    if response.status_code != 200:
        raise TranslationUnavailable(
            f"Translation failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        items = (response.json() or {}).get("data", {}).get("translations", [])
    except ValueError as exc:
        raise TranslationUnavailable(f"Translation returned non-JSON: {exc}") from exc

    produced = [item.get("translatedText", "") for item in items]
    if len(produced) != len(payload):
        raise TranslationUnavailable("Translation returned a different number of strings.")

    out: list[str] = []
    index = 0
    for original in strings:
        if not original or not original.strip():
            out.append(original)
            continue
        candidate = produced[index]
        index += 1
        if candidate and numbers_survived(original, candidate):
            out.append(candidate)
        else:
            # Per string, not all-or-nothing: one rejected sentence should not
            # push the whole advisory back to English.
            logger.warning("translation dropped: figures changed or empty result")
            out.append(original)
    return out
