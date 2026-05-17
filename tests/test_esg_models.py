"""
Tests for the global ESG stochastic models.

Covers:
- YieldCurve: discount factors, forward rates, Nelson-Siegel
- HullWhite1F: ZCB pricing, exact discretization, t=0 determinism
- EquityGBM: correct parameter application, dividend yield bounds
- CorrelationMatrix: PSD check, Cholesky shapes, correlated draw
- GlobalESGGenerator: output shape, column presence, martingale tests
"""

import numpy as np
import pytest

from par_model_v2.esg.models.hull_white_1f import (
    YieldCurve,
    HullWhite1F,
    HullWhite1FParams,
    build_default_hw_models,
)
from par_model_v2.esg.models.equity_gbm import EquityGBM, EquityGBMParams
from par_model_v2.esg.models.correlation import CorrelationMatrix
from par_model_v2.esg.global_esg import GlobalESGConfig, GlobalESGGenerator


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def flat_curve_usd():
    return YieldCurve.flat(0.045, currency="USD")


@pytest.fixture
def ns_curve_eur():
    return YieldCurve.nelson_siegel(beta0=0.030, beta1=-0.005, beta2=0.010,
                                    lambda_=0.5, currency="EUR")


@pytest.fixture
def hw_usd(flat_curve_usd):
    params = HullWhite1FParams(a=0.10, sigma=0.012, floor=-0.01)
    return HullWhite1F(flat_curve_usd, params)


# ─── YieldCurve ──────────────────────────────────────────────────────────────

class TestYieldCurve:
    def test_flat_discount_factor(self, flat_curve_usd):
        r = 0.045
        for t in [1.0, 5.0, 10.0, 30.0]:
            df = float(flat_curve_usd.discount_factor(t))
            expected = np.exp(-r * t)
            assert abs(df - expected) < 1e-4, f"t={t}: {df} vs {expected}"

    def test_flat_zero_rate_recovery(self, flat_curve_usd):
        for t in [2.0, 7.0, 20.0]:
            r = float(flat_curve_usd.zero_rate(t))
            assert abs(r - 0.045) < 1e-3

    def test_instantaneous_forward_flat(self, flat_curve_usd):
        # For a flat curve f(0,t) ≈ r everywhere
        for t in [1.0, 5.0, 10.0]:
            f = float(flat_curve_usd.instantaneous_forward(t))
            assert abs(f - 0.045) < 5e-3

    def test_nelson_siegel_monotone(self, ns_curve_eur):
        tenors = np.array([1.0, 5.0, 10.0, 20.0, 30.0])
        rates = ns_curve_eur.zero_rate(tenors)
        # EUR NS curve should be upward sloping with these params
        assert rates[-1] > rates[0], "Expected upward-sloping NS curve"

    def test_discount_factor_at_zero(self, flat_curve_usd):
        df = float(flat_curve_usd.discount_factor(1e-6))
        assert abs(df - 1.0) < 1e-3


# ─── HullWhite1F ─────────────────────────────────────────────────────────────

