"""Federation endpoints: the public face of an AgriN node to its peers.

Everything here is deliberately readable without authentication, because a
federated network whose discovery requires prior credentials cannot be joined.
What is public is: who this node is, what it can do, what models it offers, and
aggregate statistics that have passed k-anonymity. What is never public is any
individual farmer's data.
"""

import datetime
import pathlib

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.federation import client as peer_client
from app.federation import registry, vocab
from app.federation.aggregate import (
    DEFAULT_K,
    Observation,
    aggregate,
    contains_identifiers,
)
from app.federation.identity import ensure_identity, private_key
from app.federation.signing import seal

router = APIRouter(prefix="/federation", tags=["federation"])

# RFC 8615 puts well-known URIs at the origin root, and that is the whole point
# of them: a peer that has only a hostname can discover the node without being
# told this implementation's prefix first. Served at both paths -- the root one
# is canonical and is what /conformance now requires; the prefixed one stays
# because peers already pinned to it should not break on an upgrade.
well_known = APIRouter(tags=["federation"])

SPEC_VERSION = "agrin-core-1.0.0"
MODELS_DIR = pathlib.Path("models")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@well_known.get("/.well-known/agrin-node")
@router.get("/.well-known/agrin-node")
async def node_descriptor(db: AsyncSession = Depends(get_db)) -> dict:
    """Discovery document. A peer reads this first to learn what it can ask for."""
    settings = get_settings()
    identity = await ensure_identity(db)
    return {
        "spec_version": SPEC_VERSION,
        "node_id": identity["node_id"],
        "country": identity["country"],
        "public_key": identity["public_key"],
        "algorithm": "ed25519",
        "capabilities": [
            "advisory", "disease_diagnosis", "model_registry", "aggregate_statistics",
        ],
        "vocabularies": {
            "crops": "AGROVOC",
            "units": "UCUM",
            "geometry": "GeoJSON RFC 7946",
            "time": "RFC 3339",
        },
        "data_policy": {
            "shares_raw_farmer_data": False,
            "shares_aggregates": True,
            "minimum_k_anonymity": DEFAULT_K,
            "shares_models": True,
        },
        "licence": {"code": "Apache-2.0", "data": "CC-BY-4.0"},
        "endpoints": {
            "models": "/federation/models",
            "aggregates": "/federation/aggregates",
            "vocabulary": "/federation/vocabulary",
            "conformance": "/federation/conformance",
        },
    }


@router.get("/vocabulary")
async def vocabulary() -> dict:
    """The shared terms. A peer uses this to map its own codes onto ours."""
    return {
        "spec_version": SPEC_VERSION,
        "crops": [vocab.describe_crop(code) for code in sorted(vocab.CROP_CONCEPTS)],
        "units": vocab.UNITS,
        "indicators": vocab.INDICATORS,
    }


@router.get("/models")
async def list_models() -> dict:
    """Model cards this node publishes. Cards are served even without weights."""
    models = registry.discover(MODELS_DIR)
    return {
        "spec_version": SPEC_VERSION,
        "count": len(models),
        "models": [m.to_dict() for m in models],
        "note": (
            "Models are shared; training data is not. A peer should evaluate any "
            "model against its own held-out data before adopting it."
        ),
    }


@router.get("/models/{model_id}/artifact")
async def model_artifact(model_id: str, acknowledge_not_for_deployment: bool = False):
    """Serve the weights. The card is the contract; this is just the payload.

    A model the publishing node has marked `not_for_deployment` needs the
    acknowledgement flag. Failed runs are published deliberately -- a peer that
    can see the negative result does not spend a month reproducing it -- but
    the thing pulling weights across a border is a script, and this registry
    was listing a fit model and an unfit one at the same size with nothing
    machine-readable between them. Opting in has to be an act, not an oversight.
    """
    for model in registry.discover(MODELS_DIR):
        if model.model_id == model_id:
            if model.artifact_path is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Model '{model_id}' is published as a card only; its weights "
                        "have not been trained yet."
                    ),
                )
            if model.status == "not_for_deployment" and not acknowledge_not_for_deployment:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Model '{model_id}' is published as a negative result and is "
                        "not fit for farmer-facing diagnosis. Read its card, then "
                        "retry with acknowledge_not_for_deployment=true if you want "
                        "it for research or comparison."
                    ),
                )
            return FileResponse(
                model.artifact_path,
                media_type="application/octet-stream",
                filename=model.artifact_path.name,
                headers={"X-Model-SHA256": model.sha256 or ""},
            )
    raise HTTPException(status_code=404, detail=f"Unknown model '{model_id}'")


