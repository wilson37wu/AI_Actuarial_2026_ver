"""Tests for the monthly projection engine (5Y / 10Y / 20Y PAR endowment)."""

from __future__ import annotations
import numpy as np
import pytest
from par_model_v2.projection.monthly_projection import (
    AssetPosition, ParEndowmentProduct, VALID_TERMS,
    monthly_discount_factor, monthly_mortality_qx,
    project_asset_cashflows, project_asset_share,
    project_liability_cashflows, run_full_projection,
)

# --- fixtures ---

def _prod(term=10):
    return ParEndowmentProduct(term, 35, "M", 100_000.0, 5_000.0,
                                rb_rate_annual=0.03, terminal_bonus_pct=0.5,
                                surrender_value_pct=0.9, initial_rb_accum=0.0)

def _pos(scale=0.01):
    return [
        AssetPosition("Govt",     900_000*scale, 880_000*scale, 8.5, 0.032, 0.0,  8.5, ""),
        AssetPosition("Credit_A", 575_000*scale, 570_000*scale, 6.2, 0.038, 0.0,  6.2, "A"),
        AssetPosition("Equity",   700_000*scale, 700_000*scale, 0.0, 0.025, 0.06, 0.0, ""),
        AssetPosition("Cash",     125_000*scale, 125_000*scale, 0.0, 0.020, 0.0,  0.0, ""),
    ]

# --- helpers ---

class TestHelpers:
    def test_v_monthly_compounds_to_annual(self):
        v = monthly_discount_factor(0.06)
        assert v**12 == pytest.approx(1/1.06, rel=1e-6)

    def test_monthly_qx_udd(self):
        qx = 0.05
        qx_m = monthly_mortality_qx(qx)
        assert (1 - qx_m)**12 == pytest.approx(1 - qx, rel=1e-6)

    def test_monthly_qx_bounded(self):
        for q in [0.001, 0.05, 0.5, 0.99]:
            m = monthly_mortality_qx(q)
            assert 0 < m < 1

# --- product ---

class TestProduct:
    def test_valid_terms(self):
        for t in VALID_TERMS:
            assert _prod(t).term_months == t * 12

    def test_invalid_term(self):
        with pytest.raises(ValueError):
            ParEndowmentProduct(7, 35, "M", 100_000, 5_000)

    def test_rb_monthly_compounds_annual(self):
        p = _prod()
        assert (1 + p.rb_rate_monthly)**12 == pytest.approx(1.03, rel=1e-6)

# --- liability cashflows ---

class TestLiability:
    @pytest.fixture(params=VALID_TERMS)
    def res(self, request):
        return project_liability_cashflows(_prod(request.param))

    def test_length(self, res):
        assert len(res.cashflows) == res.term_months

    def test_month_sequential(self, res):
        assert list(res.cashflows["month"]) == list(range(1, res.term_months + 1))

    def test_in_force_starts_one(self, res):
        assert res.cashflows["in_force_prob"].iloc[0] == pytest.approx(1.0)

    def test_in_force_monotone(self, res):
        ifp = res.cashflows["in_force_prob"].values
        assert np.all(ifp[1:] <= ifp[:-1] + 1e-9)

    def test_in_force_non_negative(self, res):
        assert (res.cashflows["in_force_prob"] >= 0).all()

    def test_acq_expense_month_1_only(self, res):
        df = res.cashflows
        assert df["acq_expense"].iloc[0] > 0
        assert df["acq_expense"].iloc[1:].sum() == pytest.approx(0.0)

    def test_maturity_only_at_last_month(self, res):
        df = res.cashflows
        assert df["maturity_benefit_guar"].iloc[:-1].sum() == pytest.approx(0.0, abs=1e-6)
        assert df["maturity_benefit_guar"].iloc[-1] > 0

    def test_pv_components_sign(self, res):
        assert res.pv_premiums > 0
        assert res.pv_guaranteed_benefits > 0
        assert res.pv_expenses > 0

    def test_pv_net_consistency(self, res):
        # net liability = PV(all outflows) - PV(premiums)
        expected = (res.pv_guaranteed_benefits + res.pv_non_guaranteed_benefits
                    + res.pv_surrender_benefits + res.pv_expenses - res.pv_premiums)
        assert res.pv_net_liability == pytest.approx(expected, rel=1e-4)

    def test_discount_factor_pv_check(self, res):
        df = res.cashflows
        recomputed = (df["premium"] * df["discount_factor"]).sum()
        assert recomputed == pytest.approx(res.pv_premiums, rel=1e-6)

    def test_no_nulls(self, res):
        assert not res.cashflows.isnull().any().any()

    def test_20y_higher_pv_premium_than_5y(self):
        r5  = project_liability_cashflows(_prod(5))
        r20 = project_liability_cashflows(_prod(20))
        assert r20.pv_premiums > r5.pv_premiums

    def test_ng_benefit_grows_with_term(self):
        r5  = project_liability_cashflows(_prod(5))
        r20 = project_liability_cashflows(_prod(20))
        assert r20.pv_non_guaranteed_benefits > r5.pv_non_guaranteed_benefits

