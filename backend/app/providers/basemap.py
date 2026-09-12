"""Which basemap this node tells its app to draw, and why it is the node's call.

The app used to hardcode OpenStreetMap raster tiles. Those are street tiles:
roads, settlements, administrative lines. For finding a restaurant that is the
right map. For drawing the boundary of your own field it is close to useless --
a farmer taps corners against a blank green area with no hedge, no bund and no
field edge visible, and the drawn polygon carries that guesswork into an area
figure that multiplies every kilogram and millimetre of the advice.

Google's Map Tiles API serves satellite imagery for the same tile scheme, so
MapLibre keeps rendering and only the source changes. On imagery a farmer can
see their own field.

Three things this deliberately does not do.

**It does not put the key in the APK.** An app-embedded map key is extractable
by anyone who downloads the release, and the bill lands on whoever deployed the
node. The node holds the key, creates the session, and hands the app a tile URL.
That is also the honest architecture for a federated network: the operator in
each country picks and pays for their own basemap, and the app asks rather than
assumes.

**It does not make Google mandatory.** No key configured means OSM, exactly as
before. `docs/DPG.md` indicator 4 claims platform independence, and that claim
survives only if the vendor-free path still runs. It does.

**It does not pretend the key is secret once served.** The tile URL contains it,
so any signed-in user can read it. Restrict the key to the Map Tiles API and set
a quota ceiling in the Google Cloud console; treat it as a spend limit, not a
secret. This is the same trade every mobile map makes, and saying so is better
than implying otherwise.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

SESSION_URL = "https://tile.googleapis.com/v1/createSession"
TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "(c) OpenStreetMap contributors"

# Google documents session tokens as valid for two weeks. Refreshed well inside
# that: a token that expires mid-draw leaves a farmer tapping corners onto grey.
SESSION_TTL_SECONDS = 6 * 24 * 3600


@dataclass(frozen=True)
class Basemap:
    provider: str
    tile_url: str
    attribution: str
    max_zoom: int
    # True when this is imagery a boundary can actually be drawn against.
    satellite: bool


OSM = Basemap(
    provider="openstreetmap",
    tile_url=OSM_TILE_URL,
    attribution=OSM_ATTRIBUTION,
    max_zoom=19,
    satellite=False,
)

_cache: dict[str, tuple[float, Basemap]] = {}


def _session_body(language: str, region: str) -> dict:
    return {
        "mapType": "satellite",
        "language": language,
        "region": region,
        # Imagery alone has no labels, and a farmer orienting themselves needs
        # the village name. Roads over satellite is the readable combination.
        "layerTypes": ["layerRoadmap"],
        "overlay": True,
        "highDpi": False,
    }


async def fetch(
    api_key: str,
    *,
    language: str = "en",
    region: str = "IN",
    client: httpx.AsyncClient | None = None,
) -> Basemap:
    """Google satellite tiles, or OSM if that cannot be arranged.

    Never raises. A basemap is not worth failing a request over -- the app must
    always get something it can draw, and OSM is a working map even when it is
    the wrong one.
    """
    if not api_key:
        return OSM

    key = f"{language}:{region}"
    cached = _cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            SESSION_URL,
            params={"key": api_key},
            json=_session_body(language, region),
            timeout=20.0,
        )
        if response.status_code != 200:
            # 403 is usually the Map Tiles API not enabled on the project, which
            # is the common first-run mistake and worth naming in the log.
            logger.warning(
                "Google Map Tiles session refused (%s): %s; using OpenStreetMap",
                response.status_code, response.text[:200],
            )
            return OSM

        token = (response.json() or {}).get("session")
        if not token:
            logger.warning("Google Map Tiles returned no session; using OpenStreetMap")
            return OSM

    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Google Map Tiles unreachable (%s); using OpenStreetMap", exc)
        return OSM
    finally:
        if owns:
            await client.aclose()

    basemap = Basemap(
        provider="google-satellite",
        tile_url=f"{TILE_URL}?session={token}&key={api_key}",
        attribution="Imagery (c) Google",
        max_zoom=20,
        satellite=True,
    )
    _cache[key] = (time.time() + SESSION_TTL_SECONDS, basemap)
    return basemap
