

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
