"""USDA texture classification and FAO-56 water-holding properties.

SoilGrids returns sand/silt/clay fractions; irrigation needs field capacity and
wilting point. Rather than a fitted pedotransfer function (which would need its
own validation), this maps texture to the FAO-56 Table 19 class averages. It is
coarse but traceable, and a farmer-supplied soil health card can override it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextureClass:
    name: str
    theta_fc: float          # field capacity, m3/m3 (FAO-56 Table 19 midpoint)
    theta_wp: float          # wilting point, m3/m3
    hydrologic_group: str    # A-D, for SCS curve-number runoff


# FAO-56 Table 19 midpoints. TAW per metre of root depth = 1000*(fc-wp).
TEXTURES: dict[str, TextureClass] = {
    "sand": TextureClass("sand", 0.12, 0.04, "A"),
    "loamy_sand": TextureClass("loamy sand", 0.14, 0.06, "A"),
    "sandy_loam": TextureClass("sandy loam", 0.23, 0.10, "B"),
    "loam": TextureClass("loam", 0.25, 0.12, "B"),
    "silt_loam": TextureClass("silt loam", 0.30, 0.15, "B"),
    "silt": TextureClass("silt", 0.32, 0.17, "B"),
    "sandy_clay_loam": TextureClass("sandy clay loam", 0.27, 0.15, "C"),
    "clay_loam": TextureClass("clay loam", 0.32, 0.18, "C"),
    "silty_clay_loam": TextureClass("silty clay loam", 0.34, 0.21, "C"),
    "sandy_clay": TextureClass("sandy clay", 0.33, 0.21, "D"),
    "silty_clay": TextureClass("silty clay", 0.36, 0.23, "D"),
    "clay": TextureClass("clay", 0.36, 0.22, "D"),
}


def classify_texture(sand_pct: float, silt_pct: float, clay_pct: float) -> TextureClass:
    """USDA soil texture triangle. Percentages are normalised to sum to 100."""
    total = sand_pct + silt_pct + clay_pct
    if total <= 0:
        raise ValueError("texture fractions must be positive")
    sand = sand_pct / total * 100
    silt = silt_pct / total * 100
    clay = clay_pct / total * 100

    # Order matters: these follow the USDA triangle boundaries top-down.
    if clay >= 40 and silt >= 40:
        key = "silty_clay"
    elif clay >= 40 and sand >= 45:
        key = "sandy_clay"
    elif clay >= 40:
        key = "clay"
    elif clay >= 27 and sand <= 20:
        key = "silty_clay_loam"
    elif clay >= 27 and sand <= 45:
        key = "clay_loam"
    elif clay >= 20 and sand > 45:
        key = "sandy_clay_loam"
    elif silt >= 80 and clay < 12:
        key = "silt"
    elif silt >= 50:
        key = "silt_loam"
    elif clay >= 7 and sand <= 52:
        key = "loam"
    elif sand >= 85:
        key = "sand"
    elif sand >= 70:
        key = "loamy_sand"
    else:
        key = "sandy_loam"
    return TEXTURES[key]


def total_available_water(texture: TextureClass, root_depth_m: float) -> float:
    """TAW in mm. FAO-56 Eq. 82."""
    return 1000 * (texture.theta_fc - texture.theta_wp) * root_depth_m


# SCS curve numbers for row crops, straight row, good hydrologic condition,
# antecedent moisture condition II (NRCS TR-55 Table 2-2b).
CURVE_NUMBERS = {"A": 67, "B": 78, "C": 85, "D": 89}


def runoff_mm(rainfall_mm: float, hydrologic_group: str) -> float:
    """SCS curve-number direct runoff. Q = (P-Ia)^2 / (P-Ia+S), Ia = 0.2S."""
    if rainfall_mm <= 0:
        return 0.0
    cn = CURVE_NUMBERS[hydrologic_group]
    s = 25400 / cn - 254
    ia = 0.2 * s
    if rainfall_mm <= ia:
        return 0.0
    return (rainfall_mm - ia) ** 2 / (rainfall_mm - ia + s)
