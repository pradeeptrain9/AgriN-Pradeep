"""The numeric guard is the enforcement mechanism for the product's core rule:
a language model may rephrase engine output, never produce a quantity.
"""

import pytest

from app.ai.guard import (
    check_narration,
    collect_allowed_numbers,
    is_supported,
    numbers_in_text,
    unsupported_numbers,
)

PAYLOAD = {
    "irrigation": {
        "recommended_depth_mm": 45.4,
        "gross_depth_mm": 69.8,
        "irrigate_now": True,
        "notes": ["Apply water within 2 days."],
    },
    "nutrients": {"n": {"low_kg_ha": 71.8, "high_kg_ha": 111.8}},
    "crop": {"label": "Rice (paddy)", "sowing_date": "2026-06-20"},
    "gaps": ["Manure at 5 t/ha would raise organic carbon."],
}


class TestExtraction:
    def test_finds_integers_and_decimals(self):
        assert numbers_in_text("apply 45 mm now, or 69.8 mm gross") == [45.0, 69.8]

    def test_handles_thousands_separators(self):
        assert numbers_in_text("1,250 litres") == [1250.0]

    def test_ignores_text_without_numbers(self):
        assert numbers_in_text("irrigate the field today") == []

    def test_handles_empty(self):
        assert numbers_in_text("") == []


class TestAllowedCollection:
    def test_collects_numeric_leaves(self):
        allowed = collect_allowed_numbers(PAYLOAD)
        assert 45.4 in allowed
        assert 111.8 in allowed

    def test_collects_numbers_embedded_in_strings(self):
        """Engine notes legitimately carry figures, e.g. '5 t/ha of manure'."""
        allowed = collect_allowed_numbers(PAYLOAD)
        assert 5.0 in allowed
        assert 2.0 in allowed

    def test_collects_date_components(self):
        allowed = collect_allowed_numbers(PAYLOAD)
        assert 2026.0 in allowed

    def test_admits_rounded_forms(self):
        allowed = collect_allowed_numbers(PAYLOAD)
        assert 45.0 in allowed  # rounded form of 45.4

    def test_booleans_are_not_quantities(self):
        allowed = collect_allowed_numbers({"irrigate_now": True, "done": False})
        assert 1.0 not in allowed
        assert 0.0 not in allowed


class TestSupport:
    def test_exact_match_supported(self):
        assert is_supported(45.4, {45.4})

    def test_tolerance_alone_does_not_admit_a_rounded_value(self):
        """0.4 away from 45.4 exceeds the 0.227 relative tolerance."""
        assert not is_supported(45.0, {45.4})

    def test_rounded_form_is_admitted_by_the_allowed_set(self):
        """collect_allowed_numbers adds rounded variants, so "45 mm" passes."""
        assert is_supported(45.0, collect_allowed_numbers(PAYLOAD))

    def test_large_numbers_use_relative_tolerance(self):
        assert is_supported(1000.0, {1002.0})

    def test_unrelated_number_not_supported(self):
        assert not is_supported(120.0, {45.4, 69.8})


class TestTheCoreRule:
    def test_narration_using_only_engine_figures_passes(self):
        narration = {
            "summary": "Irrigate today.",
            "actions": [
                {
                    "title": "Irrigate",
                    "detail": "Apply about 69.8 mm of water.",
                    "urgency": "now",
                }
            ],
            "explanation": "Nitrogen should be 71.8 to 111.8 kg per hectare.",
        }
        assert check_narration(narration, PAYLOAD) == []

    def test_invented_irrigation_depth_is_caught(self):
        """The exact failure this guard exists to prevent."""
        narration = {
            "summary": "Irrigate today.",
            "actions": [
                {"title": "Irrigate", "detail": "Apply 120 mm of water.", "urgency": "now"}
            ],
            "explanation": "",
        }
        violations = check_narration(narration, PAYLOAD)
        assert violations
        assert "120" in violations[0]

    def test_invented_nitrogen_dose_is_caught(self):
        narration = {
            "summary": "",
            "actions": [],
            "explanation": "Apply 250 kg of urea per hectare.",
        }
        violations = check_narration(narration, PAYLOAD)
        assert violations
        assert "250" in violations[0]

    def test_violation_names_the_location(self):
        narration = {
            "summary": "Yield will rise 30 percent.",
            "actions": [],
            "explanation": "",
        }
        violations = check_narration(narration, PAYLOAD)
        assert violations[0].startswith("summary")

    def test_multiple_violations_all_reported(self):
        narration = {
            "summary": "Use 900 kg.",
            "actions": [
                {"title": "Do", "detail": "Wait 77 days.", "urgency": "now"}
            ],
            "explanation": "Expect 88 quintals.",
        }
        assert len(check_narration(narration, PAYLOAD)) == 3

    def test_qualitative_text_is_always_allowed(self):
        narration = {
            "summary": "Your crop needs water soon.",
            "actions": [
                {
                    "title": "Irrigate",
                    "detail": "Water the field as soon as you can.",
                    "urgency": "now",
                }
            ],
            "explanation": "The soil has dried out since the last rain.",
        }
        assert check_narration(narration, PAYLOAD) == []

    def test_copied_dates_are_allowed(self):
        narration = {
            "summary": "Sown on 2026-06-20.",
            "actions": [],
            "explanation": "",
        }
        assert check_narration(narration, PAYLOAD) == []


def test_unsupported_numbers_helper():
    allowed = collect_allowed_numbers(PAYLOAD)
    assert unsupported_numbers("apply 69.8 mm", allowed) == []
    assert unsupported_numbers("apply 333 mm", allowed) == [333.0]