class TestHullWhite1F:
    def test_B_formula(self, hw_usd):
        a = 0.10
        tau = 10.0
        expected = (1 - np.exp(-a * tau)) / a
        result = float(hw_usd.B(0.0, tau))
        assert abs(result - expected) < 1e-10

    def test_zcb_at_t0_is_deterministic(self, hw_usd):
        """At t=0, ZCB price must be identical across all trials."""
        n_trials = 200
        r0 = float(hw_usd.curve.zero_rate(np.array([1e-6]))[0])
        r_vec = np.full(n_trials, r0)

        for tenor in [1, 5, 10, 20]:
            prices = hw_usd.zcb_price(0.0, float(tenor), r_vec)
            std = float(np.std(prices))
            assert std < 1e-8, f"ZCB t=0 not deterministic for tenor={tenor}, std={std}"

    def test_zcb_at_t0_matches_initial_curve(self, hw_usd):
        """P_model(0,T) must equal P_market(0,T) for all T."""
        n_trials = 50
        r0 = float(hw_usd.curve.zero_rate(np.array([1e-6]))[0])
        r_vec = np.full(n_trials, r0)

        for tenor in [1, 5, 10, 20, 30]:
            prices = float(np.mean(hw_usd.zcb_price(0.0, float(tenor), r_vec)))
            expected = float(hw_usd.curve.discount_factor(float(tenor)))
            rel_err = abs(prices - expected) / expected
            assert rel_err < 0.005, f"ZCB mismatch tenor={tenor}: {prices:.6f} vs {expected:.6f}"

    def test_zcb_prices_in_unit_interval(self, hw_usd):
        n_trials = 500
        dt = 1 / 12
        r_paths = hw_usd.simulate(n_trials, 60, dt, z=np.random.default_rng(0).standard_normal((n_trials, 60)))
        for step in [0, 12, 36, 60]:
            t = step * dt
            prices = hw_usd.zcb_price(t, t + 10.0, r_paths[:, step])
            assert np.all(prices > 0), "Negative ZCB prices found"
            assert np.all(prices <= 1.0 + 1e-6), "ZCB > 1 found"

    def test_simulate_shape(self, hw_usd):
        n_trials, n_steps = 100, 120
        r = hw_usd.simulate(n_trials, n_steps, 1 / 12)
        assert r.shape == (n_trials, n_steps + 1)

    def test_simulate_floor_respected(self, hw_usd):
        n_trials = 200
        r = hw_usd.simulate(n_trials, 360, 1 / 12)
        assert np.all(r >= hw_usd.p.floor - 1e-8)

    def test_default_models_build(self):
        models = build_default_hw_models()
        assert set(models.keys()) == {"USD", "EUR", "GBP", "JPY", "CNY"}


# ─── Martingale test ──────────────────────────────────────────────────────────

class TestMartingale:
    """
    Risk-neutral martingale property:
        E[P(t,T) · D(0,t)] ≈ P(0,T)

    where D(0,t) = exp(-∫₀ᵗ r(s)ds) is the stochastic discount factor.
    With 2000 trials the error should be < 2%.
    """

    def test_martingale_usd_10yr(self):
        curve = YieldCurve.nelson_siegel(0.045, -0.008, 0.015, 0.5, "USD")
        hw = HullWhite1F(curve, HullWhite1FParams(a=0.10, sigma=0.012))

        n_trials = 2000
        dt = 1 / 12
        n_steps = 60  # 5 years to horizon

        rng = np.random.default_rng(1234)
        z = rng.standard_normal((n_trials, n_steps))
        r = hw.simulate(n_trials, n_steps, dt, z=z)

        # Stochastic discount factor to t=5
        cum_r = np.cumsum(r[:, :-1] * dt, axis=1)
        D_5 = np.exp(-cum_r[:, -1])  # D(0,5)

        # ZCB price at t=5 for T=15 (5+10 year bond)
        T_test = 15.0
        t_test = 5.0
        P_t_T = hw.zcb_price(t_test, T_test, r[:, n_steps])

        empirical = float(np.mean(P_t_T * D_5))
        theoretical = float(curve.discount_factor(T_test))
        err_pct = abs(empirical - theoretical) / theoretical * 100

        assert err_pct < 2.0, (
            f"Martingale error {err_pct:.2f}% > 2% "
            f"(empirical={empirical:.5f}, theoretical={theoretical:.5f})"
        )


# ─── EquityGBM ───────────────────────────────────────────────────────────────

class TestEquityGBM:
    def test_output_shapes(self):
        eq = EquityGBM("E_USD", EquityGBMParams(sigma=0.18))
        n_trials, n_steps = 100, 120
        dt = 1 / 12
        hw = HullWhite1F(YieldCurve.flat(0.045, "USD"))
        rng = np.random.default_rng(42)
        r = hw.simulate(n_trials, n_steps, dt, z=rng.standard_normal((n_trials, n_steps)))
        z_e = rng.standard_normal((n_trials, n_steps))
        tr, dy = eq.simulate(r, dt, z_e)
        assert tr.shape == (n_trials, n_steps + 1)
        assert dy.shape == (n_trials, n_steps + 1)

    def test_total_return_starts_at_one(self):
        eq = EquityGBM("E_CNY", EquityGBMParams(sigma=0.25))
        n_trials, n_steps = 50, 60
        hw = HullWhite1F(YieldCurve.flat(0.025, "CNY"))
        rng = np.random.default_rng(0)
        r = hw.simulate(n_trials, n_steps, 1 / 12, z=rng.standard_normal((n_trials, n_steps)))
        tr, _ = eq.simulate(r, 1 / 12, rng.standard_normal((n_trials, n_steps)))
        assert np.allclose(tr[:, 0], 1.0)

    def test_dividend_yield_positive(self):
        eq = EquityGBM("E_EUR")
        n_trials, n_steps = 100, 120
        hw = HullWhite1F(YieldCurve.flat(0.03, "EUR"))
        rng = np.random.default_rng(7)
        r = hw.simulate(n_trials, n_steps, 1 / 12, z=rng.standard_normal((n_trials, n_steps)))
        _, dy = eq.simulate(r, 1 / 12, rng.standard_normal((n_trials, n_steps)))
        assert np.all(dy >= 0.0), "Negative dividend yields found"


