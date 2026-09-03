"""Water balance for puddled, transplanted paddy rice.

Rice is not an upland crop and must not be modelled as one. `water.py` tracks
root-zone depletion, which assumes the soil drains freely and the crop is
stressed whenever water is short. A puddled paddy is deliberately ponded, sits
on a compacted plough pan, and loses most of its water to percolation rather
than transpiration. Running the depletion model on rice produces the absurd
result this module was written to fix: a field reported as water-stressed for
half the month while standing under 300 mm of monsoon rain.

State here is a single water level `h`, in mm relative to the soil surface:

    h > 0   ponded, h is the depth of standing water
    h = 0   saturated to the surface
    h < 0   perched water table, h is its depth below the surface

Irrigation regimes follow IRRI "safe AWD" (alternate wetting and drying):

  1. shallow flooding for the first two weeks after transplanting, for
     establishment and weed suppression
  2. shallow ponding from heading to the end of flowering, the stage most
     sensitive to water deficit
  3. AWD at all other times, re-flooding when the perched water table falls to
     about 15 cm below the surface

The 15 cm threshold is safe because roots still draw from the perched water
table and the near-saturated soil above it. Reported water savings against
continuous flooding are 15-30%, with no yield penalty, plus a substantial cut in
methane emissions.

Sources:
  IRRI Rice Knowledge Bank, "Saving water with alternate wetting and drying"
  Percolation rates: 0-3 mm/day on fine-textured soils; 9.8 mm/day (high
  puddling) to 14.1 mm/day (low puddling) on sandy loam.
"""

from dataclasses import dataclass, field as dc_field
from datetime import date

from app.engine.crops import Crop, Stage
from app.engine.soil_texture import TextureClass

ENGINE_VERSION = "paddy-1.0.0"

PADDY_CROPS = frozenset({"rice"})

# Steady-state percolation through a WELL-PUDDLED profile with an intact plough
# pan, mm/day by texture. Clay soils are preferred for paddy precisely because
# these losses are small.
#
# Anchored on measured sandy loam values: 9.8 mm/day under high puddling rising
# to 14.1 mm/day under low puddling. The table holds the high-puddling figure
# and PUDDLING_FACTOR scales upward from there, so 9.8 * 1.44 = 14.1 reproduces
# the published pair.
PERCOLATION_MM_DAY = {
    "clay": 1.5,
    "silty clay": 1.5,
    "silty clay loam": 2.0,
    "clay loam": 2.0,
    "sandy clay": 3.0,
    "silt": 3.5,
    "silt loam": 3.5,
    "loam": 3.5,
    "sandy clay loam": 5.0,
    "sandy loam": 9.8,
    "loamy sand": 13.0,
    "sand": 15.0,
}
DEFAULT_PERCOLATION = 3.5

# Puddling quality scales percolation upward from the well-puddled baseline.
# A well-formed plough pan is the single biggest lever a rice farmer has over
# water use: puddling has been measured to cut percolation many-fold.
PUDDLING_FACTOR = {"high": 1.0, "medium": 1.2, "low": 1.44, "none": 2.5}

# Once the water table drops below the surface the ponded head driving flow
# through the plough pan is gone, and percolation continues only under matric
# gradients. 0.3 reflects that collapse; it is the least certain parameter here
# and should be calibrated per district against measured tube readings.
UNSATURATED_PERCOLATION_FACTOR = 0.3

# Drainable porosity of a puddled profile: how far the water table falls per mm
# of water lost. Calibratable per district; 0.18 gives recession of roughly
# 30 mm/day at typical evaporative demand, which matches observed AWD cycles.
DRAINABLE_POROSITY = 0.18

# IRRI safe AWD threshold, mm below the soil surface.
AWD_THRESHOLD_MM = -150.0
# Below the safe threshold the crop starts to suffer; fully stressed by here.
AWD_STRESS_FLOOR_MM = -300.0

ESTABLISHMENT_DAYS = 14          # shallow flood after transplanting
ESTABLISHMENT_TARGET_MM = 30.0
ESTABLISHMENT_TRIGGER_MM = 5.0

FLOOD_TARGET_MM = 50.0           # refill depth when re-flooding
FLOOD_TRIGGER_MM = 10.0          # continuous-flood regime trigger

BUND_HEIGHT_MM = 150.0           # above this, rain runs off over the bund

# Land preparation (puddling) is a one-off cost, not a daily flux.
LAND_PREPARATION_MM = 175.0

FINAL_DRAINAGE_DAYS_BEFORE_HARVEST = 14


def is_paddy(crop: Crop) -> bool:
    return crop.code in PADDY_CROPS


