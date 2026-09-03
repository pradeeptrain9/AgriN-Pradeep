import pytest

from app.engine.crops import CROPS, Stage, get_crop, list_crops


def test_all_crops_have_consistent_stage_lengths():
    for crop in CROPS.values():
        assert crop.season_days == sum(crop.stage_days)
        assert all(d > 0 for d in crop.stage_days), crop.code


def test_alias_lookup():
    assert get_crop("paddy").code == "rice"
    assert get_crop("Corn").code == "maize"
    assert get_crop("BAJRA").code == "pearl_millet"
    assert get_crop("wheat").code == "wheat_spring"


def test_unknown_crop_raises():
    with pytest.raises(KeyError):
        get_crop("dragonfruit")


class TestKcCurve:
    crop = CROPS["maize"]  # stages 30/40/50/30, Kc 0.30 -> 1.20 -> 0.45

    def test_initial_stage_is_flat_kc_ini(self):
        assert self.crop.kc_at(0) == pytest.approx(0.30)
        assert self.crop.kc_at(29) == pytest.approx(0.30)

    def test_development_interpolates_linearly(self):
        # Halfway through the 40-day development stage: 0.30 + 0.5*(1.20-0.30)
        assert self.crop.kc_at(50) == pytest.approx(0.75, abs=0.01)

    def test_mid_season_holds_kc_mid(self):
        assert self.crop.kc_at(80) == pytest.approx(1.20)
        assert self.crop.kc_at(119) == pytest.approx(1.20)

    def test_late_season_declines_to_kc_end(self):
        assert self.crop.kc_at(150) == pytest.approx(0.45)

    def test_kc_never_exceeds_kc_mid(self):
        assert max(self.crop.kc_at(d) for d in range(0, 200)) <= self.crop.kc_mid + 1e-9


class TestStages:
    crop = CROPS["maize"]

    def test_stage_boundaries(self):
        assert self.crop.stage_at(0) is Stage.INITIAL
        assert self.crop.stage_at(29) is Stage.INITIAL
        assert self.crop.stage_at(30) is Stage.DEVELOPMENT
        assert self.crop.stage_at(69) is Stage.DEVELOPMENT
        assert self.crop.stage_at(70) is Stage.MID
        assert self.crop.stage_at(119) is Stage.MID
        assert self.crop.stage_at(120) is Stage.LATE
        assert self.crop.stage_at(149) is Stage.LATE
        assert self.crop.stage_at(150) is Stage.DONE


class TestDepletionAdjustment:
    def test_fao56_eq83(self):
        crop = CROPS["maize"]  # p = 0.55
        # At ETc = 5 mm/day the table value is unchanged.
        assert crop.adjusted_p(5.0) == pytest.approx(0.55)
        # Low demand raises the allowable depletion.
        assert crop.adjusted_p(2.0) == pytest.approx(0.67)
        # High demand lowers it.
        assert crop.adjusted_p(10.0) == pytest.approx(0.35)

    def test_bounds_enforced(self):
        crop = CROPS["rice"]  # p = 0.20
        assert crop.adjusted_p(20.0) >= 0.1
        assert CROPS["cotton"].adjusted_p(-50.0) <= 0.8


def test_root_depth_grows_and_saturates():
    crop = CROPS["maize"]
    early = crop.root_depth_at(5)
    mid = crop.root_depth_at(60)
    late = crop.root_depth_at(140)
    assert early < mid < crop.root_depth_m[1] + 1e-9
    assert late == pytest.approx(crop.root_depth_m[1], abs=1e-9)


def test_legumes_declare_fixation_and_cereals_do_not():
    assert CROPS["soybean"].n_fixation_kg_ha > 0
    assert CROPS["chickpea"].n_fixation_kg_ha > 0
    assert CROPS["groundnut"].n_fixation_kg_ha > 0
    assert CROPS["maize"].n_fixation_kg_ha == 0
    assert CROPS["rice"].n_fixation_kg_ha == 0


def test_nutrient_ranges_are_ordered_and_sourced():
    for crop in list_crops():
        n = crop.nutrient
        for lo, hi in (n.n, n.p2o5, n.k2o):
            assert lo < hi, crop.code
        assert n.source, crop.code
        assert n.typical_yield_t_ha > 0