# ─── CorrelationMatrix ────────────────────────────────────────────────────────

class TestCorrelationMatrix:
    def test_default_psd(self):
        cm = CorrelationMatrix()
        validation = cm.validate()
        assert validation["positive_definite"], "Default correlation matrix not PD"
        assert validation["symmetric"]
        assert validation["unit_diagonal"]

    def test_correlated_draw_shape(self):
        cm = CorrelationMatrix()
        n_trials, n_steps = 100, 60
        rng = np.random.default_rng(42)
        z = cm.draw_correlated(n_trials, n_steps, rng)
        assert z.shape == (n_trials, n_steps, len(cm.factor_names))

    def test_correlated_draw_mean_near_zero(self):
        cm = CorrelationMatrix()
        rng = np.random.default_rng(42)
        z = cm.draw_correlated(5000, 1, rng)
        means = np.abs(z[:, 0, :].mean(axis=0))
        assert np.all(means < 0.1), f"Large factor means: {means}"

    def test_custom_2x2(self):
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        cm = CorrelationMatrix(corr, factor_names=["r_A", "E_A"])
        assert cm.validate()["positive_definite"]


# ─── GlobalESGGenerator ──────────────────────────────────────────────────────

class TestGlobalESGGenerator:
    @pytest.fixture(scope="class")
    def small_run(self):
        cfg = GlobalESGConfig(
            n_trials=200,
            n_years=5,
            dt=1.0 / 12,
            currencies=["USD", "CNY"],
            equity_tickers=["E_USD", "E_CNY"],
            bond_tenors=[1, 5, 10],
            seed=42,
        )
        gen = GlobalESGGenerator(cfg)
        df = gen.run()
        return gen, df

    def test_output_rows(self, small_run):
        gen, df = small_run
        cfg = gen.cfg
        expected = cfg.n_trials * (cfg.n_steps + 1)
        assert len(df) == expected

    def test_required_columns_present(self, small_run):
        _, df = small_run
        required = [
            "Trial", "Timestep",
            "ESG.Economies.USD.NominalZCBP(Govt, 10, 3)",
            "ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)",
            "ESG.Assets.EquityAssets.E_USD.TotalReturn",
            "ESG.Assets.EquityAssets.E_CNY.TotalReturn",
            "ESG.Economies.USD.NominalYieldCurves.NominalYieldCurve.CashTotalReturn",
        ]
        for col in required:
            assert col in df.columns, f"Missing column: {col}"

    def test_trial_range(self, small_run):
        gen, df = small_run
        assert df["Trial"].min() == 1
        assert df["Trial"].max() == gen.cfg.n_trials

    def test_zcb_price_bounds(self, small_run):
        _, df = small_run
        zcb_col = "ESG.Economies.USD.NominalZCBP(Govt, 10, 3)"
        vals = df[zcb_col].values
        assert np.all(vals > 0), "Non-positive ZCB prices"
        assert np.all(vals <= 1.001), "ZCB > 1 found"

    def test_equity_total_return_t0(self, small_run):
        gen, df = small_run
        t0_rows = df[df["Timestep"] == 0]
        tr = t0_rows["ESG.Assets.EquityAssets.E_USD.TotalReturn"].values
        assert np.allclose(tr, 1.0, atol=1e-4), "Equity total return at t=0 not 1.0"

    def test_quality_report_runs(self, small_run):
        gen, _ = small_run
        report = gen.quality_report()
        assert "tests" in report
        assert "overall_pass" in report
        # All martingale tests should pass (small tolerance for 200 trials → 5%)
        for name, result in report["tests"].items():
            if "martingale" in name:
                assert result["error_pct"] < 5.0, (
                    f"Martingale error too large: {name} = {result['error_pct']:.2f}%"
                )
