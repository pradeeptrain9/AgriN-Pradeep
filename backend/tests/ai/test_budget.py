"""The cost table and the caps.

A ledger that prices calls wrongly is worse than no ledger, because the number
it reports would be believed and planned against.
"""

from dataclasses import dataclass

import pytest

from app.ai import budget


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def test_prices_input_and_output_at_the_published_rates():
    # Gemini 2.5 Pro, as priced in budget.RATES: $2.50 per Mtok in, $15 out.
    usd = budget.price("gemini-2.5-pro", FakeUsage(input_tokens=1_000_000))
    assert usd == pytest.approx(2.50)

    usd = budget.price("gemini-2.5-pro", FakeUsage(output_tokens=1_000_000))
    assert usd == pytest.approx(15.00)


def test_output_tokens_dominate_a_typical_diagnosis():
    # The realistic shape of one leaf photo: a 640px image plus a short prompt
    # in, a structured answer plus thinking out. Output is where the money is,
    # which is why effort is the lever that matters and image size is not.
    usd = budget.price(
        "gemini-2.5-pro", FakeUsage(input_tokens=800, output_tokens=2000)
    )
    assert usd == pytest.approx(0.032, abs=0.001)


def test_cache_reads_are_a_tenth_of_input():
    full = budget.price("gemini-2.5-pro", FakeUsage(input_tokens=100_000))
    cached = budget.price("gemini-2.5-pro", FakeUsage(cache_read_input_tokens=100_000))
    assert cached == pytest.approx(full * 0.1)


def test_cache_writes_cost_more_than_plain_input():
    full = budget.price("gemini-2.5-pro", FakeUsage(input_tokens=100_000))
    written = budget.price(
        "gemini-2.5-pro", FakeUsage(cache_creation_input_tokens=100_000)
    )
    assert written == pytest.approx(full * 1.25)


def test_cheaper_models_price_lower():
    usage = FakeUsage(input_tokens=1_000_000, output_tokens=100_000)
    pro = budget.price("gemini-2.5-pro", usage)
    flash = budget.price("gemini-2.5-flash", usage)
    lite = budget.price("gemini-2.0-flash", usage)
    assert lite < flash < pro


def test_an_unknown_model_prices_at_zero_rather_than_guessing():
    # A guessed rate would be silently wrong in the ledger. Zero is visibly
    # wrong, which is the failure mode to prefer.
    assert budget.price("some-future-model", FakeUsage(input_tokens=1_000_000)) == 0.0


def test_missing_usage_fields_do_not_raise():
    class Sparse:
        input_tokens = 500

    assert budget.price("gemini-2.5-pro", Sparse()) == pytest.approx(0.00125)


def test_none_usage_does_not_raise():
    assert budget.price("gemini-2.5-pro", None) == 0.0


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_none_valued_token_counts_are_treated_as_zero(field):
    usage = FakeUsage()
    setattr(usage, field, None)
    assert budget.price("gemini-2.5-pro", usage) == 0.0


def test_a_served_model_name_with_a_suffix_still_prices():
    # Gemini reports e.g. "gemini-2.5-flash-002". An exact-match table would
    # price that at zero, and a model that costs nothing never reaches the cap.
    assert budget.price("gemini-2.5-flash-002", FakeUsage(input_tokens=1_000_000)) \
        == pytest.approx(0.30)


def test_the_published_rate_for_the_default_model():
    # Standard tier, https://ai.google.dev/gemini-api/docs/pricing, 2026-09-12.
    from datetime import date

    today = date(2026, 9, 12)
    assert budget.price(
        "gemini-3.6-flash", FakeUsage(input_tokens=1_000_000), on=today
    ) == pytest.approx(0.75)
    assert budget.price(
        "gemini-3.6-flash", FakeUsage(output_tokens=1_000_000), on=today
    ) == pytest.approx(3.75)


def test_a_dated_price_increase_is_charged_from_the_day_it_takes_effect():
    # Gemini 3.6 Flash doubles on 1 January 2027. A node running unattended
    # past that date must not keep billing itself the old rate, or the monthly
    # cap stops binding at the figure it promises.
    from datetime import date

    usage = FakeUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    before = budget.price("gemini-3.6-flash", usage, on=date(2026, 12, 31))
    after = budget.price("gemini-3.6-flash", usage, on=date(2027, 1, 1))
    assert after == pytest.approx(before * 2)


def test_a_served_variant_inherits_the_dated_schedule():
    from datetime import date

    usage = FakeUsage(input_tokens=1_000_000)
    assert budget.price(
        "gemini-3.6-flash-preview-11-2026", usage, on=date(2027, 6, 1)
    ) == pytest.approx(1.50)
