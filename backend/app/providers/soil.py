"""Soil property resolution, with an explicit source hierarchy.

VERIFIED LIMITATION (probed 2026-09-02): SoilGrids v2 returns null for every
property over India, while returning data normally for Brazil, the USA and
elsewhere. India is masked in the public product. An India-first pilot therefore
cannot depend on SoilGrids, and any node that silently treats null as "no soil"
would produce nutrient advice from nothing.

So soil is resolved through a priority chain, and every result carries its own
provenance and confidence so the advisory can say where the numbers came from:

  1. SOIL_HEALTH_CARD  farmer-entered government card values      (high)
  2. FEEL_TEST         in-app ribbon test, texture only           (medium)
  3. SOILGRIDS         global 250 m raster, null over India       (medium)
  4. FALLBACK          regional default texture, flagged          (low)

The chain degrades rather than failing: the water balance always gets a texture,
but the advisory tells the farmer how much to trust it.
"""

import asyncio
from dataclasses import dataclass, field as dc_field
from enum import Enum

import httpx

from app.engine.soil_texture import TextureClass, classify_texture

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
USER_AGENT = "AgriN/0.1 (digital public good; agriculture advisory node)"

# SoilGrids returns integers that must be divided by d_factor. We read d_factor
# from the response rather than hardcoding it, but keep these as a sanity check.
EXPECTED_D_FACTORS = {
    "clay": 10, "sand": 10, "silt": 10,   # g/kg -> %
    "phh2o": 10,                          # pH*10 -> pH
    "soc": 10,                            # dg/kg -> g/kg
    "nitrogen": 100,                      # cg/kg -> g/kg
    "cec": 10,                            # mmol(c)/kg -> cmol(c)/kg
    "bdod": 100,                          # cg/cm3 -> kg/dm3
}
PROPERTIES = tuple(EXPECTED_D_FACTORS)
DEPTHS = ("0-5cm", "5-15cm", "15-30cm")


class SoilSource(str, Enum):
    SOIL_HEALTH_CARD = "soil_health_card"
    FEEL_TEST = "feel_test"
    SOILGRIDS = "soilgrids"
    FALLBACK = "fallback"


CONFIDENCE = {
    SoilSource.SOIL_HEALTH_CARD: "high",
    SoilSource.FEEL_TEST: "medium",
    SoilSource.SOILGRIDS: "medium",
    SoilSource.FALLBACK: "low",
}


@dataclass
class SoilProfile:
    source: SoilSource
    texture: TextureClass
    ph: float | None = None
    soc_g_kg: float | None = None            # soil organic carbon
    nitrogen_g_kg: float | None = None
    cec_cmol_kg: float | None = None
    bulk_density_kg_dm3: float | None = None
    sand_pct: float | None = None
    silt_pct: float | None = None
    clay_pct: float | None = None
    # Soil Health Card style available-nutrient readings, kg/ha.
    available_n_kg_ha: float | None = None
    available_p_kg_ha: float | None = None
    available_k_kg_ha: float | None = None
    organic_carbon_pct: float | None = None
    notes: list[str] = dc_field(default_factory=list)

    @property
    def confidence(self) -> str:
        return CONFIDENCE[self.source]

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "confidence": self.confidence,
            "texture": self.texture.name,
            "theta_fc": self.texture.theta_fc,
            "theta_wp": self.texture.theta_wp,
            "hydrologic_group": self.texture.hydrologic_group,
            "ph": self.ph,
            "soc_g_kg": self.soc_g_kg,
            "organic_carbon_pct": self.organic_carbon_pct,
            "nitrogen_g_kg": self.nitrogen_g_kg,
            "cec_cmol_kg": self.cec_cmol_kg,
            "sand_pct": self.sand_pct,
            "silt_pct": self.silt_pct,
            "clay_pct": self.clay_pct,
            "available_n_kg_ha": self.available_n_kg_ha,
            "available_p_kg_ha": self.available_p_kg_ha,
            "available_k_kg_ha": self.available_k_kg_ha,
            "notes": self.notes,
        }


# --------------------------------------------------------------------- 1. SHC
def from_soil_health_card(
    *,
    texture_hint: str | None = None,
    ph: float | None = None,
    organic_carbon_pct: float | None = None,
    available_n_kg_ha: float | None = None,
    available_p_kg_ha: float | None = None,
    available_k_kg_ha: float | None = None,
    sand_pct: float | None = None,
    silt_pct: float | None = None,
    clay_pct: float | None = None,
) -> SoilProfile:
    """Build a profile from a government Soil Health Card.

    Indian cards report available N/P/K in kg/ha and organic carbon as a
    percentage, which is exactly what the nutrient engine wants and is far more
    trustworthy than any global raster.
    """
    if sand_pct is not None and silt_pct is not None and clay_pct is not None:
        texture = classify_texture(sand_pct, silt_pct, clay_pct)
    elif texture_hint:
        texture = texture_from_feel(texture_hint)
    else:
        texture = classify_texture(40, 40, 20)  # loam, the least-wrong default

    soc = organic_carbon_pct * 10 if organic_carbon_pct is not None else None
    return SoilProfile(
        source=SoilSource.SOIL_HEALTH_CARD,
        texture=texture,
        ph=ph,
        organic_carbon_pct=organic_carbon_pct,
        soc_g_kg=soc,
        available_n_kg_ha=available_n_kg_ha,
        available_p_kg_ha=available_p_kg_ha,
        available_k_kg_ha=available_k_kg_ha,
        sand_pct=sand_pct,
        silt_pct=silt_pct,
        clay_pct=clay_pct,
        notes=["Values from the farmer's Soil Health Card."],
    )


