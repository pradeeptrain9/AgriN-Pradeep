"""Crop parameter registry.

Water parameters (stage lengths, Kc, rooting depth, depletion fraction) are
taken verbatim from FAO-56 tables 11, 12 and 22 and are treated as authoritative.

Nutrient removal rates are NOT of the same quality. Published values vary widely
by variety, yield level and whether residue is removed, so each entry carries an
explicit range and `nutrient_confidence`. The advisory surfaces a range and tells
the farmer to confirm against a local soil health card -- it never presents a
single fertiliser figure as precise.

Crop codes are lowercase snake_case and map to AGROVOC URIs in federation/vocab.py.
"""

from dataclasses import dataclass, field as dc_field
from enum import Enum


class Stage(str, Enum):
    INITIAL = "initial"
    DEVELOPMENT = "development"
    MID = "mid"
    LATE = "late"
    DONE = "done"


@dataclass(frozen=True)
class NutrientRemoval:
    """kg of nutrient removed per tonne of economic yield, as (low, high).

    Ranges are indicative and include typical straw/stover removal.
    """

    n: tuple[float, float]
    p2o5: tuple[float, float]
    k2o: tuple[float, float]
    typical_yield_t_ha: float
    source: str
    confidence: str = "indicative"


@dataclass(frozen=True)
class Crop:
    code: str
    label_en: str
    # FAO-56 Table 11 stage lengths in days: initial, development, mid, late.
    stage_days: tuple[int, int, int, int]
    # FAO-56 Table 12 single crop coefficients.
    kc_ini: float
    kc_mid: float
    kc_end: float
    # FAO-56 Table 22.
    root_depth_m: tuple[float, float]
    depletion_p: float
    max_height_m: float
    nutrient: NutrientRemoval
    # Nitrogen fixed biologically, kg N/ha/season. Credited against the N dose.
    n_fixation_kg_ha: float = 0.0
    # Peak NDVI a healthy canopy of this crop should reach at mid-season.
    ndvi_peak: float = 0.85
    fao_note: str = ""
    aliases: tuple[str, ...] = dc_field(default_factory=tuple)

    @property
    def season_days(self) -> int:
        return sum(self.stage_days)

    def stage_at(self, days_after_sowing: int) -> Stage:
        ini, dev, mid, late = self.stage_days
        if days_after_sowing < 0:
            return Stage.INITIAL
        if days_after_sowing < ini:
            return Stage.INITIAL
        if days_after_sowing < ini + dev:
            return Stage.DEVELOPMENT
        if days_after_sowing < ini + dev + mid:
            return Stage.MID
        if days_after_sowing < ini + dev + mid + late:
            return Stage.LATE
        return Stage.DONE

    def kc_at(self, days_after_sowing: int) -> float:
        """Kc following the FAO-56 four-stage curve, linear across dev and late."""
        ini, dev, mid, late = self.stage_days
        d = max(0, days_after_sowing)
        if d <= ini:
            return self.kc_ini
        if d <= ini + dev:
            frac = (d - ini) / dev if dev else 1.0
            return self.kc_ini + frac * (self.kc_mid - self.kc_ini)
        if d <= ini + dev + mid:
            return self.kc_mid
        if d <= ini + dev + mid + late:
            frac = (d - ini - dev - mid) / late if late else 1.0
            return self.kc_mid + frac * (self.kc_end - self.kc_mid)
        return self.kc_end

    def root_depth_at(self, days_after_sowing: int) -> float:
        """Roots grow linearly from 30% of minimum depth to maximum by mid-season."""
        ini, dev, mid, _ = self.stage_days
        z_min, z_max = self.root_depth_m
        full_by = ini + dev + mid * 0.5
        frac = min(1.0, max(0.0, days_after_sowing / full_by)) if full_by else 1.0
        return z_min * 0.3 + frac * (z_max - z_min * 0.3)

    def adjusted_p(self, etc_mm_day: float) -> float:
        """FAO-56 Eq. 83: p = p_table + 0.04 * (5 - ETc), bounded to [0.1, 0.8]."""
        return min(0.8, max(0.1, self.depletion_p + 0.04 * (5 - etc_mm_day)))


