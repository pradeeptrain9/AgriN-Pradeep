"""Narration tests.

No network. The model path is exercised with a stubbed gemini.generate so the guard,
the corrective retry, and every fallback route are covered deterministically.
"""

import json
from types import SimpleNamespace

import pytest

from app.ai.narrate import build_template_narration, narrate

ADVISORY = {
    "status": "ok",
    "crop": {
        "label": "Rice (paddy)",
        "stage": "mid",
        "days_after_sowing": 74,
        "sowing_date": "2026-06-20",
    },
    "soil": {"source": "soil_health_card", "confidence": "high", "texture": "sandy loam"},
    "health": {"severity": "alert", "notes": ["Last cloud-free image is 17 days old."]},
    "irrigation": {
        "model": "paddy",
        "irrigate_now": True,
        "recommended_depth_mm": 43.1,
        "gross_depth_mm": 66.3,
        "notes": ["Keep the field ponded through flowering."],
    },
    "nutrients": {
        "n": {"low_kg_ha": 71.8, "high_kg_ha": 111.8},
        "p2o5": {"low_kg_ha": 28.0, "high_kg_ha": 44.0},
        "k2o": {"low_kg_ha": 80.0, "high_kg_ha": 128.0},
        "products_kg_ha": {"urea": 168.9, "dap": 78.3, "mop": 173.3},
        "splits": [
            {"when": "basal (at sowing)", "days_after_sowing": 0, "n_kg_ha": 36.6}
        ],
        "regenerative_actions": ["Retain crop residue rather than burning it."],
    },
    "rotation": [{"label": "Chickpea", "reasons": ["Fixes about 70 kg N/ha."]}],
    "gaps": [],
}


class StubResponse:
    """Shaped like what gemini.generate returns, which is all narrate() sees."""

    def __init__(self, data, stop_reason=None, model="gemini-2.5-flash"):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.model = model
        self.usage = SimpleNamespace(
            input_tokens=10, output_tokens=20,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


class FakeGemini:
    """Records each request and returns queued responses, or raises."""

    def __init__(self, responses=(), raises=None):
        self._responses = list(responses)
        self.raises = raises
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self._responses.pop(0)


def patch_gemini(monkeypatch, fake):
    """Give the node a key and make gemini.generate return what we queued."""
    from app.ai import gemini
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini, "generate", fake)
    return fake


GOOD = {
    "summary": "Your rice is at the mid stage, 74 days after sowing. It needs water today.",
    "actions": [
        {
            "title": "Irrigate today",
            "detail": "Apply about 66.3 mm of water.",
            "urgency": "now",
        }
    ],
    "explanation": "Nitrogen should be 71.8 to 111.8 kg per hectare.",
}

INVENTED = {
    "summary": "Your rice needs 250 mm of water immediately.",
    "actions": [
        {"title": "Irrigate", "detail": "Apply 250 mm now.", "urgency": "now"}
    ],
    "explanation": "Use 400 kg of urea.",
}


class TestTemplate:
    def test_works_with_no_client_and_no_key(self):
        result = build_template_narration(ADVISORY)
        assert result.source == "template"
        assert result.summary
        assert result.actions

    def test_template_only_uses_engine_figures(self):
        """The template must satisfy the same guard the model does."""
        from app.ai.guard import check_narration

        result = build_template_narration(ADVISORY)
        violations = check_narration(result.to_dict(), ADVISORY)
        assert violations == [], violations

    def test_irrigate_now_leads_with_an_urgent_action(self):
        result = build_template_narration(ADVISORY)
        assert result.actions[0]["urgency"] == "now"
        assert "Irrigate" in result.actions[0]["title"]

    def test_alert_health_asks_the_farmer_to_walk_the_field(self):
        result = build_template_narration(ADVISORY)
        assert any("on foot" in a["title"] for a in result.actions)

    def test_unknown_health_is_stated_not_guessed(self):
        payload = {**ADVISORY, "health": {"severity": "unknown", "notes": []}}
        result = build_template_narration(payload)
        titles = [a["title"] for a in result.actions]
        assert "Crop health not available" in titles

    def test_no_crop_gives_a_single_clear_instruction(self):
        result = build_template_narration({"status": "no_crop"})
        assert result.actions[0]["title"] == "Add your crop"

    def test_fertiliser_products_are_included(self):
        result = build_template_narration(ADVISORY)
        detail = " ".join(a["detail"] for a in result.actions)
        assert "urea" in detail and "DAP" in detail

    def test_regenerative_actions_carried_through(self):
        result = build_template_narration(ADVISORY)
        assert any("residue" in a["detail"] for a in result.actions)

    def test_gaps_become_notes(self):
        payload = {**ADVISORY, "gaps": ["No satellite image yet."]}
        result = build_template_narration(payload)
        assert "No satellite image yet." in result.notes


