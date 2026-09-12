"""Fetch external data into the node's own store.

All of this is deliberately off the request path. Satellite calls take tens of
seconds and are quota-limited; weather calls are fast but still remote. A farmer
opening the app must read from local tables, never wait on Copernicus.
"""

import json
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.providers import sentinel as sentinel_provider
from app.providers import weather_chain
from app.providers.soil import resolve_soil

WEATHER_HISTORY_DAYS = 200
# ERA5 reanalysis lags real time; asking for the last few days returns nothing.
ARCHIVE_LAG_DAYS = 6

# How far back to re-read satellite dates that are already stored. Covers a
# Sentinel-2 revisit interval, so a scene reprocessed after first download is
# picked up rather than frozen at its first value.
RESCAN_OVERLAP_DAYS = 7

# Minimum gap between Copernicus queries for the same field. Sentinel-2's
# revisit is about five days, so anything shorter than this spends quota to
# receive the same answer. Well under a revisit so a farmer who waits a day
# still gets a fresh look.
MIN_RECHECK_HOURS = 12


async def ingest_weather(
    db: AsyncSession, field_id: str, lat: float, lon: float, *, history_days: int = WEATHER_HISTORY_DAYS
) -> dict:
    """Backfill archive weather and refresh the forecast for one field."""
    today = date.today()
    archive_end = today - timedelta(days=ARCHIVE_LAG_DAYS)
    archive_start = today - timedelta(days=history_days)

    # Through the chain, not straight at Open-Meteo: its rate limit is per IP
    # and on shared hosting that IP is the platform's, so a node making two
    # calls a day can be refused over traffic it had no part in. See
    # providers/weather_chain.py.
    #
    # No shared client here either. Each source has its own identification
    # requirements -- MET Norway refuses a request carrying someone else's
    # User-Agent -- so each opens and closes its own.
    archive = await weather_chain.fetch_archive(lat, lon, archive_start, archive_end)
    forecast = await weather_chain.fetch_forecast(lat, lon, days=16)

    rows = {d.day: d for d in archive}
    rows.update({d.day: d for d in forecast})  # forecast wins for overlapping days

    for day, record in rows.items():
        await db.execute(
            text(
                "INSERT INTO weather_daily (time, field_id, tmax_c, tmin_c, tmean_c, "
                "rh_mean, wind2_ms, rad_mj, precip_mm, et0_mm, soil_moist, source) "
                "VALUES (:time, :field_id, :tmax, :tmin, :tmean, :rh, :wind, :rad, "
                ":precip, :et0, :soil, :source) "
                "ON CONFLICT (field_id, time) DO UPDATE SET "
                "tmax_c = EXCLUDED.tmax_c, tmin_c = EXCLUDED.tmin_c, "
                "tmean_c = EXCLUDED.tmean_c, rh_mean = EXCLUDED.rh_mean, "
                "wind2_ms = EXCLUDED.wind2_ms, rad_mj = EXCLUDED.rad_mj, "
                "precip_mm = EXCLUDED.precip_mm, et0_mm = EXCLUDED.et0_mm, "
                "soil_moist = EXCLUDED.soil_moist"
            ),
            {
                "time": datetime.combine(day, time.min, tzinfo=timezone.utc),
                "field_id": field_id,
                "tmax": record.tmax_c,
                "tmin": record.tmin_c,
                "tmean": record.tmean_c,
                "rh": record.rh_mean,
                "wind": record.wind2_ms,
                "rad": record.rad_mj,
                "precip": record.precip_mm,
                "et0": record.et0_mm,
                "soil": record.soil_moist,
                "source": record.source,
            },
        )
    await db.commit()
    return {"days_written": len(rows), "archive": len(archive), "forecast": len(forecast)}


async def month_pu_spent(db: AsyncSession) -> float:
    result = await db.execute(
        text(
            "SELECT COALESCE(SUM(units), 0) FROM pu_ledger "
            "WHERE month = date_trunc('month', CURRENT_DATE)::date"
        )
    )
    return float(result.scalar() or 0.0)


