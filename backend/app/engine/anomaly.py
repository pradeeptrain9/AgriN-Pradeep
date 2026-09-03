"""Crop health from vegetation index time series.

Two things this module refuses to do, because both would mislead a farmer:

1. Treat a cloudy date as a low-NDVI date. Dates that failed the cloud mask
   never reach here; what does reach here carries `days_since_observation`, and
   the advisory states it plainly. Stale is reported as stale.
2. Score a field against a global NDVI threshold. A healthy crop's NDVI depends
   entirely on how many days it is past sowing, so the comparison is always
   against the expected curve for that crop at that age.
"""

from dataclasses import dataclass, field as dc_field
from datetime import date

import numpy as np

from app.engine.crops import Crop, Stage

ENGINE_VERSION = "anomaly-1.0.0"

BARE_SOIL_NDVI = 0.15
EMERGENCE_NDVI_FRACTION = 0.30   # share of peak reached by the end of the initial stage
SENESCENCE_NDVI = 0.30

# Residual thresholds, in NDVI units below the expected curve.
WATCH_RESIDUAL = -0.05
ALERT_RESIDUAL = -0.15

# NDMI below this suggests canopy water stress.
NDMI_STRESS = 0.10

STALE_AFTER_DAYS = 12   # roughly two Sentinel-2 revisits


@dataclass(frozen=True)
class IndexPoint:
    day: date
    value: float
    valid_fraction: float = 1.0


@dataclass
class CropHealth:
    engine_version: str
    as_of: date | None
    days_after_sowing: int | None
    stage: Stage | None
    latest_ndvi: float | None
    smoothed_ndvi: float | None
    expected_ndvi: float | None
    residual: float | None
    severity: str                    # ok | watch | alert | unknown
    trend_per_day: float | None
    latest_ndmi: float | None
    water_stress_flag: bool
    days_since_observation: int | None
    is_stale: bool
    observations_used: int
    notes: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "days_after_sowing": self.days_after_sowing,
            "stage": self.stage.value if self.stage else None,
            "latest_ndvi": self.latest_ndvi,
            "smoothed_ndvi": self.smoothed_ndvi,
            "expected_ndvi": self.expected_ndvi,
            "residual": self.residual,
            "severity": self.severity,
            "trend_per_day": self.trend_per_day,
            "latest_ndmi": self.latest_ndmi,
            "water_stress_flag": self.water_stress_flag,
            "days_since_observation": self.days_since_observation,
            "is_stale": self.is_stale,
            "observations_used": self.observations_used,
            "notes": self.notes,
        }


def expected_ndvi(crop: Crop, days_after_sowing: int) -> float:
    """The NDVI a healthy crop of this type should show at this age.

    Shaped by the FAO-56 stage lengths but not derived from Kc: for flooded rice
    Kc starts at 1.05 because of open water, which says nothing about canopy
    greenness.
    """
    ini, dev, mid, late = crop.stage_days
    peak = crop.ndvi_peak
    emergence = BARE_SOIL_NDVI + EMERGENCE_NDVI_FRACTION * (peak - BARE_SOIL_NDVI)
    d = days_after_sowing

    if d < 0:
        return BARE_SOIL_NDVI
    if d <= ini:
        frac = d / ini if ini else 1.0
        return BARE_SOIL_NDVI + frac * (emergence - BARE_SOIL_NDVI)
    if d <= ini + dev:
        frac = (d - ini) / dev if dev else 1.0
        return emergence + frac * (peak - emergence)
    if d <= ini + dev + mid:
        return peak
    if d <= ini + dev + mid + late:
        frac = (d - ini - dev - mid) / late if late else 1.0
        return peak + frac * (SENESCENCE_NDVI - peak)
    return SENESCENCE_NDVI


