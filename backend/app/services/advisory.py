"""Assemble a full field advisory from stored data.

This is the deterministic layer, end to end. It reads only from the node's own
tables, runs the engine modules, and returns a JSON payload. No language model
is involved: `ai/narrate.py` consumes this output and may only rephrase it.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import nutrients as nutrients_engine
from app.engine import rotation as rotation_engine
from app.engine.anomaly import IndexPoint, assess
from app.engine.crops import get_crop
from app.engine.paddy import PaddyDay
from app.engine.paddy import is_paddy
from app.engine.paddy import plan_irrigation as plan_paddy_irrigation
from app.engine.soil_texture import TEXTURES, classify_texture
from app.engine.water import DayInput, plan_irrigation

ADVISORY_VERSION = "advisory-1.0.0"


async def _weather_rows(db: AsyncSession, field_id: str, since: date) -> list[dict]:
    result = await db.execute(
        text(
            "SELECT time::date AS day, et0_mm, precip_mm, tmax_c, tmin_c "
            "FROM weather_daily WHERE field_id = :id AND time >= :since "
            "ORDER BY time"
        ),
        {"id": field_id, "since": since},
    )
    return [dict(row) for row in result.mappings()]


async def _why_no_health(db: AsyncSession, field_id: str, sowing: date) -> str:
    """Say which of the two reasons health cannot be scored, because they are
    not the same and only one of them is worth waiting for.

    Health reads NDVI from the sowing date onward -- imagery of bare soil, or
    of the previous crop, says nothing about this one. So a field can hold two
    hundred days of perfectly clear observations and still score nothing,
    simply because it was sown last week.

    Saying "no cloud-free satellite observation yet" in that case is false. The
    node has seen the field, repeatedly. A farmer reading that reasonably
    concludes the satellite cannot see their land, when what is actually true
    is that the crop is younger than the most recent clear pass and the answer
    is a few days away.
    """
    result = await db.execute(
        text(
            "SELECT count(*) AS clear_views, max(time)::date AS latest "
            "FROM observations WHERE field_id = :id AND index_name = 'ndvi'"
        ),
        {"id": field_id},
    )
    row = result.mappings().first()
    clear_views = int((row and row["clear_views"]) or 0)
    latest = row and row["latest"]

    if not clear_views or latest is None:
        return (
            "No cloud-free satellite picture of this field yet. Sentinel-2 "
            "passes every few days; under monsoon cloud it can take longer. "
            "Crop health scoring is paused until one arrives."
        )

    return (
        f"The satellite has {clear_views} clear view(s) of this field, but the "
        f"most recent is from {latest:%-d %B}, before this crop was sown on "
        f"{sowing:%-d %B}. Pictures of the previous crop say nothing about this "
        "one, so health scoring starts at the next clear pass."
    )


async def _index_points(
    db: AsyncSession, field_id: str, index_name: str, since: date
) -> list[IndexPoint]:
    result = await db.execute(
        text(
            "SELECT time::date AS day, value, valid_fraction FROM observations "
            "WHERE field_id = :id AND index_name = :name AND time >= :since "
            "ORDER BY time"
        ),
        {"id": field_id, "name": index_name, "since": since},
    )
    return [
        IndexPoint(day=row["day"], value=row["value"], valid_fraction=row["valid_fraction"])
        for row in result.mappings()
    ]


def _texture_from_soil(soil: dict):
    """Rebuild a TextureClass from a stored soil profile."""
    if soil.get("sand_pct") is not None and soil.get("clay_pct") is not None:
        return classify_texture(soil["sand_pct"], soil.get("silt_pct") or 0.0, soil["clay_pct"])
    name = (soil.get("texture") or "loam").replace(" ", "_")
    return TEXTURES.get(name, TEXTURES["loam"])


async def build_advisory(
    db: AsyncSession,
    *,
    field: dict,
    today: date | None = None,
) -> dict:
    """Produce the deterministic advisory payload for one field.

    `field` must carry id, lat, lon, area_ha, the active crop cycle and the soil
    profile. Missing inputs degrade to explicit gaps rather than silent defaults.
    """
    today = today or date.today()
    field_id = str(field["id"])
    gaps: list[str] = []

    crop_row = field.get("crop")
    if not crop_row:
        return {
            "version": ADVISORY_VERSION,
            "field_id": field_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "status": "no_crop",
            "gaps": ["No active crop recorded. Add the crop and sowing date."],
        }

    crop = get_crop(crop_row["crop_code"])
    sowing = crop_row["sowing_date"]
    if isinstance(sowing, str):
        sowing = date.fromisoformat(sowing)

    soil = field.get("soil")
    if not soil:
        soil = {"source": "fallback", "confidence": "low", "texture": "loam"}
        gaps.append("No soil data. Advice assumes a loam soil.")
    texture = _texture_from_soil(soil)

    # ---------------------------------------------------------------- water
    weather = await _weather_rows(db, field_id, sowing - timedelta(days=5))
    irrigation = None
    if not weather:
        gaps.append("No weather data ingested yet for this field.")
    else:
        history = [
            DayInput(day=r["day"], et0_mm=r["et0_mm"] or 0.0, rain_mm=r["precip_mm"] or 0.0)
            for r in weather
            if r["day"] <= today
        ]
        forecast = [
            DayInput(day=r["day"], et0_mm=r["et0_mm"] or 0.0, rain_mm=r["precip_mm"] or 0.0)
            for r in weather
            if r["day"] > today
        ]
        if history and is_paddy(crop):
            # Puddled paddy is ponded on a plough pan, not drained. Running the
            # upland depletion model on rice reports stress under monsoon rain.
            paddy_plan = plan_paddy_irrigation(
                crop=crop,
                texture=texture,
                sowing_date=sowing,
                history=[
                    PaddyDay(day=d.day, et0_mm=d.et0_mm, rain_mm=d.rain_mm)
                    for d in history
                ],
                forecast=[
                    PaddyDay(day=d.day, et0_mm=d.et0_mm, rain_mm=d.rain_mm)
                    for d in forecast
                ],
                puddling=(soil.get("puddling") or "medium"),
            )
            irrigation = paddy_plan.to_dict()
            if not forecast:
                gaps.append("No forecast available, so no irrigation date is projected.")
        elif history:
            plan = plan_irrigation(
                crop=crop,
                texture=texture,
                sowing_date=sowing,
                history=history,
                forecast=forecast,
            )
            irrigation = {
                "model": "upland",
                "engine_version": plan.engine_version,
                "as_of": plan.as_of.isoformat(),
                "stage": plan.stage.value,
                "days_after_sowing": plan.days_after_sowing,
                "irrigate_now": plan.irrigate_now,
                "recommended_depth_mm": plan.recommended_depth_mm,
                "gross_depth_mm": plan.gross_depth_mm,
                "days_until_irrigation": plan.days_until_irrigation,
                "forecast_irrigation_date": (
                    plan.forecast_irrigation_date.isoformat()
                    if plan.forecast_irrigation_date
                    else None
                ),
                "soil_moisture_pct": plan.soil_moisture_pct,
                "depletion_mm": plan.depletion_mm,
                "taw_mm": plan.taw_mm,
                "raw_mm": plan.raw_mm,
                "rainfall_next_7d_mm": plan.rainfall_next_7d_mm,
                "stress_days_last_30": plan.stress_days_last_30,
                "notes": plan.notes,
            }
            if not forecast:
                gaps.append("No forecast available, so no irrigation date is projected.")

    # --------------------------------------------------------------- health
    ndvi = await _index_points(db, field_id, "ndvi", sowing)
    ndmi = await _index_points(db, field_id, "ndmi", sowing)
    health = assess(crop=crop, sowing_date=sowing, ndvi=ndvi, ndmi=ndmi, today=today).to_dict()
    if not ndvi:
        gaps.append(await _why_no_health(db, field_id, sowing))

    # ------------------------------------------------------------ nutrients
    previous = None
    if crop_row.get("previous_crop"):
        try:
            previous = get_crop(crop_row["previous_crop"])
        except KeyError:
            previous = None
    nutrient_plan = nutrients_engine.recommend(
        crop=crop, soil=soil, previous_crop=previous
    ).to_dict()

    # ------------------------------------------------------------- rotation
    mean_et0 = (
        sum(r["et0_mm"] or 0.0 for r in weather) / len(weather) if weather else 4.5
    )
    season_rain = sum(r["precip_mm"] or 0.0 for r in weather)
    rotation = [
        c.to_dict()
        for c in rotation_engine.recommend_rotation(
            previous_crop_code=crop.code,
            expected_rainfall_mm=season_rain,
            mean_et0_mm_day=mean_et0,
            irrigation_available=bool(field.get("irrigation_available")),
        )
    ]

    return {
        "version": ADVISORY_VERSION,
        "field_id": field_id,
        "field_name": field.get("name"),
        "area_ha": field.get("area_ha"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "status": "ok",
        "crop": {
            "code": crop.code,
            "label": crop.label_en,
            "sowing_date": sowing.isoformat(),
            "days_after_sowing": (today - sowing).days,
            "stage": crop.stage_at((today - sowing).days).value,
            "season_days": crop.season_days,
            "fao_reference": crop.fao_note,
        },
        "soil": soil,
        "health": health,
        "irrigation": irrigation,
        "nutrients": nutrient_plan,
        "rotation": rotation,
        "climate": {
            "mean_et0_mm_day": round(mean_et0, 2),
            "rainfall_since_sowing_mm": round(season_rain, 1),
            "weather_days_available": len(weather),
        },
        "gaps": gaps,
    }
