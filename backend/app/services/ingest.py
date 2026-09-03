"""Fetch external data into the node's own store.

All of this is deliberately off the request path. Satellite calls take tens of
seconds and are quota-limited; weather calls are fast but still remote. A farmer
opening the app must read from local tables, never wait on Copernicus.
"""

import json
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.providers import sentinel as sentinel_provider
from app.providers import weather as weather_provider
from app.providers.soil import resolve_soil

WEATHER_HISTORY_DAYS = 200
# ERA5 reanalysis lags real time; asking for the last few days returns nothing.
ARCHIVE_LAG_DAYS = 6


async def ingest_weather(
    db: AsyncSession, field_id: str, lat: float, lon: float, *, history_days: int = WEATHER_HISTORY_DAYS
) -> dict:
    """Backfill archive weather and refresh the forecast for one field."""
    today = date.today()
    archive_end = today - timedelta(days=ARCHIVE_LAG_DAYS)
    archive_start = today - timedelta(days=history_days)

    async with httpx.AsyncClient(
        headers={"User-Agent": weather_provider.USER_AGENT}
    ) as client:
        archive = await weather_provider.fetch_archive(
            lat, lon, archive_start, archive_end, client=client
        )
        forecast = await weather_provider.fetch_forecast(lat, lon, days=16, client=client)

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
    start = end - timedelta(days=history_days)
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
