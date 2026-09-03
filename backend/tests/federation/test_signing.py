"""Signing tests. Without these, any host that can reach a node can claim to be
a peer, and a payload can be replayed under another node's name."""

import pytest

from app.federation.signing import (
    canonical_bytes,
    generate_keypair,
    open_envelope,
    seal,
    sign,
    verify,
)

ISSUED = "2026-09-02T12:00:00+00:00"


@pytest.fixture
def keys():
    return generate_keypair()


class TestCanonicalisation:
    def test_key_order_does_not_change_the_bytes(self):
        """Two encoders must agree, or signatures fail for no visible reason."""
        assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})

    def test_whitespace_does_not_change_the_bytes(self):
        assert canonical_bytes({"a": [1, 2]}) == b'{"a":[1,2]}'

    def test_different_values_differ(self):
        assert canonical_bytes({"a": 1}) != canonical_bytes({"a": 2})

    def test_unicode_is_preserved_not_escaped(self):
        assert "गेहूँ" in canonical_bytes({"crop": "गेहूँ"}).decode("utf-8")


class TestSignAndVerify:
    def test_roundtrip(self, keys):
        private, public = keys
        payload = {"indicator": "ndvi_anomaly_mean", "cells": []}
        assert verify(payload, sign(payload, private), public)

    def test_tampered_payload_fails(self, keys):
        private, public = keys
        payload = {"value": 10}
        signature = sign(payload, private)
        assert not verify({"value": 11}, signature, public)

    def test_wrong_key_fails(self, keys):
        private, _ = keys
        _, other_public = generate_keypair()
        payload = {"a": 1}
        assert not verify(payload, sign(payload, private), other_public)

    def test_garbage_signature_is_rejected_not_raised(self, keys):
        _, public = keys
        assert not verify({"a": 1}, "not-base64-!!", public)

    def test_empty_signature_rejected(self, keys):
        _, public = keys
        assert not verify({"a": 1}, "", public)


class TestEnvelope:
    def test_sealed_envelope_verifies(self, keys):
        private, public = keys
        envelope = seal("node-in", {"cells": []}, private, ISSUED)
        assert open_envelope(envelope.to_dict(), public)

    def test_node_id_is_inside_the_signature(self, keys):
        """Otherwise a payload could be replayed under another node's name."""
        private, public = keys
        envelope = seal("node-in", {"cells": []}, private, ISSUED).to_dict()
        envelope["node_id"] = "node-br"
        assert not open_envelope(envelope, public)

    def test_timestamp_is_inside_the_signature(self, keys):
        private, public = keys
        envelope = seal("node-in", {"cells": []}, private, ISSUED).to_dict()
        envelope["issued_at"] = "2030-01-01T00:00:00+00:00"
        assert not open_envelope(envelope, public)

    def test_licence_is_inside_the_signature(self, keys):
        private, public = keys
        envelope = seal("node-in", {"cells": []}, private, ISSUED).to_dict()
        envelope["licence"] = "proprietary"
        assert not open_envelope(envelope, public)

    def test_payload_tampering_is_caught(self, keys):
        private, public = keys
        envelope = seal("node-in", {"cells": [{"mean": 0.1}]}, private, ISSUED).to_dict()
        envelope["payload"]["cells"][0]["mean"] = 0.9
        assert not open_envelope(envelope, public)

    def test_missing_signature_rejected(self, keys):
        _, public = keys
        assert not open_envelope({"node_id": "x", "payload": {}}, public)

    def test_envelope_never_contains_the_private_key(self, keys):
        private, _ = keys
        envelope = seal("node-in", {"a": 1}, private, ISSUED).to_dict()
        assert private not in str(envelope)
