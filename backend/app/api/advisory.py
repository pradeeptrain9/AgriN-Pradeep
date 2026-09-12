"""Advisory and ingest endpoints."""

import json
import logging
from datetime import date, datetime

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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["advisory"])


def _round(value, places: int):
    """None stays None. A missing reading must not render as 0.0."""
    return None if value is None else round(float(value), places)


def _narrate_model() -> str:
    from app.config import get_settings

    return get_settings().gemini_narrate_model


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
    """Background refresh. Failures are logged, never surfaced mid-request.

    Each source is independent and each catches broadly, because the three
    have nothing to do with one another: soil is SoilGrids, weather is
    Open-Meteo, satellite is Copernicus. Catching only each provider's own
    exception type looked tidier and was wrong -- anything else weather could
    raise (an httpx timeout, a DNS failure, a database error) escaped the
    handler, killed the task, and took satellite down with it. The symptom is
    the worst kind: a farmer taps Update, gets no error because this runs in
    the background, and the node ends up with no weather AND no satellite,
    with nothing to say which one actually broke.

    The exception type is logged alongside the message. `except
    WeatherUnavailable` at least named its own failure; a bare message from an
    arbitrary exception often does not.
    """
    async with SessionLocal() as db:
        for label, coro in (
            ("soil", ingest_soil(db, field_id, lat, lon)),
            ("weather", ingest_weather(db, field_id, lat, lon)),
            ("satellite", ingest_satellite(db, field_id, geometry)),
        ):
            try:
                summary = await coro
                # The counts, not just "ok". Every ingest already returns them
                # and they were being thrown away, which left the one question
                # anyone actually asks unanswerable: a satellite run that finds
                # nothing but cloud writes no observations, spends processing
                # units, raises nothing, and logged exactly the same word as a
                # run that worked. Crop health then reads "not known" with no
                # way to tell a cloudy fortnight from a broken pipeline.
                detail = ""
                if isinstance(summary, dict):
                    detail = " " + " ".join(
                        f"{k}={v}" for k, v in summary.items()
                        if isinstance(v, (int, float))
                    )
                print(f"[ingest] {label} ok for {field_id}{detail}")
            except (
                WeatherUnavailable, SentinelUnavailable, ProcessingUnitCapReached
            ) as exc:
                # Expected and survivable: upstream is down, or the monthly
                # Copernicus budget is spent. Not a fault in this node.
                print(f"[ingest] {label} unavailable for {field_id}: {exc}")
            except Exception as exc:  # noqa: BLE001 - one source must not stop the rest
                print(f"[ingest] {label} FAILED for {field_id}: "
                      f"{type(exc).__name__}: {exc}")


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
    #
    # Tested against "not the template" rather than a provider name. This
    # briefly read `== "claude"` while the model was being changed, which
    # silently stopped caching every narration -- a paid call on every screen
    # focus, and the advisory screen re-focuses constantly. Naming a provider
    # here is a thing that goes out of date quietly and expensively.
    if narration.source != "template":
        await _store_narration(db, field_id, lang, payload, rendered)
    else:
        # The template is English-only, so without this the farmer most likely
        # to be handed English is the one whose node ran out of credit.
        rendered = await _translate_template(rendered, lang)
    return {"advisory": payload, "narration": rendered}


