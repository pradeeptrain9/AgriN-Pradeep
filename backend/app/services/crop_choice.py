"""What to grow on a field that has none yet.

`build_advisory` answers "how do I grow the crop I already have". This answers
the question that comes before it: a farmer has just walked a boundary and the
app asks them to type a crop, with no help at all.

The scoring engine for this already existed -- `engine/rotation.py` weighs every
crop in the registry on nitrogen fixation, water demand against local rainfall,
soil carbon, pest-cycle break against the previous crop, and market price, and
returns the reasoning rather than just a ranking. It was only ever called from
inside `build_advisory`, which returns early for a field with no crop, so the
one situation where a recommendation is most useful was the one situation it
was never computed for.

Two things this deliberately does not do:

  * It does not pick for the farmer. It ranks, explains, and leaves the choice
    open -- a farmer knows things about their land, their labour and their
    buyer that no scoring function does.
  * It does not pretend to recommend without inputs. With no weather ingested
    the water-fit component is the largest weight in the model and would be
    computed against a default; that is reported as a gap, not smoothed over.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import rotation as rotation_engine
from app.engine.crops import get_crop

# Enough weather to characterise a season's rainfall and evaporative demand.
# Below this the water-fit score is guesswork dressed as arithmetic.
MIN_WEATHER_DAYS = 30

# How far back to look when summarising the climate a crop would grow into.
CLIMATE_WINDOW_DAYS = 180

SUGGESTION_VERSION = "crop-choice-1.0.0"


async def _climate(db: AsyncSession, field_id: str, today: date) -> dict:
    """Recent rainfall and evaporative demand for this field."""
    result = await db.execute(
        text(
            "SELECT count(*) AS days, "
            "       coalesce(sum(precip_mm), 0) AS rain_mm, "
            "       coalesce(avg(et0_mm), 0) AS mean_et0 "
            "FROM weather_daily "
            "WHERE field_id = :id AND time >= :since AND time <= :today"
        ),
        {
            "id": field_id,
            "since": today - timedelta(days=CLIMATE_WINDOW_DAYS),
            "today": today,
        },
    )
    row = result.mappings().first()
    return {
        "days": int(row["days"] or 0),
        "rain_mm": float(row["rain_mm"] or 0.0),
        "mean_et0": float(row["mean_et0"] or 0.0),
    }


async def _previous_crop(db: AsyncSession, field_id: str) -> str | None:
    """The last crop grown here, which drives the pest-cycle break score."""
    result = await db.execute(
        text(
            "SELECT crop_code FROM crop_cycles WHERE field_id = :id "
            "ORDER BY sowing_date DESC LIMIT 1"
        ),
        {"id": field_id},
    )
    found = result.scalar()
    return str(found) if found else None


async def suggest_crops(
    db: AsyncSession,
    *,
    field: dict,
    today: date | None = None,
    top_n: int = 4,
) -> dict:
    """Rank what could be grown here, with the reasoning attached."""
    today = today or date.today()
    field_id = str(field["id"])
    gaps: list[str] = []

    climate = await _climate(db, field_id, today)
    if climate["days"] == 0:
        gaps.append(
            "No weather data for this field yet. Tap Update so the suggestion "
            "can be based on the rainfall your field actually gets."
        )
    elif climate["days"] < MIN_WEATHER_DAYS:
        gaps.append(
            f"Only {climate['days']} days of weather so far. How well a crop "
            "fits the rainfall here is the largest part of this score, so "
            "treat the order as provisional."
        )

    soil = field.get("soil")
    if not soil or soil.get("source") == "fallback":
        gaps.append(
            "No soil information for this field. Entering your Soil Health "
            "Card makes the fertiliser advice that follows much more specific."
        )

    previous_code = await _previous_crop(db, field_id)
    previous_label = None
    if previous_code:
        try:
            previous_label = get_crop(previous_code).label_en
        except KeyError:
            previous_code = None

    # mean_et0 of 0 means no weather at all; the engine's own default is a
    # better answer than dividing by a measured zero.
    mean_et0 = climate["mean_et0"] or 4.5

    candidates = rotation_engine.recommend_rotation(
        previous_crop_code=previous_code,
        expected_rainfall_mm=climate["rain_mm"],
        mean_et0_mm_day=mean_et0,
        top_n=top_n,
    )

    return {
        "version": SUGGESTION_VERSION,
        "field_id": field_id,
        "generated_at": today.isoformat(),
        # What the ranking was computed against, so a farmer or an officer can
        # see the basis rather than being asked to trust a number.
        "based_on": {
            "rainfall_last_180d_mm": round(climate["rain_mm"], 1),
            "mean_et0_mm_day": round(mean_et0, 2),
            "weather_days": climate["days"],
            "previous_crop": previous_label,
            "soil_source": (soil or {}).get("source"),
        },
        "suggestions": [c.to_dict() for c in candidates],
        # Said plainly, and repeated in the UI: this is a ranking, not an
        # instruction. A farmer may grow anything in the registry.
        "choice_is_open": True,
        "gaps": gaps,
    }
