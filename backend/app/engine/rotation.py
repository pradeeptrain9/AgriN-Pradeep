"""Regenerative crop rotation scoring.

Every candidate crop gets a weighted score built from named, inspectable
components. There is no learned model here on purpose: a farmer deciding what to
plant next season deserves to see *why* a crop was suggested, and an
unexplainable ranking would not survive contact with an extension officer.

Components (each normalised to 0-1):
  nitrogen        legumes that reduce the next crop's fertiliser bill
  water_fit       seasonal water demand against expected rainfall
  soil_carbon     residue and rooting contribution to organic matter
  rotation_break  botanical distance from the previous crop, to break pest and
                  disease cycles
  market          relative price signal, when the caller supplies one
"""

from dataclasses import dataclass, field as dc_field

from app.engine.crops import CROPS, Crop, get_crop

ENGINE_VERSION = "rotation-1.0.0"

# Botanical family drives pest and disease carry-over.
FAMILY = {
    "wheat_spring": "poaceae",
    "wheat_winter": "poaceae",
    "rice": "poaceae",
    "maize": "poaceae",
    "sorghum": "poaceae",
    "pearl_millet": "poaceae",
    "sugarcane": "poaceae",
    "soybean": "fabaceae",
    "chickpea": "fabaceae",
    "groundnut": "fabaceae",
    "cotton": "malvaceae",
    "potato": "solanaceae",
    "sunflower": "asteraceae",
    "mustard": "brassicaceae",
}

# Relative residue biomass and rooting contribution to soil organic matter.
SOIL_CARBON_INDEX = {
    "sugarcane": 1.00,
    "maize": 0.85,
    "sorghum": 0.85,
    "pearl_millet": 0.80,
    "wheat_winter": 0.75,
    "wheat_spring": 0.70,
    "rice": 0.65,
    "cotton": 0.60,
    "sunflower": 0.55,
    "mustard": 0.50,
    "soybean": 0.50,
    "chickpea": 0.45,
    "groundnut": 0.45,
    "potato": 0.25,
}

DEFAULT_WEIGHTS = {
    "nitrogen": 0.25,
    "water_fit": 0.30,
    "soil_carbon": 0.20,
    "rotation_break": 0.20,
    "market": 0.05,
}

MAX_FIXATION = 90.0  # soybean, the highest in the registry


@dataclass
class RotationCandidate:
    crop_code: str
    label: str
    score: float
    components: dict[str, float]
    seasonal_water_need_mm: float
    reasons: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "crop_code": self.crop_code,
            "label": self.label,
            "score": round(self.score, 3),
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "seasonal_water_need_mm": round(self.seasonal_water_need_mm, 1),
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


def seasonal_water_need_mm(crop: Crop, mean_et0_mm_day: float) -> float:
    """Integrate Kc over the season to get total crop water demand."""
    return sum(crop.kc_at(day) * mean_et0_mm_day for day in range(crop.season_days))


def _rotation_break_score(candidate: Crop, previous: Crop | None) -> tuple[float, str]:
    if previous is None:
        return 0.7, "No previous crop recorded, so pest carry-over is unknown."
    same_crop = candidate.code == previous.code
    same_family = FAMILY.get(candidate.code) == FAMILY.get(previous.code)
    if same_crop:
        return 0.0, (
            f"Same crop as last season ({previous.label_en}). Repeating it lets "
            "soil-borne pests and diseases build up."
        )
    if same_family:
        return 0.35, (
            f"Same botanical family as {previous.label_en}, so several pests and "
            "diseases will carry over."
        )
    return 1.0, f"Different family from {previous.label_en}, which breaks pest cycles."


def score_candidate(
    *,
    candidate: Crop,
    previous: Crop | None,
    expected_rainfall_mm: float,
    mean_et0_mm_day: float,
    irrigation_available: bool,
    market_index: float | None = None,
    weights: dict[str, float] | None = None,
) -> RotationCandidate:
    weights = weights or DEFAULT_WEIGHTS
    reasons: list[str] = []
    warnings: list[str] = []

    water_need = seasonal_water_need_mm(candidate, mean_et0_mm_day)

    # Water fit: how much of the crop's demand rainfall alone can cover.
    coverage = expected_rainfall_mm / water_need if water_need > 0 else 1.0
    if irrigation_available:
        # Irrigation removes most of the penalty but not the cost of pumping.
        water_fit = min(1.0, 0.7 + 0.3 * min(coverage, 1.0))
    else:
        water_fit = min(1.0, coverage)
        if coverage < 0.6:
            warnings.append(
                f"Rainfall covers only {coverage:.0%} of this crop's water need "
                f"({water_need:.0f} mm) and no irrigation is recorded."
            )
    if coverage >= 1.0 and not irrigation_available:
        reasons.append("Expected rainfall alone covers this crop's water need.")

    nitrogen = min(1.0, candidate.n_fixation_kg_ha / MAX_FIXATION)
    if candidate.n_fixation_kg_ha > 0:
        reasons.append(
            f"Fixes about {candidate.n_fixation_kg_ha:.0f} kg N/ha, cutting "
            "fertiliser cost for the crop that follows."
        )

    soil_carbon = SOIL_CARBON_INDEX.get(candidate.code, 0.5)
    if soil_carbon >= 0.75:
        reasons.append("Leaves substantial residue, which builds soil organic matter.")
    elif soil_carbon <= 0.3:
        warnings.append("Leaves little residue, so soil organic matter may decline.")

    rotation_break, break_reason = _rotation_break_score(candidate, previous)
    (reasons if rotation_break >= 0.7 else warnings).append(break_reason)

    market = market_index if market_index is not None else 0.5

    components = {
        "nitrogen": nitrogen,
        "water_fit": water_fit,
        "soil_carbon": soil_carbon,
        "rotation_break": rotation_break,
        "market": market,
    }
    score = sum(components[k] * weights.get(k, 0.0) for k in components)

    return RotationCandidate(
        crop_code=candidate.code,
        label=candidate.label_en,
        score=score,
        components=components,
        seasonal_water_need_mm=water_need,
        reasons=reasons,
        warnings=warnings,
    )


def recommend_rotation(
    *,
    previous_crop_code: str | None,
    expected_rainfall_mm: float,
    mean_et0_mm_day: float,
    irrigation_available: bool = False,
    market_index: dict[str, float] | None = None,
    candidates: list[str] | None = None,
    top_n: int = 4,
) -> list[RotationCandidate]:
    previous = get_crop(previous_crop_code) if previous_crop_code else None
    pool = [get_crop(c) for c in candidates] if candidates else list(CROPS.values())
    market_index = market_index or {}

    scored = [
        score_candidate(
            candidate=crop,
            previous=previous,
            expected_rainfall_mm=expected_rainfall_mm,
            mean_et0_mm_day=mean_et0_mm_day,
            irrigation_available=irrigation_available,
            market_index=market_index.get(crop.code),
        )
        for crop in pool
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_n]
