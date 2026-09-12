"""The Google providers, tested where they can actually be wrong.

None of these call a live service. What is pinned is the logic that decides what
a farmer sees when a service answers oddly, answers partially, or does not answer
at all -- which is where every one of these can do harm, and the only part that
does not need a credential to exercise.
"""

import pytest

from app.providers import basemap, bigquery, earth_engine, speech, translate, vertex


class TestBasemapNeverLeavesTheAppWithoutAMap:
    @pytest.mark.asyncio
    async def test_no_key_means_openstreetmap(self):
        # The keyless path has to keep working or docs/DPG.md indicator 4 --
        # platform independence -- becomes a false claim.
        result = await basemap.fetch("")
        assert result.provider == "openstreetmap"
        assert result.satellite is False

    @pytest.mark.asyncio
    async def test_a_refused_session_falls_back_rather_than_raising(self, monkeypatch):
        class Refusing:
            status_code = 403
            text = "Map Tiles API has not been used in project"

            async def aclose(self): ...
            async def post(self, *a, **k): return self

        result = await basemap.fetch("key", client=Refusing())
        assert result.provider == "openstreetmap"

    @pytest.mark.asyncio
    async def test_a_session_with_no_token_is_not_trusted(self):
        class Empty:
            status_code = 200

            async def aclose(self): ...
            async def post(self, *a, **k): return self
            def json(self): return {}

        # A 200 with no session would otherwise build a tile URL that 404s on
        # every tile, which looks like a broken map rather than a missing key.
        assert (await basemap.fetch("key", client=Empty())).provider == "openstreetmap"

    def test_the_street_map_is_marked_as_not_satellite(self):
        # The draw screen tells a farmer whether there is imagery to trace
        # against. Getting this flag wrong means telling them to align to a
        # field edge that is not drawn.
        assert basemap.OSM.satellite is False


class TestTranslationRefusesToMoveANumber:
    @pytest.mark.parametrize("source,translated", [
        ("Apply 40 kg per hectare", "40 किलोग्राम प्रति हेक्टेयर डालें"),
        ("Irrigate 25 mm", "25 मिमी सिंचाई करें"),
        ("1,250 litres", "1250 लीटर"),          # separators normalised
    ])
    def test_a_faithful_translation_is_accepted(self, source, translated):
        assert translate.numbers_survived(source, translated) is True

    @pytest.mark.parametrize("source,translated", [
        ("Apply 40 kg per hectare", "४० किलोग्राम प्रति हेक्टेयर डालें"),  # Devanagari digits
        ("Irrigate 25 mm", "सिंचाई करें"),                                  # figure dropped
        ("Apply 40 kg", "Apply 400 kg"),                                    # figure changed
    ])
    def test_anything_that_moves_a_figure_is_rejected(self, source, translated):
        # Rejection keeps the English. A farmer reading a correct dose in the
        # wrong language is recoverable; a farmer reading a wrong dose in their
        # own language is not.
        assert translate.numbers_survived(source, translated) is False

    @pytest.mark.asyncio
    async def test_no_key_raises_rather_than_silently_returning_english(self):
        with pytest.raises(translate.TranslationUnavailable):
            await translate.translate(["x"], api_key="", lang="hi")

    @pytest.mark.asyncio
    async def test_an_unsupported_language_is_refused(self):
        with pytest.raises(translate.TranslationUnavailable):
            await translate.translate(["x"], api_key="k", lang="zu")


class TestSpeechSaysNothingRatherThanTheWrongThing:
    def test_every_narration_language_has_a_voice_or_none(self):
        from app.ai.narrate import LANGUAGES

        # Indian languages must have a locale; the rest may legitimately have
        # none. What must never happen is a locale that is not the language.
        for code in ("en", "hi", "pa", "bn", "mr", "te"):
            assert code in LANGUAGES
            assert speech.locale_for(code) is not None

    def test_an_unvoiced_language_returns_none_not_english(self):
        # Speaking confident English over a Zulu advisory gives a listener no
        # way to know the words do not match the screen.
        assert speech.locale_for("zu") is None

    @pytest.mark.asyncio
    async def test_no_key_is_an_error_not_silence(self):
        with pytest.raises(speech.SpeechUnavailable):
            await speech.synthesise("hello", api_key="", lang="en")

    @pytest.mark.asyncio
    async def test_empty_text_is_refused(self):
        with pytest.raises(speech.SpeechUnavailable):
            await speech.synthesise("   ", api_key="k", lang="en")


