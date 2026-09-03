"""Per-model request parameters.

Every entry here was established by sending the parameter to the live API and
reading the 400 back. They are not inferred from version numbers -- the support
matrix is not monotonic across generations.

This matters because both call sites treat a 400 as "the cloud is unavailable"
and quietly fall back to the deterministic template. A wrong row here does not
raise; it silently turns the feature off.
"""

import pytest

from app.ai import capabilities


class TestOpus5:
    """The default. Accepts everything."""

    def test_gets_refusal_fallbacks(self):
        kwargs = capabilities.request_kwargs("claude-opus-5", effort="low")
        assert kwargs["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in kwargs["betas"]

    def test_gets_adaptive_thinking(self):
        kwargs = capabilities.request_kwargs("claude-opus-5", effort="low")
        assert kwargs["thinking"] == {"type": "adaptive"}

    def test_gets_effort(self):
        kwargs = capabilities.request_kwargs("claude-opus-5", effort="high")
        assert kwargs["output_config"]["effort"] == "high"


class TestSonnet5:
    def test_rejects_fallbacks_so_they_are_omitted(self):
        # Live API: "'claude-sonnet-5' does not support the `fallbacks`
        # parameter."
        kwargs = capabilities.request_kwargs("claude-sonnet-5", effort="low")
        assert "fallbacks" not in kwargs
        assert "betas" not in kwargs

    def test_still_gets_thinking_and_effort(self):
        kwargs = capabilities.request_kwargs("claude-sonnet-5", effort="low")
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"]["effort"] == "low"


class TestHaiku45:
    def test_gets_no_thinking(self):
        # Live API: "adaptive thinking is not supported on this model".
        kwargs = capabilities.request_kwargs("claude-haiku-4-5", effort="low")
        assert "thinking" not in kwargs

    def test_gets_no_effort(self):
        kwargs = capabilities.request_kwargs("claude-haiku-4-5", effort="low")
        assert "effort" not in kwargs.get("output_config", {})

    def test_gets_no_fallbacks(self):
        kwargs = capabilities.request_kwargs("claude-haiku-4-5", effort="low")
        assert "fallbacks" not in kwargs

    def test_still_gets_the_response_schema(self):
        # The structured output is what makes the answer parseable at all, so
        # it must survive on every model.
        schema = {"type": "object", "properties": {}}
        kwargs = capabilities.request_kwargs(
            "claude-haiku-4-5", effort="low", schema=schema
        )
        assert kwargs["output_config"]["format"]["schema"] is schema


@pytest.mark.parametrize(
    "model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
)
def test_every_configurable_model_carries_the_schema(model):
    schema = {"type": "object", "properties": {}}
    kwargs = capabilities.request_kwargs(model, effort="low", schema=schema)
    assert kwargs["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
)
def test_every_configurable_model_is_priced(model):
    # A model that can be configured but not priced would spend real money and
    # record zero against the budget cap.
    from app.ai.budget import RATES

    assert model in RATES


def test_no_schema_means_no_format_key():
    kwargs = capabilities.request_kwargs("claude-opus-5", effort="low")
    assert "format" not in kwargs.get("output_config", {})


def test_an_unknown_model_gets_the_minimal_request():
    # Better a plain request that works than a tuned one that 400s into a
    # silent fallback.
    kwargs = capabilities.request_kwargs("some-future-model", effort="high")
    assert "thinking" not in kwargs
    assert "fallbacks" not in kwargs
    assert "effort" not in kwargs.get("output_config", {})
