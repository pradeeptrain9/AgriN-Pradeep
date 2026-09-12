"""BigQuery export for federation aggregates, for when a node is a country.

At three fields this is pointless and I said so. The case for building it now is
that the shape of what gets exported is a governance decision, not a scaling one,
and it is far easier to argue about while the table is empty.

What crosses this boundary is exactly what `/federation/aggregates` already
publishes: district-level rows that survived k-anonymity, signed by the node. Not
fields, not farmers, not geometry. The same `contains_identifiers` refusal that
guards the HTTP endpoint runs again here, because an export path that skipped it
would be the obvious way for raw data to escape a system whose whole promise is
that it does not.

Why a separate destination at all. A ministry asking "which districts are in
deficit this week, across every state" is a question about tens of millions of
rows from hundreds of nodes. That is a warehouse query, and answering it by
fanning out HTTP requests to every node in the federation would be slow, fragile
and a denial-of-service on whoever is smallest. Each node pushes its own signed
aggregates; the warehouse is a reader, never an authority.

The node stays the source of truth. A row in BigQuery is a copy of something a
node published and signed, and the signature travels with it -- so a
disagreement between warehouse and node is always resolved in the node's favour,
and can be checked rather than argued.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/bigquery.insertdata"

INSERT_URL = (
    "https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
    "/datasets/{dataset}/tables/{table}/insertAll"
)

# One row per district per indicator per publication. Flat on purpose: a
# warehouse table that mirrors a nested API payload is painful to query, and the
# people who need this most are analysts, not engineers.
SCHEMA_FIELDS = (
    "node_id", "country", "spec_version", "indicator", "unit",
    "district", "value", "n", "generated_at", "signature",
)


class BigQueryUnavailable(RuntimeError):
    pass


def rows_from_envelope(envelope: dict) -> list[dict[str, Any]]:
    """Flatten one signed aggregates envelope into warehouse rows.

    The signature is carried on every row rather than once per batch. It is
    duplication, and it means a single row copied out of the table is still
    attributable to the node that published it -- which is the property that
    makes the warehouse safe to share more widely than the nodes themselves.
    """
    payload = envelope.get("payload") or {}
    signature = envelope.get("signature") or ""
    node_id = envelope.get("node_id") or ""

    rows: list[dict[str, Any]] = []
    for group in payload.get("groups") or payload.get("districts") or []:
        rows.append({
            "node_id": node_id,
            "country": payload.get("country"),
            "spec_version": payload.get("spec_version"),
            "indicator": payload.get("indicator"),
            "unit": payload.get("unit"),
            "district": group.get("district") or group.get("admin2"),
            "value": group.get("value") or group.get("mean"),
            "n": group.get("n") or group.get("count"),
            "generated_at": payload.get("generated_at"),
            "signature": signature,
        })
    return rows


def _access_token(credentials_path: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise BigQueryUnavailable(
            "google-auth is not installed, so BigQuery cannot be reached."
        ) from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=[SCOPE]
        )
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001
        raise BigQueryUnavailable(f"BigQuery credentials rejected: {exc}") from exc
    return creds.token


async def export(
    envelope: dict,
    *,
    project: str,
    dataset: str,
    table: str,
    credentials_path: str,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Push one signed envelope. Returns the number of rows accepted."""
    if not (project and dataset and table and credentials_path):
        raise BigQueryUnavailable("BigQuery export is not configured on this node.")

    # The same refusal that guards the HTTP endpoint. An export path that
    # skipped it would be the shortest route out of this system for raw data.
    from app.federation.aggregate import contains_identifiers

    leaked = contains_identifiers(envelope.get("payload") or {})
    if leaked:
        raise BigQueryUnavailable(
            f"Refusing to export: payload contains identifiers {leaked}"
        )

    rows = rows_from_envelope(envelope)
    if not rows:
        return 0

    body = {
        # Makes the insert idempotent: a retry after a timeout cannot
        # double-count a district, which would move a national mean.
        "rows": [
            {
                "insertId": f"{row['node_id']}:{row['indicator']}:"
                            f"{row['district']}:{row['generated_at']}",
                "json": row,
            }
            for row in rows
        ],
        "skipInvalidRows": False,
        "ignoreUnknownValues": False,
    }

    token = _access_token(credentials_path)
    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            INSERT_URL.format(project=project, dataset=dataset, table=table),
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise BigQueryUnavailable(f"BigQuery unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    if response.status_code != 200:
        raise BigQueryUnavailable(
            f"BigQuery insert failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        result = response.json() or {}
    except ValueError as exc:
        raise BigQueryUnavailable(f"BigQuery returned non-JSON: {exc}") from exc

    if errors := result.get("insertErrors"):
        # Partial success is still a failure here: a half-written district makes
        # the warehouse disagree with the node, and the node is the truth.
        raise BigQueryUnavailable(
            f"BigQuery rejected rows: {json.dumps(errors)[:300]}"
        )

    return len(rows)
