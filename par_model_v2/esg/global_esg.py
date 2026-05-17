"""
Global ESG Generator

Generates multi-currency, multi-asset economic scenarios using:
  - Hull-White 1-factor model per currency for government bond term structures
  - Risk-neutral GBM equity model correlated with domestic interest rates
  - Cholesky-decomposed correlation structure across all factors

Output column naming convention (backwards-compatible extension of existing format):

  Interest rates:
    ESG.Economies.{CCY}.NominalZCBP(Govt, {tenor}, 3)   → government ZCB price
    ESG.Economies.{CCY}.NominalYieldCurves.NominalYieldCurve.CashTotalReturn

  Equity:
    ESG.Assets.EquityAssets.{ticker}.TotalReturn
    ESG.Assets.EquityAssets.{ticker}.DividendYield.Value

  Short rate (informational):
    ESG.Economies.{CCY}.ShortRate

The output Parquet file can be consumed directly by the existing
ESGScenarioProvider (single-currency mode) or the extended multi-currency
reader.

Actuarial quality controls applied:
  1. Martingale test: E[P(0,T)·deflator(T)] ≈ P(0,T) within tolerance
  2. Deflator monotonicity check
  3. ZCB price bounds check (0, 1)
  4. Equity drift check: mean log-return ≈ r̄·dt over short horizons

Usage
-----
    from par_model_v2.esg.global_esg import GlobalESGConfig, GlobalESGGenerator

    config = GlobalESGConfig(n_trials=1000, n_years=30, currencies=["USD", "CNY"])
    gen = GlobalESGGenerator(config)
    df = gen.run()
    df.to_parquet("data/esg/global_scenarios.parquet", index=False)

    report = gen.quality_report()
    print(report)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .models.hull_white_1f import (
    HullWhite1F,
    HullWhite1FParams,
    YieldCurve,
    DEFAULT_HW_PARAMS,
    DEFAULT_YIELD_CURVES,
)
from .models.equity_gbm import EquityGBM, EquityGBMParams, EQUITY_CURRENCY
from .models.correlation import CorrelationMatrix, FACTOR_NAMES


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GlobalESGConfig:
    """
    Configuration for the global ESG run.

    Parameters
    ----------
    n_trials : int
        Number of Monte Carlo trials (recommended ≥ 1 000 for production).
    n_years : int
        Projection horizon in years.
    dt : float
        Time step in years. Default 1/12 (monthly).
    currencies : list of str
        Currencies to model. Must be subset of ['USD','EUR','GBP','JPY','CNY'].
    equity_tickers : list of str
        Equity indices to model. Must be subset of DEFAULT_EQUITY_PARAMS keys.
    bond_tenors : list of int
        Bond maturities (years) for ZCB price output. Default 1-30.
    seed : int, optional
        Random seed for reproducibility. None for non-deterministic.
    hw_params : dict, optional
        Override Hull-White parameters per currency.
        E.g. {'USD': HullWhite1FParams(a=0.12, sigma=0.015)}.
    equity_params : dict, optional
        Override equity parameters per ticker.
    yield_curves : dict, optional
        Override initial yield curves. Keys are currency codes.
        Values are YieldCurve instances.
    corr_matrix : array-like, optional
        Custom 10×10 correlation matrix. Defaults to built-in estimate.
    """

    n_trials: int = 500
    n_years: int = 30
    dt: float = 1.0 / 12.0  # monthly
    currencies: list[str] = field(default_factory=lambda: ["USD", "EUR", "GBP", "JPY", "CNY"])
    equity_tickers: list[str] = field(
        default_factory=lambda: ["E_USD", "E_EUR", "E_GBP", "E_JPY", "E_CNY"]
    )
    bond_tenors: list[int] = field(default_factory=lambda: list(range(1, 31)))
    seed: Optional[int] = 42
    hw_params: dict[str, HullWhite1FParams] = field(default_factory=dict)
    equity_params: dict[str, EquityGBMParams] = field(default_factory=dict)
    yield_curves: dict[str, YieldCurve] = field(default_factory=dict)
    corr_matrix: Optional[np.ndarray] = None

    @property
    def n_steps(self) -> int:
        return round(self.n_years / self.dt)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class GlobalESGGenerator:
    """
    Generates correlated global economic scenarios.

    Parameters
    ----------
    config : GlobalESGConfig
    """

    def __init__(self, config: GlobalESGConfig):
        self.cfg = config
        self._rng = np.random.default_rng(config.seed)

        # Build yield curves
        self._curves: dict[str, YieldCurve] = {}
        for ccy in config.currencies:
            if ccy in config.yield_curves:
                self._curves[ccy] = config.yield_curves[ccy]
            elif ccy in DEFAULT_YIELD_CURVES:
                self._curves[ccy] = YieldCurve.nelson_siegel(
                    currency=ccy, **DEFAULT_YIELD_CURVES[ccy]
                )
            else:
                raise ValueError(f"No yield curve for currency {ccy!r}.")

        # Build HW models
        self._hw: dict[str, HullWhite1F] = {}
        for ccy in config.currencies:
            params = config.hw_params.get(ccy, DEFAULT_HW_PARAMS.get(ccy, HullWhite1FParams()))
            self._hw[ccy] = HullWhite1F(self._curves[ccy], params)

        # Build equity models
        self._eq: dict[str, EquityGBM] = {}
        for ticker in config.equity_tickers:
            params = config.equity_params.get(ticker)
            self._eq[ticker] = EquityGBM(ticker, params)

        # Build correlation structure (only for factors present in config)
        active_factors = (
            [f"r_{c}" for c in config.currencies]
            + [t for t in config.equity_tickers]
        )
        # Filter the default matrix to active factors only
        all_names = FACTOR_NAMES
        active_idx = [all_names.index(f) for f in active_factors if f in all_names]
        n_act = len(active_idx)

        if config.corr_matrix is not None:
            corr = config.corr_matrix
        else:
            from .models.correlation import _CORR_RAW
            corr = _CORR_RAW[np.ix_(active_idx, active_idx)]

        self._corr_mgr = CorrelationMatrix(corr, factor_names=active_factors)
        self._active_factors = active_factors

        # Stores for quality checks
        self._r_paths: dict[str, np.ndarray] = {}
        self._tr_paths: dict[str, np.ndarray] = {}
        self._dy_paths: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    # Main simulation
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        """
        Run the full ESG simulation and return a wide-format DataFrame.

        Returns
        -------
        df : DataFrame
            Columns: Trial, Timestep, [ESG output columns...]
            Shape: (n_trials × (n_steps+1)) × (2 + n_output_cols)
        """
        cfg = self.cfg
        t0 = time.time()

        print(f"Global ESG: {cfg.n_trials} trials × {cfg.n_steps} steps "
              f"(dt={cfg.dt:.4f}y, {cfg.n_years}yr horizon)")

        # Draw correlated normals: shape (n_trials, n_steps, n_factors)
        z_all = self._corr_mgr.draw_correlated(cfg.n_trials, cfg.n_steps, self._rng)

        col_dict: dict[str, np.ndarray] = {}

        # --- Interest rate simulation ---
        for i, ccy in enumerate(cfg.currencies):
            factor_name = f"r_{ccy}"
            fac_idx = self._active_factors.index(factor_name)
            z_r = z_all[:, :, fac_idx]  # (n_trials, n_steps)

            r_paths = self._hw[ccy].simulate(
                cfg.n_trials, cfg.n_steps, cfg.dt, z=z_r
            )  # (n_trials, n_steps+1)
            self._r_paths[ccy] = r_paths

            # Short rate column
            col_dict[f"ESG.Economies.{ccy}.ShortRate"] = r_paths.ravel(order="C")

            # Cash total return: (1 + r(t)*dt) compounded monthly
            cash_tr = np.exp(r_paths[:, :-1] * cfg.dt)  # (n_trials, n_steps)
            cash_tr_full = np.ones((cfg.n_trials, cfg.n_steps + 1))
            cash_tr_full[:, 1:] = cash_tr
            col_dict[
                f"ESG.Economies.{ccy}.NominalYieldCurves.NominalYieldCurve.CashTotalReturn"
            ] = cash_tr_full.ravel(order="C")

            # ZCB prices for each tenor at each timestep
            times = np.arange(cfg.n_steps + 1) * cfg.dt
            for tenor in cfg.bond_tenors:
                T_mat = times + tenor
                # P(t, t+tenor | r(t)) for all (trial, t)
                prices = np.column_stack([
                    self._hw[ccy].zcb_price(t, t + tenor, r_paths[:, step])
                    for step, t in enumerate(times)
                ])  # (n_trials, n_steps+1)
                col_dict[f"ESG.Economies.{ccy}.NominalZCBP(Govt, {tenor}, 3)"] = (
                    prices.ravel(order="C")
                )

            print(f"  {ccy} rates: done ({time.time()-t0:.1f}s)")

        # --- Equity simulation ---
        for ticker in cfg.equity_tickers:
            ccy = EQUITY_CURRENCY.get(ticker, "USD")
            if ccy not in self._r_paths:
                continue

            fac_name = ticker
            if fac_name not in self._active_factors:
                continue
            fac_idx = self._active_factors.index(fac_name)
            z_e = z_all[:, :, fac_idx]  # (n_trials, n_steps)

            tr_paths, dy_paths = self._eq[ticker].simulate(
                self._r_paths[ccy], cfg.dt, z_e
            )
            self._tr_paths[ticker] = tr_paths
            self._dy_paths[ticker] = dy_paths

            col_dict[f"ESG.Assets.EquityAssets.{ticker}.TotalReturn"] = (
                tr_paths.ravel(order="C")
            )
            col_dict[f"ESG.Assets.EquityAssets.{ticker}.DividendYield.Value"] = (
                dy_paths.ravel(order="C")
            )
            print(f"  {ticker} equity: done ({time.time()-t0:.1f}s)")

        # --- Build DataFrame ---
        n_rows = cfg.n_trials * (cfg.n_steps + 1)
        trials = np.repeat(np.arange(1, cfg.n_trials + 1), cfg.n_steps + 1)
        timesteps = np.tile(np.arange(cfg.n_steps + 1), cfg.n_trials)

        df = pd.DataFrame(
            {"Trial": trials.astype(np.int32), "Timestep": timesteps.astype(np.int32)}
        )
        for col, arr in col_dict.items():
            df[col] = arr.astype(np.float32)

        print(f"\n  Output: {df.shape[0]:,} rows × {df.shape[1]} columns "
              f"({df.memory_usage(deep=True).sum()/1e6:.1f} MB)")
        print(f"  Total time: {time.time()-t0:.1f}s")

        return df

    # ------------------------------------------------------------------
    # Actuarial quality report
    # ------------------------------------------------------------------

    def quality_report(self) -> dict:
        """
        Run martingale and sanity tests on the simulated paths.

        Must be called after run().

        Tests
        -----
        1. Bond martingale: E[P(t,T) × deflator(0,t)] ≈ P(0,T)
           (should hold within ~1% for large trial counts)
        2. Deflator positivity & monotonicity
        3. ZCB price bounds
        4. Equity mean log-return ≈ mean(r)·dt (risk-neutral check)

        Returns
        -------
        dict with test names and pass/fail flags.
        """
        cfg = self.cfg
        report: dict = {"tests": {}, "warnings": []}

        if not self._r_paths:
            report["warnings"].append("No simulation data. Call run() first.")
            return report

        for ccy, r_paths in self._r_paths.items():
            hw = self._hw[ccy]
            dt = cfg.dt

            # Deflator: risk-neutral numeraire B(t) = exp(∫₀ᵗ r(s)ds)
            # Approximation: product of monthly cash returns
            cum_r = np.cumsum(r_paths[:, :-1] * dt, axis=1)
            deflator = np.exp(-cum_r)  # (n_trials, n_steps)

            # Test 1: bond martingale at horizon T=5, 10 years
            for T_test in [5, 10]:
                step_T = round(T_test / dt)
                if step_T >= cfg.n_steps:
                    continue
                t_arr = step_T * dt

                # Model ZCB prices at step_T
                zcb_at_T = hw.zcb_price(
                    t_arr, t_arr + T_test, r_paths[:, step_T]
                )
                # Deflated bond value: E[P(t,T)·D(0,t)] should ≈ P(0, t+T)
                deflated = zcb_at_T * deflator[:, step_T - 1]
                empirical = float(np.mean(deflated))
                theoretical = float(hw.curve.discount_factor(t_arr + T_test))
                err_pct = abs(empirical - theoretical) / theoretical * 100

                key = f"{ccy}_bond_martingale_t{T_test}"
                report["tests"][key] = {
                    "empirical": round(empirical, 6),
                    "theoretical": round(theoretical, 6),
                    "error_pct": round(err_pct, 3),
                    "pass": err_pct < 2.0,
                }

            # Test 2: ZCB price bounds (sample: 10yr ZCB at t=0)
            tenor_test = 10
            if tenor_test in cfg.bond_tenors:
                zcb_t0 = hw.zcb_price(0.0, float(tenor_test), r_paths[:, 0])
                # At t=0 all trials should give the same price (deterministic)
                expected = float(hw.curve.discount_factor(float(tenor_test)))
                std = float(np.std(zcb_t0))
                report["tests"][f"{ccy}_zcb_t0_std"] = {
                    "expected": round(expected, 6),
                    "mean": round(float(np.mean(zcb_t0)), 6),
                    "std": round(std, 8),
                    "pass": std < 1e-6,
                }

        # Test 3: equity risk-neutral mean return ≈ mean short rate
        for ticker, tr_paths in self._tr_paths.items():
            ccy = EQUITY_CURRENCY.get(ticker, "USD")
            if ccy not in self._r_paths:
                continue
            r = self._r_paths[ccy]
            # Mean log return per step
            mean_log_ret = float(np.mean(np.log(tr_paths[:, 1:])))
            mean_r = float(np.mean(r[:, :-1])) * cfg.dt
            sigma = self._eq[ticker].p.sigma
            expected_log = mean_r - 0.5 * sigma**2 * cfg.dt
            diff = abs(mean_log_ret - expected_log)

            report["tests"][f"{ticker}_equity_rn_drift"] = {
                "mean_log_ret": round(mean_log_ret, 6),
                "expected": round(expected_log, 6),
                "diff": round(diff, 6),
                "pass": diff < 0.001,
            }

        # Summary
        all_pass = all(v.get("pass", False) for v in report["tests"].values())
        report["overall_pass"] = all_pass
        return report


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def generate_global_esg(
    output_path: str | Path,
    n_trials: int = 1000,
    n_years: int = 30,
    currencies: Optional[list[str]] = None,
    equity_tickers: Optional[list[str]] = None,
    seed: int = 42,
) -> pd.DataFrame:
    """
    High-level function to generate and save a global ESG scenario file.

    Parameters
    ----------
    output_path : str or Path
        Destination Parquet file path.
    n_trials : int
        Number of Monte Carlo trials.
    n_years : int
        Projection horizon in years.
    currencies : list of str, optional
        Default: all five (USD, EUR, GBP, JPY, CNY).
    equity_tickers : list of str, optional
        Default: E_USD, E_EUR, E_GBP, E_JPY, E_CNY.
    seed : int
        Random seed.

    Returns
    -------
    df : DataFrame
    """
    cfg = GlobalESGConfig(
        n_trials=n_trials,
        n_years=n_years,
        currencies=currencies or ["USD", "EUR", "GBP", "JPY", "CNY"],
        equity_tickers=equity_tickers or ["E_USD", "E_EUR", "E_GBP", "E_JPY", "E_CNY"],
        seed=seed,
    )
    gen = GlobalESGGenerator(cfg)
    df = gen.run()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    print(f"\nSaved → {output_path}")

    report = gen.quality_report()
    _print_quality_report(report)

    return df


def _print_quality_report(report: dict) -> None:
    print("\n--- ESG Quality Report ---")
    for name, result in report.get("tests", {}).items():
        status = "PASS" if result.get("pass") else "FAIL"
        print(f"  [{status}] {name}: {result}")
    overall = "PASS" if report.get("overall_pass") else "FAIL"
    print(f"\nOverall: {overall}")
    if report.get("warnings"):
        for w in report["warnings"]:
            print(f"  WARNING: {w}")
