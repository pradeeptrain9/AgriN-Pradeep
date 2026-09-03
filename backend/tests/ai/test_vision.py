"""Vision fallback tests. No network: the client is stubbed."""

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.ai.disease import Prediction, gate
from app.ai.vision import _schema, identify_with_vision, prepare_image

DECISION = gate([Prediction("rice__blast", 0.40), Prediction("rice__normal", 0.38)],
                crop_code="rice")


GPS_IFD = 0x8825
MAKE_TAG = 0x010F


def make_image(size=(2000, 1500), with_exif=True) -> bytes:
    """A JPEG carrying the GPS EXIF a real phone camera embeds by default."""
    image = Image.new("RGB", size, (60, 120, 60))
    buffer = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[MAKE_TAG] = "TestPhone"
        gps = exif.get_ifd(GPS_IFD)
        gps.update({
            1: "N", 2: (30.0, 54.0, 0.0),   # 30 deg 54' N  -- Ludhiana
            3: "E", 4: (75.0, 51.0, 0.0),
        })
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


class StubResponse:
    def __init__(self, data, stop_reason="end_turn"):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.model = "claude-opus-5"


class StubClient:
    def __init__(self, response):
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return response

        self.beta = SimpleNamespace(messages=Messages())


class TestImagePreparation:
    def test_downscales_large_images(self):
        prepared, media_type = prepare_image(make_image((3000, 2000)))
        with Image.open(io.BytesIO(prepared)) as image:
            assert max(image.size) <= 1024
        assert media_type == "image/jpeg"

    def test_strips_exif_including_gps(self):
        """Phone photos carry GPS. A diagnosis must not ship the farmer's location."""
        raw = make_image(with_exif=True)
        with Image.open(io.BytesIO(raw)) as original:
            exif = original.getexif()
            assert dict(exif), "fixture carries no EXIF"
            assert dict(exif.get_ifd(GPS_IFD)), "fixture carries no GPS"

        prepared, _ = prepare_image(raw)
        with Image.open(io.BytesIO(prepared)) as cleaned:
            cleaned_exif = cleaned.getexif()
            assert not dict(cleaned_exif)
            assert not dict(cleaned_exif.get_ifd(GPS_IFD))

    def test_output_is_smaller_than_input(self):
        raw = make_image((3000, 2000))
        prepared, _ = prepare_image(raw)
        assert len(prepared) < len(raw)

    def test_converts_png_to_jpeg(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (500, 500), (10, 200, 10, 255)).save(buffer, format="PNG")
        prepared, media_type = prepare_image(buffer.getvalue())
        assert media_type == "image/jpeg"
        with Image.open(io.BytesIO(prepared)) as image:
            assert image.mode == "RGB"


class TestSchema:
    def test_enum_is_closed_to_the_crop_and_unknown(self):
        schema = _schema("rice")
        codes = schema["properties"]["disease_code"]["enum"]
        assert "unknown" in codes
        assert "rice__blast" in codes
        # Cannot answer with another crop's disease.
        assert "potato__late_blight" not in codes

    def test_schema_carries_no_range_keywords(self):
        # Structured outputs reject minimum/maximum on a number with a 400, and
        # the vision path swallows request errors as "could not be reached" --
        # so this shipped as a silently, totally broken cloud fallback until it
        # was run against the live API. The bound lives in code instead.
        confidence = _schema("rice")["properties"]["confidence"]
        assert "maximum" not in confidence
        assert "minimum" not in confidence
        assert confidence["type"] == "number"

    def test_confidence_is_bounded_in_code(self):
        from app.ai.vision import _clamped_confidence

        assert _clamped_confidence(1.4) == 1.0
        assert _clamped_confidence(-0.2) == 0.0
        assert _clamped_confidence(0.87) == 0.87

    def test_unusable_confidence_becomes_zero_not_a_crash(self):
        from app.ai.vision import _clamped_confidence

        assert _clamped_confidence(None) == 0.0
        assert _clamped_confidence("high") == 0.0
        assert _clamped_confidence(float("nan")) == 0.0


