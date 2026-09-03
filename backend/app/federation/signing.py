"""Ed25519 signing for anything one node sends another.

A federated network without signatures is a network where any host that can
reach your endpoint can claim to be a peer. Every payload a node publishes --
model cards, aggregate statistics -- is signed, and every payload a node ingests
is verified against the sending node's registered public key before it is used.

Canonicalisation is the part that quietly breaks: two JSON encoders will produce
different bytes for the same object, and the signature then fails for no visible
reason. Payloads are serialised with sorted keys, no whitespace, and UTF-8, and
that exact form is what gets signed and verified.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_bytes(payload: Any) -> bytes:
    """The exact byte form that gets signed. Both sides must agree on this."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) for a new node identity."""
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return (
        base64.b64encode(private_raw).decode(),
        base64.b64encode(public_raw).decode(),
    )


def public_key_from_b64(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(value))


def private_key_from_b64(value: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(value))


def sign(payload: Any, private_key_b64: str) -> str:
    signature = private_key_from_b64(private_key_b64).sign(canonical_bytes(payload))
    return base64.b64encode(signature).decode()


def verify(payload: Any, signature_b64: str, public_key_b64: str) -> bool:
    try:
        public_key_from_b64(public_key_b64).verify(
            base64.b64decode(signature_b64), canonical_bytes(payload)
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class SignedEnvelope:
    """What actually crosses the wire between nodes."""

    node_id: str
    issued_at: str
    payload: Any
    signature: str
    algorithm: str = "ed25519"
    licence: str = "CC-BY-4.0"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "issued_at": self.issued_at,
            "algorithm": self.algorithm,
            "licence": self.licence,
            "payload": self.payload,
            "signature": self.signature,
        }


def seal(node_id: str, payload: Any, private_key_b64: str, issued_at: str,
         licence: str = "CC-BY-4.0") -> SignedEnvelope:
    """Sign the whole envelope body, not just the payload.

    Signing the payload alone would let anyone replay it under a different
    node_id or timestamp, so those fields are inside the signed bytes.
    """
    body = {
        "node_id": node_id,
        "issued_at": issued_at,
        "algorithm": "ed25519",
        "licence": licence,
        "payload": payload,
    }
    return SignedEnvelope(
        node_id=node_id,
        issued_at=issued_at,
        payload=payload,
        signature=sign(body, private_key_b64),
        licence=licence,
    )


def open_envelope(envelope: dict, public_key_b64: str) -> bool:
    """Verify an envelope received from a peer."""
    body = {
        "node_id": envelope.get("node_id"),
        "issued_at": envelope.get("issued_at"),
        "algorithm": envelope.get("algorithm", "ed25519"),
        "licence": envelope.get("licence", "CC-BY-4.0"),
        "payload": envelope.get("payload"),
    }
    signature = envelope.get("signature")
    if not signature:
        return False
    return verify(body, signature, public_key_b64)