_FAO_T11_T12 = "FAO-56 tables 11, 12, 22 (Allen et al. 1998)"

CROPS: dict[str, Crop] = {
    "wheat_spring": Crop(
        code="wheat_spring",
        label_en="Wheat (spring)",
        stage_days=(20, 25, 60, 30),
        kc_ini=0.30,
        kc_mid=1.15,
        kc_end=0.33,
        root_depth_m=(1.0, 1.5),
        depletion_p=0.55,
        max_height_m=1.0,
        nutrient=NutrientRemoval(
            n=(20.0, 30.0),
            p2o5=(8.0, 12.0),
            k2o=(20.0, 35.0),
            typical_yield_t_ha=3.5,
            source="ICAR/IPNI removal ranges, grain + straw",
        ),
        ndvi_peak=0.85,
        fao_note=f"{_FAO_T11_T12}; 135-day spring wheat, 35-45 deg latitude",
        aliases=("wheat", "gehu"),
    ),
    "wheat_winter": Crop(
        code="wheat_winter",
        label_en="Wheat (winter)",
        stage_days=(30, 140, 40, 30),
        kc_ini=0.70,
        kc_mid=1.15,
        kc_end=0.33,
        root_depth_m=(1.5, 1.8),
        depletion_p=0.55,
        max_height_m=1.0,
        nutrient=NutrientRemoval(
            n=(20.0, 30.0),
            p2o5=(8.0, 12.0),
            k2o=(20.0, 35.0),
            typical_yield_t_ha=4.5,
            source="ICAR/IPNI removal ranges, grain + straw",
        ),
        ndvi_peak=0.88,
        fao_note=f"{_FAO_T11_T12}; November sowing, Mediterranean, 240 days",
    ),
    "rice": Crop(
        code="rice",
        label_en="Rice (paddy)",
        stage_days=(30, 30, 60, 30),
        kc_ini=1.05,
        kc_mid=1.20,
        kc_end=0.75,
        root_depth_m=(0.5, 1.0),
        depletion_p=0.20,
        max_height_m=1.0,
        nutrient=NutrientRemoval(
            n=(16.0, 24.0),
            p2o5=(7.0, 11.0),
            k2o=(20.0, 32.0),
            typical_yield_t_ha=4.0,
            source="ICAR/IRRI removal ranges, grain + straw",
        ),
        ndvi_peak=0.85,
        fao_note=f"{_FAO_T11_T12}; 150-day tropics/Mediterranean. Kc_end 0.90 if "
        "fields stay flooded to harvest, 0.60 if drained",
        aliases=("paddy", "dhan"),
    ),
    "maize": Crop(
        code="maize",
        label_en="Maize (grain)",
        stage_days=(30, 40, 50, 30),
        kc_ini=0.30,
        kc_mid=1.20,
        kc_end=0.45,
        root_depth_m=(1.0, 1.7),
        depletion_p=0.55,
        max_height_m=2.0,
        nutrient=NutrientRemoval(
            n=(18.0, 27.0),
            p2o5=(7.0, 11.0),
            k2o=(18.0, 30.0),
            typical_yield_t_ha=5.0,
            source="ICAR/IPNI removal ranges, grain + stover",
        ),
        ndvi_peak=0.88,
        fao_note=f"{_FAO_T11_T12}; 150-day April sowing, Spain/California",
        aliases=("corn", "makka"),
    ),
    "cotton": Crop(
        code="cotton",
        label_en="Cotton",
        stage_days=(30, 50, 60, 55),
        kc_ini=0.35,
        kc_mid=1.18,
        kc_end=0.60,
        root_depth_m=(1.0, 1.7),
        depletion_p=0.65,
        max_height_m=1.35,
        nutrient=NutrientRemoval(
            n=(45.0, 65.0),
            p2o5=(15.0, 25.0),
            k2o=(45.0, 65.0),
            typical_yield_t_ha=2.0,
            source="ICAR removal ranges, seed cotton + stalk",
        ),
        ndvi_peak=0.82,
        fao_note=f"{_FAO_T11_T12}; 195-day Egypt/Pakistan/California",
        aliases=("kapas",),
    ),
    "soybean": Crop(
        code="soybean",
        label_en="Soybean",
        stage_days=(20, 30, 60, 25),
        kc_ini=0.40,
        kc_mid=1.15,
        kc_end=0.50,
        root_depth_m=(0.6, 1.3),
        depletion_p=0.50,
        max_height_m=0.75,
        nutrient=NutrientRemoval(
            n=(50.0, 70.0),
            p2o5=(12.0, 18.0),
            k2o=(20.0, 32.0),
            typical_yield_t_ha=2.5,
            source="IPNI removal ranges, grain + residue",
        ),
        n_fixation_kg_ha=90.0,
        ndvi_peak=0.87,
        fao_note=f"{_FAO_T11_T12}; 140-day May sowing, central USA",
    ),
    "chickpea": Crop(
        code="chickpea",
        label_en="Chickpea",
        stage_days=(20, 30, 40, 20),
        kc_ini=0.40,
        kc_mid=1.00,
        kc_end=0.35,
        root_depth_m=(0.6, 1.0),
        depletion_p=0.50,
        max_height_m=0.4,
        nutrient=NutrientRemoval(
            n=(30.0, 45.0),
            p2o5=(8.0, 14.0),
            k2o=(15.0, 25.0),
            typical_yield_t_ha=1.5,
            source="ICAR removal ranges, grain + haulm",
        ),
        n_fixation_kg_ha=70.0,
        ndvi_peak=0.78,
        fao_note=f"{_FAO_T11_T12}; Table 12 Kc. Table 11 has no chickpea row, "
        "stage lengths from ICAR rabi pulse calendars",
        aliases=("chana", "gram", "bengal_gram"),
    ),
    "groundnut": Crop(
        code="groundnut",
        label_en="Groundnut",
        stage_days=(25, 35, 45, 25),
        kc_ini=0.40,
        kc_mid=1.15,
        kc_end=0.60,
        root_depth_m=(0.5, 1.0),
        depletion_p=0.50,
        max_height_m=0.4,
        nutrient=NutrientRemoval(
            n=(45.0, 65.0),
            p2o5=(8.0, 14.0),
            k2o=(20.0, 32.0),
            typical_yield_t_ha=1.8,
            source="ICAR removal ranges, pod + haulm",
        ),
        n_fixation_kg_ha=60.0,
        ndvi_peak=0.82,
        fao_note=f"{_FAO_T11_T12}; 130-day dry season, West Africa",
        aliases=("peanut", "moongphali"),
    ),
    "potato": Crop(
        code="potato",
        label_en="Potato",
        stage_days=(25, 30, 45, 30),
        kc_ini=0.50,
        kc_mid=1.15,
        kc_end=0.75,
        root_depth_m=(0.4, 0.6),
        depletion_p=0.35,
        max_height_m=0.6,
        nutrient=NutrientRemoval(
            n=(3.0, 5.0),
            p2o5=(1.0, 2.0),
            k2o=(5.0, 8.0),
            typical_yield_t_ha=20.0,
            source="IPNI removal ranges, per tonne fresh tuber",
        ),
        ndvi_peak=0.85,
        fao_note=f"{_FAO_T11_T12}; 130-day May sowing, continental",
        aliases=("aloo",),
    ),
    "sorghum": Crop(
        code="sorghum",
        label_en="Sorghum (grain)",
        stage_days=(20, 35, 40, 30),
        kc_ini=0.30,
        kc_mid=1.05,
        kc_end=0.55,
        root_depth_m=(1.0, 2.0),
        depletion_p=0.55,
        max_height_m=1.5,
        nutrient=NutrientRemoval(
            n=(18.0, 28.0),
            p2o5=(7.0, 12.0),
            k2o=(18.0, 30.0),
            typical_yield_t_ha=2.5,
            source="ICAR removal ranges, grain + stover",
        ),
        ndvi_peak=0.80,
        fao_note=f"{_FAO_T11_T12}; 130-day May/June, USA/Pakistan/Mediterranean",
        aliases=("jowar",),
    ),
    "pearl_millet": Crop(
        code="pearl_millet",
        label_en="Pearl millet",
        stage_days=(15, 25, 40, 25),
        kc_ini=0.30,
        kc_mid=1.00,
        kc_end=0.30,
        root_depth_m=(1.0, 2.0),
        depletion_p=0.55,
        max_height_m=1.5,
        nutrient=NutrientRemoval(
            n=(18.0, 28.0),
            p2o5=(6.0, 10.0),
            k2o=(18.0, 28.0),
            typical_yield_t_ha=1.5,
            source="ICAR removal ranges, grain + stover",
        ),
        ndvi_peak=0.75,
        fao_note=f"{_FAO_T11_T12}; 105-day June sowing, Pakistan",
        aliases=("bajra", "millet"),
    ),
    "sunflower": Crop(
        code="sunflower",
        label_en="Sunflower",
        stage_days=(25, 35, 45, 25),
        kc_ini=0.35,
        kc_mid=1.08,
        kc_end=0.35,
        root_depth_m=(0.8, 1.5),
        depletion_p=0.45,
        max_height_m=2.0,
        nutrient=NutrientRemoval(
            n=(35.0, 50.0),
            p2o5=(12.0, 20.0),
            k2o=(35.0, 55.0),
            typical_yield_t_ha=2.0,
            source="IPNI removal ranges, seed + residue",
        ),
        ndvi_peak=0.85,
        fao_note=f"{_FAO_T11_T12}; 130-day April/May, Mediterranean/California",
    ),
    "mustard": Crop(
        code="mustard",
        label_en="Mustard / rapeseed",
        stage_days=(25, 35, 45, 25),
        kc_ini=0.35,
        kc_mid=1.08,
        kc_end=0.35,
        root_depth_m=(1.0, 1.5),
        depletion_p=0.60,
        max_height_m=0.6,
        nutrient=NutrientRemoval(
            n=(35.0, 55.0),
            p2o5=(12.0, 20.0),
            k2o=(25.0, 40.0),
            typical_yield_t_ha=1.5,
            source="ICAR removal ranges, seed + straw",
        ),
        ndvi_peak=0.80,
        fao_note=f"{_FAO_T11_T12}; Table 12 rapeseed/canola Kc. Table 11 has no "
        "row, stage lengths from ICAR rabi oilseed calendars",
        aliases=("rapeseed", "canola", "sarson"),
    ),
    "sugarcane": Crop(
        code="sugarcane",
        label_en="Sugarcane",
        stage_days=(35, 60, 190, 120),
        kc_ini=0.40,
        kc_mid=1.25,
        kc_end=0.75,
        root_depth_m=(1.2, 2.0),
        depletion_p=0.65,
        max_height_m=3.0,
        nutrient=NutrientRemoval(
            n=(1.0, 1.6),
            p2o5=(0.4, 0.8),
            k2o=(1.5, 3.0),
            typical_yield_t_ha=70.0,
            source="ICAR removal ranges, per tonne millable cane",
        ),
        ndvi_peak=0.90,
        fao_note=f"{_FAO_T11_T12}; 405-day virgin cane, low latitudes",
        aliases=("ganna",),
    ),
}

_ALIASES: dict[str, str] = {
    alias: crop.code for crop in CROPS.values() for alias in crop.aliases
}


def get_crop(code: str) -> Crop:
    key = code.strip().lower().replace("-", "_").replace(" ", "_")
    if key in CROPS:
        return CROPS[key]
    if key in _ALIASES:
        return CROPS[_ALIASES[key]]
    raise KeyError(f"unknown crop code: {code!r}")


def list_crops() -> list[Crop]:
    return sorted(CROPS.values(), key=lambda c: c.label_en)
