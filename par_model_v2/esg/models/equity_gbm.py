"""
Risk-Neutral GBM Equity Model correlated with Hull-White interest rates.

Under the risk-neutral measure, an equity index price S(t) satisfies:

    dS(t)/S(t) = r(t) dt + σ_E · dW_E(t)

where r(t) is the stochastic short rate from HullWhite1F and W_E is a
Brownian motion correlated with the rate Brownian W_r:

    dW_E · dW_r = ρ dt

Discrete update (exact for GBM given the short rate path):

    S(t+Δ) = S(t) · exp[(r(t) - σ_E²/2)·Δ + σ_E·√Δ · Z_E]

The total return index and dividend yield are both modelled.

Dividend model: dividend yield δ(t) follows a mean-reverting process:

    d(δ) = κ_δ·(δ̄ - δ)·dt + σ_δ·dW_δ   (independent of equity/rate W)

so that δ stays positive and mean-reverts to the long-run yield δ̄.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class EquityGBMParams:
    """
    Parameters for the risk-neutral GBM equity model.

    sigma : float
        Annualised equity volatility. Typical range: 0.12 - 0.25.
    rho_rate : float
        Correlation between equity returns and the domestic short rate.
        Typically negative: -0.3 to -0.1 (rates up → equities down on impact).
    div_yield_mean : float
        Long-run mean dividend yield (per annum). Typical: 0.02 - 0.04.
    div_yield_kappa : float
        Mean-reversion speed for dividend yield. Typical: 0.5 - 2.0.
    div_yield_sigma : float
        Volatility of dividend yield process. Typical: 0.005 - 0.015.
    """

    sigma: float = 0.18
    rho_rate: float = -0.20
    div_yield_mean: float = 0.025
    div_yield_kappa: float = 1.0
    div_yield_sigma: float = 0.008


# Default parameters per equity market (approximate 2024-2025 calibration)
DEFAULT_EQUITY_PARAMS: dict[str, EquityGBMParams] = {
    "E_USD": EquityGBMParams(sigma=0.18, rho_rate=-0.20, div_yield_mean=0.018),  # S&P 500
    "E_EUR": EquityGBMParams(sigma=0.20, rho_rate=-0.18, div_yield_mean=0.030),  # Euro Stoxx 50
    "E_GBP": EquityGBMParams(sigma=0.19, rho_rate=-0.18, div_yield_mean=0.035),  # FTSE 100
    "E_JPY": EquityGBMParams(sigma=0.22, rho_rate=-0.15, div_yield_mean=0.020),  # Nikkei 225
    "E_CNY": EquityGBMParams(sigma=0.25, rho_rate=-0.10, div_yield_mean=0.025),  # CSI 300
}

# Map equity ticker → domestic currency
EQUITY_CURRENCY: dict[str, str] = {
    "E_USD": "USD",
    "E_EUR": "EUR",
    "E_GBP": "GBP",
    "E_JPY": "JPY",
    "E_CNY": "CNY",
}


class EquityGBM:
    """
    Risk-neutral GBM equity model correlated with a Hull-White short rate.

    Parameters
    ----------
    ticker : str
        Equity identifier, e.g. 'E_USD'.
    params : EquityGBMParams, optional
        Model parameters. Defaults to DEFAULT_EQUITY_PARAMS[ticker].
    """

    def __init__(self, ticker: str, params: Optional[EquityGBMParams] = None):
        self.ticker = ticker
        self.p = params or DEFAULT_EQUITY_PARAMS.get(ticker, EquityGBMParams())

    def simulate(
        self,
        r_paths: np.ndarray,
        dt: float,
        z_equity: np.ndarray,
        z_div: Optional[np.ndarray] = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Simulate equity total-return index and dividend yield paths.

        Parameters
        ----------
        r_paths : array, shape (n_trials, n_steps+1)
            Short-rate paths from HullWhite1F.simulate() for the domestic currency.
        dt : float
            Time step in years.
        z_equity : array, shape (n_trials, n_steps)
            Pre-drawn standard normal variates (may be correlated with rate shocks).
        z_div : array, shape (n_trials, n_steps), optional
            Independent normals for dividend yield process. Generated internally
            if not provided.

        Returns
        -------
        total_return : array, shape (n_trials, n_steps+1)
            Gross total return factor at each step. Column 0 = 1.0.
            The value at step t is S(t)/S(t-1) * exp(δ(t)·dt) approximately.
        div_yield : array, shape (n_trials, n_steps+1)
            Dividend yield (annualised) at each step.
        """
        n_trials, n_time = r_paths.shape
        n_steps = n_time - 1
        sigma = self.p.sigma
        kappa_d = self.p.div_yield_kappa
        theta_d = self.p.div_yield_mean
        sigma_d = self.p.div_yield_sigma

        if z_div is None:
            rng = np.random.default_rng()
            z_div = rng.standard_normal((n_trials, n_steps))

        # --- Equity price (log-space) ---
        # Step: ln S(t+1) - ln S(t) = (r(t) - σ²/2)·Δ + σ·√Δ·Z_E
        log_return = (r_paths[:, :-1] - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z_equity

        # Total return factor at each step: exp(log_return)
        step_factor = np.exp(log_return)

        # Build cumulative total-return index (includes price appreciation)
        total_return = np.ones((n_trials, n_steps + 1))
        total_return[:, 1:] = step_factor

        # --- Dividend yield (exact OU discretization) ---
        e_kd_dt = np.exp(-kappa_d * dt)
        std_d = sigma_d * np.sqrt((1.0 - np.exp(-2.0 * kappa_d * dt)) / (2.0 * kappa_d))

        div_yield = np.empty((n_trials, n_steps + 1))
        div_yield[:, 0] = theta_d  # start at long-run mean

        for step in range(n_steps):
            div_yield[:, step + 1] = (
                div_yield[:, step] * e_kd_dt
                + theta_d * (1.0 - e_kd_dt)
                + std_d * z_div[:, step]
            )

        # Floor dividend yield at 0
        div_yield = np.maximum(div_yield, 0.0)

        return total_return, div_yield
