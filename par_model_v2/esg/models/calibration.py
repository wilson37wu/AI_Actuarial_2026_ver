"""
Hull-White 1-Factor Calibration

Calibrates the Hull-White (a, σ) parameters to market-observed ATM
swaption implied volatilities (normal/Bachelier convention, which is
standard post-2015 for rates markets).

Hull-White ATM swaption normal vol formula (Brigo & Mercurio 2006, §3.3.3):

    σ_N(T_exp, T_mat) = σ · √[v_p(T_exp)] / A(0; T_exp, T_mat)

where:
    v_p(T_exp) = (σ²/a²) · [T_exp - 2·B(0,T_exp)/a + (1-e^{-2aT_exp})/(2a)]
    A(0;T_exp,T_mat) = annuity = Σ τ_i · P(0, T_i)

The calibration minimises:
    Σ_k [σ_N_model(k) - σ_N_market(k)]²

using scipy.optimize.minimize (L-BFGS-B).

Also provides a flat-vol approximation for quick parameter estimation from
a single swaption quote or historical short-rate volatility.

References:
    Brigo, D. & Mercurio, F. (2006). Interest Rate Models — Theory and Practice,
    2nd ed. Springer. Chapter 3.

    IAA (2013). Note on ESG Requirements. Section 5: Calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

try:
    from scipy.optimize import minimize
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False


@dataclass
class SwaptionQuote:
    """
    Single ATM swaption market quote.

    expiry : float
        Option expiry in years (e.g. 1.0 for 1Y expiry).
    tenor : float
        Swap tenor in years (e.g. 10.0 for 10Y swap).
    normal_vol : float
        ATM implied normal (Bachelier) volatility in absolute terms
        (e.g. 0.006 for 60 bps normal vol).
    weight : float
        Calibration weight (default 1.0). Set higher for liquid/important quotes.
    """

    expiry: float
    tenor: float
    normal_vol: float
    weight: float = 1.0


class HullWhiteCalibrator:
    """
    Calibrates Hull-White (a, σ) to ATM normal swaption vols.

    Parameters
    ----------
    discount_fn : callable
        P(0, T) → float. Discount factor from the initial yield curve.
    swaption_quotes : list of SwaptionQuote
        Market swaption quotes to calibrate to.
    freq : float
        Swap coupon frequency per year (default 2 for semi-annual).
    """

    def __init__(
        self,
        discount_fn: Callable[[float], float],
        swaption_quotes: list[SwaptionQuote],
        freq: float = 2.0,
    ):
        self.discount = discount_fn
        self.quotes = swaption_quotes
        self.freq = freq

    # ------------------------------------------------------------------
    # Analytical formulas
    # ------------------------------------------------------------------

    @staticmethod
    def B(a: float, t: float, T: float) -> float:
        """B(t,T) = (1 - e^{-a(T-t)}) / a."""
        return (1.0 - np.exp(-a * (T - t))) / a

    def annuity(self, T_exp: float, T_mat: float) -> float:
        """
        Forward annuity A(0; T_exp, T_mat) = Σ τ_i · P(0, T_exp + i/freq).
        """
        n = round((T_mat - T_exp) * self.freq)
        if n <= 0:
            return 1e-10
        pay_times = T_exp + np.arange(1, n + 1) / self.freq
        tau = 1.0 / self.freq
        return float(sum(tau * self.discount(t) for t in pay_times))

    def vp(self, a: float, sigma: float, T_exp: float) -> float:
        """
        Variance proxy for swaption pricing:
        v_p = (σ²/a²) · [T - 2B(0,T)/a + (1-e^{-2aT})/(2a)]
        """
        T = T_exp
        if a < 1e-6:
            # Limit a→0: Vasicek → σ²·T³/3
            return sigma**2 * T**3 / 3.0
        return (sigma**2 / a**2) * (
            T - 2.0 * self.B(a, 0.0, T) + (1.0 - np.exp(-2.0 * a * T)) / (2.0 * a)
        )

    def model_normal_vol(self, a: float, sigma: float, T_exp: float, T_mat: float) -> float:
        """
        HW model ATM normal (Bachelier) swaption vol in absolute terms.
        """
        Ann = self.annuity(T_exp, T_mat)
        if Ann < 1e-10:
            return 0.0
        vp_val = self.vp(a, sigma, T_exp)
        return sigma * np.sqrt(max(vp_val, 0.0)) / (Ann * sigma + 1e-20) * sigma
        # Simplified: σ_N = sqrt(vp) · P(0,T_exp) / Ann  (Brigo eq 3.71)
        # Exact: σ_N = sqrt(vp) * P(0, T_exp) / Ann  where vp uses only σ, not normalized
        # Re-derive:

    def _swaption_normal_vol(self, a: float, sigma: float, quote: SwaptionQuote) -> float:
        """HW ATM normal swaption vol formula (Brigo & Mercurio 2006, eq 3.71)."""
        T_exp = quote.expiry
        T_mat = quote.expiry + quote.tenor
        Ann = self.annuity(T_exp, T_mat)
        if Ann < 1e-10:
            return 0.0
        vp_val = self.vp(a, sigma, T_exp)
        P0T = self.discount(T_exp)
        return P0T / Ann * np.sqrt(max(vp_val, 0.0))

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(
        self,
        a0: float = 0.10,
        sigma0: float = 0.010,
        a_bounds: tuple = (0.001, 1.0),
        sigma_bounds: tuple = (0.0001, 0.10),
    ) -> dict:
        """
        Minimise weighted squared error between model and market normal vols.

        Parameters
        ----------
        a0, sigma0 : float
            Initial parameter guesses.
        a_bounds, sigma_bounds : tuples
            (lower, upper) bounds for optimisation.

        Returns
        -------
        result : dict with keys:
            a : calibrated mean reversion
            sigma : calibrated short-rate volatility
            rmse_bps : root-mean-squared error in basis points
            converged : bool
            details : optimizer output
        """
        if not _SCIPY_AVAILABLE:
            raise ImportError("scipy is required for calibration. pip install scipy")

        market_vols = np.array([q.normal_vol for q in self.quotes])
        weights = np.array([q.weight for q in self.quotes])

        def objective(params):
            a, sigma = params[0], params[1]
            model_vols = np.array([
                self._swaption_normal_vol(a, sigma, q) for q in self.quotes
            ])
            err = weights * (model_vols - market_vols) ** 2
            return float(np.sum(err))

        res = minimize(
            objective,
            x0=[a0, sigma0],
            method="L-BFGS-B",
            bounds=[a_bounds, sigma_bounds],
            options={"ftol": 1e-12, "gtol": 1e-8, "maxiter": 500},
        )

        a_cal, sigma_cal = res.x

        # Compute RMSE in bps
        model_vols = np.array([self._swaption_normal_vol(a_cal, sigma_cal, q) for q in self.quotes])
        rmse = float(np.sqrt(np.mean((model_vols - market_vols) ** 2))) * 10_000  # bps

        return {
            "a": float(a_cal),
            "sigma": float(sigma_cal),
            "rmse_bps": round(rmse, 2),
            "converged": bool(res.success),
            "model_vols_bps": (model_vols * 10_000).tolist(),
            "market_vols_bps": (market_vols * 10_000).tolist(),
            "details": res,
        }


def quick_sigma_from_historical(
    short_rate_vol_pa: float,
    a: float = 0.10,
) -> float:
    """
    Estimate Hull-White σ from observed historical short-rate volatility
    (annualised standard deviation of monthly rate changes × √12).

    Under HW, the stationary std of r(t) is:
        std_r = σ / √(2a)

    So: σ = std_r · √(2a)
    """
    return short_rate_vol_pa * np.sqrt(2.0 * a)


def example_swaption_surface(currency: str = "USD") -> list[SwaptionQuote]:
    """
    Synthetic ATM normal swaption vol surface (approximate May 2026 levels).

    These are illustrative — replace with live market data for production use.
    Vols in absolute normal terms (e.g. 0.006 = 60 bps normal vol).
    """
    surfaces = {
        "USD": [
            # expiry, tenor, normal vol
            SwaptionQuote(1, 1,  0.0080),
            SwaptionQuote(1, 5,  0.0090),
            SwaptionQuote(1, 10, 0.0095),
            SwaptionQuote(2, 5,  0.0085),
            SwaptionQuote(2, 10, 0.0090),
            SwaptionQuote(5, 10, 0.0080),
            SwaptionQuote(5, 20, 0.0075),
            SwaptionQuote(10, 10, 0.0065),
        ],
        "EUR": [
            SwaptionQuote(1, 5,  0.0060),
            SwaptionQuote(1, 10, 0.0065),
            SwaptionQuote(2, 10, 0.0062),
            SwaptionQuote(5, 10, 0.0058),
            SwaptionQuote(10, 10, 0.0050),
        ],
        "CNY": [
            SwaptionQuote(1, 5,  0.0040),
            SwaptionQuote(1, 10, 0.0045),
            SwaptionQuote(5, 10, 0.0038),
        ],
    }
    return surfaces.get(currency, surfaces["USD"])
