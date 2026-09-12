"""Naming a disease on a crop with no verified list.

The whole point of this path is that it names something and treats nothing. The
safety chain everywhere else is `disease_code -> ipm_actions -> pesticide
allowlist`; a provisional name deliberately keys into none of it. These tests
exist to make that structural, so a later change cannot quietly start attaching
advice to an unverified name.
"""

import json
from types import SimpleNamespace

import pytest

from app.ai.disease import Route, build_diagnosis, gate
from app.ai.open_ended import MAX_NAME_CHARS, identify_open_ended
from app.ai.vision import _open_ended_schema


def make_image() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 700), (40, 110, 45)).save(buffer, format="JPEG")
    return buffer.getvalue()


class StubResponse:
    """Shaped like what gemini.generate returns, which is all the code sees."""

    def __init__(self, data, stop_reason=None, model="gemini-2.5-pro"):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.model = model
        self.usage = SimpleNamespace(
            input_tokens=100, output_tokens=40,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


class StubClient:
    """Kept as a shim so each test still reads as "queue a response".

    Installing it patches gemini.generate, because the code now calls that
    module function directly instead of taking an injected SDK client.
    """

    _monkeypatch = None

    def __init__(self, response):
        self.calls = []
        if isinstance(response, list):
            queue = list(response)
            take = lambda: queue.pop(0)
        else:
            take = lambda: response

        def generate(**kwargs):
            self.calls.append(kwargs)
            return take()

        from app.ai import gemini
        from app.config import get_settings

        get_settings.cache_clear()
        StubClient._monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        StubClient._monkeypatch.setattr(gemini, "generate", generate)


class RaisingClient(StubClient):
    def __init__(self, exc):
        self.calls = []

        def generate(**kwargs):
            raise exc

        from app.ai import gemini
        from app.config import get_settings

        get_settings.cache_clear()
        StubClient._monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        StubClient._monkeypatch.setattr(gemini, "generate", generate)


@pytest.fixture(autouse=True)
def _wire_stub_clients(monkeypatch):
    StubClient._monkeypatch = monkeypatch
    yield
    StubClient._monkeypatch = None


GOOD = {
    "disease_name": "Cotton leaf curl virus",
    "confidence": 0.62,
    "visible_symptoms": "Upward curling of leaf margins with thickened veins.",
    "image_quality_issue": None,
}


class TestTheSchemaHasNoEnum:
    def test_disease_name_is_free_text(self):
        # The coded path constrains the answer to a list. This one cannot --
        # there is no list, which is the entire reason it exists.
        schema = _open_ended_schema()
        assert "enum" not in schema["properties"]["disease_name"]

    def test_there_is_no_disease_code_to_key_treatment_off(self):
        assert "disease_code" not in _open_ended_schema()["properties"]

    def test_confidence_carries_no_range_keywords(self):
        # Structured outputs reject minimum/maximum on a number with a 400, and
        # the vision path swallows request errors as "could not be reached" --
        # which is how the coded path shipped completely broken once.
        confidence = _open_ended_schema()["properties"]["confidence"]
        assert "minimum" not in confidence and "maximum" not in confidence


class TestIdentification:
    def test_returns_the_name_it_was_given(self):
        client = StubClient([StubResponse(GOOD)])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.provisional_name == "Cotton leaf curl virus"
        assert result.confidence == pytest.approx(0.62)

    def test_never_returns_a_disease_code(self):
        client = StubClient([StubResponse(GOOD)])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.disease_code is None
        assert not result.identified

    def test_says_plainly_that_the_name_is_unchecked(self):
        client = StubClient([StubResponse(GOOD)])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        joined = " ".join(result.notes).lower()
        assert "not been checked" in joined or "has not been checked" in joined
        assert "extension officer" in joined

    def test_says_no_treatment_is_given(self):
        client = StubClient([StubResponse(GOOD)])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert any("no treatment is given" in n.lower() for n in result.notes)

    @pytest.mark.parametrize("name", ["unknown", "Unknown", "none", "", None])
    def test_a_non_answer_is_not_dressed_up_as_a_name(self, name):
        client = StubClient([StubResponse({**GOOD, "disease_name": name})])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.provisional_name is None
        assert result.confidence == 0.0

    def test_a_rambling_answer_is_truncated_to_a_name(self):
        # It is rendered as a title and repeated aloud to an officer. A
        # paragraph is not a name.
        client = StubClient([StubResponse({**GOOD, "disease_name": "x" * 400})])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert len(result.provisional_name) == MAX_NAME_CHARS

    def test_a_refusal_is_not_an_identification(self):
        client = StubClient([StubResponse(GOOD, stop_reason="refusal")])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.provisional_name is None

    def test_unreadable_output_is_not_an_identification(self):
        client = StubClient([StubResponse("not json at all")])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.provisional_name is None

    def test_usage_is_returned_so_the_call_can_be_billed(self):
        # A call that is not recorded is spend the cap cannot see.
        client = StubClient([StubResponse(GOOD)])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.usage is not None
        assert result.model == "gemini-2.5-pro"

    def test_a_refused_call_still_reports_its_usage(self):
        client = StubClient([StubResponse(GOOD, stop_reason="refusal")])
        result = identify_open_ended(
            make_image(), crop_label="Cotton"
        )
        assert result.usage is not None


class TestThePromptForbidsChemicals:
    def test_system_prompt_bans_naming_a_product(self):
        from app.ai.open_ended import OPEN_ENDED_SYSTEM_PROMPT

        lowered = OPEN_ENDED_SYSTEM_PROMPT.lower()
        assert "never name a pesticide" in lowered
        assert "never suggest a treatment" in lowered

    def test_the_model_is_told_there_is_no_list(self):
        from app.ai.open_ended import OPEN_ENDED_USER_PROMPT

        assert "no verified disease list" in OPEN_ENDED_USER_PROMPT.lower()


class TestTheDiagnosisCarriesNoAdvice:
    """The load-bearing property: a name, and nothing that could be acted on."""

    def _provisional(self):
        decision = gate([], crop_code="cotton")
        return build_diagnosis(
            disease=None,
            crop_code="cotton",
            confidence=0.62,
            resolved_by=Route.PROVISIONAL.value,
            decision=decision,
            extra_notes=["unchecked name note"],
            provisional_name="Cotton leaf curl virus",
        )

    def test_the_name_survives(self):
        assert self._provisional().provisional_name == "Cotton leaf curl virus"

    def test_no_chemical_is_ever_attached(self):
        assert self._provisional().chemical_options == []

    def test_no_ipm_action_is_attached(self):
        # IPM text comes off a DiseaseClass. There is no DiseaseClass here, so
        # inventing actions would mean inventing agronomy.
        assert self._provisional().ipm_actions == []

    def test_no_disease_code_is_invented(self):
        diagnosis = self._provisional()
        assert diagnosis.disease_code is None
        assert diagnosis.label is None

    def test_it_always_asks_for_a_human(self):
        assert self._provisional().needs_expert_review is True

    def test_it_is_never_marked_urgent(self):
        # Urgency drives "act today" language. An unverified name should not.
        assert self._provisional().urgent is False

    def test_it_is_not_reported_as_healthy(self):
        assert self._provisional().is_healthy is False

    def test_the_route_is_distinct_from_inconclusive_and_from_verified(self):
        # Collapsing it into inconclusive would make the audit log read as
        # though nothing happened, on a call that cost money and named a
        # disease. Collapsing it into cloud_vision would imply the answer was
        # checked against a list.
        diagnosis = self._provisional()
        assert diagnosis.resolved_by == "provisional"
        assert diagnosis.resolved_by != Route.INCONCLUSIVE.value
        assert diagnosis.resolved_by != Route.CLOUD_VISION.value

    def test_the_name_reaches_the_client(self):
        assert self._provisional().to_dict()["provisional_name"] == (
            "Cotton leaf curl virus"
        )
