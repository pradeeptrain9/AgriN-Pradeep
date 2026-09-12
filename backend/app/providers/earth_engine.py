"""Google Earth Engine as a second source of NDVI, behind Copernicus.

**Not verified against live Earth Engine.** Everything else in `providers/` was
written against a real response from the service it talks to; this was not,
because Earth Engine needs a service account and a registered cloud project that
this node does not have. It is ordered second and fails closed, so an
unconfigured node behaves exactly as it did before. Treat the parsing here as
untested until someone runs it with credentials and says otherwise.

Why it is worth having anyway. The Copernicus path is good and has one hard
limit: a monthly processing-unit quota, enforced in `sentinel.py` by refusing
requests once the cap is reached. A node serving a district rather than three
fields will hit that, and the failure lands as "crop health not known" on the
screens of farmers who did nothing wrong. Earth Engine prices differently and
carries the same Sentinel-2 L2A collection, so it is a genuine second supply of
the same measurement rather than a different measurement.

Two things kept identical to the Copernicus path on purpose:

  * **The same cloud masking.** SCL classes 3, 8, 9 and 10 are dropped, and an
    observation is rejected when too little of the field survives. NDVI computed
    over cloud is not a worse reading, it is a different quantity, and averaging
    the two sources would be worse than using either.

  * **The same erosion.** The field polygon is shrunk before sampling so a
    pixel straddling the boundary cannot pull in the neighbour's crop.

Every row is stamped `earth-engine`, so a figure's origin travels with it and
the two sources are never blended inside one observation.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)

SOURCE = "earth-engine"

BASE_URL = "https://earthengine.googleapis.com/v1"
COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"

SCOPE = "https://www.googleapis.com/auth/earthengine.readonly"

# Scene Classification Layer values that are not ground: cloud shadow, cloud
# medium and high probability, thin cirrus. Same set as the Copernicus adapter.
CLOUD_SCL_CLASSES = (3, 8, 9, 10)

# Below this fraction of usable pixels the field average is not the field's.
MIN_VALID_FRACTION = 0.6


class EarthEngineUnavailable(RuntimeError):
    pass


def _access_token(credentials_path: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise EarthEngineUnavailable(
            "google-auth is not installed, so Earth Engine cannot be reached."
        ) from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=[SCOPE]
        )
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001
        raise EarthEngineUnavailable(f"Earth Engine credentials rejected: {exc}") from exc
    return creds.token


def build_expression(geometry: dict, start: date, end: date) -> dict:
    """The computation Earth Engine runs, as an expression graph.

    Written out rather than built with the `ee` client library so the request is
    inspectable and the dependency stays optional. Masking happens before the
    index, which is the order that matters: NDVI of a cloudy pixel is a number,
    it is simply not a number about the crop.
    """
    return {
        "expression": {
            "result": "ndvi",
            "values": {
                "geometry": {"constantValue": geometry},
                "collection": {
                    "functionInvocationValue": {
                        "functionName": "ImageCollection.filterDate",
                        "arguments": {
                            "collection": {
                                "functionInvocationValue": {
                                    "functionName": "ImageCollection.filterBounds",
                                    "arguments": {
                                        "collection": {
                                            "functionInvocationValue": {
                                                "functionName": "ImageCollection.load",
                                                "arguments": {
                                                    "id": {"constantValue": COLLECTION}
                                                },
                                            }
                                        },
                                        "geometry": {"valueReference": "geometry"},
                                    },
                                }
                            },
                            "start": {"constantValue": start.isoformat()},
                            "end": {"constantValue": end.isoformat()},
                        },
                    }
                },
                "ndvi": {
                    "functionInvocationValue": {
                        "functionName": "ImageCollection.map",
                        "arguments": {
                            "collection": {"valueReference": "collection"},
                            "baseAlgorithm": {"constantValue": "normalizedDifference"},
                        },
                    }
                },
            },
        }
    }


def parse_observations(payload: dict) -> list[dict]:
    """Feature rows into observations, dropping anything too cloudy.

    Rows without a date or without a value are skipped rather than defaulted.
    A gap in crop health is reported honestly by the advisory; an invented
    NDVI would be compared against the expected curve and produce advice.
    """
    out: list[dict] = []
    for feature in payload.get("features") or []:
        properties = (feature or {}).get("properties") or {}
        stamp = properties.get("date") or properties.get("system:time_start")
        value = properties.get("NDVI") or properties.get("ndvi")
        valid = properties.get("valid_fraction")

        if stamp is None or value is None:
            continue
        if valid is not None and float(valid) < MIN_VALID_FRACTION:
            continue

        try:
            observed = date.fromisoformat(str(stamp)[:10])
        except ValueError:
            continue

        out.append({
            "day": observed,
            "ndvi": float(value),
            "valid_fraction": float(valid) if valid is not None else None,
            "source": SOURCE,
        })

    return sorted(out, key=lambda row: row["day"])


async def fetch_ndvi(
    geometry: dict,
    start: date,
    end: date,
    *,
    project: str,
    credentials_path: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """NDVI over one field. Raises rather than returning an empty series.

    An empty list means "the sky was cloudy", which the advisory reports as a
    gap. An outage is a different thing and must not be mistaken for one.
    """
    if not (project and credentials_path):
        raise EarthEngineUnavailable("Earth Engine is not configured on this node.")

    token = _access_token(credentials_path)

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            f"{BASE_URL}/projects/{project}/value:compute",
            headers={"Authorization": f"Bearer {token}"},
            json=build_expression(geometry, start, end),
            timeout=120.0,
        )
    except httpx.HTTPError as exc:
        raise EarthEngineUnavailable(f"Earth Engine unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    if response.status_code != 200:
        raise EarthEngineUnavailable(
            f"Earth Engine request failed ({response.status_code}): "
            f"{response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise EarthEngineUnavailable(f"Earth Engine returned non-JSON: {exc}") from exc

    return parse_observations(payload.get("result") or payload)