async def _translate_template(rendered: dict, lang: str) -> dict:
    """Put the deterministic fallback into the farmer's language.

    Only ever applied to the template. A narration Gemini wrote in Hindi is
    already in Hindi; round-tripping it through a translator would add error and
    cost to text that is correct.

    Failure is silent and returns English, because this runs on the path that is
    already degraded -- the model was unavailable or was caught inventing a
    figure. Turning that into an error would deny a farmer advice that is
    correct, merely in the wrong language.
    """
    from app.config import get_settings
    from app.providers import translate as translator

    settings = get_settings()
    if lang == "en" or not settings.google_translate_api_key:
        return rendered

    actions = rendered.get("actions") or []
    strings: list[str] = [rendered.get("summary", ""), rendered.get("explanation", "")]
    for action in actions:
        strings.append(action.get("title", ""))
        strings.append(action.get("detail", "") or action.get("body", ""))
    strings.extend(rendered.get("notes") or [])

    try:
        done = await translator.translate(
            strings, api_key=settings.google_translate_api_key, lang=lang
        )
    except translator.TranslationUnavailable as exc:
        logger.info("template left in English (%s)", exc)
        return rendered

    out = dict(rendered)
    out["summary"], out["explanation"] = done[0], done[1]
    cursor = 2
    new_actions = []
    for action in actions:
        copied = dict(action)
        copied["title"] = done[cursor]
        key = "detail" if "detail" in action else "body"
        copied[key] = done[cursor + 1]
        cursor += 2
        new_actions.append(copied)
    out["actions"] = new_actions
    out["notes"] = done[cursor:]
    out["lang"] = lang
    out["translated"] = True
    return out


def _spoken_text(narration: dict) -> str:
    """Flatten a narration into something worth listening to.

    Order matters differently in audio than on screen. A reader skims to the
    action; a listener hears whatever comes first and may stop there. So the
    summary and the actions lead, and the explanation follows -- a farmer who
    stops listening after thirty seconds has still heard what to do.
    """
    parts: list[str] = []
    if summary := (narration.get("summary") or "").strip():
        parts.append(summary)

    for action in narration.get("actions") or []:
        title = (action.get("title") or "").strip()
        detail = (action.get("detail") or action.get("body") or "").strip()
        if title and detail:
            parts.append(f"{title}. {detail}")
        elif title or detail:
            parts.append(title or detail)

    if explanation := (narration.get("explanation") or "").strip():
        parts.append(explanation)

    return "\n\n".join(parts)


