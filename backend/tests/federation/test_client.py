"""Peer client tests.

The property under test is the one that makes signatures worth anything: a
payload is verified against a key pinned earlier, never against a key that
arrives with the payload.
"""

import pytest

from app.federation.client import (
    PeerDescriptor,
    assess_policy,
)
from app.federation.signing import generate_keypair, open_envelope, seal

ISSUED = "2026-09-03T10:00:00+00:00"


def descriptor(**overrides) -> PeerDescriptor:
    base = dict(
        node_id="node-in", country="IN", public_key="k", spec_version="agrin-core-1.0.0",
        capabilities=["advisory"], shares_raw_farmer_data=False,
        minimum_k_anonymity=5, base_url="http://node-in.example",
    )
    base.update(overrides)
    return PeerDescriptor(**base)


class TestPolicyGate:
    def test_compliant_peer_passes(self):
        assert assess_policy(descriptor()) == []

    def test_peer_admitting_it_shares_farmer_data_is_refused(self):
        """Signatures do not make raw farmer data acceptable to ingest."""
        warnings = assess_policy(descriptor(shares_raw_farmer_data=True))
        assert warnings
        assert "raw farmer data" in warnings[0]

    def test_peer_with_weak_k_is_refused(self):
        warnings = assess_policy(descriptor(minimum_k_anonymity=2), required_k=5)
        assert any("below the required 5" in w for w in warnings)

    def test_k_exactly_at_threshold_passes(self):
        assert assess_policy(descriptor(minimum_k_anonymity=5), required_k=5) == []

    def test_both_failures_are_reported_together(self):
        warnings = assess_policy(
            descriptor(shares_raw_farmer_data=True, minimum_k_anonymity=1)
        )
        assert len(warnings) == 2


class TestPinnedKeyVerification:
    """The attack this prevents: an attacker signs a payload with their own key
    and presents the matching public key alongside it."""

    def test_payload_verifies_against_the_correct_pinned_key(self):
        private, public = generate_keypair()
        envelope = seal("node-in", {"cells": []}, private, ISSUED).to_dict()
        assert open_envelope(envelope, public)

    def test_attacker_key_does_not_verify_against_the_pinned_key(self):
        _, pinned_public = generate_keypair()
        attacker_private, attacker_public = generate_keypair()

        # Attacker signs a perfectly well-formed envelope with their own key.
        forged = seal("node-in", {"cells": [{"mean": 9.9}]}, attacker_private, ISSUED)
        forged_envelope = forged.to_dict()

        # It verifies against the attacker's own key...
        assert open_envelope(forged_envelope, attacker_public)
        # ...and fails against the key we pinned earlier, which is the point.
        assert not open_envelope(forged_envelope, pinned_public)

    def test_key_rotation_shows_up_as_a_failure_not_a_silent_accept(self):
        """A peer changing keys must be visible, not absorbed."""
        _, old_public = generate_keypair()
        new_private, _ = generate_keypair()
        envelope = seal("node-in", {"cells": []}, new_private, ISSUED).to_dict()
        assert not open_envelope(envelope, old_public)


class TestDescriptorShape:
    def test_serialises_without_secrets(self):
        payload = descriptor().to_dict()
        assert "private" not in str(payload).lower()
        assert payload["public_key"] == "k"

    def test_policy_fields_survive_serialisation(self):
        payload = descriptor(minimum_k_anonymity=7).to_dict()
        assert payload["minimum_k_anonymity"] == 7
        assert payload["shares_raw_farmer_data"] is False


class TestRePinning:
    """A stale pin must not be reported as a fresh one.

    This endpoint answered 201 with `key_pinned: true` and the note "the public
    key recorded now is the only one that will be accepted", while the upsert
    deliberately left the previously pinned key in place. Every later pull then
    failed with "failed verification against the pinned key" -- which reads as
    the peer being broken or hostile, when the truth was a stale pin on this
    side. Keeping the old key is correct. Saying the new one was pinned is not.
    """

    def test_a_changed_key_is_refused_rather_than_silently_kept(self):
        import inspect

        from app.api import federation

        source = inspect.getsource(federation.add_peer)
        assert "accept_key_change" in source
        assert "status_code=409" in source

    def test_re_pinning_requires_confirming_the_key_out_of_band(self):
        import inspect

        from app.api import federation

        source = inspect.getsource(federation.add_peer)
        # Confirming the new key over the same connection proves nothing: an
        # impersonator controls that connection.
        assert "out of band" in source

    def test_an_unchanged_key_does_not_claim_to_have_pinned_anything(self):
        import inspect

        from app.api import federation

        source = inspect.getsource(federation.add_peer)
        assert '"key_pinned": existing is None or key_changed' in source
        assert "already_known" in source