class TestNoApiKey:
    def test_falls_back_to_template_and_says_so(self, monkeypatch):
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("GEMINI_API_KEY", "")
        result = narrate(ADVISORY, lang="en")
        get_settings.cache_clear()
        assert result.source == "template"
        assert any("no language model key" in n for n in result.notes)

    def test_non_english_request_is_marked_untranslated(self, monkeypatch):
        # The template is English. Claiming otherwise would have the app render
        # English text under a Hindi heading with no indication anything failed.
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("GEMINI_API_KEY", "")
        result = narrate(ADVISORY, lang="hi")
        get_settings.cache_clear()
        assert result.source == "template"
        assert result.translated is False
        assert result.lang == "en"


class TestGeminiPath:
    def test_clean_narration_is_returned(self, monkeypatch):
        patch_gemini(monkeypatch, FakeGemini([StubResponse(GOOD)]))
        result = narrate(ADVISORY, lang="en")
        assert result.source == "gemini"
        assert result.guard_violations == []
        assert result.retried is False
        assert "74 days" in result.summary

    def test_request_uses_the_configured_model_and_schema(self, monkeypatch):
        fake = patch_gemini(monkeypatch, FakeGemini([StubResponse(GOOD)]))
        narrate(ADVISORY, lang="en")
        from app.config import get_settings

        call = fake.calls[0]
        assert call["model"] == get_settings().gemini_narrate_model
        assert call["schema"]["properties"]["summary"]["type"] == "string"

    def test_language_is_passed_into_the_system_prompt(self, monkeypatch):
        fake = patch_gemini(monkeypatch, FakeGemini([StubResponse(GOOD)]))
        narrate(ADVISORY, lang="pa")
        assert "Punjabi" in fake.calls[0]["system"]

    def test_invented_numbers_trigger_a_corrective_retry(self, monkeypatch):
        fake = patch_gemini(
            monkeypatch, FakeGemini([StubResponse(INVENTED), StubResponse(GOOD)])
        )
        result = narrate(ADVISORY, lang="en")
        assert len(fake.calls) == 2
        assert result.source == "gemini"
        assert result.retried is True
        # The retry prompt must name the offending figures.
        correction = fake.calls[1]["messages"][-1]["content"]
        assert "250" in correction

    def test_the_corrective_turn_uses_the_assistant_role(self, monkeypatch):
        # gemini.py maps assistant -> model. If the rejected draft were replayed
        # under the wrong role the model would be told it wrote nothing, and the
        # correction would argue with itself.
        fake = patch_gemini(
            monkeypatch, FakeGemini([StubResponse(INVENTED), StubResponse(GOOD)])
        )
        narrate(ADVISORY, lang="en")
        roles = [m["role"] for m in fake.calls[1]["messages"]]
        assert roles == ["user", "assistant", "user"]

    def test_persistent_invention_falls_back_to_template(self, monkeypatch):
        patch_gemini(
            monkeypatch, FakeGemini([StubResponse(INVENTED), StubResponse(INVENTED)])
        )
        result = narrate(ADVISORY, lang="en")
        assert result.source == "template"
        assert result.guard_violations
        assert any("not in the calculated advice" in n for n in result.notes)

    def test_refusal_falls_back_to_template(self, monkeypatch):
        patch_gemini(
            monkeypatch, FakeGemini([StubResponse(GOOD, stop_reason="refusal")])
        )
        assert narrate(ADVISORY, lang="en").source == "template"

    def test_malformed_json_falls_back_to_template(self, monkeypatch):
        patch_gemini(monkeypatch, FakeGemini([StubResponse("not json at all")]))
        assert narrate(ADVISORY, lang="en").source == "template"

    @pytest.mark.parametrize("exc", [
        "GeminiUnavailable",   # transport, auth, quota
        "GeminiRejected",      # blocked input, empty completion
    ])
    def test_api_errors_fall_back_to_template(self, monkeypatch, exc):
        from app.ai import gemini

        patch_gemini(monkeypatch, FakeGemini(raises=getattr(gemini, exc)("boom")))
        result = narrate(ADVISORY, lang="en")
        assert result.source == "template"
        assert any("built-in template" in n for n in result.notes)

    def test_every_billed_call_is_reported(self, monkeypatch):
        # Including the corrective round. A retry that is not recorded is spend
        # that does not exist as far as the monthly cap is concerned.
        patch_gemini(
            monkeypatch, FakeGemini([StubResponse(INVENTED), StubResponse(GOOD)])
        )
        result = narrate(ADVISORY, lang="en")
        assert len(result.usages) == 2


def test_result_serialises():
    payload = build_template_narration(ADVISORY).to_dict()
    assert payload["source"] == "template"
    assert isinstance(payload["actions"], list)