@router.get("/fields/{field_id}/advisory/audio")
async def get_advisory_audio(
    field_id: str,
    lang: str = "en",
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """The advisory, spoken.

    Synthesised from the narration *after* it has passed the guard, so audio can
    never contain a figure the screen would not. If the guard rejected the
    model's wording, the deterministic template is what gets read out.

    Cached on disk by the narration's own hash: the same advice in the same
    language is synthesised once. Cloud TTS bills per character, and without
    this an advisory screen would re-bill on every open.
    """
    import hashlib
    import pathlib

    from fastapi.responses import FileResponse, Response

    from app.config import get_settings
    from app.providers import speech

    settings = get_settings()
    if not settings.google_tts_api_key:
        raise HTTPException(
            status_code=503,
            detail="This node has no text-to-speech configured, so advice cannot be read aloud.",
        )
    if speech.locale_for(lang) is None:
        raise HTTPException(
            status_code=422,
            detail=f"No voice is available for {lang}.",
        )

    field = await _load_field(db, field_id, user.user_id)
    field = await _attach_crop_and_soil(db, field)
    payload = await build_advisory(db, field=field)
    digest = _payload_hash(payload)

    narration = await _cached_narration(db, field_id, lang, digest)
    if narration is None:
        # Deliberately does not generate one. Narration costs money and is
        # already produced by the screen the farmer is looking at; synthesising
        # from a narration that does not exist yet would double-bill a tap.
        from app.ai.narrate import build_template_narration

        narration = build_template_narration(payload).to_dict()

    spoken = _spoken_text(narration)
    if not spoken.strip():
        raise HTTPException(status_code=404, detail="There is no advice to read out yet.")

    key = hashlib.sha256(f"{lang}:{spoken}".encode("utf-8")).hexdigest()
    directory = pathlib.Path(settings.media_root) / "audio"
    path = directory / f"{key}.mp3"

    if path.exists():
        return FileResponse(path, media_type="audio/mpeg")

    try:
        audio, _locale = await speech.synthesise(
            spoken, api_key=settings.google_tts_api_key, lang=lang
        )
    except speech.SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    except OSError as exc:
        # A node on ephemeral storage re-synthesises every time, which costs
        # money but still works. Failing the request over a cache write would
        # turn a billing problem into a farmer hearing nothing.
        logger.warning("could not cache advisory audio: %s", exc)

    return Response(content=audio, media_type="audio/mpeg")


@router.get("/fields/{field_id}/weather")
async def field_weather(
    field_id: str,
    days: int = 7,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Daily weather for one field: a little history, then the forecast.

    Deliberately NOT part of the advisory payload. Two reasons, both concrete:
    ai/guard.py would start policing every one of these figures in narration,
    and the narration cache keys on a hash of the advisory -- daily weather
    changes on every refresh, so folding it in would turn a free cached
    narration into a paid call each time.

    weather_daily holds observations and forecast in the same table, so each
    row says which it is. A farmer must never be shown a forecast as though it
    had been measured.
    """
    await _load_field(db, field_id, user.user_id)

    days = max(1, min(days, 16))
    rows = await db.execute(
        text(
            "SELECT time::date AS day, tmax_c, tmin_c, precip_mm, et0_mm "
            "FROM weather_daily "
            "WHERE field_id = :id "
            "  AND time >= CURRENT_DATE - INTERVAL '2 days' "
            "  AND time <= CURRENT_DATE + make_interval(days => :days) "
            "ORDER BY time"
        ),
        {"id": field_id, "days": days},
    )

    today = date.today()
    daily = []
    for row in rows.mappings():
        day = row["day"]
        daily.append({
            "day": day.isoformat(),
            "kind": "observed" if day <= today else "forecast",
            "tmax_c": _round(row["tmax_c"], 1),
            "tmin_c": _round(row["tmin_c"], 1),
            "precip_mm": _round(row["precip_mm"], 1),
            "et0_mm": _round(row["et0_mm"], 2),
        })

    forecast = [d for d in daily if d["kind"] == "forecast"]
    return {
        "field_id": field_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "daily": daily,
        "rain_ahead_mm": round(
            sum(d["precip_mm"] or 0.0 for d in forecast), 1
        ),
        "forecast_days": len(forecast),
        "gaps": (
            [] if daily
            else ["No weather for this field yet. Tap Update from satellite."]
        ),
    }


@router.get("/fields/{field_id}/crop-suggestions")
async def crop_suggestions(
    field_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """What could be grown here, ranked, with the reasoning attached.

    Deliberately a separate route from the advisory: the advisory needs a crop
    and a sowing date to say anything, and this is the question a farmer has
    before either exists.
    """
    from app.services.crop_choice import suggest_crops

    field = await _load_field(db, field_id, user.user_id)
    field = await _attach_crop_and_soil(db, field)
    return await suggest_crops(db, field=field)


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
        "llm_usd_spent_this_month": round(usd, 4),
        "llm_usd_cap": settings.llm_monthly_usd_cap,
        "llm_usd_remaining": round(settings.llm_monthly_usd_cap - usd, 4),
        "your_cloud_checks_today": calls_today,
        "your_daily_limit": settings.llm_daily_calls_per_user,
    }


@router.get("/crops")
async def crops() -> list[dict]:
    from app.ai.disease_taxonomy import classes_for_crop
    from app.engine.crops import list_crops

    return [
        {
            "code": c.code,
            "label": c.label_en,
            # Every crop can now be photographed: one with no verified disease
            # list goes down the open-ended path and comes back with a name.
            "diagnosable": True,
            # ...but only a crop with a verified list can be given treatment
            # advice, because IPM actions and the pesticide allowlist are both
            # keyed on a disease_code. The client uses this to set expectations
            # BEFORE the photo is taken, rather than after.
            "treatment_available": len(classes_for_crop(c.code)) > 0,
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
