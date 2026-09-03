"""Reusing a narration until the advice changes.

The narration is a rephrasing of the payload and introduces no figure of its
own (enforced by ai/guard.py), so identical advice means identical sentences.
The advisory screen reloads on every focus; without this the same paragraph is
bought several times a day per field.

What matters is the fingerprint: too loose and a farmer is shown stale advice,
too tight and the cache never hits and costs a write for nothing.
"""

from app.api.advisory import _payload_hash

BASE = {
    "version": "advisory-1.0.0",
    "field_id": "3ef3681f-be7b-482a-815e-36000a95054c",
    "field_name": "Feedback test plot",
    "generated_at": "2026-09-03T09:38:05.440425Z",
    "crop": {"code": "rice", "days_after_sowing": 55},
    "health": {"severity": "alert", "latest_ndvi": 0.2033},
    "irrigation": {"irrigate_now": False, "depth_mm": None},
}


def test_the_same_advice_hashes_the_same():
    assert _payload_hash(BASE) == _payload_hash(dict(BASE))


def test_generated_at_does_not_change_the_hash():
    # It moves on every single request. Hashing it would make every lookup a
    # miss, turning the cache into a pure write cost.
    moved = {**BASE, "generated_at": "2026-09-03T11:02:17.000000Z"}
    assert _payload_hash(moved) == _payload_hash(BASE)


def test_field_identity_does_not_change_the_hash():
    # Cache lookups are already scoped by field_id, and neither the id nor the
    # farmer's name for the field affects a single word of the narration.
    renamed = {**BASE, "field_name": "North plot", "field_id": "other-uuid"}
    assert _payload_hash(renamed) == _payload_hash(BASE)


def test_key_order_does_not_change_the_hash():
    reordered = dict(reversed(list(BASE.items())))
    assert _payload_hash(reordered) == _payload_hash(BASE)


def test_changed_health_changes_the_hash():
    worse = {**BASE, "health": {"severity": "alert", "latest_ndvi": 0.1500}}
    assert _payload_hash(worse) != _payload_hash(BASE)


def test_changed_severity_changes_the_hash():
    better = {**BASE, "health": {"severity": "ok", "latest_ndvi": 0.2033}}
    assert _payload_hash(better) != _payload_hash(BASE)


def test_starting_to_irrigate_changes_the_hash():
    # The single most consequential flip in the whole payload. If this did not
    # bust the cache a farmer would be told not to irrigate while the engine
    # says otherwise.
    now = {**BASE, "irrigation": {"irrigate_now": True, "depth_mm": 42.0}}
    assert _payload_hash(now) != _payload_hash(BASE)


def test_a_day_passing_changes_the_hash():
    # days_after_sowing advances daily and every recommendation is a function
    # of it, so the narration must be regenerated.
    tomorrow = {**BASE, "crop": {"code": "rice", "days_after_sowing": 56}}
    assert _payload_hash(tomorrow) != _payload_hash(BASE)


def test_a_new_note_changes_the_hash():
    extra = {**BASE, "notes": ["Last cloud-free image is 24 days old."]}
    assert _payload_hash(extra) != _payload_hash(BASE)


def test_hash_is_stable_across_processes():
    # sha256 of a sorted, separator-fixed dump -- not Python's salted hash(),
    # which differs between runs and would make the cache miss after every
    # restart.
    assert _payload_hash(BASE) == _payload_hash(BASE)
    assert len(_payload_hash(BASE)) == 64


def test_non_serialisable_values_do_not_raise():
    from datetime import date

    assert _payload_hash({**BASE, "sowing_date": date(2026, 7, 10)})
