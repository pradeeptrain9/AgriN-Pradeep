"""Operational readiness.

`/health` answers "is this process up". This answers the different and more
useful question an operator has before letting real farmers depend on a node:
"what is not configured, and what happens to a farmer because of it".

Every check names the user-visible consequence rather than an internal state, so
the answer to a red line is obvious. Checks are graded:

  blocker   a farmer cannot use the service, or could be harmed
  degraded  the service works but a feature is silently unavailable
  ok
"""

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["meta"])


def _check(name: str, level: str, detail: str, consequence: str = "") -> dict:
    return {"check": name, "level": level, "detail": detail,
            "consequence": consequence}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    checks: list[dict] = []

    # --- database
    try:
        await db.execute(text("SELECT 1"))
        checks.append(_check("database", "ok", "reachable"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(
            "database", "blocker", f"unreachable: {type(exc).__name__}",
            "Nothing works. The app shows only what is cached on each phone.",
        ))
        return _summarise(checks)

    # --- secret key
    if settings.secret_key in ("dev-only-insecure-key",
                              "change-me-in-production-use-openssl-rand-hex-32"):
        checks.append(_check(
            "secret_key", "blocker", "still the default value",
            "Anyone can forge a sign-in token and read any farmer's fields. "
            "Run: openssl rand -hex 32",
        ))
    else:
        checks.append(_check("secret_key", "ok", "changed from the default"))

    # --- SMS: farmers literally cannot sign in without it
    provider = (settings.sms_provider or "console").lower()
    if provider == "console" and settings.agrin_env == "dev":
        checks.append(_check(
            "sms_delivery", "degraded", "console gateway, codes printed to the log",
            "Fine for testing. Set SMS_PROVIDER before real farmers are enrolled.",
        ))
    elif provider == "console":
        checks.append(_check(
            "sms_delivery", "blocker", "console gateway outside development",
            "The console gateway refuses to run here, so no farmer can sign in.",
        ))
    else:
        try:
            from app.sms.registry import build_gateway

            built = build_gateway()
            checks.append(_check(
                "sms_delivery", "ok", f"{built.name} gateway configured"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check(
                "sms_delivery", "blocker",
                f"{provider} gateway misconfigured: {exc}",
                "No farmer can sign in until this is fixed.",
            ))

    # --- satellite
    if not settings.cdse_client_id or not settings.cdse_client_secret:
        checks.append(_check(
            "satellite", "degraded", "no Copernicus credentials",
            "Crop health always reports 'not known'. Irrigation and fertiliser "
            "advice still work.",
        ))
    else:
        spent = await db.scalar(text(
            "SELECT COALESCE(SUM(units),0) FROM pu_ledger "
            "WHERE month = date_trunc('month', CURRENT_DATE)::date")) or 0
        share = spent / settings.cdse_monthly_pu_cap if settings.cdse_monthly_pu_cap else 0
        level = "degraded" if share >= 0.9 else "ok"
        checks.append(_check(
            "satellite", level,
            f"{spent:.0f} of {settings.cdse_monthly_pu_cap:.0f} PU used this month",
            "Satellite refresh stops when the cap is reached; crop health goes "
            "stale until the quota resets on the 1st." if level != "ok" else "",
        ))

    # --- cloud diagnosis fallback
    if not settings.anthropic_api_key:
        checks.append(_check(
            "cloud_diagnosis", "degraded", "no ANTHROPIC_API_KEY",
            "About 56% of leaf photos fail the on-device confidence gate and "
            "return 'inconclusive' instead of a diagnosis.",
        ))
    else:
        from app.ai import budget

        usd = await budget.month_usd_spent(db)
        cap = settings.llm_monthly_usd_cap
        if usd >= cap:
            checks.append(_check(
                "cloud_diagnosis", "degraded",
                f"monthly budget reached (${usd:.2f} of ${cap:.2f})",
                "Photos the on-device model cannot answer return 'inconclusive' "
                "until the 1st. Raise LLM_MONTHLY_USD_CAP to resume sooner.",
            ))
        else:
            checks.append(_check(
                "cloud_diagnosis", "ok",
                f"configured ({settings.claude_vision_model}), "
                f"${usd:.2f} of ${cap:.2f} used this month"))

    # --- districts drive aggregate grouping
    total = await db.scalar(text(
        "SELECT count(*) FROM fields WHERE archived_at IS NULL")) or 0
    missing = await db.scalar(text(
        "SELECT count(*) FROM fields WHERE archived_at IS NULL "
        "AND (district IS NULL OR district = '')")) or 0
    if total and missing:
        checks.append(_check(
            "districts", "degraded", f"{missing} of {total} fields have no district",
            "Those fields collapse into one 'unknown' bucket, so their data "
            "cannot be compared with other nodes.",
        ))
    else:
        checks.append(_check("districts", "ok", f"{total} field(s) all assigned"))

    # --- pesticide allowlist
    servable = await db.scalar(text(
        "SELECT count(*) FROM pesticides WHERE verified AND revoked_at IS NULL "
        "AND review_by >= CURRENT_DATE")) or 0
    expiring = await db.scalar(text(
        "SELECT count(*) FROM pesticides WHERE verified AND revoked_at IS NULL "
        "AND review_by BETWEEN CURRENT_DATE AND CURRENT_DATE + 60")) or 0
    if servable == 0:
        checks.append(_check(
            "pesticide_allowlist", "degraded", "empty",
            "Diagnoses give cultural and preventive advice only, never a spray. "
            "This is the safe default, not a fault.",
        ))
    elif expiring:
        checks.append(_check(
            "pesticide_allowlist", "degraded",
            f"{servable} row(s) serving, {expiring} expire within 60 days",
            "Expiring rows stop serving on their review date unless re-verified.",
        ))
    else:
        checks.append(_check("pesticide_allowlist", "ok", f"{servable} row(s) serving"))

    # --- federation identity must be backed up
    identity = await db.scalar(text(
        "SELECT count(*) FROM node_identity WHERE node_id = :id"),
        {"id": settings.node_id}) or 0
    checks.append(_check(
        "node_identity", "ok" if identity else "degraded",
        "keypair present" if identity else "not yet generated",
        "Back up the node_identity table. Losing the private key forces every "
        "peer that pinned this node to re-pin manually.",
    ))

    # --- weather freshness: the one thing every advisory depends on
    # Past records only. weather_daily also holds up to 16 days of forecast, so
    # max(time) is in the future and a naive freshness check reports a negative
    # age -- making a node whose ingest died weeks ago look perfectly current.
    freshest = await db.scalar(text(
        "SELECT max(time)::date FROM weather_daily WHERE time::date <= CURRENT_DATE"))
    if total == 0:
        checks.append(_check("weather", "ok", "no fields yet"))
    elif freshest is None:
        checks.append(_check(
            "weather", "blocker", "no weather data for any field",
            "No irrigation advice can be produced at all.",
        ))
    else:
        age = (datetime.date.today() - freshest).days
        level = "ok" if age <= 2 else "degraded"
        checks.append(_check(
            "weather", level, f"most recent record is {age} day(s) old",
            "Irrigation advice is based on stale weather. Is the arq worker "
            "running?" if level != "ok" else "",
        ))

    return _summarise(checks)


def _summarise(checks: list[dict]) -> dict:
    blockers = [c for c in checks if c["level"] == "blocker"]
    degraded = [c for c in checks if c["level"] == "degraded"]
    return {
        "ready_for_pilot": not blockers,
        "summary": (
            f"{len(blockers)} blocker(s), {len(degraded)} degraded, "
            f"{len(checks) - len(blockers) - len(degraded)} ok"
        ),
        "blockers": blockers,
        "degraded": degraded,
        "checks": checks,
    }
