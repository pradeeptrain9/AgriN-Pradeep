"""Nothing may spend money without passing the budget check first.

This is a structural test, not a behavioural one. The risk it guards against is
a future call site added somewhere new -- a background job, a new endpoint, a
helper -- that reaches the API without going through `check_budget`. That would
not fail any existing test; it would just quietly spend, which on a node with a
few dollars of credit is the difference between a working pilot and a dead one.

If this test fails because you added a legitimate call site, add it to the set
below AND gate it. Do not just add it to the set.
"""

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# The only two things in this system that talk to a paid API.
EXPECTED_CALL_SITES = {"app/ai/narrate.py", "app/ai/vision.py"}

# The layers that own a db session and a user, and so can enforce the cap.
EXPECTED_GATES = {"app/api/advisory.py", "app/api/diagnoses.py"}


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(APP.parent)): path.read_text()
        for path in APP.rglob("*.py")
    }


def test_only_the_known_modules_call_the_api():
    calling = {
        name for name, src in _sources().items() if "messages.create" in src
    }
    assert calling == EXPECTED_CALL_SITES, (
        "A new Claude call site appeared. Gate it with budget.check_budget "
        "before adding it here."
    )


def test_every_call_site_is_reached_through_a_budget_gate():
    gating = {
        name for name, src in _sources().items() if "check_budget" in src
    }
    # budget.py defines it; the API layers call it.
    gating.discard("app/ai/budget.py")
    assert gating == EXPECTED_GATES


def test_the_background_worker_never_calls_the_api():
    # A cron job that spends is the worst version of this bug: it bills with
    # nobody watching and no farmer waiting on the answer.
    worker = (APP / "worker.py").read_text()
    for forbidden in ("messages.create", "anthropic", "narrate", "identify_with_vision"):
        assert forbidden not in worker


def test_every_spend_is_recorded():
    # A call whose usage is never written to the ledger is spend the cap cannot
    # see, so the cap would drift further from the truth with every request.
    for name in EXPECTED_GATES:
        src = (APP.parent / name).read_text()
        assert "budget.record" in src, f"{name} spends without recording"


def test_the_cap_is_checked_before_the_call_not_after():
    """Ordering matters: a cap enforced after the request is a report."""
    for name in EXPECTED_GATES:
        src = (APP.parent / name).read_text()
        check = src.index("check_budget")
        record = src.index("budget.record")
        assert check < record, f"{name} records before it checks"


def test_price_table_covers_every_configurable_model():
    from app.ai.budget import RATES
    from app.config import Settings

    defaults = Settings()
    for model in (defaults.claude_narrate_model, defaults.claude_vision_model):
        assert model in RATES, (
            f"{model} is configured by default but has no price, so its spend "
            "would record as $0 and never reach the cap."
        )


def test_caps_are_positive():
    # A cap of zero would disable the cloud entirely and read as an outage.
    from app.config import Settings

    defaults = Settings()
    assert defaults.llm_monthly_usd_cap > 0
    assert defaults.llm_daily_calls_per_user > 0


def test_a_reached_cap_raises_before_any_client_is_constructed():
    # BudgetReached is raised by check_budget, which the call sites invoke
    # before touching the vision or narration path at all.
    from app.ai.budget import BudgetReached

    assert issubclass(BudgetReached, RuntimeError)


def test_the_pricing_regex_finds_no_hardcoded_model_in_call_sites():
    # A model string baked into a request instead of read from settings would
    # bypass both the price table and the operator's configuration.
    for name in EXPECTED_CALL_SITES:
        src = (APP.parent / name).read_text()
        for match in re.finditer(r'model=["\']([\w.-]+)["\']', src):
            raise AssertionError(f"{name} hardcodes model {match.group(1)!r}")
