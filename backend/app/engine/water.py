"""Root-zone soil water balance and irrigation scheduling.

Implements the FAO-56 chapter 8 daily water balance:

    Dr,i = Dr,i-1 - (P - RO) - I - CR + ETc,i + DP,i

Capillary rise (CR) is assumed zero, which is the FAO default when the water
table is more than about a metre below the root zone.

The output is deliberately a *number and a date*: "apply 34 mm on 12 September".
An LLM is never asked to produce either.
"""

from dataclasses import dataclass
from datetime import date

from app.engine.crops import Crop, Stage
from app.engine.soil_texture import TextureClass, runoff_mm, total_available_water

ENGINE_VERSION = "water-1.0.0"


@dataclass(frozen=True)
class DayInput:
    day: date
    et0_mm: float
    rain_mm: float
    irrigation_mm: float = 0.0


@dataclass
class DayState:
    day: date
    days_after_sowing: int
    stage: Stage
    kc: float
    etc_mm: float          # potential crop ET
    eta_mm: float          # actual, after water-stress coefficient Ks
    ks: float
    root_depth_m: float
    taw_mm: float
    raw_mm: float
    depletion_mm: float
    runoff_mm: float
    percolation_mm: float
    stressed: bool


@dataclass
class IrrigationPlan:
    engine_version: str
    as_of: date
    stage: Stage
    days_after_sowing: int
    depletion_mm: float
    taw_mm: float
    raw_mm: float
    soil_moisture_pct: float          # share of TAW still available
    irrigate_now: bool
    recommended_depth_mm: float       # net depth to refill to field capacity
    gross_depth_mm: float             # after application efficiency
    days_until_irrigation: int | None
    forecast_irrigation_date: date | None
    stress_days_last_30: int
    rainfall_next_7d_mm: float
    notes: list[str]


def run_balance(
    *,
    crop: Crop,
    texture: TextureClass,
    sowing_date: date,
    days: list[DayInput],
    initial_depletion_frac: float = 0.5,
) -> list[DayState]:
    """Step the water balance day by day.

    initial_depletion_frac seeds Dr at sowing as a share of TAW; 0.5 is the FAO
    suggestion when no measurement is available.
    """
    states: list[DayState] = []
    depletion = None

    for entry in days:
        das = (entry.day - sowing_date).days
        stage = crop.stage_at(das)
        kc = crop.kc_at(das)
        root_depth = crop.root_depth_at(das)
        taw = total_available_water(texture, root_depth)

        etc = kc * entry.et0_mm
        p_adj = crop.adjusted_p(etc)
        raw = p_adj * taw

        if depletion is None:
            depletion = initial_depletion_frac * taw

        # Ks throttles ET once depletion passes RAW. FAO-56 Eq. 84.
        if depletion > raw and taw > raw:
            ks = max(0.0, (taw - depletion) / (taw - raw))
        else:
            ks = 1.0
        eta = ks * etc

        ro = runoff_mm(entry.rain_mm, texture.hydrologic_group)
        infiltration = entry.rain_mm - ro + entry.irrigation_mm

        depletion = depletion - infiltration + eta
        # Water above field capacity drains away; depletion cannot go negative.
        percolation = -depletion if depletion < 0 else 0.0
        depletion = max(0.0, min(depletion, taw))

        states.append(
            DayState(
                day=entry.day,
                days_after_sowing=das,
                stage=stage,
                kc=kc,
                etc_mm=etc,
                eta_mm=eta,
                ks=ks,
                root_depth_m=root_depth,
                taw_mm=taw,
                raw_mm=raw,
                depletion_mm=depletion,
                runoff_mm=ro,
                percolation_mm=percolation,
                stressed=ks < 1.0,
            )
        )
    return states


def plan_irrigation(
    *,
    crop: Crop,
    texture: TextureClass,
    sowing_date: date,
    history: list[DayInput],
    forecast: list[DayInput],
    application_efficiency: float = 0.65,
    max_horizon_days: int = 10,
) -> IrrigationPlan:
    """Run history to establish today's depletion, then project the forecast.

    application_efficiency defaults to 0.65, typical for surface/furrow irrigation
    with smallholder field layouts. Drip or sprinkler callers should pass 0.9/0.75.
    """
    if not history:
        raise ValueError("need at least one day of history")

    past = run_balance(crop=crop, texture=texture, sowing_date=sowing_date, days=history)
    today = past[-1]
    notes: list[str] = []

    if today.stage is Stage.DONE:
        notes.append("Crop is past its expected season length; irrigation advice paused.")

    stress_days = sum(1 for s in past[-30:] if s.stressed)
    irrigate_now = today.depletion_mm >= today.raw_mm
    net_depth = today.depletion_mm if irrigate_now else 0.0

    # Project forward with no irrigation to find the first day depletion crosses RAW.
    days_until: int | None = None
    forecast_date: date | None = None
    if not irrigate_now and forecast:
        projected = run_balance(
            crop=crop,
            texture=texture,
            sowing_date=sowing_date,
            days=forecast[:max_horizon_days],
            initial_depletion_frac=today.depletion_mm / today.taw_mm if today.taw_mm else 0.5,
        )
        for offset, state in enumerate(projected, start=1):
            if state.depletion_mm >= state.raw_mm:
                days_until = offset
                forecast_date = state.day
                net_depth = state.depletion_mm
                break
        if days_until is None:
            notes.append(
                f"No irrigation needed in the next {len(projected)} days on current forecast."
            )

    rain_7d = sum(d.rain_mm for d in forecast[:7])
    if irrigate_now and rain_7d >= net_depth * 0.8 and rain_7d > 10:
        notes.append(
            f"{rain_7d:.0f} mm of rain is forecast this week; consider waiting rather "
            "than irrigating today."
        )
    if today.stage in (Stage.MID,) and today.stressed:
        notes.append("Water stress during mid-season has the largest yield penalty.")

    soil_pct = 100 * (1 - today.depletion_mm / today.taw_mm) if today.taw_mm else 0.0

    return IrrigationPlan(
        engine_version=ENGINE_VERSION,
        as_of=today.day,
        stage=today.stage,
        days_after_sowing=today.days_after_sowing,
        depletion_mm=round(today.depletion_mm, 1),
        taw_mm=round(today.taw_mm, 1),
        raw_mm=round(today.raw_mm, 1),
        soil_moisture_pct=round(soil_pct, 1),
        irrigate_now=irrigate_now,
        recommended_depth_mm=round(net_depth, 1),
        gross_depth_mm=round(net_depth / application_efficiency, 1) if net_depth else 0.0,
        days_until_irrigation=days_until,
        forecast_irrigation_date=forecast_date,
        stress_days_last_30=stress_days,
        rainfall_next_7d_mm=round(rain_7d, 1),
        notes=notes,
    )