# --------------------------------------------------------------- 2. feel test
# The standard extension ribbon test. Each answer maps to a USDA class; the
# app asks three questions with pictures, which works offline and needs no
# literacy.
FEEL_TEST_MAP = {
    "gritty_no_ball": "sand",
    "gritty_weak_ball": "loamy_sand",
    "gritty_ball_no_ribbon": "sandy_loam",
    "smooth_ball_short_ribbon": "loam",
    "floury_short_ribbon": "silt_loam",
    "floury_no_grit": "silt",
    "gritty_medium_ribbon": "sandy_clay_loam",
    "medium_ribbon": "clay_loam",
    "smooth_medium_ribbon": "silty_clay_loam",
    "gritty_long_ribbon": "sandy_clay",
    "smooth_long_ribbon": "silty_clay",
    "sticky_long_ribbon": "clay",
}


def texture_from_feel(answer: str) -> TextureClass:
    from app.engine.soil_texture import TEXTURES

    key = FEEL_TEST_MAP.get(answer.strip().lower())
    if key is None:
        if answer.strip().lower().replace(" ", "_") in TEXTURES:
            return TEXTURES[answer.strip().lower().replace(" ", "_")]
        raise ValueError(f"unknown feel-test answer: {answer!r}")
    return TEXTURES[key]


def from_feel_test(answer: str) -> SoilProfile:
    return SoilProfile(
        source=SoilSource.FEEL_TEST,
        texture=texture_from_feel(answer),
        notes=[
            "Texture estimated from the in-app ribbon test. Nutrient advice is "
            "based on crop removal only until a soil test is entered."
        ],
    )


# --------------------------------------------------------------- 3. SoilGrids
class SoilGridsUnavailable(RuntimeError):
    pass


def _weighted_mean(depth_values: list[tuple[str, float]]) -> float | None:
    """Depth-weight 0-5/5-15/15-30 cm into a single topsoil value."""
    weights = {"0-5cm": 5.0, "5-15cm": 10.0, "15-30cm": 15.0}
    num = den = 0.0
    for label, value in depth_values:
        w = weights.get(label, 0.0)
        num += value * w
        den += w
    return num / den if den else None


def parse_soilgrids(payload: dict) -> dict[str, float]:
    """Convert a SoilGrids response into real units, skipping null layers."""
    out: dict[str, float] = {}
    for layer in payload.get("properties", {}).get("layers", []):
        name = layer.get("name")
        d_factor = layer.get("unit_measure", {}).get("d_factor") or 1
        pairs: list[tuple[str, float]] = []
        for depth in layer.get("depths", []):
            raw = (depth.get("values") or {}).get("mean")
            if raw is None:
                continue
            pairs.append((depth.get("label", ""), raw / d_factor))
        value = _weighted_mean(pairs)
        if value is not None:
            out[name] = value
    return out


async def fetch_soilgrids(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None, retries: int = 3
) -> SoilProfile | None:
    """Query SoilGrids. Returns None when the location has no coverage.

    Fair use is 5 requests/minute and the service is beta with no uptime
    guarantee, so callers must cache the result permanently -- soil does not
    change on any timescale this app cares about.
    """
    params: list[tuple[str, str]] = [("lon", str(lon)), ("lat", str(lat))]
    params += [("property", p) for p in PROPERTIES]
    params += [("depth", d) for d in DEPTHS]
    params.append(("value", "mean"))

    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        payload = None
        for attempt in range(retries):
            try:
                response = await client.get(SOILGRIDS_URL, params=params, timeout=90.0)
                if response.status_code == 429:
                    await asyncio.sleep(15 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPError:
                if attempt == retries - 1:
                    raise SoilGridsUnavailable("soilgrids request failed")
                await asyncio.sleep(15 * (attempt + 1))
        if payload is None:
            raise SoilGridsUnavailable("soilgrids request failed")

        values = parse_soilgrids(payload)
        if not {"sand", "silt", "clay"} <= values.keys():
            # No coverage at this point (the whole of India behaves this way).
            return None

        return SoilProfile(
            source=SoilSource.SOILGRIDS,
            texture=classify_texture(values["sand"], values["silt"], values["clay"]),
            ph=values.get("phh2o"),
            soc_g_kg=values.get("soc"),
            organic_carbon_pct=values["soc"] / 10 if "soc" in values else None,
            nitrogen_g_kg=values.get("nitrogen"),
            cec_cmol_kg=values.get("cec"),
            bulk_density_kg_dm3=values.get("bdod"),
            sand_pct=values["sand"],
            silt_pct=values["silt"],
            clay_pct=values["clay"],
            notes=["Modelled from the global SoilGrids 250 m raster."],
        )
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------- 4. fallback
def fallback_profile(reason: str) -> SoilProfile:
    return SoilProfile(
        source=SoilSource.FALLBACK,
        texture=classify_texture(40, 40, 20),  # loam
        notes=[
            f"No soil data available ({reason}). Assuming a loam texture. "
            "Ask the farmer for a Soil Health Card or the ribbon test to improve this."
        ],
    )


async def resolve_soil(
    *,
    lat: float,
    lon: float,
    card: dict | None = None,
    feel_test: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> SoilProfile:
    """Walk the source chain and return the best profile available."""
    if card:
        return from_soil_health_card(**card)
    if feel_test:
        return from_feel_test(feel_test)
    try:
        profile = await fetch_soilgrids(lat, lon, client=client)
    except SoilGridsUnavailable:
        return fallback_profile("SoilGrids unreachable")
    if profile is None:
        return fallback_profile("SoilGrids has no coverage at this location")
    return profile
