"""
Hull-White 1-Factor Interest Rate Model

Implements the Hull-White (extended Vasicek) model under the risk-neutral measure:

    dr(t) = [θ(t) - a·r(t)] dt + σ·dW(t)

where θ(t) is calibrated to fit the initial yield curve exactly (no-arbitrage).

Key properties:
- Closed-form zero-coupon bond prices
- Exact Ornstein-Uhlenbeck discretization (no Euler-Maruyama bias)
- Initial yield curve fitting via α(t) = f(0,t) + σ²/(2a²)·(1-e^{-at})²
- Supports multi-currency simulation with correlated Brownian motions

References:
    Hull & White (1990), "Pricing Interest-Rate Derivative Securities",
    Review of Financial Studies 3(4), 573-592.

    International Actuarial Association (IAA), Note on ESG Requirements for
    Insurance Companies (2013).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.interpolate import CubicSpline


class YieldCurve:
    """
    Initial risk-free yield curve used to calibrate Hull-White.

    Parameters
    ----------
    tenors : array-like
        Tenors in years, e.g. [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
    zero_rates : array-like
        Continuously-compounded zero rates (per annum) matching tenors
    currency : str
        Currency identifier, e.g. 'USD', 'EUR'
    """

    def __init__(
        self,
        tenors: np.ndarray,
        zero_rates: np.ndarray,
        currency: str = "USD",
    ):
        tenors = np.asarray(tenors, dtype=float)
        zero_rates = np.asarray(zero_rates, dtype=float)

        if np.any(tenors <= 0):
            raise ValueError("All tenors must be positive.")
        if tenors[0] > 0.001:
            # Prepend r(0) = short end approximation
            tenors = np.r_[1e-6, tenors]
            zero_rates = np.r_[zero_rates[0], zero_rates]

        self.currency = currency
        self._tenors = tenors
        self._zero_rates = zero_rates
        # Cubic spline on log-discount factors for smooth forward rates
        self._log_df_spline = CubicSpline(tenors, -zero_rates * tenors)

    def zero_rate(self, t: float | np.ndarray) -> np.ndarray:
        """Continuously-compounded zero rate R(0,t)."""
        t = np.asarray(t, dtype=float)
        t = np.maximum(t, 1e-9)
        return -self._log_df_spline(t) / t

    def discount_factor(self, t: float | np.ndarray) -> np.ndarray:
        """Risk-free discount factor P(0,t) = exp(-R(0,t)·t)."""
        t = np.maximum(np.asarray(t, dtype=float), 1e-9)
        return np.exp(self._log_df_spline(t))

    def instantaneous_forward(self, t: float | np.ndarray) -> np.ndarray:
        """
        Instantaneous forward rate f(0,t) = -∂ln P(0,t)/∂t.
        Uses the first derivative of the log-discount factor spline.
        """
        t = np.maximum(np.asarray(t, dtype=float), 1e-9)
        return -self._log_df_spline(t, 1)  # first derivative

    @classmethod
    def flat(cls, rate: float, currency: str = "USD") -> "YieldCurve":
        """Convenience constructor for a flat yield curve."""
        tenors = np.array([0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30], dtype=float)
        return cls(tenors, np.full_like(tenors, rate), currency=currency)

    @classmethod
    def nelson_siegel(
        cls,
        beta0: float,
        beta1: float,
        beta2: float,
        lambda_: float,
        currency: str = "USD",
    ) -> "YieldCurve":
        """
        Yield curve from Nelson-Siegel parameterization.

            R(t) = β0 + β1*(1-e^{-λt})/(λt) + β2*[(1-e^{-λt})/(λt) - e^{-λt}]

        Typical parameters:
            USD (current): beta0=0.045, beta1=-0.01, beta2=0.02, lambda_=0.4
            EUR: beta0=0.03, beta1=-0.005, beta2=0.015, lambda_=0.4
        """
        tenors = np.array([0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30], dtype=float)
        lt = lambda_ * tenors
        factor1 = (1 - np.exp(-lt)) / lt
        factor2 = factor1 - np.exp(-lt)
        rates = beta0 + beta1 * factor1 + beta2 * factor2
        return cls(tenors, rates, currency=currency)


@dataclass
class HullWhite1FParams:
    """
    Parameters for the Hull-White 1-factor model.

    a : float
        Mean-reversion speed (> 0). Typical range: 0.03 - 0.20.
        Higher values → faster reversion, less long-rate volatility.
    sigma : float
        Short-rate volatility (> 0). Typical range: 0.003 - 0.020.
    floor : float
        Hard floor on nominal short rate (default: -0.02, i.e. -2%).
        Set to 0.0 for non-negative rates (e.g. CNY regulatory constraint).
    """

    a: float = 0.10
    sigma: float = 0.010
    floor: float = -0.02


# Sensible default parameters per currency (calibrated to approximate
# historical swaption implied volatilities as of 2024-2025)
DEFAULT_HW_PARAMS: dict[str, HullWhite1FParams] = {
    "USD": HullWhite1FParams(a=0.10, sigma=0.012, floor=-0.01),
    "EUR": HullWhite1FParams(a=0.08, sigma=0.010, floor=-0.01),
    "GBP": HullWhite1FParams(a=0.10, sigma=0.012, floor=-0.01),
    "JPY": HullWhite1FParams(a=0.05, sigma=0.006, floor=-0.01),
    "CNY": HullWhite1FParams(a=0.10, sigma=0.008, floor=0.00),
}

# Approximate initial yield curves (May 2026 proxy) — Nelson-Siegel style
DEFAULT_YIELD_CURVES: dict[str, dict] = {
    "USD": dict(beta0=0.045, beta1=-0.008, beta2=0.015, lambda_=0.5),
    "EUR": dict(beta0=0.030, beta1=-0.005, beta2=0.010, lambda_=0.5),
    "GBP": dict(beta0=0.045, beta1=-0.006, beta2=0.012, lambda_=0.5),
    "JPY": dict(beta0=0.010, beta1=0.002, beta2=0.005, lambda_=0.4),
    "CNY": dict(beta0=0.025, beta1=0.003, beta2=0.008, lambda_=0.4),
}


class HullWhite1F:
    """
    Hull-White 1-factor stochastic interest rate model.

    Under the risk-neutral measure the short rate satisfies:
        r(t) = x(t) + α(t)

    where x(t) is a zero-mean OU process:
        dx = -a·x dt + σ dW

    and the deterministic shift α(t) fits the initial term structure:
        α(t) = f(0,t) + σ²/(2a²) · (1 - e^{-at})²

    Exact OU discretization (no approximation error):
        x_{t+Δ} = x_t · e^{-aΔ} + σ · √[(1 - e^{-2aΔ})/(2a)] · Z

    Closed-form ZCB price:
        P(t,T) = (P(0,T)/P(0,t)) · exp[B(t,T)·α(t) - B(t,T)·r(t) - V(t,T)]

    where:
        B(t,T) = (1 - e^{-a(T-t)}) / a
        V(t,T) = σ²/(4a) · B(t,T)² · (1 - e^{-2at})

    Parameters
    ----------
    yield_curve : YieldCurve
        Initial risk-free term structure.
    params : HullWhite1FParams
        Model parameters a and σ.
    """

    def __init__(self, yield_curve: YieldCurve, params: Optional[HullWhite1FParams] = None):
        self.curve = yield_curve
        self.p = params or DEFAULT_HW_PARAMS.get(yield_curve.currency, HullWhite1FParams())

    # ------------------------------------------------------------------
    # Analytical building blocks
    # ------------------------------------------------------------------

    def B(self, t: np.ndarray, T: np.ndarray) -> np.ndarray:
        """Bond price sensitivity B(t,T) = (1 - e^{-a(T-t)}) / a."""
        a = self.p.a
        tau = np.asarray(T, dtype=float) - np.asarray(t, dtype=float)
        return (1.0 - np.exp(-a * tau)) / a

    def alpha(self, t: np.ndarray) -> np.ndarray:
        """
        Deterministic drift α(t) = f(0,t) + σ²/(2a²) · (1-e^{-at})².

        This is the key that aligns the model with the initial yield curve.
        """
        t = np.asarray(t, dtype=float)
        a, sigma = self.p.a, self.p.sigma
        f0t = self.curve.instantaneous_forward(t)
        correction = (sigma**2 / (2 * a**2)) * (1.0 - np.exp(-a * t)) ** 2
        return f0t + correction

    def zcb_price(
        self,
        t: float | np.ndarray,
        T: float | np.ndarray,
        r_t: np.ndarray,
    ) -> np.ndarray:
        """
        Closed-form zero-coupon bond price P(t,T) given r(t).

        Parameters
        ----------
        t : float or array
            Current time in years.
        T : float or array
            Bond maturity in years (T > t).
        r_t : array, shape (n_trials,)
            Short rate at time t for each trial.

        Returns
        -------
        prices : array, shape (n_trials,)
            ZCB prices in [0,1].
        """
        t = np.asarray(t, dtype=float)
        T = np.asarray(T, dtype=float)
        r_t = np.asarray(r_t, dtype=float)

        a, sigma = self.p.a, self.p.sigma

        P0T = self.curve.discount_factor(T)
        P0t = self.curve.discount_factor(t)
        Bval = self.B(t, T)
        alpha_t = self.alpha(t)

        # V(t,T): conditional variance contribution
        V = (sigma**2 / (4 * a)) * Bval**2 * (1.0 - np.exp(-2.0 * a * t))

        log_price = (
            np.log(P0T / P0t)
            + Bval * alpha_t
            - Bval * r_t
            - V
        )
        return np.exp(log_price)

    def zero_curve(
        self,
        t: float,
        r_t: np.ndarray,
        tenors: np.ndarray,
    ) -> np.ndarray:
        """
        Full zero curve at time t for a vector of tenors.

        Returns
        -------
        rates : array, shape (n_trials, n_tenors)
            Continuously-compounded zero rates.
        """
        r_t = np.asarray(r_t, dtype=float)  # (n_trials,)
        tenors = np.asarray(tenors, dtype=float)  # (n_tenors,)

        prices = np.column_stack([
            self.zcb_price(t, t + tau, r_t) for tau in tenors
        ])  # (n_trials, n_tenors)
        return -np.log(prices) / tenors[np.newaxis, :]

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def simulate(
        self,
        n_trials: int,
        n_steps: int,
        dt: float,
        z: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Simulate short-rate paths using exact OU discretization.

        Parameters
        ----------
        n_trials : int
            Number of Monte Carlo trials.
        n_steps : int
            Number of time steps (excluding t=0).
        dt : float
            Time step in years (e.g. 1/12 for monthly).
        z : array, shape (n_trials, n_steps), optional
            Pre-drawn standard normal variates. If None, drawn internally.
            Pass correlated z values for multi-currency simulation.

        Returns
        -------
        r : array, shape (n_trials, n_steps+1)
            Short rate paths. Column 0 is r(0) = f(0,0).
        """
        a, sigma = self.p.a, self.p.sigma
        floor = self.p.floor

        if z is None:
            rng = np.random.default_rng()
            z = rng.standard_normal((n_trials, n_steps))

        # Exact OU step parameters
        e_a_dt = np.exp(-a * dt)
        std_step = sigma * np.sqrt((1.0 - np.exp(-2.0 * a * dt)) / (2.0 * a))

        # x(0) = r(0) - α(0): start deviations at zero
        alpha_0 = self.alpha(np.array([0.0]))[0]
        r0 = self.curve.zero_rate(np.array([1e-6]))[0]  # short end rate
        x0 = r0 - alpha_0

        # Precompute alpha grid for all future times
        times = np.arange(n_steps + 1) * dt
        alpha_grid = self.alpha(times)  # (n_steps+1,)

        # Simulate x process (zero-mean OU)
        x = np.empty((n_trials, n_steps + 1))
        x[:, 0] = x0

        for step in range(n_steps):
            x[:, step + 1] = x[:, step] * e_a_dt + std_step * z[:, step]

        # r(t) = x(t) + α(t), apply floor
        r = x + alpha_grid[np.newaxis, :]
        r = np.maximum(r, floor)

        return r


def build_default_hw_models() -> dict[str, HullWhite1F]:
    """
    Build Hull-White models for the five major currencies using
    approximate May 2026 yield curves.
    """
    models = {}
    for ccy, ns_params in DEFAULT_YIELD_CURVES.items():
        curve = YieldCurve.nelson_siegel(currency=ccy, **ns_params)
        hw_params = DEFAULT_HW_PARAMS[ccy]
        models[ccy] = HullWhite1F(curve, hw_params)
    return models
