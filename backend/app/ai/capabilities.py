"""Which request parameters each model actually accepts.

Sending a parameter a model does not support is a 400, and both call sites here
treat a 400 as "the cloud is unavailable" and fall back. So a node configured
with a cheaper model did not get cheaper narration -- it got no narration at
all, silently, with the deterministic template standing in and nothing in the
logs a operator would read as a misconfiguration.

That is worth a table rather than a comment, because the whole point of making
the model configurable is that somebody will configure it.

Sources: the Anthropic model documentation. Verify when adding a model; the
support matrix is not monotonic across generations.
"""

from __future__ import annotations

# Adaptive thinking: current-generation models only.
_ADAPTIVE_THINKING = {
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
}

# output_config.effort. Notably absent: Haiku 4.5, which 400s on it.
_EFFORT = _ADAPTIVE_THINKING

# Server-side refusal fallbacks. Opus 5 and Fable 5 only -- Sonnet 5 rejects the
# parameter outright.
_FALLBACKS = {"claude-fable-5", "claude-opus-5"}


def request_kwargs(
    model: str, *, effort: str, schema: dict | None = None
) -> dict:
    """The subset of tuning parameters this model will accept.

    Anything the model does not support is omitted rather than translated. A
    silently different request is worse than a plainly simpler one: on a model
    without adaptive thinking the honest thing is to send no thinking parameter
    and let it answer directly.
    """
    kwargs: dict = {}

    if model in _FALLBACKS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"

    if model in _ADAPTIVE_THINKING:
        kwargs["thinking"] = {"type": "adaptive"}

    output_config: dict = {}
    if model in _EFFORT:
        output_config["effort"] = effort
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    if output_config:
        kwargs["output_config"] = output_config

    return kwargs


def supports_effort(model: str) -> bool:
    return model in _EFFORT
