"""Stochastic participating-product projection with TVOG-style outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

import numpy as np
import pandas as pd


class HKMortalityTable:
    """Hong Kong mortality table loaded from user-provided CSV.

    Expected CSV columns (case-insensitive):
    - attained_age
    - male
    - female
    """

    def __init__(self, csv_path: str) -> None:
        df = pd.read_csv(csv_path)
        lower = {c.lower(): c for c in df.columns}
        required = {"attained_age", "male", "female"}
        missing = required - set(lower.keys())
        if missing:
            raise ValueError(f"Missing mortality table columns: {sorted(missing)}")

        self._df = pd.DataFrame(
            {
                "attained_age": pd.to_numeric(df[lower["attained_age"]], errors="coerce"),
                "male": pd.to_numeric(df[lower["male"]], errors="coerce"),
                "female": pd.to_numeric(df[lower["female"]], errors="coerce"),
            }
        ).dropna()

        self._df = self._df.sort_values("attained_age").drop_duplicates("attained_age", keep="last")
        if self._df.empty:
            raise ValueError("Mortality table has no valid rows")

        self._ages = self._df["attained_age"].to_numpy(dtype=float)
        self._male = self._df["male"].to_numpy(dtype=float)
        self._female = self._df["female"].to_numpy(dtype=float)

    def qx(self, attained_age: int, gender: str) -> float:
        age = float(attained_age)
        g = str(gender).upper()
        series = self._male if g.startswith("M") else self._female
        qx = float(np.interp(age, self._ages, series))
        return float(np.clip(qx, 0.0, 1.0))


def _select_factor(policy_year: int) -> float:
    if policy_year <= 1:
        return 0.70
    if policy_year == 2:
        return 0.90
    return 1.00


def _lapse_rate(policy_year: int) -> float:
    return 0.10 if policy_year == 1 else 0.03


def _interpolate_curve(curve: Dict[int, float], maturity: float) -> float:
    tenors = np.array(sorted(curve.keys()), dtype=float)
    yields = np.array([curve[int(t)] for t in tenors], dtype=float)
    return float(np.interp(maturity, tenors, yields))


def calibrate_sp500_gbm_params(total_returns_15y: Iterable[float]) -> Tuple[float, float]:
    arr = np.asarray(list(total_returns_15y), dtype=float)
    if arr.size < 5:
        raise ValueError("Need at least 5 annual total-return observations")
    log_r = np.log1p(arr)
    mu = float(np.mean(log_r) + 0.5 * np.var(log_r, ddof=1))
    sigma = float(np.std(log_r, ddof=1))
    return mu, sigma


@dataclass
class ParticipatingProductConfig:
    issue_age: int = 35
    gender: str = "M"
    projection_years: int = 20
    annual_premium: float = 10_000.0
    sum_assured: float = 200_000.0
    min_guaranteed_irr_yr20: float = 0.015
    policyholder_surplus_share: float = 0.80
    n_scenarios: int = 1000
    random_seed: int = 42


class StochasticParticipatingModel:
    def __init__(
        self,
        config: ParticipatingProductConfig,
        current_yield_curve: Dict[int, float],
        sp500_total_returns_15y: Iterable[float],
        hk_mortality_table_csv: str,
    ) -> None:
        self.config = config
        self.current_yield_curve = current_yield_curve
        self.eq_mu, self.eq_sigma = calibrate_sp500_gbm_params(sp500_total_returns_15y)
        self.mortality_table = HKMortalityTable(hk_mortality_table_csv)

    def _allocation_weights(self, policy_year: int) -> Tuple[float, float]:
        if policy_year <= 10:
            w_eq = 0.10 + (0.30 - 0.10) * ((policy_year - 1) / 9.0)
        else:
            w_eq = 0.30
        w_eq = float(np.clip(w_eq, 0.0, 1.0))
        return 1.0 - w_eq, w_eq

    def _simulate_vasicek_short_rate(self, n_scen: int, years: int) -> np.ndarray:
        rng = np.random.default_rng(self.config.random_seed)
        curve_lvl = _interpolate_curve(self.current_yield_curve, 1.0)
        curve_long = _interpolate_curve(self.current_yield_curve, 10.0)

        kappa = 0.35
        theta = curve_long
        sigma = max(0.0075, abs(curve_long - curve_lvl) * 0.35)

        rates = np.zeros((n_scen, years), dtype=float)
        rates[:, 0] = curve_lvl
        z = rng.standard_normal((n_scen, years - 1))
        for t in range(1, years):
            prev = rates[:, t - 1]
            rates[:, t] = prev + kappa * (theta - prev) + sigma * z[:, t - 1]
            rates[:, t] = np.clip(rates[:, t], -0.01, 0.15)
        return rates

    def _simulate_equity_returns(self, n_scen: int, years: int) -> np.ndarray:
        rng = np.random.default_rng(self.config.random_seed + 7)
        z = rng.standard_normal((n_scen, years))
        gross = np.exp((self.eq_mu - 0.5 * self.eq_sigma**2) + self.eq_sigma * z)
        return gross - 1.0

    def _guaranteed_surrender_value(self, cumulative_premiums: float, year: int) -> float:
        cfg = self.config
        t = max(1, year)
        target20 = ((1 + cfg.min_guaranteed_irr_yr20) ** cfg.projection_years - 1) / (
            cfg.min_guaranteed_irr_yr20 * cfg.projection_years
        )
        if t <= 10:
            factor = 0.85 + 0.15 * (t / 10.0)
        else:
            factor = 1.0 + (target20 - 1.0) * ((t - 10) / max(cfg.projection_years - 10, 1))
        return cumulative_premiums * factor

    def run(self) -> Dict[str, np.ndarray | float]:
        cfg = self.config
        n = cfg.n_scenarios
        years = cfg.projection_years

        rates = self._simulate_vasicek_short_rate(n, years)
        eq_ret = self._simulate_equity_returns(n, years)

        account = np.zeros((n,), dtype=float)
        inforce = 1.0

        death_cf = np.zeros((n, years), dtype=float)
        surrender_cf = np.zeros((n, years), dtype=float)
        dividend_cf = np.zeros((n, years), dtype=float)
        premium_cf = np.zeros((years,), dtype=float)

        cumulative_premiums = 0.0
        for y in range(1, years + 1):
            t = y - 1
            cumulative_premiums += cfg.annual_premium
            premium_cf[t] = cfg.annual_premium
            account += cfg.annual_premium

            w_tsy, w_eq = self._allocation_weights(y)
            port_ret = w_tsy * rates[:, t] + w_eq * eq_ret[:, t]
            guaranteed_growth = cfg.min_guaranteed_irr_yr20

            pre_credit = account * (1.0 + port_ret)
            guarantee_base = account * (1.0 + guaranteed_growth)
            surplus = np.maximum(pre_credit - guarantee_base, 0.0)
            dividend = cfg.policyholder_surplus_share * surplus
            post_div_account = np.maximum(pre_credit - dividend, guarantee_base)

            age = cfg.issue_age + y - 1
            qx = self.mortality_table.qx(age, cfg.gender) * _select_factor(y)
            lx = _lapse_rate(y)

            death_cf[:, t] = inforce * qx * cfg.sum_assured

            guaranteed_sv = self._guaranteed_surrender_value(cumulative_premiums, y)
            surrender_benefit = np.maximum(post_div_account, guaranteed_sv)
            surrender_cf[:, t] = inforce * lx * surrender_benefit
            dividend_cf[:, t] = inforce * (1.0 - qx - lx) * dividend

            survive_factor = max(1.0 - qx - lx, 0.0)
            inforce *= survive_factor
            account = post_div_account

        maturity_cf = inforce * np.maximum(
            account,
            self._guaranteed_surrender_value(cumulative_premiums, years),
        )

        policyholder_cf_mean = death_cf.mean(axis=0) + surrender_cf.mean(axis=0) + dividend_cf.mean(axis=0)
        policyholder_cf_mean[-1] += float(np.mean(maturity_cf))

        irr = _solve_irr([-x for x in premium_cf] + policyholder_cf_mean.tolist())

        inforce_to_10 = 1.0
        for y in range(1, 11):
            qxy = self.mortality_table.qx(cfg.issue_age + y - 1, cfg.gender) * _select_factor(y)
            lxy = _lapse_rate(y)
            inforce_to_10 *= max(1.0 - qxy - lxy, 0.0)
        optional_sv_10 = inforce_to_10 * float(
            np.mean(np.maximum(account, self._guaranteed_surrender_value(cfg.annual_premium * 10, 10)))
        )
        cumulative_benefit_10 = np.sum(policyholder_cf_mean[:10]) + optional_sv_10
        breakeven_ratio_yr10 = cumulative_benefit_10 / np.sum(premium_cf[:10])

        tvog_like = np.mean(
            np.maximum(
                self._guaranteed_surrender_value(cumulative_premiums, years) - account,
                0.0,
            )
        )

        return {
            "premium_cf": premium_cf,
            "death_cf_mean": death_cf.mean(axis=0),
            "surrender_cf_mean": surrender_cf.mean(axis=0),
            "cash_dividend_cf_mean": dividend_cf.mean(axis=0),
            "maturity_cf_mean": float(np.mean(maturity_cf)),
            "breakeven_ratio_yr10": float(breakeven_ratio_yr10),
            "policyholder_irr": float(irr),
            "tvog_like": float(tvog_like),
            "short_rate_paths": rates,
            "equity_return_paths": eq_ret,
        }


def _solve_irr(cashflows: Iterable[float]) -> float:
    cf = np.asarray(list(cashflows), dtype=float)

    def npv(r: float) -> float:
        t = np.arange(cf.size)
        return float(np.sum(cf / ((1.0 + r) ** t)))

    low, high = -0.95, 1.5
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return np.nan

    for _ in range(120):
        mid = 0.5 * (low + high)
        f_mid = npv(mid)
        if abs(f_mid) < 1e-10:
            return mid
        if f_low * f_mid < 0:
            high = mid
        else:
            low = mid
    return 0.5 * (low + high)


__all__ = [
    "HKMortalityTable",
    "ParticipatingProductConfig",
    "StochasticParticipatingModel",
    "calibrate_sp500_gbm_params",
]