class TestIdentification:
    def test_identifies_a_disease(self):
        client = StubClient(StubResponse({
            "disease_code": "rice__blast",
            "confidence": 0.82,
            "visible_symptoms": "Diamond lesions with grey centres.",
            "image_quality_issue": None,
        }))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice (paddy)", client=client
        )
        assert result.identified
        assert result.disease_code == "rice__blast"
        assert result.confidence == pytest.approx(0.82)
        assert any("Diamond lesions" in n for n in result.notes)

    def test_on_device_guess_is_not_sent(self):
        """Sending the CNN's guess would anchor the model toward the answer we
        already decided we cannot trust."""
        client = StubClient(StubResponse({
            "disease_code": "rice__blast", "confidence": 0.8,
            "visible_symptoms": "x", "image_quality_issue": None,
        }))
        identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice (paddy)", client=client
        )
        sent = json.dumps(client.calls[0], default=str)
        assert "0.40" not in sent and "on-device" not in sent.lower()

    def test_unknown_is_not_turned_into_a_guess(self):
        client = StubClient(StubResponse({
            "disease_code": "unknown", "confidence": 0.2,
            "visible_symptoms": "Too blurred to tell.",
            "image_quality_issue": "blurred",
        }))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice (paddy)", client=client
        )
        assert not result.identified
        assert result.disease_code is None
        assert any("could not be matched" in n for n in result.notes)

    def test_image_quality_issue_is_surfaced(self):
        client = StubClient(StubResponse({
            "disease_code": "unknown", "confidence": 0.1,
            "visible_symptoms": "", "image_quality_issue": "too dark",
        }))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice", client=client
        )
        assert any("too dark" in n for n in result.notes)

    def test_out_of_taxonomy_code_is_rejected(self):
        client = StubClient(StubResponse({
            "disease_code": "rice__invented_disease", "confidence": 0.99,
            "visible_symptoms": "x", "image_quality_issue": None,
        }))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice", client=client
        )
        assert not result.identified

    def test_refusal_yields_no_identification(self):
        client = StubClient(StubResponse({"disease_code": "rice__blast",
                                          "confidence": 0.9, "visible_symptoms": "x",
                                          "image_quality_issue": None},
                                         stop_reason="refusal"))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice", client=client
        )
        assert not result.identified

    def test_unreadable_answer_yields_no_identification(self):
        client = StubClient(StubResponse("not json"))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice", client=client
        )
        assert not result.identified

    def test_no_api_key_says_so_plainly(self, monkeypatch):
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice"
        )
        get_settings.cache_clear()
        assert not result.identified
        assert any("extension officer" in n for n in result.notes)

    def test_identification_always_advises_confirmation(self):
        client = StubClient(StubResponse({
            "disease_code": "rice__blast", "confidence": 0.9,
            "visible_symptoms": "lesions", "image_quality_issue": None,
        }))
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice", client=client
        )
        assert any("Confirm it with your extension officer" in n for n in result.notes)


class TestCloudFallbackOperability:
    """The fallback runs on a shared node serving many farmers."""

    def test_vision_call_is_dispatched_off_the_event_loop(self):
        """A synchronous multi-second call inline in an async endpoint stalls
        every other request on the node."""
        import inspect

        from app.api import diagnoses

        source = inspect.getsource(diagnoses.create_diagnosis)
        assert "run_in_threadpool" in source
        assert "await run_in_threadpool" in source

    def test_image_sent_to_the_cloud_is_capped(self):
        """Image tokens scale with pixels, so an uncapped gallery pick would
        cost several times a camera capture for no benefit."""
        from app.ai.vision import MAX_EDGE_PX

        assert MAX_EDGE_PX <= 640

    def test_missing_key_is_a_clear_message_not_a_crash(self, monkeypatch):
        # Force the unconfigured case. Reading it from the environment made this
        # test pass only on a machine with no key in .env, and quietly stop
        # testing anything on one that has it.
        from app.config import get_settings

        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        get_settings.cache_clear()
        result = identify_with_vision(
            make_image(), crop_code="rice", crop_label="Rice (paddy)"
        )
        get_settings.cache_clear()
        assert not result.identified
        assert any("extension officer" in n for n in result.notes)


class TestImageFingerprint:
    """The diagnosis cache keys on a hash of the prepared bytes.

    If preparation were not byte-deterministic the hash would differ every time
    and the cache would never hit -- silently, with no failure to notice, just
    a bill that stays high.
    """

    def test_preparing_the_same_image_twice_gives_identical_bytes(self):
        import hashlib

        from app.ai.vision import prepare_image

        raw = make_image()
        first, _ = prepare_image(raw)
        second, _ = prepare_image(raw)
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    def test_different_images_do_not_collide(self):
        import hashlib

        from app.ai.vision import prepare_image

        a, _ = prepare_image(make_image(size=(2000, 1500)))
        b, _ = prepare_image(make_image(size=(1200, 1600)))
        assert hashlib.sha256(a).hexdigest() != hashlib.sha256(b).hexdigest()