def percolation_for(texture: TextureClass, puddling: str = "medium") -> float:
    base = PERCOLATION_MM_DAY.get(texture.name, DEFAULT_PERCOLATION)
    return base * PUDDLING_FACTOR.get(puddling, 1.0)


class Regime:
    ESTABLISHMENT = "establishment"
    AWD = "awd"
    FLOWERING_FLOOD = "flowering_flood"
    CONTINUOUS_FLOOD = "continuous_flood"
    FINAL_DRAINAGE = "final_drainage"
    DONE = "done"


def flowering_window(crop: Crop) -> tuple[int, int]:
    """Days after sowing over which the crop must stay ponded.

    Heading through the end of flowering. Placed just inside the mid-season
    stage, which is where reproductive development sits in the FAO-56 curve.
    """
    ini, dev, mid, _ = crop.stage_days
    start = ini + dev + int(mid * 0.15)
    end = ini + dev + int(mid * 0.60)
    return start, end


def regime_at(crop: Crop, das: int, *, awd_enabled: bool = True) -> str:
    if das >= crop.season_days:
        return Regime.DONE
    if das >= crop.season_days - FINAL_DRAINAGE_DAYS_BEFORE_HARVEST:
        return Regime.FINAL_DRAINAGE
    if das < ESTABLISHMENT_DAYS:
        return Regime.ESTABLISHMENT
    start, end = flowering_window(crop)
    if start <= das <= end:
        return Regime.FLOWERING_FLOOD
    return Regime.AWD if awd_enabled else Regime.CONTINUOUS_FLOOD


def _trigger_and_target(regime: str) -> tuple[float, float]:
    """(water level at which to irrigate, level to refill to), in mm."""
    if regime == Regime.ESTABLISHMENT:
        return ESTABLISHMENT_TRIGGER_MM, ESTABLISHMENT_TARGET_MM
    if regime in (Regime.FLOWERING_FLOOD, Regime.CONTINUOUS_FLOOD):
        return FLOOD_TRIGGER_MM, FLOOD_TARGET_MM
    if regime == Regime.AWD:
        return AWD_THRESHOLD_MM, FLOOD_TARGET_MM
    return float("-inf"), 0.0   # final drainage / done: never irrigate


def stress_coefficient(h_mm: float) -> float:
    """Ks from the water level. No stress while within safe AWD."""
    if h_mm >= AWD_THRESHOLD_MM:
        return 1.0
    if h_mm <= AWD_STRESS_FLOOR_MM:
        return 0.0
    span = AWD_THRESHOLD_MM - AWD_STRESS_FLOOR_MM
    return (h_mm - AWD_STRESS_FLOOR_MM) / span


@dataclass(frozen=True)
class PaddyDay:
    day: date
    et0_mm: float
    rain_mm: float


@dataclass
class PaddyState:
    day: date
    das: int
    stage: Stage
    regime: str
    kc: float
    etc_mm: float
    eta_mm: float
    ks: float
    percolation_mm: float
    rain_mm: float
    runoff_mm: float
    irrigation_mm: float
    water_level_mm: float
    ponded: bool
    stressed: bool


@dataclass
class PaddyPlan:
    engine_version: str
    as_of: date
    das: int
    stage: Stage
    regime: str
    water_level_mm: float
    ponded_depth_mm: float
    water_table_depth_mm: float
    irrigate_now: bool
    recommended_depth_mm: float
    gross_depth_mm: float
    days_until_irrigation: int | None
    forecast_irrigation_date: date | None
    percolation_mm_day: float
    stress_days_last_30: int
    rainfall_next_7d_mm: float
    season_irrigation_mm: float
    season_irrigation_if_continuous_mm: float
    water_saving_pct: float
    land_preparation_mm: float
    notes: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "model": "paddy",
            "as_of": self.as_of.isoformat(),
            "days_after_sowing": self.das,
            "stage": self.stage.value,
            "regime": self.regime,
            "water_level_mm": round(self.water_level_mm, 1),
            "ponded_depth_mm": round(self.ponded_depth_mm, 1),
            "water_table_depth_mm": round(self.water_table_depth_mm, 1),
            "irrigate_now": self.irrigate_now,
            "recommended_depth_mm": round(self.recommended_depth_mm, 1),
            "gross_depth_mm": round(self.gross_depth_mm, 1),
            "days_until_irrigation": self.days_until_irrigation,
            "forecast_irrigation_date": (
                self.forecast_irrigation_date.isoformat()
                if self.forecast_irrigation_date
                else None
            ),
            "percolation_mm_day": round(self.percolation_mm_day, 2),
            "stress_days_last_30": self.stress_days_last_30,
            "rainfall_next_7d_mm": round(self.rainfall_next_7d_mm, 1),
            "season_irrigation_mm": round(self.season_irrigation_mm, 1),
            "season_irrigation_if_continuous_mm": round(
                self.season_irrigation_if_continuous_mm, 1
            ),
            "water_saving_pct": round(self.water_saving_pct, 1),
            "land_preparation_mm": self.land_preparation_mm,
            "notes": self.notes,
        }


