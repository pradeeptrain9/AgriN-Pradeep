"""Google Cloud Text-to-Speech, so an advisory can be listened to.

The reason this is not a nice-to-have. India's agricultural workforce has
markedly lower literacy than the national average, and it skews female and
older. An app that only writes excludes exactly the people least likely to reach
an extension officer -- the ones the whole node exists for. A farmer who cannot
comfortably read 200 words of Hindi can listen to them.

What is spoken is the narration that already passed `guard.py`. That ordering is
the point: audio is generated from text whose every figure was checked against
the deterministic payload, so listening cannot expose a number that reading
would not. If the guard rejected the narration and the template was used
instead, the template is what gets spoken.

Voice selection is by language, and deliberately by language *only*. Google
publishes several voices per Indian language; picking among them by any
attribute other than availability would be a preference dressed as engineering.
The first standard voice for the locale is used, which is also the cheapest --
WaveNet and Neural2 cost several times more per character and this is a public
service that has to survive its own success.

Cost control: synthesis is billed per character, so a long advisory is a real
cost repeated on every screen open. The caller caches by content hash, the same
way narration is cached, so re-reading yesterday's advice is free.
"""

from __future__ import annotations

import base64
import logging

import httpx

logger = logging.getLogger(__name__)

URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Cloud TTS rejects a request above 5000 bytes outright. An advisory is far
# shorter, but a narration that ran long should degrade to no audio rather than
# to a 400 the caller has to interpret.
MAX_CHARACTERS = 4800

# BCP-47 locales for the languages the narration layer already supports. Absent
# means no audio for that language rather than audio in the wrong one, which
# would be worse than silence -- a farmer hearing confident Hindi for a Telugu
# advisory has no way to know the words do not match the screen.
LOCALES: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "pa": "pa-IN",
    "bn": "bn-IN",
    "mr": "mr-IN",
    "te": "te-IN",
}


class SpeechUnavailable(RuntimeError):
    pass


def locale_for(lang: str) -> str | None:
    return LOCALES.get(lang)


async def synthesise(
    text: str,
    *,
    api_key: str,
    lang: str = "en",
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Spoken audio for one narration. Returns (mp3 bytes, locale).

    Raises rather than returning silence, so the caller can tell a farmer the
    audio is unavailable instead of handing them a player that does nothing.
    """
    if not api_key:
        raise SpeechUnavailable("No text-to-speech key is configured on this node.")

    locale = locale_for(lang)
    if locale is None:
        raise SpeechUnavailable(f"No voice is configured for {lang}.")

    spoken = text.strip()
    if not spoken:
        raise SpeechUnavailable("There is nothing to read out.")
    if len(spoken) > MAX_CHARACTERS:
        # Cut at a sentence end so the audio does not stop mid-figure, which
        # would be the one failure mode that could mislead: "apply 40" is a
        # different instruction from "apply 40 kilograms per hectare".
        cut = spoken[:MAX_CHARACTERS]
        stop = max(cut.rfind("."), cut.rfind("।"), cut.rfind("\n"))
        spoken = cut[: stop + 1] if stop > 0 else cut

    body = {
        "input": {"text": spoken},
        "voice": {"languageCode": locale},
        "audioConfig": {
            "audioEncoding": "MP3",
            # Slightly slower than default. This is instructional speech about
            # quantities, listened to once, often outdoors.
            "speakingRate": 0.92,
        },
    }

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            URL, params={"key": api_key}, json=body, timeout=30.0
        )
    except httpx.HTTPError as exc:
        raise SpeechUnavailable(f"Text-to-speech unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    if response.status_code != 200:
        raise SpeechUnavailable(
            f"Text-to-speech failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        encoded = (response.json() or {}).get("audioContent")
    except ValueError as exc:
        raise SpeechUnavailable(f"Text-to-speech returned non-JSON: {exc}") from exc

    if not encoded:
        raise SpeechUnavailable("Text-to-speech returned no audio.")

    return base64.b64decode(encoded), locale
