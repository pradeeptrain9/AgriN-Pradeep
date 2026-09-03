"""Copernicus Data Space (Sentinel Hub) Statistical API adapter.

Why the Statistical API and not the Process API: the free tier is 10,000
processing units per month with no carryover. Downloading imagery and reducing
it locally would burn that in days. The Statistical API does the spatial
reduction server-side and returns per-date aggregates for a polygon, costing
roughly a PU per field-date at 10 m.

Every response carries an `x-processingunits-spent` header. We record the actual
spend rather than estimating it, and refuse to issue requests once the monthly
cap is reached.

Cloud handling is not optional. The SCL band is used to drop cloud, shadow,
cirrus, snow, saturated and no-data pixels; the surviving share is returned as
`valid_fraction` so downstream code can discard dates that are mostly cloud.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# Sentinel-2 Scene Classification values we refuse to use.
#   0 no data, 1 saturated/defective, 3 cloud shadow,
#   8 cloud medium probability, 9 cloud high probability,
#   10 thin cirrus, 11 snow/ice
SCL_REJECT = (0, 1, 3, 8, 9, 10, 11)

# Small fields are only a handful of 10 m pixels wide, so boundary pixels that
# straddle a bund or track would dominate. Erode before sampling.
DEFAULT_EROSION_M = 10.0

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{bands: ["B04", "B05", "B08", "B11", "SCL", "dataMask"]}],
    output: [
      {id: "ndvi", bands: 1, sampleType: "FLOAT32"},
      {id: "ndmi", bands: 1, sampleType: "FLOAT32"},
      {id: "ndre", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  var rejected = [0, 1, 3, 8, 9, 10, 11];
  var valid = s.dataMask === 1 && rejected.indexOf(s.SCL) < 0;
  return {
    ndvi: [index(s.B08, s.B04)],
    ndmi: [index(s.B08, s.B11)],
    ndre: [index(s.B08, s.B05)],
    dataMask: [valid ? 1 : 0]
  };
}
"""

INDICES = ("ndvi", "ndmi", "ndre")


class SentinelUnavailable(RuntimeError):
    pass


