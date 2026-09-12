"""What the cloud model costs this node, and the caps that stop it running away.

Two separate jobs:

  * Record what every call actually cost, from the `usage` the API returns --
    not an estimate. Without this the only cost signal is the invoice, which
    arrives a month late and says nothing about which feature spent it.
  * Refuse the call when a cap is reached, before spending anything.

Two caps, because they fail differently. The monthly cap protects the budget:
it is a promise that this node cannot cost more than a stated figure, which is
what makes the number in a funding application true. The daily per-user cap
protects against one handset -- a retry loop, a child with the camera, a
misconfigured client -- draining the month in an afternoon while every other
farmer gets nothing.

Reaching a cap is never an error the farmer sees as a failure. The on-device
model still answers, and an ungraded photo falls back to "inconclusive, show
this to your extension officer" -- the same path taken when the network is
down. Degraded, not broken.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings


class BudgetReached(RuntimeError):
    """Raised before a call is made, never after money is spent."""


@dataclass(frozen=True)
class Rate:
    """USD per million tokens, as published on the pricing page."""

    input_usd: float
    output_usd: float


# Google first-party rates. Deliberately a table and not a lookup at call
# time: a node in the field cannot reach a pricing endpoint, and a wrong price
# silently mis-reports the budget rather than failing loudly.
#
# Check these against https://ai.google.dev/gemini-api/docs/pricing when
# upgrading models.
RATES: dict[str, Rate] = {
    # Rounded UP to the next published tier where a model prices by context
    # length. `price()` returns 0.0 for a model it does not know, so an
    # unlisted model would spend without ever reaching the monthly cap --
    # over-pricing makes the cap bind early, which is the safe direction, and
    # under-pricing makes it not bind at all.
    "gemini-2.5-flash": Rate(input_usd=0.30, output_usd=2.50),
    "gemini-2.5-pro": Rate(input_usd=2.50, output_usd=15.00),
    "gemini-2.0-flash": Rate(input_usd=0.10, output_usd=0.40),
}

# Gemini reports the model it actually served as e.g. "gemini-2.5-flash-002".
# Pricing is per family, so an exact-match table would silently price a served
# response at zero.
def rate_for(model: str) -> Rate | None:
    if (exact := RATES.get(model)) is not None:
        return exact
    for name, rate in RATES.items():
        if model.startswith(name):
            return rate
    return None

# Cache reads bill at 0.1x input, 5-minute writes at 1.25x.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def price(model: str, usage: object) -> float:
    """USD for one response, from the four token counts the API reports.

    An unknown model prices at zero rather than guessing. A wrong number in the
    ledger is worse than a visible gap: it would be believed.
    """
    rate = rate_for(model)
    if rate is None:
        return 0.0

    per_input = rate.input_usd / 1_000_000
    per_output = rate.output_usd / 1_000_000

    def count(field: str) -> int:
        return int(getattr(usage, field, 0) or 0)

    return (
        count("input_tokens") * per_input
        + count("cache_read_input_tokens") * per_input * CACHE_READ_MULTIPLIER
        + count("cache_creation_input_tokens") * per_input * CACHE_WRITE_MULTIPLIER
        + count("output_tokens") * per_output
    )


async def month_usd_spent(db: AsyncSession) -> float:
    result = await db.execute(
        text(
            "SELECT COALESCE(SUM(usd), 0) FROM llm_ledger "
            "WHERE month = date_trunc('month', CURRENT_DATE)::date"
        )
    )
    return float(result.scalar() or 0.0)


async def user_calls_today(db: AsyncSession, user_id: str | None) -> int:
    if user_id is None:
        return 0
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM llm_ledger "
            "WHERE user_id = :user_id AND created_at >= date_trunc('day', now())"
        ),
        {"user_id": user_id},
    )
    return int(result.scalar() or 0)


async def check_budget(db: AsyncSession, user_id: str | None = None) -> None:
    """Raise BudgetReached if this call must not be made. Call before spending."""
    settings = get_settings()

    spent = await month_usd_spent(db)
    if spent >= settings.llm_monthly_usd_cap:
        raise BudgetReached(
            f"Monthly cloud-model budget reached (${spent:.2f} of "
            f"${settings.llm_monthly_usd_cap:.2f}). Cloud diagnosis is paused "
            "until the 1st. On-device diagnosis is unaffected."
        )

    calls = await user_calls_today(db, user_id)
    if calls >= settings.llm_daily_calls_per_user:
        raise BudgetReached(
            f"This phone has used {calls} cloud checks today, which is the "
            f"daily limit of {settings.llm_daily_calls_per_user}. On-device "
            "diagnosis still works, and the limit resets tomorrow."
        )


async def record(
    db: AsyncSession,
    *,
    purpose: str,
    model: str,
    usage: object,
    user_id: str | None = None,
) -> float:
    """Write one call to the ledger and return what it cost."""
    usd = price(model, usage)

    def count(field: str) -> int:
        return int(getattr(usage, field, 0) or 0)

    await db.execute(
        text(
            "INSERT INTO llm_ledger (month, purpose, model, user_id, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, usd) VALUES"
            " (date_trunc('month', CURRENT_DATE)::date, :purpose, :model, :user_id,"
            " :input_tokens, :output_tokens, :cache_read, :cache_write, :usd)"
        ),
        {
            "purpose": purpose,
            "model": model,
            "user_id": user_id,
            "input_tokens": count("input_tokens"),
            "output_tokens": count("output_tokens"),
            "cache_read": count("cache_read_input_tokens"),
            "cache_write": count("cache_creation_input_tokens"),
            "usd": usd,
        },
    )
    await db.commit()
    return usd