@router.get("/aggregates")
async def aggregates(
    indicator: str = Query("ndvi_anomaly_mean"),
    k: int = Query(DEFAULT_K, ge=2, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """District-level statistics, k-anonymised and signed.

    `k` may be raised by the caller but never lowered below 2, and the node's own
    floor still applies: a request for k=2 does not weaken a node configured for
    a higher threshold.
    """
    if indicator not in vocab.INDICATORS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown indicator. Known: {sorted(vocab.INDICATORS)}",
        )

    effective_k = max(k, DEFAULT_K)
    observations = await _collect(db, indicator)
    result = aggregate(observations, k=effective_k)

    payload = {
        "spec_version": SPEC_VERSION,
        "indicator": indicator,
        "unit": vocab.INDICATORS[indicator]["unit"],
        "generated_at": _now(),
        **result.to_dict(),
    }

    # Belt and braces: refuse to publish if anything identifying slipped in.
    leaked = contains_identifiers(payload)
    if leaked:
        raise HTTPException(
            status_code=500,
            detail=f"Refusing to publish: payload contains identifiers {leaked}",
        )

    identity = await ensure_identity(db)
    envelope = seal(
        node_id=identity["node_id"],
        payload=payload,
        private_key_b64=await private_key(db),
        issued_at=_now(),
    )
    return envelope.to_dict()


async def _collect(db: AsyncSession, indicator: str) -> list[Observation]:
    """Read the node's own data into observations. District, never geometry."""
    if indicator == "ndvi_anomaly_mean":
        rows = await db.execute(
            text(
                "SELECT f.id::text AS field_id, COALESCE(f.district, 'unknown') AS district, "
                "  c.crop_code, o.value "
                "FROM observations o "
                "JOIN fields f ON f.id = o.field_id "
                "JOIN crop_cycles c ON c.field_id = f.id AND c.status = 'active' "
                "WHERE o.index_name = 'ndvi' AND f.archived_at IS NULL"
            )
        )
    elif indicator == "disease_incidence":
        rows = await db.execute(
            text(
                "SELECT d.id::text AS field_id, "
                "  COALESCE(f.district, 'unknown') AS district, "
                "  COALESCE(d.crop_code, 'unknown') AS crop_code, 1.0 AS value "
                "FROM diagnoses d LEFT JOIN fields f ON f.id = d.field_id "
                "WHERE d.resolved_by <> 'inconclusive'"
            )
        )
    else:
        return []

    return [
        Observation(
            field_id=row["field_id"], district=row["district"],
            crop_code=row["crop_code"], indicator=indicator, value=float(row["value"]),
        )
        for row in rows.mappings()
    ]


@router.get("/conformance")
async def conformance() -> dict:
    """What a node must implement to be part of this network.

    Published by the node itself so conformance can be checked over the wire
    rather than by reading a document.
    """
    return {
        "spec_version": SPEC_VERSION,
        "required_endpoints": [
            {"path": "/.well-known/agrin-node", "method": "GET"},
            {"path": "/federation/.well-known/agrin-node", "method": "GET",
             "note": "deprecated alias; RFC 8615 puts this at the origin root"},
            {"path": "/federation/vocabulary", "method": "GET"},
            {"path": "/federation/models", "method": "GET"},
            {"path": "/federation/aggregates", "method": "GET"},
            {"path": "/federation/conformance", "method": "GET"},
        ],
        "required_behaviours": [
            "Crop codes resolve to AGROVOC URIs.",
            "Quantities carry UCUM units.",
            "Geometry is GeoJSON per RFC 7946.",
            "Aggregates enforce k-anonymity of at least 5 distinct fields.",
            "Group totals are suppressed when any constituent cell is suppressed.",
            "Published payloads are Ed25519-signed over a canonical JSON encoding.",
            "No endpoint exposes individual farmer records.",
            "Every published model carries a model card with stated limitations.",
        ],
        "prohibited": [
            "Serving raw farmer data, geometry or contact details to a peer.",
            "Publishing an aggregate cell backed by fewer than k distinct fields.",
            "Serving model weights without a model card.",
        ],
    }


# --------------------------------------------------------------------- peers
@router.post("/peers", status_code=201)
async def add_peer(
    base_url: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Register a peer by URL, pinning the key it presents at this moment.

    Pinning is the whole security model. Every later payload from this peer is
    verified against the key stored here, never against a key that arrives with
    the payload -- otherwise an attacker signs with their own key and passes.

    This is trust-on-first-use, and it is stated as such rather than dressed up:
    the pin is only as good as this first contact. A production deployment would
    confirm the key out of band before calling this.
    """
    descriptor = await peer_client.discover(base_url)
    warnings = peer_client.assess_policy(descriptor)

    await db.execute(
        text(
            "INSERT INTO peer_nodes (node_id, public_key, base_url, country, trusted) "
            "VALUES (:id, :key, :url, :country, :trusted) "
            "ON CONFLICT (node_id) DO UPDATE SET base_url = EXCLUDED.base_url, "
            "country = EXCLUDED.country, last_seen_at = now()"
        ),
        {
            "id": descriptor.node_id, "key": descriptor.public_key,
            "url": descriptor.base_url, "country": descriptor.country,
            "trusted": not warnings,
        },
    )
    await db.commit()
    return {
        "peer": descriptor.to_dict(),
        "key_pinned": True,
        "trusted": not warnings,
        "warnings": warnings,
        "note": (
            "The public key recorded now is the only one that will be accepted "
            "from this peer. A key change will show up as a verification failure, "
            "which is the intended behaviour."
        ),
    }


@router.get("/peers")
async def list_peers(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        text(
            "SELECT node_id, country, base_url, trusted, added_at, last_seen_at "
            "FROM peer_nodes ORDER BY added_at"
        )
    )
    return {
        "peers": [
            {
                "node_id": row["node_id"], "country": row["country"],
                "base_url": row["base_url"], "trusted": row["trusted"],
                "added_at": row["added_at"].isoformat(),
                "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
            }
            for row in result.mappings()
        ]
    }


@router.post("/peers/{node_id}/pull")
async def pull_from_peer(
    node_id: str,
    indicator: str = Query("ndvi_anomaly_mean"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Pull models and aggregates from a registered peer.

    Verification uses the key pinned at registration, read from our own database.
    """
    result = await db.execute(
        text("SELECT node_id, public_key, base_url, country, trusted FROM peer_nodes "
             "WHERE node_id = :id"),
        {"id": node_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{node_id}' is not a registered peer. Register it first.",
        )

    descriptor = peer_client.PeerDescriptor(
        node_id=row["node_id"], country=row["country"] or "??",
        public_key=row["public_key"], spec_version=SPEC_VERSION,
        capabilities=[], shares_raw_farmer_data=False,
        minimum_k_anonymity=DEFAULT_K, base_url=row["base_url"],
    )
    try:
        pulled = await peer_client.pull(
            descriptor, row["public_key"], indicator=indicator
        )
    except peer_client.SignatureRejected as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except peer_client.PeerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await db.execute(
        text("UPDATE peer_nodes SET last_seen_at = now() WHERE node_id = :id"),
        {"id": node_id},
    )
    await db.commit()
    return pulled.to_dict()
