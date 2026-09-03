"""Import validation for the pesticide allowlist.

Naming a pesticide, a dose and a pre-harvest interval is the most dangerous
output this system produces. These tests cover the checks a machine can make;
the ones it cannot -- is this dose right, is this product actually registered --
are why `--verified-by` is mandatory and attributed.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

from pesticides import COLUMNS, validate  # noqa: E402


def row(**overrides) -> dict:
    base = {
        "country": "IN", "crop_code": "rice", "disease_code": "rice__blast",
        "active_ingredient": "EXAMPLE-A", "product_name": "", "dose": "1 g/litre",
        "phi_days": "21", "max_applications_per_season": "2", "notes_for_farmer": "",
    }
    base.update(overrides)
    return base


class TestTaxonomyChecks:
    def test_valid_row_passes(self):
        assert validate([row()]) == []

    def test_unknown_crop_rejected(self):
        assert any("unknown crop_code" in p for p in validate([row(crop_code="tomato")]))

    def test_unknown_disease_rejected(self):
        assert any("unknown disease_code" in p
                   for p in validate([row(disease_code="rice__invented")]))

    def test_disease_must_belong_to_the_crop(self):
        problems = validate([row(crop_code="maize", disease_code="rice__blast")])
        assert any("does not belong to crop" in p for p in problems)


class TestPathogenLogic:
    def test_chemical_for_a_bacterial_disease_is_rejected(self):
        """The diagnosis path strips chemicals for bacterial disease, so such a
        row could never serve. Better to refuse it at import than to let someone
        believe it is in place."""
        problems = validate([row(disease_code="rice__bacterial_leaf_blight")])
        assert any("bacterial" in p for p in problems)

    def test_chemical_for_a_viral_disease_is_rejected(self):
        problems = validate([row(disease_code="rice__tungro")])
        assert any("viral" in p for p in problems)

    def test_chemical_for_the_healthy_class_is_rejected(self):
        problems = validate([row(disease_code="rice__normal")])
        assert any("healthy class" in p for p in problems)


class TestFieldChecks:
    def test_zero_phi_rejected(self):
        """A zero pre-harvest interval is the error that puts residue on food."""
        assert any("outside 1-365" in p for p in validate([row(phi_days="0")]))

    def test_absurd_phi_rejected(self):
        assert any("outside 1-365" in p for p in validate([row(phi_days="900")]))

    def test_non_numeric_phi_rejected(self):
        assert any("whole number" in p for p in validate([row(phi_days="two weeks")]))

    def test_missing_dose_rejected(self):
        assert any("dose is required" in p for p in validate([row(dose="")]))

    def test_missing_active_ingredient_rejected(self):
        assert any("active_ingredient is required" in p
                   for p in validate([row(active_ingredient="")]))

    def test_missing_country_rejected(self):
        assert any("country is required" in p for p in validate([row(country="")]))


class TestDuplicates:
    def test_duplicate_rows_rejected(self):
        assert any("duplicate" in p for p in validate([row(), row()]))

    def test_same_active_different_disease_is_not_a_duplicate(self):
        problems = validate([row(), row(disease_code="rice__brown_spot")])
        assert not any("duplicate" in p for p in problems)

    def test_duplicate_check_ignores_ingredient_case(self):
        problems = validate([row(active_ingredient="Example-A"),
                             row(active_ingredient="EXAMPLE-a")])
        assert any("duplicate" in p for p in problems)


class TestTemplate:
    def test_template_columns_match_the_importer(self):
        assert COLUMNS[0] == "country"
        assert "dose" in COLUMNS and "phi_days" in COLUMNS
        assert "verified_by" not in COLUMNS, (
            "provenance must come from the command line, not the CSV, so it "
            "cannot be copied along with the rows"
        )