class TestVertexNeverMisattributesAScore:
    LABELS = ["rice__blast", "rice__brown_spot", "rice__tungro"]

    def test_a_score_vector_maps_to_labels_in_order(self):
        out = vertex.parse_predictions({"predictions": [[0.1, 0.7, 0.2]]}, self.LABELS)
        assert out[0] == ("rice__brown_spot", pytest.approx(0.7))

    def test_a_short_vector_is_refused_rather_than_zipped(self):
        # zip() would silently pair two scores with the first two labels and
        # report a disease the model never scored.
        with pytest.raises(vertex.VertexUnavailable):
            vertex.parse_predictions({"predictions": [[0.4, 0.6]]}, self.LABELS)

    def test_automl_shape_is_understood(self):
        payload = {"predictions": [{
            "displayNames": ["rice__tungro", "rice__blast"],
            "confidences": [0.8, 0.2],
        }]}
        assert vertex.parse_predictions(payload, self.LABELS)[0][0] == "rice__tungro"

    def test_mismatched_names_and_scores_are_refused(self):
        payload = {"predictions": [{"displayNames": ["a", "b"], "confidences": [0.9]}]}
        with pytest.raises(vertex.VertexUnavailable):
            vertex.parse_predictions(payload, self.LABELS)

    def test_no_predictions_is_an_outage_not_an_answer(self):
        with pytest.raises(vertex.VertexUnavailable):
            vertex.parse_predictions({"predictions": []}, self.LABELS)

    @pytest.mark.asyncio
    async def test_unconfigured_refuses_before_touching_credentials(self):
        with pytest.raises(vertex.VertexUnavailable):
            await vertex.classify(
                b"x", labels=self.LABELS, project="", location="",
                endpoint_id="", credentials_path="",
            )


class TestEarthEngineDropsCloudRatherThanAveragingIt:
    def test_a_too_cloudy_observation_is_discarded(self):
        rows = earth_engine.parse_observations({"features": [
            {"properties": {"date": "2026-08-01", "NDVI": 0.42, "valid_fraction": 0.2}},
        ]})
        # NDVI over cloud is not a worse reading of the crop, it is a reading of
        # something else.
        assert rows == []

    def test_a_clear_observation_survives_and_is_stamped(self):
        rows = earth_engine.parse_observations({"features": [
            {"properties": {"date": "2026-08-01", "NDVI": 0.42, "valid_fraction": 0.9}},
        ]})
        assert rows[0]["ndvi"] == pytest.approx(0.42)
        assert rows[0]["source"] == "earth-engine"

    def test_a_row_without_a_value_is_skipped_not_defaulted(self):
        # A defaulted 0.0 would be compared against the expected curve and
        # generate advice about a crash that did not happen.
        assert earth_engine.parse_observations(
            {"features": [{"properties": {"date": "2026-08-01"}}]}
        ) == []

    def test_observations_come_back_in_date_order(self):
        rows = earth_engine.parse_observations({"features": [
            {"properties": {"date": "2026-08-10", "NDVI": 0.5, "valid_fraction": 0.9}},
            {"properties": {"date": "2026-08-01", "NDVI": 0.4, "valid_fraction": 0.9}},
        ]})
        assert [r["day"].isoformat() for r in rows] == ["2026-08-01", "2026-08-10"]

    def test_the_cloud_mask_matches_the_copernicus_one(self):
        # Two sources of the same measurement have to mask identically or the
        # series has a step in it wherever the provider changed.
        assert earth_engine.CLOUD_SCL_CLASSES == (3, 8, 9, 10)

    @pytest.mark.asyncio
    async def test_unconfigured_refuses(self):
        with pytest.raises(earth_engine.EarthEngineUnavailable):
            await earth_engine.fetch_ndvi(
                {}, __import__("datetime").date(2026, 1, 1),
                __import__("datetime").date(2026, 2, 1),
                project="", credentials_path="",
            )


class TestBigQueryExportsOnlyWhatIsAlreadyPublic:
    ENVELOPE = {
        "node_id": "node-in",
        "signature": "sig",
        "payload": {
            "indicator": "ndvi_anomaly_mean",
            "unit": "1",
            "generated_at": "2026-09-12T00:00:00Z",
            "groups": [{"district": "Ludhiana", "value": -0.12, "n": 14}],
        },
    }

    def test_each_row_carries_the_signature(self):
        # So a single row copied out of the warehouse is still attributable to
        # the node that published it.
        rows = bigquery.rows_from_envelope(self.ENVELOPE)
        assert rows[0]["signature"] == "sig"
        assert rows[0]["node_id"] == "node-in"

    def test_no_geometry_or_field_reaches_the_warehouse(self):
        rows = bigquery.rows_from_envelope(self.ENVELOPE)
        forbidden = {"geometry", "field_id", "user_id", "phone", "lat", "lon"}
        assert forbidden.isdisjoint(set(rows[0]))

    def test_an_empty_envelope_exports_nothing(self):
        assert bigquery.rows_from_envelope({"payload": {}}) == []

    @pytest.mark.asyncio
    async def test_unconfigured_refuses_before_reading_credentials(self):
        with pytest.raises(bigquery.BigQueryUnavailable):
            await bigquery.export(
                self.ENVELOPE, project="", dataset="", table="", credentials_path="",
            )

    @pytest.mark.asyncio
    async def test_a_payload_with_identifiers_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            "app.federation.aggregate.contains_identifiers", lambda p: ["phone"]
        )
        with pytest.raises(bigquery.BigQueryUnavailable, match="identifiers"):
            await bigquery.export(
                self.ENVELOPE, project="p", dataset="d", table="t",
                credentials_path="/nonexistent",
            )