def run_balance(
    *,
    crop: Crop,
    texture: TextureClass,
    sowing_date: date,
    days: list[PaddyDay],
    puddling: str = "medium",
    awd_enabled: bool = True,
    initial_level_mm: float = ESTABLISHMENT_TARGET_MM,
    percolation_mm_day: float | None = None,
) -> list[PaddyState]:
    """Step the paddy balance day by day, applying irrigation when triggered."""
    perc_rate = (
        percolation_mm_day
        if percolation_mm_day is not None
        else percolation_for(texture, puddling)
    )
    states: list[PaddyState] = []
    h = initial_level_mm

    for entry in days:
        das = (entry.day - sowing_date).days
        stage = crop.stage_at(das)
        regime = regime_at(crop, das, awd_enabled=awd_enabled)
        kc = crop.kc_at(das)
        etc = kc * entry.et0_mm

        ks = stress_coefficient(h)
        eta = ks * etc

        ponded = h > 0
        percolation = perc_rate if ponded else perc_rate * UNSATURATED_PERCOLATION_FACTOR
        if regime in (Regime.FINAL_DRAINAGE, Regime.DONE):
            percolation = perc_rate * UNSATURATED_PERCOLATION_FACTOR

        # Rain first, then losses, so a downpour can re-flood a dry field.
        runoff = 0.0
        h = _add_water(h, entry.rain_mm)
        if h > BUND_HEIGHT_MM:
            runoff = h - BUND_HEIGHT_MM
            h = BUND_HEIGHT_MM

        h = _remove_water(h, eta + percolation)

        # Irrigate if the regime's trigger has been crossed.
        trigger, target = _trigger_and_target(regime)
        irrigation = 0.0
        if h <= trigger and target > h:
            irrigation = _water_to_reach(h, target)
            h = target

        states.append(
            PaddyState(
                day=entry.day,
                das=das,
                stage=stage,
                regime=regime,
                kc=kc,
                etc_mm=etc,
                eta_mm=eta,
                ks=ks,
                percolation_mm=percolation,
                rain_mm=entry.rain_mm,
                runoff_mm=runoff,
                irrigation_mm=irrigation,
                water_level_mm=h,
                ponded=h > 0,
                stressed=ks < 1.0,
            )
        )
    return states


def _add_water(h: float, mm: float) -> float:
    """Add water. Below the surface it first refills pore space."""
    if mm <= 0:
        return h
    if h >= 0:
        return h + mm
    # Refilling the profile: each mm raises the table by 1/porosity.
    rise_capacity = -h * DRAINABLE_POROSITY
    if mm <= rise_capacity:
        return h + mm / DRAINABLE_POROSITY
    return (mm - rise_capacity)  # profile refilled, remainder ponds


def _remove_water(h: float, mm: float) -> float:
    """Remove water. Above the surface it drains ponding, then the table falls."""
    if mm <= 0:
        return h
    if h >= mm:
        return h - mm
    remaining = mm - max(h, 0.0)
    start = min(h, 0.0)
    return start - remaining / DRAINABLE_POROSITY


def _water_to_reach(h: float, target: float) -> float:
    """Depth of irrigation needed to raise the level from h to target."""
    if h >= target:
        return 0.0
    if h >= 0:
        return target - h
    return -h * DRAINABLE_POROSITY + max(target, 0.0)