def _smooth(points: list[IndexPoint]) -> tuple[float, float | None]:
    """Return (smoothed latest value, slope per day).

    Cloud gaps make the series irregular, so this fits over actual day offsets
    rather than assuming even spacing. With few points it degrades to the raw
    value instead of inventing a curve.
    """
    if not points:
        return 0.0, None
    if len(points) == 1:
        return points[0].value, None

    days = np.array([(p.day - points[0].day).days for p in points], dtype=float)
    values = np.array([p.value for p in points], dtype=float)

    if len(points) == 2:
        slope = (values[1] - values[0]) / (days[1] - days[0]) if days[1] != days[0] else 0.0
        return float(values[-1]), float(slope)

    # Weight recent observations, and weight clear ones over partly-cloudy ones.
    recency = np.exp(-(days[-1] - days) / 20.0)
    quality = np.array([max(p.valid_fraction, 0.1) for p in points])
    weights = recency * quality

    # Local linear fit over the trailing window gives both a de-noised current
    # value and the trend, without needing an even grid.
    window = days >= days[-1] - 30
    if window.sum() >= 3:
        coeffs = np.polyfit(days[window], values[window], 1, w=weights[window])
        slope = float(coeffs[0])
        smoothed = float(np.polyval(coeffs, days[-1]))
    else:
        slope = float((values[-1] - values[-2]) / max(days[-1] - days[-2], 1))
        smoothed = float(np.average(values[-3:], weights=weights[-3:]))

    # Never let the fit wander outside the observed range.
    smoothed = float(np.clip(smoothed, values.min(), values.max()))
    return smoothed, slope


def assess(
    *,
    crop: Crop,
    sowing_date: date,
    ndvi: list[IndexPoint],
    ndmi: list[IndexPoint] | None = None,
    today: date | None = None,
) -> CropHealth:
    today = today or date.today()
    ndvi = sorted(ndvi, key=lambda p: p.day)
    ndmi = sorted(ndmi or [], key=lambda p: p.day)
    notes: list[str] = []

    if not ndvi:
        return CropHealth(
            engine_version=ENGINE_VERSION,
            as_of=None,
            days_after_sowing=(today - sowing_date).days,
            stage=crop.stage_at((today - sowing_date).days),
            latest_ndvi=None,
            smoothed_ndvi=None,
            expected_ndvi=None,
            residual=None,
            severity="unknown",
            trend_per_day=None,
            latest_ndmi=None,
            water_stress_flag=False,
            days_since_observation=None,
            is_stale=True,
            observations_used=0,
            notes=[
                "No cloud-free satellite observation yet for this field. "
                "Health scoring is paused until one arrives."
            ],
        )

    latest = ndvi[-1]
    das = (latest.day - sowing_date).days
    stage = crop.stage_at(das)
    smoothed, slope = _smooth(ndvi)
    expected = expected_ndvi(crop, das)
    residual = smoothed - expected

    days_since = (today - latest.day).days
    is_stale = days_since > STALE_AFTER_DAYS

    if stage is Stage.DONE:
        severity = "ok"
        notes.append("Crop is past its expected season length; health scoring paused.")
    elif residual <= ALERT_RESIDUAL:
        severity = "alert"
    elif residual <= WATCH_RESIDUAL:
        severity = "watch"
    else:
        severity = "ok"

    if is_stale:
        notes.append(
            f"Last cloud-free image is {days_since} days old. "
            "Cloud cover can hide a problem, so treat this as out of date."
        )
        if severity == "ok":
            severity = "unknown"

    latest_ndmi = ndmi[-1].value if ndmi else None
    water_stress = latest_ndmi is not None and latest_ndmi < NDMI_STRESS
    if water_stress:
        notes.append("Canopy moisture index is low, which points to water stress.")

    if slope is not None and slope < -0.004 and stage in (Stage.DEVELOPMENT, Stage.MID):
        notes.append("Greenness is falling while the crop should still be building canopy.")

    if latest.valid_fraction < 0.8:
        notes.append(
            f"Only {latest.valid_fraction:.0%} of the field was cloud-free in the "
            "latest image."
        )

    return CropHealth(
        engine_version=ENGINE_VERSION,
        as_of=latest.day,
        days_after_sowing=das,
        stage=stage,
        latest_ndvi=round(latest.value, 4),
        smoothed_ndvi=round(smoothed, 4),
        expected_ndvi=round(expected, 4),
        residual=round(residual, 4),
        severity=severity,
        trend_per_day=round(slope, 5) if slope is not None else None,
        latest_ndmi=round(latest_ndmi, 4) if latest_ndmi is not None else None,
        water_stress_flag=water_stress,
        days_since_observation=days_since,
        is_stale=is_stale,
        observations_used=len(ndvi),
        notes=notes,
    )
