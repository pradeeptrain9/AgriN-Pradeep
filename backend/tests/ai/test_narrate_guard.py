

def test_a_field_with_no_crop_never_calls_the_model():
    """The answer is one fixed sentence asking for the crop. There is nothing
    for a model to rephrase, and this is the screen a farmer sees before the
    app has done anything for them -- the one most likely to be opened and
    abandoned. Billing it is the worst trade in the product."""
    from app.ai import narrate as narrate_mod

    called = []

    def explode(*args, **kwargs):
        called.append(args)
        raise AssertionError("the model was called for a field with no crop")

    original = narrate_mod._call_gemini
    narrate_mod._call_gemini = explode
    try:
        result = narrate_mod.narrate({"status": "no_crop"}, lang="hi")
    finally:
        narrate_mod._call_gemini = original

    assert called == []
    assert result.source == "template"


class TestNoCropScreen:
    """Reached before the app has done anything for the farmer.

    It must work with no model, no key and no network, and it must not be the
    one screen that is English-only -- the farmer who cannot read it is the
    farmer who never gets past it.
    """

    def test_every_indian_language_with_a_voice_has_the_strings(self):
        from app.ai.narrate import NO_CROP_STRINGS
        from app.providers.speech import LOCALES

        for lang in LOCALES:
            if lang == "en":
                continue
            assert lang in NO_CROP_STRINGS, (
                f"{lang} has a text-to-speech voice but no no-crop wording, so "
                "it would be read out in English"
            )

    def test_each_translation_is_complete(self):
        from app.ai.narrate import NO_CROP_STRINGS

        for lang, words in NO_CROP_STRINGS.items():
            for key in ("summary", "title", "detail", "explanation"):
                assert words.get(key), f"{lang} is missing {key}"

    def test_translations_are_not_left_in_english(self):
        from app.ai.narrate import NO_CROP_STRINGS

        english = "No crop is recorded for this field yet."
        for lang, words in NO_CROP_STRINGS.items():
            assert words["summary"] != english, f"{lang} was never translated"
            assert not words["summary"].isascii(), (
                f"{lang} reads as ASCII, which means the placeholder is still there"
            )

    def test_an_unsupported_language_says_so_rather_than_guessing(self):
        from app.ai.narrate import build_template_narration

        result = build_template_narration({"status": "no_crop"}, lang="ru")
        assert result.lang == "en"
        assert result.translated is False


class TestWhyTheTemplateAnswered:
    """A silent fallback is indistinguishable from a working node.

    Twice now the model stopped answering and nothing said so: the advice was
    correct, the farmer saw nothing wrong, and the only signal was a log line
    on a host nobody was reading. `guard_violations: []` plus "fell back to
    the built-in template" is true and tells an operator nothing.
    """

    def test_a_transport_failure_is_named(self):
        from app.ai import gemini, narrate as narrate_mod

        def refuse(*args, **kwargs):
            raise gemini.GeminiUnavailable("Gemini request failed (429): quota")

        original = narrate_mod._call_gemini
        available = narrate_mod.gemini.available
        narrate_mod._call_gemini = refuse
        narrate_mod.gemini.available = lambda settings: True
        try:
            result = narrate_mod.narrate({"status": "ok", "crop": {}}, lang="en")
        finally:
            narrate_mod._call_gemini = original
            narrate_mod.gemini.available = available

        assert result.source == "template"
        assert result.fallback_reason is not None
        assert "429" in result.fallback_reason
        assert any("429" in note for note in result.notes)

    def test_an_empty_completion_is_named_as_such(self):
        from app.ai import gemini, narrate as narrate_mod

        def empty(*args, **kwargs):
            raise gemini.GeminiRejected("empty completion (finishReason=MAX_TOKENS)")

        original = narrate_mod._call_gemini
        available = narrate_mod.gemini.available
        narrate_mod._call_gemini = empty
        narrate_mod.gemini.available = lambda settings: True
        try:
            result = narrate_mod.narrate({"status": "ok", "crop": {}}, lang="en")
        finally:
            narrate_mod._call_gemini = original
            narrate_mod.gemini.available = available

        assert "MAX_TOKENS" in (result.fallback_reason or "")

    def test_a_successful_narration_carries_no_reason(self):
        from app.ai.narrate import build_template_narration

        # The template is also the answer on a node with no key at all, and
        # that case has its own note -- inventing a failure there would send an
        # operator after a fault that does not exist.
        result = build_template_narration({"status": "no_crop"})
        assert result.fallback_reason is None