def plan_irrigation(
    *,
    crop: Crop,
    texture: TextureClass,
    sowing_date: date,
    history: list[PaddyDay],
    forecast: list[PaddyDay],
    puddling: str = "medium",
    awd_enabled: bool = True,
    application_efficiency: float = 0.65,
    max_horizon_days: int = 10,
) -> PaddyPlan:
    if not history:
        raise ValueError("need at least one day of history")

    perc_rate = percolation_for(texture, puddling)
    past = run_balance(
        crop=crop,
        texture=texture,
        sowing_date=sowing_date,
        days=history,
        puddling=puddling,
        awd_enabled=awd_enabled,
    )
    today = past[-1]
    notes: list[str] = []

    season_irrigation = sum(s.irrigation_mm for s in past)
    continuous = run_balance(
        crop=crop,
        texture=texture,
        sowing_date=sowing_date,
        days=history,
        puddling=puddling,
        awd_enabled=False,
    )
    continuous_irrigation = sum(s.irrigation_mm for s in continuous)
    saving = (
        100 * (continuous_irrigation - season_irrigation) / continuous_irrigation
        if continuous_irrigation > 0
        else 0.0
    )

    trigger, target = _trigger_and_target(today.regime)
    irrigate_now = today.water_level_mm <= trigger and target > today.water_level_mm
    depth = _water_to_reach(today.water_level_mm, target) if irrigate_now else 0.0

    days_until: int | None = None
    forecast_date: date | None = None
    if not irrigate_now and forecast:
        projected = run_balance(
            crop=crop,
            texture=texture,
            sowing_date=sowing_date,
            days=forecast[:max_horizon_days],
            puddling=puddling,
            awd_enabled=awd_enabled,
            initial_level_mm=today.water_level_mm,
        )
        for offset, state in enumerate(projected, start=1):
            if state.irrigation_mm > 0:
                days_until = offset
                forecast_date = state.day
                depth = state.irrigation_mm
                break
        if days_until is None:
            notes.append(
                f"No irrigation needed in the next {len(projected)} days on the "
                "current forecast."
            )

    rain_7d = sum(d.rain_mm for d in forecast[:7])

    # Regime-specific guidance.
    if today.regime == Regime.ESTABLISHMENT:
        notes.append(
            "First two weeks after transplanting: keep a shallow flood of about "
            "3 cm for establishment and weed control. Do not dry the field yet."
        )
    elif today.regime == Regime.FLOWERING_FLOOD:
        notes.append(
            "The crop is heading and flowering, the stage most sensitive to water "
            "shortage. Keep the field ponded and do not practise AWD until "
            "flowering has finished."
        )
    elif today.regime == Regime.AWD:
        notes.append(
            "Safe AWD applies now: let the water fall to 15 cm below the soil "
            "surface, then re-flood to about 5 cm. Use a field water tube to see "
            "the level."
        )
    elif today.regime == Regime.FINAL_DRAINAGE:
        notes.append(
            "Approaching harvest. Drain the field so the soil firms up and "
            "harvesting is easier. No further irrigation."
        )

    if perc_rate >= 8.0:
        notes.append(
            f"This soil percolates about {perc_rate:.0f} mm/day, which is high for "
            "paddy. Better puddling to build a plough pan would cut water use "
            "sharply."
        )
    if awd_enabled and saving > 0:
        notes.append(
            f"AWD has saved about {saving:.0f}% of irrigation water so far against "
            "continuous flooding, and also lowers methane emissions."
        )
        if saving < 12:
            # Be honest about why this field falls short of the headline figure
            # rather than letting the farmer assume the model is wrong.
            notes.append(
                "That is below the 15-30% usually quoted for AWD. On this field the "
                f"soil percolates {perc_rate:.0f} mm/day and the crop must stay "
                "ponded through establishment and flowering, which limits how many "
                "days AWD can actually be applied. Improving the plough pan would "
                "save more than changing the watering schedule."
            )
    if rain_7d > 40:
        notes.append(
            f"{rain_7d:.0f} mm of rain is forecast this week; hold off irrigating "
            "and let the rain fill the field."
        )

    stress_days = sum(1 for s in past[-30:] if s.stressed)
    if stress_days > 0:
        notes.append(
            f"The water table fell past the safe 15 cm mark on {stress_days} of the "
            "last 30 days."
        )

    return PaddyPlan(
        engine_version=ENGINE_VERSION,
        as_of=today.day,
        das=today.das,
        stage=today.stage,
        regime=today.regime,
        water_level_mm=today.water_level_mm,
        ponded_depth_mm=max(0.0, today.water_level_mm),
        water_table_depth_mm=min(0.0, today.water_level_mm),
        irrigate_now=irrigate_now,
        recommended_depth_mm=depth,
        gross_depth_mm=depth / application_efficiency if depth else 0.0,
        days_until_irrigation=days_until,
        forecast_irrigation_date=forecast_date,
        percolation_mm_day=perc_rate,
        stress_days_last_30=stress_days,
        rainfall_next_7d_mm=rain_7d,
        season_irrigation_mm=season_irrigation,
        season_irrigation_if_continuous_mm=continuous_irrigation,
        water_saving_pct=saving,
        land_preparation_mm=LAND_PREPARATION_MM,
        notes=notes,
    )
