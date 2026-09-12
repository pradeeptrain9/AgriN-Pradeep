"""A drawn boundary must never be mistaken for a walked one.

Walking a boundary is a survey: the farmer stood on each corner and the GPS
recorded where they were. Tapping corners on a satellite basemap is an
estimate, carrying the basemap's registration error plus whatever the farmer
remembers about where their land ends.

Both produce real advice -- NDVI and weather for those coordinates are
genuinely real -- but the advisory works in kilograms and millimetres *per
hectare*, so the area is a multiplier on every figure a farmer acts on. The
provenance has to survive the wire, the insert and the read back, or a drawn
estimate silently becomes a survey in a dispute or a district aggregate.

These are contract tests, not database tests: this suite has no Postgres, so
what is pinned is that the column is named in every statement that touches a
field and that the API refuses a value outside the two it knows.
"""

import pathlib

import pytest
from pydantic import ValidationError

from app.schemas import FieldCreate, FieldOut

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[77.0, 13.0], [77.001, 13.0], [77.001, 13.001], [77.0, 13.001], [77.0, 13.0]]],
}

API = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "fields.py"
MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "db" / "migrations" / "009_field_source.sql"
)


class TestTheRequestContract:
    def test_a_walked_field_is_the_default(self):
        # Every field that existed before this shipped was walked, and an app
        # that has not been updated still sends no source at all.
        assert FieldCreate(name="North plot", geometry=SQUARE).source == "walked"

    def test_a_drawn_field_can_say_so(self):
        assert FieldCreate(name="North plot", geometry=SQUARE, source="drawn").source == "drawn"

    @pytest.mark.parametrize("value", ["surveyed", "imported", "", "WALKED", "guess"])
    def test_an_unknown_provenance_is_refused(self, value):
        # Accepting free text here would let a caller write a provenance that
        # reads as authoritative and means nothing.
        with pytest.raises(ValidationError):
            FieldCreate(name="North plot", geometry=SQUARE, source=value)


class TestTheResponseContract:
    def test_source_is_returned_to_the_app(self):
        out = FieldOut(
            id="f-1", name="North plot", area_ha=1.2, centroid=[77.0, 13.0],
            geometry=SQUARE, created_at="2026-09-12T00:00:00Z", source="drawn",
        )
        assert out.source == "drawn"

    def test_a_field_from_before_this_column_reads_as_walked(self):
        out = FieldOut(
            id="f-1", name="North plot", area_ha=1.2, centroid=[77.0, 13.0],
            geometry=SQUARE, created_at="2026-09-12T00:00:00Z",
        )
        assert out.source == "walked"


class TestItSurvivesTheRoundTrip:
    """Structural, because a column that is written and never read back is the
    failure mode this is guarding against: the app sends 'drawn', the insert
    stores it, and the SELECT that builds the response never asks for it -- so
    every field comes back 'walked' and the tests above still pass."""

    def test_the_insert_names_the_column(self):
        sql = API.read_text()
        assert "area_ha, source) VALUES" in sql
        assert '"source": payload.source' in sql

    def test_every_read_of_a_field_selects_it(self):
        body = API.read_text()
        reads = [ln for ln in body.splitlines() if "ST_AsGeoJSON(geom::geometry)" in ln]
        assert len(reads) == 2, "a new field SELECT was added without source"
        assert body.count("created_at, source ") == 2

    def test_the_response_carries_it_rather_than_hardcoding_walked(self):
        assert 'source=field.get("source") or "walked"' in API.read_text()


class TestTheMigration:
    def test_it_defaults_existing_rows_to_walked(self):
        # Backfilling to 'drawn' would relabel real surveys as estimates.
        sql = MIGRATION.read_text()
        assert "ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'walked'" in sql

    def test_the_database_refuses_a_value_the_api_would_not(self):
        # Defence in depth: the ingest path is not the only writer a node
        # operator has.
        sql = MIGRATION.read_text()
        assert "CHECK (source IN ('walked', 'drawn'))" in sql

    def test_it_can_be_applied_twice(self):
        # migrate.py records what it ran, but a node operator restoring a dump
        # or running it by hand should not get a duplicate-constraint error.
        sql = MIGRATION.read_text()
        assert "IF NOT EXISTS" in sql
        assert "duplicate_object" in sql
