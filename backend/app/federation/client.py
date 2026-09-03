"""The consuming half of federation: pulling from a peer.

Everything so far has been the publishing side. This is what a node does when it
wants something from another node, and the order of operations is the whole
point:

  1. discover   read the peer's descriptor to learn its id, public key and policy
  2. pin        record that key locally, once, deliberately
  3. verify     check every later payload against the PINNED key, not against
                whatever key the payload arrives with

Step 3 is what makes the signature worth anything. Verifying a payload using a
key fetched in the same request proves only that the sender can sign - which any
attacker can do with their own keypair. The key must come from a prior, separate
act of trust.
"""

import datetime
from dataclasses import dataclass, field as dc_field

import httpx

from app.federation.signing import open_envelope

USER_AGENT = "AgriN-node/1.0 (federation client)"
TIMEOUT = 30.0


class PeerError(RuntimeError):
    pass


class SignatureRejected(PeerError):
    """A payload failed verification against the peer's pinned key."""


@dataclass
class PeerDescriptor:
    node_id: str
    country: str
    public_key: str
    spec_version: str
    capabilities: list[str]
    shares_raw_farmer_data: bool
    minimum_k_anonymity: int
    base_url: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "country": self.country,
            "public_key": self.public_key, "spec_version": self.spec_version,
            "capabilities": self.capabilities, "base_url": self.base_url,
            "shares_raw_farmer_data": self.shares_raw_farmer_data,
            "minimum_k_anonymity": self.minimum_k_anonymity,
        }


@dataclass
class PullResult:
    peer: str
    models: list[dict] = dc_field(default_factory=list)
    aggregates: dict | None = None
    signature_verified: bool = False
    warnings: list[str] = dc_field(default_factory=list)
    rejected: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "peer": self.peer,
            "models": self.models,
            "aggregates": self.aggregates,
            "signature_verified": self.signature_verified,
            "warnings": self.warnings,
            "rejected": self.rejected,
        }


async def discover(base_url: str, *, client: httpx.AsyncClient | None = None) -> PeerDescriptor:
    """Read a peer's descriptor. This is the only time a key is taken on trust."""
    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        url = base_url.rstrip("/") + "/federation/.well-known/agrin-node"
        response = await client.get(url, timeout=TIMEOUT)
        if response.status_code != 200:
            raise PeerError(f"{url} returned {response.status_code}")
        data = response.json()
    except httpx.HTTPError as exc:
        raise PeerError(f"could not reach {base_url}: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    policy = data.get("data_policy", {})
    missing = [f for f in ("node_id", "public_key", "spec_version") if not data.get(f)]
    if missing:
        raise PeerError(f"descriptor is missing required fields: {missing}")

    return PeerDescriptor(
        node_id=data["node_id"],
        country=data.get("country", "??"),
        public_key=data["public_key"],
        spec_version=data["spec_version"],
        capabilities=data.get("capabilities", []),
        shares_raw_farmer_data=bool(policy.get("shares_raw_farmer_data", True)),
        minimum_k_anonymity=int(policy.get("minimum_k_anonymity", 0)),
        base_url=base_url.rstrip("/"),
    )


def assess_policy(descriptor: PeerDescriptor, *, required_k: int = 5) -> list[str]:
    """Check a peer's stated policy before accepting anything from it.

    A node that admits to sharing raw farmer data is not one this network should
    be ingesting from, whatever its signatures say.
    """
    warnings: list[str] = []
    if descriptor.shares_raw_farmer_data:
        warnings.append(
            f"{descriptor.node_id} declares that it shares raw farmer data. "
            "Refusing to ingest from it."
        )
    if descriptor.minimum_k_anonymity < required_k:
        warnings.append(
            f"{descriptor.node_id} declares k={descriptor.minimum_k_anonymity}, "
            f"below the required {required_k}."
        )
    return warnings


async def pull(
    descriptor: PeerDescriptor,
    pinned_public_key: str,
    *,
    indicator: str = "ndvi_anomaly_mean",
    client: httpx.AsyncClient | None = None,
    required_k: int = 5,
) -> PullResult:
    """Fetch models and aggregates from a peer, verifying against the pinned key.

    `pinned_public_key` is supplied by the caller from local storage. It is
    deliberately a separate argument from `descriptor` so that using the
    descriptor's own key - which would defeat the purpose - has to be a
    conscious act rather than an accident.
    """
    result = PullResult(peer=descriptor.node_id)
    result.warnings.extend(assess_policy(descriptor, required_k=required_k))
    if result.warnings:
        result.rejected.append("policy check failed; nothing ingested")
        return result

    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        models = await client.get(
            f"{descriptor.base_url}/federation/models", timeout=TIMEOUT
        )
        if models.status_code == 200:
            for model in models.json().get("models", []):
                card = model.get("card") or {}
                # A model card without stated limitations is not usable by a peer.
                if not card.get("limitations"):
                    result.rejected.append(
                        f"model {model.get('model_id')} has no stated limitations"
                    )
                    continue
                result.models.append({
                    "model_id": model.get("model_id"),
                    "version": model.get("version"),
                    "reported_accuracy": (card.get("evaluation") or {}).get(
                        "reported_accuracy"
                    ),
                    "classes": len(card.get("classes") or []),
                    "weights_available": (model.get("artifact") or {}).get("available"),
                    "limitations": card.get("limitations", [])[:2],
                    "licence": card.get("license"),
                })

        aggregates = await client.get(
            f"{descriptor.base_url}/federation/aggregates",
            params={"indicator": indicator}, timeout=TIMEOUT,
        )
        if aggregates.status_code == 200:
            envelope = aggregates.json()
            if not open_envelope(envelope, pinned_public_key):
                raise SignatureRejected(
                    f"aggregate from {descriptor.node_id} failed verification "
                    "against the pinned key"
                )
            if envelope.get("node_id") != descriptor.node_id:
                raise SignatureRejected(
                    f"envelope claims node {envelope.get('node_id')} but was "
                    f"fetched from {descriptor.node_id}"
                )
            result.signature_verified = True
            result.aggregates = envelope.get("payload")
    except httpx.HTTPError as exc:
        raise PeerError(f"pull from {descriptor.node_id} failed: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    return result


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
