"""Stochastic par fund projection using Moody ESG scenario paths.

This module defines ``ParFundStochastic``, which projects asset returns using
scenario paths (from ``MoodyESGAdapter``) and computes annual / hybrid-grid
par fund surplus and bonus rates.

The implementation is intentionally simplified and can be refined with more
product-specific logic. It is vectorised within each scenario using NumPy, and
aggregates results into arrays over all scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from par_model_v2.esg import ESGAdapter
from par_model_v2.grid.grid_manager import TimeGrid


@dataclass
class ParFundConfig:
    """Configuration parameters for the par fund projection.

    Attributes
    ----------
    guaranteed_rate:
        Annual guaranteed crediting rate (e.g. 0.02 for 2% p.a.).
    shareholder_margin:
        Fraction of distributable surplus allocated to shareholders.
    smoothing_alpha:
        Smoothing coefficient in (0, 1]; higher means less smoothing.
    policyholder_share:
        Fraction of distributable surplus (after shareholder margin) that goes
        to policyholders.
    bonus_cap:
        Optional cap on the bonus rate (per step). If None, no cap is applied.
    bonus_floor:
        Optional floor on the bonus rate (per step).
    """

    guaranteed_rate: float
    shareholder_margin: float
    smoothing_alpha: float
    policyholder_share: float
    bonus_cap: Optional[float] = None
    bonus_floor: Optional[float] = None


@dataclass
class AssetAllocation:
    """Simplified asset allocation for the par fund.

    Attributes
    ----------
    equity_weight:
        Fraction of the portfolio invested in equity. The remaining
        (1 - equity_weight) is treated as fixed income (government / credit)
        proxied by the risk-free return derived from deflators.
    """

    equity_weight: float


class ParFundStochastic:
    """Stochastic par fund engine using ESG scenario paths.

    The projection is performed on the hybrid time grid defined by ``TimeGrid``.
    For each scenario, it computes portfolio returns, guaranteed accumulation,
    surplus, and three bonus-related time series:

    - ``bonus_wl_cashdiv``: whole-life cash dividend bonus rate (here modelled
      as zero by default, but the same framework can be extended to split
      bonuses between cash and reversionary forms).
    - ``bonus_wl_rb``: whole-life reversionary bonus rate.
    - ``crediting_rate_pension``: total crediting rate for pension business
      (guaranteed + bonus).
    """

    def __init__(
        self,
        time_grid: TimeGrid,
        config: ParFundConfig,
        allocation: AssetAllocation,
        esg_adapter: ESGAdapter,
    ) -> None:
        self.time_grid = time_grid
        self.config = config
        self.allocation = allocation
        self.esg_adapter = esg_adapter

        # Precompute hybrid month indices and step lengths in years
        self._hybrid_months = self.time_grid.hybrid_month_indices()
        # Differences in months between consecutive hybrid points
        month_deltas = np.diff(self._hybrid_months, prepend=self._hybrid_months[0])
        # Convert to year fractions; the first step is taken as 0.
        month_deltas[0] = 0
        self._dt_years = month_deltas / 12.0

    # ------------------------------------------------------------------
    # Core projection
    # ------------------------------------------------------------------

    def _portfolio_return_from_paths(
        self,
        trial: int,
    ) -> np.ndarray:
        """Compute portfolio returns on the hybrid grid for one scenario.

        The portfolio is a simple mix of:
        - equity: using ``get_equity_total_return(trial, annual=True)``
        - fixed income: using returns implied by the risk-free deflators

        Returns
        -------
        np.ndarray
            1D array of portfolio returns on the hybrid grid.
        """

        # Deflators and implied risk-free returns on the hybrid grid
        defl = self.esg_adapter.get_deflator(trial=trial, annual=True)
        if defl.shape[0] != self._hybrid_months.shape[0]:
            raise ValueError("Deflator series length is inconsistent with hybrid time grid")

        # r_rf[t] approximated from deflators: DF(t-1)/DF(t) - 1
        rf_ret = np.empty_like(defl, dtype=float)
        rf_ret[0] = 0.0
        rf_ret[1:] = defl[:-1] / defl[1:] - 1.0

        # Equity total return on hybrid grid (already compounded within buckets)
        eq_ret = self.esg_adapter.get_equity_total_return(trial=trial, annual=True)
        if eq_ret.shape[0] != self._hybrid_months.shape[0]:
            raise ValueError("Equity return series length is inconsistent with hybrid time grid")

        w_eq = float(self.allocation.equity_weight)
        w_fi = 1.0 - w_eq

        return w_eq * eq_ret + w_fi * rf_ret

    def _bonus_rate_from_surplus(
        self,
        surplus: np.ndarray,
        liability_shadow: np.ndarray,
    ) -> np.ndarray:
        """Compute smoothed bonus rate from surplus and liability base.

        Raw bonus rate is based on positive surplus, shared between
        shareholders and policyholders, then smoothed and capped/floored.
        """

        cfg = self.config
        raw_bonus = np.zeros_like(surplus, dtype=float)

        # Surplus participation in each step
        positive_surplus = np.maximum(surplus, 0.0)
        # After shareholder margin
        to_policyholders_amount = positive_surplus * (1.0 - cfg.shareholder_margin)
        # Convert to rate relative to liability base
        with np.errstate(divide="ignore", invalid="ignore"):
            rate = np.where(
                liability_shadow > 0.0,
                to_policyholders_amount / liability_shadow,
                0.0,
            )
        raw_bonus = cfg.policyholder_share * rate

        # Apply caps/floors
        if cfg.bonus_cap is not None:
            raw_bonus = np.minimum(raw_bonus, cfg.bonus_cap)
        if cfg.bonus_floor is not None:
            raw_bonus = np.maximum(raw_bonus, cfg.bonus_floor)

        # Exponential smoothing over time
        alpha = float(cfg.smoothing_alpha)
        smoothed = np.zeros_like(raw_bonus, dtype=float)
        if raw_bonus.size > 0:
            smoothed[0] = raw_bonus[0]
            for t in range(1, raw_bonus.size):
                smoothed[t] = alpha * raw_bonus[t] + (1.0 - alpha) * smoothed[t - 1]

        return smoothed

    def project(
        self,
        n_scenarios: int,
    ) -> Dict[str, np.ndarray]:
        """Project par fund surplus and bonus rates across scenarios.

        Parameters
        ----------
        n_scenarios:
            Number of scenarios (trials) to project; scenarios are assumed to be
            indexed from 1..n_scenarios.

        Returns
        -------
        dict
            Dictionary with keys:
            - "bonus_wl_cashdiv": 2D array [scenario, time]
            - "bonus_wl_rb": 2D array [scenario, time]
            - "crediting_rate_pension": 2D array [scenario, time]
            - "surplus": 2D array [scenario, time]
        """

        n_scen = int(n_scenarios)
        T = self._hybrid_months.shape[0]

        bonus_wl_cashdiv = np.zeros((n_scen, T), dtype=float)
        bonus_wl_rb = np.zeros((n_scen, T), dtype=float)
        crediting_rate_pension = np.zeros((n_scen, T), dtype=float)
        surplus_all = np.zeros((n_scen, T), dtype=float)

        g_annual = float(self.config.guaranteed_rate)

        for s in range(n_scen):
            trial = s + 1  # trials are 1-based

            # Portfolio returns on hybrid grid
            port_ret = self._portfolio_return_from_paths(scenario_paths, trial)

            # Accumulate assets and shadow liability
            assets = np.ones(T, dtype=float)
            liability_shadow = np.ones(T, dtype=float)

            for t in range(1, T):
                dt = self._dt_years[t]
                # Asset growth
                assets[t] = assets[t - 1] * (1.0 + port_ret[t])
                # Liability growth at guaranteed rate
                liab_step_rate = (1.0 + g_annual) ** dt - 1.0
                liability_shadow[t] = liability_shadow[t - 1] * (1.0 + liab_step_rate)

            surplus = assets - liability_shadow

            # Bonus rate (smoothed, capped/floored)
            bonus_rate = self._bonus_rate_from_surplus(surplus, liability_shadow)

            # Map bonus rate to required outputs
            # - Here, we treat whole-life reversionary bonus as this smoothed
            #   rate, with no cash dividend component by default.
            # - Pension crediting rate is guaranteed + bonus.
            bonus_wl_rb[s, :] = bonus_rate
            # Cash dividend bonus left as zero; framework can be extended later.
            bonus_wl_cashdiv[s, :] = 0.0
            crediting_rate_pension[s, :] = g_annual + bonus_rate

            surplus_all[s, :] = surplus

        return {
            "bonus_wl_cashdiv": bonus_wl_cashdiv,
            "bonus_wl_rb": bonus_wl_rb,
            "crediting_rate_pension": crediting_rate_pension,
            "surplus": surplus_all,
        }


__all__ = ["ParFundConfig", "AssetAllocation", "ParFundStochastic"]
