"""Advisory and ingest endpoints."""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.fields import _attach_crop_and_soil, _load_field
from app.db.session import SessionLocal, get_db
from app.providers.sentinel import ProcessingUnitCapReached, SentinelUnavailable
from app.providers.weather import WeatherUnavailable
from app.security import CurrentUser, current_user
from app.services.advisory import build_advisory
from app.services.ingest import ingest_satellite, ingest_soil, ingest_weather, month_pu_spent

router = APIRouter(tags=["advisory"])


def _narrate_model() -> str:
    from app.config import get_settings

    return get_settings().claude_narrate_model


def _payload_hash(payload: dict) -> str:
    """Stable fingerprint of the advice, ignoring when it was generated.

    `generated_at` moves every request, so hashing the payload whole would make
    every lookup a miss and the cache a pure cost. Same for the field's own
    identifiers, which never affect the wording.
    """
    import hashlib
    import json

    volatile = {"generated_at", "field_id", "field_name"}
    stable = {k: v for k, v in payload.items() if k not in volatile}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _cached_narration(
    db: AsyncSession, field_id: str, lang: str, payload_hash: str
) -> dict | None:
    row = await db.execute(
        text(
            "SELECT narration FROM advisories WHERE field_id = :field_id "
            "AND lang = :lang AND payload_hash = :hash AND narration IS NOT NULL "
            "ORDER BY generated_at DESC LIMIT 1"
        ),
        {"field_id": field_id, "lang": lang, "hash": payload_hash},
    )
    found = row.scalar()
    return found if isinstance(found, dict) else None


async def _store_narration(
    db: AsyncSession, field_id: str, lang: str, payload: dict, narration: dict
) -> None:
    await db.execute(
        text(
            "INSERT INTO advisories (field_id, engine_version, payload, narration,"
            " lang, payload_hash) VALUES (:field_id, :version, :payload, :narration,"
            " :lang, :hash)"
        ),
        {
            "field_id": field_id,
            "version": payload.get("version", "advisory-1.0.0"),
            "payload": json.dumps(payload, default=str),
            "narration": json.dumps(narration, default=str),
            "lang": lang,
            "hash": _payload_hash(payload),
        },
    )
    await db.commit()


async def _refresh_field(field_id: str, lat: float, lon: float, geometry: dict) -> None:
    """Background refresh. Failures are logged, never surfaced mid-request."""
    async with SessionLocal() as db:
        try:
            await ingest_soil(db, field_id, lat, lon)
        except Exception as exc:  # noqa: BLE001 - background task must not die
            print(f"[ingest] soil failed for {field_id}: {exc}")
        try:
            await ingest_weather(db, field_id, lat, lon)
        except WeatherUnavailable as exc:
            print(f"[ingest] weather failed for {field_id}: {exc}")
        try:
            await ingest_satellite(db, field_id, geometry)
        except (SentinelUnavailable, ProcessingUnitCapReached) as exc:
            print(f"[ingest] satellite skipped for {field_id}: {exc}")


@router.post("/fields/{field_id}/refresh", status_code=202)
async def refresh_field(
    field_id: str,
    background: BackgroundTasks,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    import json

    field = await _load_field(db, field_id, user.user_id)
    background.add_task(
        _refresh_field,
        field_id,
        field["lat"],
        field["lon"],
        json.loads(field["geometry"]),
    )
    return {"queued": True, "field_id": field_id}


@router.get("/fields/{field_id}/advisory")
async def get_advisory(
    field_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    field = await _load_field(db, field_id, user.user_id)
    field = await _attach_crop_and_soil(db, field)
    return await build_advisory(db, field=field)


@router.get("/fields/{field_id}/advisory/narrated")
async def get_narrated_advisory(
    field_id: str,
    lang: str = "en",
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Advisory plus a plain-language narration.

    The narration is a rephrasing of the payload, not a second opinion: every
    figure in it is checked against the computed advisory before it is returned.
    """
    from app.ai import budget
    from app.ai.narrate import build_template_narration, narrate

    field = await _load_field(db, field_id, user.user_id)
    field = await _attach_crop_and_soil(db, field)
    payload = await build_advisory(db, field=field)

    # Over budget falls back to the deterministic template, which says the same
    # things in the same plain language -- the model only ever rephrases. This
    # is the cheapest possible degradation: the advice itself is unaffected,
    # because the advice was never the model's to make.
    # Same advice as last time? Then it is the same sentences. Reuse them.
    # This is the difference between paying per screen-open and paying per
    # actual change in the advice, and the advisory screen is reloaded on every
    # focus.
    digest = _payload_hash(payload)
    cached = await _cached_narration(db, field_id, lang, digest)
    if cached is not None:
        return {"advisory": payload, "narration": cached}

    try:
        await budget.check_budget(db, user.user_id)
    except budget.BudgetReached:
        narration = build_template_narration(payload)
    else:
        # Off the event loop: narrate() uses the synchronous Anthropic client
        # and takes seconds. Called inline it stalls every other request on
        # this node, the same way the diagnosis path would.
        narration = await run_in_threadpool(narrate, payload, lang=lang)
        for usage in narration.usages:
            await budget.record(
                db,
                purpose="narration",
                model=narration.model or _narrate_model(),
                usage=usage,
                user_id=user.user_id,
            )

    rendered = narration.to_dict()
    # Only a real model narration is worth storing. Caching the deterministic
    # template would pin the fallback in place, so the next request after the
    # budget resets would still get the fallback.
    if narration.source == "claude":
        await _store_narration(db, field_id, lang, payload, rendered)
    return {"advisory": payload, "narration": rendered}


@router.get("/quota")
async def quota(
    user: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.config import get_settings

    from app.ai import budget

    settings = get_settings()
    spent = await month_pu_spent(db)
    usd = await budget.month_usd_spent(db)
    calls_today = await budget.user_calls_today(db, user.user_id)
    return {
        "copernicus_pu_spent_this_month": round(spent, 2),
        "copernicus_pu_cap": settings.cdse_monthly_pu_cap,
        "copernicus_pu_remaining": round(settings.cdse_monthly_pu_cap - spent, 2),
        # Both dependencies that can run out, reported the same way.
        "claude_usd_spent_this_month": round(usd, 4),
        "claude_usd_cap": settings.llm_monthly_usd_cap,
        "claude_usd_remaining": round(settings.llm_monthly_usd_cap - usd, 4),
        "your_cloud_checks_today": calls_today,
        "your_daily_limit": settings.llm_daily_calls_per_user,
    }


@router.get("/crops")
async def crops() -> list[dict]:
    from app.engine.crops import list_crops

    return [
        {
            "code": c.code,
            "label": c.label_en,
            "season_days": c.season_days,
            "stages": {
                "initial": c.stage_days[0],
                "development": c.stage_days[1],
                "mid": c.stage_days[2],
                "late": c.stage_days[3],
            },
            "fixes_nitrogen": c.n_fixation_kg_ha > 0,
            "fao_reference": c.fao_note,
        }
        for c in list_crops()
    ]
