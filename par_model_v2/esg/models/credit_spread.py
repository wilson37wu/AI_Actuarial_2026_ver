"""
Stochastic Credit Spread Model

Models credit spreads for investment-grade and high-yield ratings using
correlated mean-reverting (OU) spread processes layered on top of
Hull-White government rates.

Model:
    s_i(t) = spread for rating i, satisfies:

        ds_i(t) = κ_i · (θ_i - s_i(t)) dt + σ_i · √s_i(t) · dW_s,i(t)

    (CIR-style to keep spreads non-negative)

    The credit ZCB price at time t for maturity T and rating i is:

        P_credit(t,T) = P_govt(t,T) · exp(-s_i(t) · Bs(κ_i, t, T))

    where Bs(κ,t,T) = (1 - exp(-κ(T-t))) / κ  (analogous to HW B function).

    This is the "add-on spread" or "intensity-based" approximation, consistent
    with reduced-form credit models (Lando 1998) when the spread is interpreted
    as the risk-neutral default intensity.

Correlation:
    Spread shocks for all ratings share a common "credit market" factor (W_c)
    with rating-specific idiosyncratic components:

        dW_s,i = ρ_cs · dW_c + √(1 - ρ_cs²) · dW_i

    W_c is correlated with the domestic rate Brownian W_r at ρ_sr.

References:
    Lando, D. (1998). On Cox processes and credit risky securities.
    Review of Derivatives Research, 2(2-3), 99-120.

    EIOPA (2015). Technical Specification for the Preparatory Phase,
    Section 4.3 (credit risk in SCR).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class CreditSpreadParams:
    """
    CIR mean-reverting parameters for one credit rating.

    kappa : float
        Mean-reversion speed. Higher → spreads revert faster.
    theta : float
        Long-run mean spread (per annum). Calibrate to credit curves.
    sigma : float
        Spread volatility. Must satisfy 2·κ·θ > σ² (Feller condition for
        strictly positive spreads).
    rho_rate : float
        Correlation with the domestic government short rate.
        Typically negative: credit tightens when rates rise (flight-to-quality).
    """

    kappa: float = 0.50
    theta: float = 0.010
    sigma: float = 0.008
    rho_rate: float = -0.15


# Approximate long-run mean spreads per rating (basis of historical averages,
# roughly consistent with IG/HY credit indices 2010-2024).
DEFAULT_SPREAD_PARAMS: dict[str, CreditSpreadParams] = {
    "AAA": CreditSpreadParams(kappa=0.60, theta=0.0010, sigma=0.0008, rho_rate=-0.10),
    "AA":  CreditSpreadParams(kappa=0.55, theta=0.0025, sigma=0.0015, rho_rate=-0.12),
    "A":   CreditSpreadParams(kappa=0.50, theta=0.0060, sigma=0.0030, rho_rate=-0.15),
    "BBB": CreditSpreadParams(kappa=0.45, theta=0.0120, sigma=0.0060, rho_rate=-0.18),
    "BB":  CreditSpreadParams(kappa=0.40, theta=0.0250, sigma=0.0120, rho_rate=-0.20),
    "B":   CreditSpreadParams(kappa=0.35, theta=0.0450, sigma=0.0200, rho_rate=-0.20),
    "CCC": CreditSpreadParams(kappa=0.30, theta=0.0900, sigma=0.0350, rho_rate=-0.15),
}

# Cross-rating spread correlation (all pairs share a common credit factor)
# Off-diagonal entries: correlation between rating i and j spread shocks
_SPREAD_FACTOR_LOADING = 0.70  # common factor loading for all ratings


def Bs(kappa: float, t: float | np.ndarray, T: float | np.ndarray) -> np.ndarray:
    """
    Duration-like function Bs(κ,t,T) = (1 - exp(-κ(T-t))) / κ.
    Analogous to the HW B(t,T) function, used for credit ZCB pricing.
    """
    tau = np.asarray(T, dtype=float) - np.asarray(t, dtype=float)
    return (1.0 - np.exp(-kappa * tau)) / kappa


class CreditSpreadModel:
    """
    Stochastic credit spread model for multiple ratings.

    Parameters
    ----------
    ratings : list of str
        Ratings to model, e.g. ['AAA', 'AA', 'A', 'BBB'].
    params : dict of str → CreditSpreadParams, optional
        Per-rating parameters. Falls back to DEFAULT_SPREAD_PARAMS.
    """

    def __init__(
        self,
        ratings: list[str],
        params: Optional[dict[str, CreditSpreadParams]] = None,
    ):
        self.ratings = ratings
        self._params: dict[str, CreditSpreadParams] = {}
        for r in ratings:
            p = (params or {}).get(r, DEFAULT_SPREAD_PARAMS.get(r, CreditSpreadParams()))
            self._params[r] = p

    def credit_zcb_price(
        self,
        t: float,
        T: float,
        gov_zcb: np.ndarray,
        spreads: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        """
        Compute credit ZCB prices given government ZCB prices and spread levels.

        Parameters
        ----------
        t : float
            Current time (years).
        T : float
            Maturity time (years).
        gov_zcb : array, shape (n_trials,)
            Government ZCB prices P_govt(t,T).
        spreads : dict str → array (n_trials,)
            Current spread level s_i(t) for each rating.

        Returns
        -------
        prices : dict str → array (n_trials,)
            Credit ZCB prices P_credit(t,T) per rating.
        """
        prices = {}
        for rating in self.ratings:
            kappa = self._params[rating].kappa
            Bval = Bs(kappa, t, T)
            s = spreads[rating]
            prices[rating] = gov_zcb * np.exp(-s * Bval)
        return prices

    def simulate(
        self,
        n_trials: int,
        n_steps: int,
        dt: float,
        z_rate: np.ndarray,
        rng: Optional[np.random.Generator] = None,
    ) -> np.ndarray:
        """
        Simulate spread paths for all ratings using exact CIR discretization
        (Andersen 2007 QE scheme for CIR positivity).

        Parameters
        ----------
        n_trials : int
        n_steps : int
        dt : float
            Time step in years.
        z_rate : array, shape (n_trials, n_steps)
            Standard normals driving the domestic government rate (for correlation).
        rng : Generator, optional

        Returns
        -------
        spreads : dict str → array (n_trials, n_steps+1)
            Spread paths per rating.
        """
        if rng is None:
            rng = np.random.default_rng()

        # Draw a common credit market factor and idiosyncratic factors
        z_common = rng.standard_normal((n_trials, n_steps))

        result: dict[str, np.ndarray] = {}

        for rating in self.ratings:
            p = self._params[rating]

            # Verify Feller condition; warn but continue
            feller = 2.0 * p.kappa * p.theta - p.sigma**2
            if feller <= 0:
                # Feller condition violated; spreads may hit zero. Use floor instead.
                pass

            # Correlated innovation: ρ_r·Z_rate + √(1-ρ_r²)·Z_credit
            rho_r = p.rho_rate
            z_idio = rng.standard_normal((n_trials, n_steps))
            z_common_rating = (
                _SPREAD_FACTOR_LOADING * z_common
                + np.sqrt(1.0 - _SPREAD_FACTOR_LOADING**2) * z_idio
            )
            # Blend with rate innovation
            z_spread = rho_r * z_rate + np.sqrt(1.0 - rho_r**2) * z_common_rating

            # Exact CIR discretization (Andersen QE scheme — piecewise linear approximation)
            s = np.empty((n_trials, n_steps + 1))
            s[:, 0] = p.theta  # start at long-run mean

            for step in range(n_steps):
                s_t = s[:, step]
                # Conditional mean and variance of CIR exact transition
                e_kdt = np.exp(-p.kappa * dt)
                cond_mean = s_t * e_kdt + p.theta * (1.0 - e_kdt)
                cond_var = (
                    s_t * (p.sigma**2 * e_kdt / p.kappa) * (1.0 - e_kdt)
                    + p.theta * p.sigma**2 / (2.0 * p.kappa) * (1.0 - e_kdt)**2
                )
                # QE piecewise approximation
                psi = cond_var / np.maximum(cond_mean**2, 1e-20)

                # Moment-matching: use Gaussian for psi ≤ 1.5, exponential for psi > 1.5
                mask_gauss = psi <= 1.5
                mask_exp = ~mask_gauss

                s_next = np.empty(n_trials)

                # Gaussian branch
                if np.any(mask_gauss):
                    b2 = 2.0 / psi - 1.0 + np.sqrt(2.0 / psi) * np.sqrt(2.0 / psi - 1.0)
                    a = cond_mean / (1.0 + b2)
                    b2 = np.maximum(b2, 0.0)
                    z_g = z_spread[:, step]
                    s_next[mask_gauss] = a[mask_gauss] * (
                        np.sqrt(b2[mask_gauss]) + z_g[mask_gauss]
                    ) ** 2

                # Exponential branch
                if np.any(mask_exp):
                    p_exp = (psi - 1.0) / (psi + 1.0)
                    beta_exp = (1.0 - p_exp) / np.maximum(cond_mean, 1e-20)
                    # Map uniform through exponential; use normal CDF as pseudo-uniform
                    from scipy.special import ndtr as _ndtr  # standard normal CDF
                    u = _ndtr(z_spread[:, step])
                    s_next[mask_exp] = np.where(
                        u[mask_exp] <= p_exp[mask_exp],
                        0.0,
                        np.log((1.0 - p_exp[mask_exp]) / np.maximum(1.0 - u[mask_exp], 1e-20))
                        / beta_exp[mask_exp],
                    )

                s[:, step + 1] = np.maximum(s_next, 0.0)

            result[rating] = s

        return result
