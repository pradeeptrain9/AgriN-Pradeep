"""Bhuvan (ISRO / NRSC) national soil texture, for the country SoilGrids masks.

SoilGrids v2 returns null for every property over India -- verified again on
2026-09-12 against a Brazilian control, which answered normally. So on the
India node the chain fell straight to its last resort and assumed loam for
every field in the country.

That assumption is not neutral. Texture sets field capacity and wilting point,
and therefore total available water, which the irrigation engine divides into
to decide when a crop runs dry. FAO-56 Table 19 puts loam at 130 mm of
available water per metre of root depth and loamy sand at 80. Assuming loam on
sandy land overstates the reservoir by more than half, and the advice that
follows tells a farmer they have water in the profile that is not there.

Bhuvan is India's own answer: ISRO's national geoportal, free, no key, no
registration. `SOIL_TEX_250K` covers the whole country. Queried at a Punjab
field it returns:

    {"SO_TEX": "03", "DESCR": "Coarse Texture", "DESCR_MAP": "Loamy sand,sand"}

where the fallback had been saying loam.

What this is not: a reading of one field. The polygon that answered spans
69-77 E and 24-32 N, so it is a broad regional class carrying a texture, at
1:250,000. It belongs above the fallback and below anything measured -- a
farmer's Soil Health Card is laboratory analysis of their own soil and beats
this outright. Bhuvan also publishes 1:50,000 state layers, but only for eight
states; Punjab is not among them.
"""

from __future__ import annotations

import httpx

from app.engine.soil_texture import TextureClass, classify_texture

WMS_URL = "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms"
LAYER = "soil:SOIL_TEX_250K"

USER_AGENT = "AgriN/0.1 (digital public good; agriculture advisory node)"

# Half-width of the query box in degrees, about 1 km. Small enough that the
# answer belongs to the field rather than its district, large enough that the
# point does not land in a sliver between polygons.
QUERY_HALF_DEGREES = 0.01


class BhuvanUnavailable(RuntimeError):
    pass


# Bhuvan's classes are groupings, not USDA classes, so each maps to the
# representative sand/silt/clay of its group and goes through the same USDA
# triangle everything else uses. Deliberately mapped to the *coarser* end of
# each band: overstating available water is the harmful direction here, because
# it tells a farmer the soil is holding water that is not in it.
TEXTURE_GROUPS: dict[str, tuple[float, float, float]] = {
    # description fragment    ->  (sand, silt, clay)
    "coarse": (82.0, 12.0, 6.0),              # loamy sand, sand
    "moderately coarse": (65.0, 25.0, 10.0),  # sandy loam
    "medium": (40.0, 40.0, 20.0),             # loam, silt loam
    "moderately fine": (32.0, 34.0, 34.0),    # clay loam and relatives
    "fine": (20.0, 25.0, 55.0),               # clay, silty clay
    "very fine": (15.0, 20.0, 65.0),
}


def texture_from_description(descr: str) -> TextureClass | None:
    """Map a Bhuvan texture class to a USDA class, or None if unrecognised.

    Longest match first: "moderately coarse" must not be read as "coarse",
    which would put a sandy loam in the sand band and understate its water by
    a third.
    """
    text = (descr or "").strip().lower()
    if not text:
        return None
    for name in sorted(TEXTURE_GROUPS, key=len, reverse=True):
        if name in text:
            return classify_texture(*TEXTURE_GROUPS[name])
    return None


async def fetch_texture(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None
) -> tuple[TextureClass, str] | None:
    """Texture class at a point, with the description Bhuvan gave for it.

    None means Bhuvan answered and had nothing here -- outside India, or a gap
    in the layer. That is different from an outage, which raises.
    """
    d = QUERY_HALF_DEGREES
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": LAYER,
        "query_layers": LAYER,
        "info_format": "application/json",
        "srs": "EPSG:4326",
        "width": "101",
        "height": "101",
        "x": "50",
        "y": "50",
        "feature_count": "1",
        "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
    }

    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        response = await client.get(WMS_URL, params=params, timeout=45.0)
        if response.status_code != 200:
            raise BhuvanUnavailable(
                f"Bhuvan WMS returned {response.status_code}"
            )
        try:
            features = response.json().get("features", [])
        except ValueError as exc:
            # A WMS serving an XML exception report where JSON was asked for.
            raise BhuvanUnavailable(f"Bhuvan returned non-JSON: {exc}") from exc

        if not features:
            return None

        properties = features[0].get("properties", {}) or {}
        descr = properties.get("DESCR") or properties.get("DESCR_MAP") or ""
        texture = texture_from_description(descr)
        if texture is None:
            return None
        return texture, str(descr).strip()
    except httpx.HTTPError as exc:
        raise BhuvanUnavailable(f"Bhuvan unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()
