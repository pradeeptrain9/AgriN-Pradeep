"""Scheduled ingest.

Until now, satellite and weather data refreshed only when a farmer opened a
field and the request queued a background task. That is backwards: the data a
farmer needs is the data that was already fetched before they opened the app,
because the moment they open it they are standing in a field with poor signal.

Three jobs, on deliberately different cadences:

  weather    daily. Forecasts change daily and Open-Meteo is cheap and fast.
  satellite  every three days. Sentinel-2 revisits every ~5 days and the free
             Copernicus tier is 10,000 processing units a month, so polling
             harder buys nothing and spends quota.
  soil       once per field, ever. Soil does not change on any timescale this
             app cares about, and SoilGrids is a beta service with a 5 req/min
             fair-use limit.

Every job is guarded by the PU ledger; when the monthly Copernicus budget is
reached, satellite refresh stops rather than failing loudly for every field.
"""

import logging
from datetime import date, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text

from app.config import get_settings
from app.db.session import SessionLocal
from app.providers.sentinel import ProcessingUnitCapReached, SentinelUnavailable
from app.providers.weather import WeatherUnavailable
from app.services.ingest import ingest_satellite, ingest_soil, ingest_weather, month_pu_spent

logger = logging.getLogger(__name__)

# Stagger work so a node with many fields does not open hundreds of upstream
# connections at once and get itself rate-limited.
BATCH_SIZE = 25
SATELLITE_MIN_AGE_DAYS = 3


async def _active_fields(db, *, limit: int = 500) -> list[dict]:
    """Fields worth spending quota on: not archived, with an active crop."""
    result = await db.execute(
        text(
            "SELECT f.id::text AS id, ST_X(f.centroid::geometry) AS lon, "
            "  ST_Y(f.centroid::geometry) AS lat, "
            "  ST_AsGeoJSON(f.geom::geometry) AS geometry "
            "FROM fields f "
            "JOIN crop_cycles c ON c.field_id = f.id AND c.status = 'active' "
            "WHERE f.archived_at IS NULL "
            "ORDER BY f.created_at LIMIT :limit"
        ),
        {"limit": limit},
    )
    return [dict(row) for row in result.mappings()]


async def refresh_weather(ctx) -> dict:
    """Daily. Cheap, fast, and what irrigation advice depends on most."""
    updated = failed = 0
    async with SessionLocal() as db:
        for field in await _active_fields(db):
            try:
                await ingest_weather(db, field["id"], field["lat"], field["lon"])
                updated += 1
            except WeatherUnavailable as exc:
                logger.warning("weather refresh failed for %s: %s", field["id"], exc)
                failed += 1
    logger.info("weather refresh: %d updated, %d failed", updated, failed)
    return {"updated": updated, "failed": failed}


async def refresh_satellite(ctx) -> dict:
    """Every three days, and only for fields whose last observation is stale.

    Re-requesting a field that already has a recent cloud-free observation spends
    processing units for nothing.
    """
    import json

    settings = get_settings()
    updated = skipped = failed = 0

    async with SessionLocal() as db:
        spent = await month_pu_spent(db)
        if spent >= settings.cdse_monthly_pu_cap:
            logger.warning(
                "satellite refresh skipped: %.1f of %.1f PU used this month",
                spent, settings.cdse_monthly_pu_cap,
            )
            return {"skipped_all": True, "pu_spent": spent}

        cutoff = date.today() - timedelta(days=SATELLITE_MIN_AGE_DAYS)
        for field in await _active_fields(db):
            recent = await db.execute(
                text(
                    "SELECT max(time)::date FROM observations "
                    "WHERE field_id = :id AND index_name = 'ndvi'"
                ),
                {"id": field["id"]},
            )
            latest = recent.scalar()
            if latest and latest >= cutoff:
                skipped += 1
                continue
            try:
                await ingest_satellite(db, field["id"], json.loads(field["geometry"]))
                updated += 1
            except ProcessingUnitCapReached as exc:
                logger.warning("stopping satellite refresh: %s", exc)
                break
            except SentinelUnavailable as exc:
                logger.warning("satellite failed for %s: %s", field["id"], exc)
                failed += 1

        spent_after = await month_pu_spent(db)
    logger.info(
        "satellite refresh: %d updated, %d already fresh, %d failed, %.1f PU used",
        updated, skipped, failed, spent_after,
    )
    return {"updated": updated, "skipped": skipped, "failed": failed, "pu_spent": spent_after}


async def backfill_soil(ctx) -> dict:
    """Fetch soil once per field, ever. Cached permanently after that."""
    filled = 0
    async with SessionLocal() as db:
        result = await db.execute(
            text(
                "SELECT f.id::text AS id, ST_X(f.centroid::geometry) AS lon, "
                "  ST_Y(f.centroid::geometry) AS lat FROM fields f "
                "LEFT JOIN soil_profiles s ON s.field_id = f.id "
                "WHERE s.field_id IS NULL AND f.archived_at IS NULL LIMIT :limit"
            ),
            {"limit": BATCH_SIZE},
        )
        for field in result.mappings():
            try:
                await ingest_soil(db, field["id"], field["lat"], field["lon"])
                filled += 1
            except Exception as exc:  # noqa: BLE001 - a soil failure must not stop the batch
                logger.warning("soil backfill failed for %s: %s", field["id"], exc)
    logger.info("soil backfill: %d fields filled", filled)
    return {"filled": filled}


class WorkerSettings:
    """arq entrypoint:  arq app.worker.WorkerSettings"""

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [refresh_weather, refresh_satellite, backfill_soil]
    cron_jobs = [
        # Early morning local time, before farmers are likely to open the app.
        cron(refresh_weather, hour=4, minute=0),
        cron(refresh_satellite, hour={2, 14}, minute=30, day=None),
        cron(backfill_soil, minute=15),
    ]
    max_jobs = 4
    job_timeout = 900