# --- asset cashflows ---

class TestAssets:
    @pytest.fixture
    def res(self):
        return project_asset_cashflows(_pos(1.0), 120, 0.035)

    def test_length(self, res):
        assert len(res.cashflows) == 120

    def test_income_non_negative(self, res):
        for col in ["Govt_coupon", "Credit_coupon", "Equity_dividend", "Cash_interest"]:
            assert (res.cashflows[col] >= 0).all()

    def test_total_income_sum(self, res):
        df = res.cashflows
        recomp = (df["Govt_coupon"] + df["Credit_coupon"]
                  + df["Equity_dividend"] + df["Cash_interest"])
        np.testing.assert_allclose(df["total_income"].values, recomp.values, rtol=1e-6)

    def test_running_mv_positive(self, res):
        assert (res.cashflows["running_fund_mv"] > 0).all()

    def test_pv_income_consistent(self, res):
        df = res.cashflows
        expected = (df["total_income"] * df["discount_factor"]).sum()
        assert res.pv_total_income == pytest.approx(expected, rel=1e-6)

    def test_summary_total_coupon(self, res):
        s = res.by_class_summary
        class_rows = s[s["asset_class"] != "Total"]
        total_row  = s[s["asset_class"] == "Total"].iloc[0]
        assert total_row["total_coupon_div"] == pytest.approx(
            class_rows["total_coupon_div"].sum(), rel=1e-5)

    def test_no_nulls(self, res):
        assert not res.cashflows.isnull().any().any()

# --- asset share ---

class TestAssetShare:
    @pytest.fixture
    def res(self):
        return run_full_projection(_prod(10), _pos(0.01))

    def test_projection_length(self, res):
        assert len(res.asset_share.projection) == 120

    def test_bom_starts_zero(self, res):
        assert res.asset_share.projection["asset_share_bom"].iloc[0] == pytest.approx(0.0)

    def test_eom_non_negative(self, res):
        assert (res.asset_share.projection["asset_share_eom"] >= 0).all()

    def test_70_30_split(self, res):
        proj = res.asset_share.projection
        total_dist = proj["distributable_surplus"].sum()
        if total_dist > 0:
            sh_ratio = proj["shareholder_dist"].sum() / total_dist
            assert sh_ratio == pytest.approx(0.30, rel=0.01)

    def test_dist_sums_to_surplus(self, res):
        proj = res.asset_share.projection
        dist  = proj["distributable_surplus"].sum()
        sh_ph = proj["shareholder_dist"].sum() + proj["policyholder_dist"].sum()
        assert sh_ph == pytest.approx(dist, rel=1e-5)

    def test_summary_keys(self, res):
        for k in ["pv_premiums", "pv_guaranteed_benefits", "asset_share_at_maturity",
                  "total_shareholder_dist", "total_policyholder_dist"]:
            assert k in res.summary()

    def test_no_nulls(self, res):
        assert not res.asset_share.projection.isnull().any().any()

# --- end-to-end ---

class TestEndToEnd:
    @pytest.mark.parametrize("term", VALID_TERMS)
    def test_runs_all_terms(self, term):
        r = run_full_projection(_prod(term), _pos(0.01))
        assert len(r.liability.cashflows) == term * 12
        assert len(r.assets.cashflows)    == term * 12
        assert len(r.asset_share.projection) == term * 12

    @pytest.mark.parametrize("term", VALID_TERMS)
    def test_no_nulls_all_terms(self, term):
        r = run_full_projection(_prod(term), _pos(0.01))
        assert not r.liability.cashflows.isnull().any().any()
        assert not r.assets.cashflows.isnull().any().any()
        assert not r.asset_share.projection.isnull().any().any()

    def test_maturity_ng_grows_with_term(self):
        r5  = run_full_projection(_prod(5),  _pos(0.01))
        r20 = run_full_projection(_prod(20), _pos(0.01))
        ng5  = r5.liability.cashflows["maturity_benefit_ng"].iloc[-1]
        ng20 = r20.liability.cashflows["maturity_benefit_ng"].iloc[-1]
        assert ng20 > ng5