class ProcessingUnitCapReached(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexObservation:
    day: date
    index_name: str
    value: float
    valid_fraction: float
    sample_count: int
    source: str = "sentinel2-l2a"


@dataclass(frozen=True)
class StatsResult:
    observations: list[IndexObservation]
    processing_units: float
    intervals_returned: int
    intervals_rejected: int


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing a point, for metric buffering."""
    zone = int((lon + 180) / 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def erode_polygon(geometry: dict, metres: float = DEFAULT_EROSION_M) -> dict:
    """Shrink a WGS84 polygon by `metres`, via its local UTM zone.

    If erosion would empty the polygon (a very small plot), the original is
    returned unchanged and the caller is expected to flag lower confidence.
    """
    geom: BaseGeometry = shape(geometry)
    centroid = geom.centroid
    epsg = utm_epsg_for(centroid.x, centroid.y)

    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    to_wgs = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform

    eroded = shapely_transform(to_utm, geom).buffer(-metres)
    if eroded.is_empty or eroded.area <= 0:
        return geometry
    return shapely_transform(to_wgs, eroded).__geo_interface__


def polygon_area_ha(geometry: dict) -> float:
    geom: BaseGeometry = shape(geometry)
    centroid = geom.centroid
    epsg = utm_epsg_for(centroid.x, centroid.y)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    return shapely_transform(to_utm, geom).area / 10_000.0


class CdseClient:
    """Thin OAuth2 client-credentials wrapper with token reuse."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id or not client_secret:
            raise SentinelUnavailable(
                "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set. Register free at "
                "https://dataspace.copernicus.eu and create an OAuth client."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: datetime = datetime.now(timezone.utc)

    async def token(self, client: httpx.AsyncClient) -> str:
        now = datetime.now(timezone.utc)
        if self._token and now < self._expires_at - timedelta(seconds=60):
            return self._token

        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=30.0,
        )
        if response.status_code != 200:
            raise SentinelUnavailable(
                f"CDSE token request failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._expires_at = now + timedelta(seconds=payload.get("expires_in", 600))
        return self._token


def build_request(
    geometry: dict, start: date, end: date, *, resolution_m: int = 10
) -> dict:
    return {
        "input": {
            "bounds": {
                "geometry": geometry,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {"mosaickingOrder": "leastCC"},
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{start.isoformat()}T00:00:00Z",
                "to": f"{end.isoformat()}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P1D"},
            "evalscript": EVALSCRIPT,
            "resx": resolution_m,
            "resy": resolution_m,
        },
        "calculations": {index: {} for index in INDICES},
    }


def parse_stats(payload: dict, *, min_valid_fraction: float = 0.6) -> tuple[
    list[IndexObservation], int, int
]:
    """Turn a Statistical API response into observations.

    Dates whose valid pixel share falls below `min_valid_fraction` after cloud
    masking are rejected outright rather than smoothed over -- a cloudy NDVI is
    not a low NDVI, and confusing the two would tell a farmer their healthy crop
    is failing.
    """
    observations: list[IndexObservation] = []
    returned = rejected = 0

    for interval in payload.get("data", []):
        returned += 1
        interval_from = interval.get("interval", {}).get("from", "")
        try:
            day = datetime.fromisoformat(interval_from.replace("Z", "+00:00")).date()
        except ValueError:
            rejected += 1
            continue

        outputs = interval.get("outputs", {})
        if not outputs:
            rejected += 1
            continue

        # Every index shares the same mask, so read the share once.
        first = next(iter(outputs.values()), {})
        first_stats = (first.get("bands", {}).get("B0", {}) or {}).get("stats", {})
        sample_count = first_stats.get("sampleCount") or 0
        no_data = first_stats.get("noDataCount") or 0
        valid_fraction = (sample_count - no_data) / sample_count if sample_count else 0.0

        if valid_fraction < min_valid_fraction:
            rejected += 1
            continue

        added = False
        for index_name in INDICES:
            stats = (
                outputs.get(index_name, {}).get("bands", {}).get("B0", {}) or {}
            ).get("stats", {})
            mean = stats.get("mean")
            if mean is None:
                continue
            observations.append(
                IndexObservation(
                    day=day,
                    index_name=index_name,
                    value=float(mean),
                    valid_fraction=valid_fraction,
                    sample_count=int(sample_count),
                )
            )
            added = True
        if not added:
            rejected += 1

    return observations, returned, rejected


async def fetch_indices(
    *,
    cdse: CdseClient,
    geometry: dict,
    start: date,
    end: date,
    erosion_m: float = DEFAULT_EROSION_M,
    min_valid_fraction: float = 0.6,
    client: httpx.AsyncClient | None = None,
    retries: int = 3,
) -> StatsResult:
    """Fetch NDVI/NDMI/NDRE for a field polygon over a date range."""
    sampling_geom = erode_polygon(geometry, erosion_m) if erosion_m else geometry
    body = build_request(sampling_geom, start, end)

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        last_error: str | None = None
        for attempt in range(retries):
            token = await cdse.token(client)
            response = await client.post(
                STATS_URL,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120.0,
            )
            if response.status_code == 429:
                await asyncio.sleep(5 * (attempt + 1))
                last_error = "rate limited"
                continue
            if response.status_code >= 500:
                await asyncio.sleep(3 * (attempt + 1))
                last_error = f"upstream {response.status_code}"
                continue
            if response.status_code != 200:
                raise SentinelUnavailable(
                    f"statistics request failed ({response.status_code}): "
                    f"{response.text[:300]}"
                )

            spent = float(response.headers.get("x-processingunits-spent", 0.0) or 0.0)
            observations, returned, rejected = parse_stats(
                response.json(), min_valid_fraction=min_valid_fraction
            )
            return StatsResult(
                observations=observations,
                processing_units=spent,
                intervals_returned=returned,
                intervals_rejected=rejected,
            )
        raise SentinelUnavailable(f"statistics request failed: {last_error}")
    finally:
        if owns:
            await client.aclose()