async def ingest_satellite(
    db: AsyncSession, field_id: str, geometry: dict, *, history_days: int = 200
) -> dict:
    """Pull NDVI/NDMI/NDRE for a field, respecting the monthly PU cap."""
    settings = get_settings()
    spent = await month_pu_spent(db)
    if spent >= settings.cdse_monthly_pu_cap:
        raise sentinel_provider.ProcessingUnitCapReached(
            f"Monthly Copernicus budget reached ({spent:.1f} of "
            f"{settings.cdse_monthly_pu_cap} PU). Satellite refresh paused until "
            "the quota resets on the 1st."
        )

    cdse = sentinel_provider.CdseClient(settings.cdse_client_id, settings.cdse_client_secret)
    end = date.today()

    # Only ask for what is not already stored.
    #
    # This used to request the full history on every call, which is what a
    # farmer tapping "Update from satellite" triggers. Two consecutive taps on
    # the deployed node each spent 1.67 processing units to re-fetch two
    # hundred days and rewrite the same seventy-two observations. Harmless at
    # one field and not at twenty-five, where the free Copernicus quota is the
    # thing standing between a pilot and no crop health at all.
    #
    # Processing units scale with area times time span, so narrowing a
    # 200-day window to a fortnight is roughly a fortieth of the cost.
    latest = await db.scalar(
        text("SELECT max(time)::date FROM observations WHERE field_id = :id"),
        {"id": field_id},
    )
    if latest is None:
        start = end - timedelta(days=history_days)
    else:
        # Overlap deliberately. Sentinel-2 scenes are sometimes reprocessed and
        # a later pass can improve a date already stored; the insert is an
        # upsert, so re-reading a few days costs a little quota and corrects
        # them. Never earlier than the history window, so a long-dormant field
        # does not silently pull a year.
        start = max(latest - timedelta(days=RESCAN_OVERLAP_DAYS),
                    end - timedelta(days=history_days))

    # Do not re-ask within a revisit interval. Sentinel-2 passes the same point
    # every five days or so, so a farmer tapping Update twice in an afternoon
    # is asking a question whose answer cannot have changed -- and paying quota
    # for the privilege. The ledger already records when this field was last
    # queried, so this needs no new state.
    recent = await db.scalar(
        text(
            "SELECT max(created_at) FROM pu_ledger "
            "WHERE field_id = :id AND endpoint = 'statistics'"
        ),
        {"id": field_id},
    )
    if recent is not None:
        age_hours = (
            datetime.now(timezone.utc) - recent
        ).total_seconds() / 3600.0
        if age_hours < MIN_RECHECK_HOURS:
            return {
                "observations": 0,
                "processing_units": 0.0,
                "intervals_returned": 0,
                "intervals_rejected_for_cloud": 0,
                "month_pu_spent": spent,
                "skipped_checked_recently": 1,
            }

    if start >= end:
        return {
            "observations": 0,
            "processing_units": 0.0,
            "intervals_returned": 0,
            "intervals_rejected_for_cloud": 0,
            "month_pu_spent": spent,
            "skipped_already_current": 1,
        }

    result = await sentinel_provider.fetch_indices(
        cdse=cdse, geometry=geometry, start=start, end=end
    )

    for obs in result.observations:
        await db.execute(
            text(
                "INSERT INTO observations (time, field_id, index_name, value, "
                "valid_fraction, source) VALUES (:time, :field_id, :index_name, "
                ":value, :valid_fraction, :source) "
                "ON CONFLICT (field_id, index_name, time) DO UPDATE SET "
                "value = EXCLUDED.value, valid_fraction = EXCLUDED.valid_fraction"
            ),
            {
                "time": datetime.combine(obs.day, time.min, tzinfo=timezone.utc),
                "field_id": field_id,
                "index_name": obs.index_name,
                "value": obs.value,
                "valid_fraction": obs.valid_fraction,
                "source": obs.source,
            },
        )

    await db.execute(
        text(
            "INSERT INTO pu_ledger (month, units, endpoint, field_id) VALUES "
            "(date_trunc('month', CURRENT_DATE)::date, :units, 'statistics', :field_id)"
        ),
        {"units": result.processing_units, "field_id": field_id},
    )
    await db.commit()
    return {
        "observations": len(result.observations),
        "processing_units": result.processing_units,
        "intervals_returned": result.intervals_returned,
        "intervals_rejected_for_cloud": result.intervals_rejected,
        "month_pu_spent": spent + result.processing_units,
    }


async def ingest_soil(db: AsyncSession, field_id: str, lat: float, lon: float) -> dict:
    """Resolve soil once and cache it permanently; soil does not change."""
    existing = await db.execute(
        text("SELECT props FROM soil_profiles WHERE field_id = :id"), {"id": field_id}
    )
    row = existing.first()
    if row is not None:
        return row[0]

    profile = await resolve_soil(lat=lat, lon=lon)
    props = profile.to_dict()
    await db.execute(
        text(
            "INSERT INTO soil_profiles (field_id, source, props) "
            "VALUES (:id, :source, CAST(:props AS jsonb)) "
            "ON CONFLICT (field_id) DO NOTHING"
        ),
        {"id": field_id, "source": profile.source.value, "props": json.dumps(props)},
    )
    await db.commit()
    return props
