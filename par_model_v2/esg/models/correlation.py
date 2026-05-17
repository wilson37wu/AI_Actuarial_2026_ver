"""
Multi-Asset Correlation Structure for Global ESG

Manages the correlation matrix between stochastic drivers and provides
correlated standard-normal variates via Cholesky decomposition.

Factor ordering (used throughout the global ESG):
    [r_USD, r_EUR, r_GBP, r_JPY, r_CNY,   # 5 short rates
     E_USD, E_EUR, E_GBP, E_JPY, E_CNY]   # 5 equity indices

The matrix is sourced from approximate empirical estimates calibrated to
historical data (2010-2024). Users can override with their own estimates.

References:
    IAA Note on ESG for insurance (2013), Section 4: Correlation handling.
    Solvency II Standard Formula correlation matrices (EIOPA, 2015).
"""

from __future__ import annotations

from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Default correlation matrix
# Factor order: r_USD, r_EUR, r_GBP, r_JPY, r_CNY, E_USD, E_EUR, E_GBP, E_JPY, E_CNY
# ---------------------------------------------------------------------------
FACTOR_NAMES = ["r_USD", "r_EUR", "r_GBP", "r_JPY", "r_CNY",
                "E_USD", "E_EUR", "E_GBP", "E_JPY", "E_CNY"]

_N = len(FACTOR_NAMES)

# Approximate pairwise correlations
_CORR_RAW = np.array([
    # r_USD  r_EUR  r_GBP  r_JPY  r_CNY  E_USD  E_EUR  E_GBP  E_JPY  E_CNY
    [1.00,  0.65,  0.60,  0.25,  0.20, -0.15, -0.12, -0.12, -0.08, -0.05],  # r_USD
    [0.65,  1.00,  0.75,  0.30,  0.15, -0.12, -0.15, -0.13, -0.08, -0.05],  # r_EUR
    [0.60,  0.75,  1.00,  0.28,  0.18, -0.12, -0.14, -0.16, -0.08, -0.04],  # r_GBP
    [0.25,  0.30,  0.28,  1.00,  0.15, -0.08, -0.08, -0.08, -0.12, -0.05],  # r_JPY
    [0.20,  0.15,  0.18,  0.15,  1.00, -0.05, -0.04, -0.04, -0.06, -0.10],  # r_CNY
    [-0.15,-0.12, -0.12, -0.08, -0.05,  1.00,  0.85,  0.80,  0.55,  0.40],  # E_USD
    [-0.12,-0.15, -0.14, -0.08, -0.04,  0.85,  1.00,  0.82,  0.55,  0.40],  # E_EUR
    [-0.12,-0.13, -0.16, -0.08, -0.04,  0.80,  0.82,  1.00,  0.52,  0.38],  # E_GBP
    [-0.08,-0.08, -0.08, -0.12, -0.06,  0.55,  0.55,  0.52,  1.00,  0.45],  # E_JPY
    [-0.05,-0.05, -0.04, -0.05, -0.10,  0.40,  0.40,  0.38,  0.45,  1.00],  # E_CNY
], dtype=float)


def _nearest_psd(A: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    """
    Project a symmetric matrix onto the nearest positive semi-definite cone
    using eigenvalue clipping (Higham 1988 approximation).
    """
    eigvals, eigvecs = np.linalg.eigh(A)
    eigvals = np.maximum(eigvals, eps)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T


def _make_correlation(raw: np.ndarray) -> np.ndarray:
    """Ensure the correlation matrix is symmetric PSD with unit diagonal."""
    C = (raw + raw.T) / 2.0
    np.fill_diagonal(C, 1.0)
    return _nearest_psd(C)


class CorrelationMatrix:
    """
    Correlation matrix and Cholesky generator for multi-asset ESG.

    Parameters
    ----------
    corr : array-like, shape (n_factors, n_factors), optional
        Custom correlation matrix. Must be symmetric PD. Defaults to the
        built-in global estimate.
    factor_names : list of str, optional
        Names for each factor row/column.
    """

    def __init__(
        self,
        corr: Optional[np.ndarray] = None,
        factor_names: Optional[list[str]] = None,
    ):
        self.factor_names = factor_names or FACTOR_NAMES
        n = len(self.factor_names)

        raw = np.asarray(corr, dtype=float) if corr is not None else _CORR_RAW
        if raw.shape != (n, n):
            raise ValueError(f"Correlation matrix must be ({n},{n}), got {raw.shape}")

        self._corr = _make_correlation(raw)
        self._chol = np.linalg.cholesky(self._corr)  # lower triangular

    @property
    def matrix(self) -> np.ndarray:
        """Full correlation matrix (n_factors × n_factors)."""
        return self._corr.copy()

    def factor_index(self, name: str) -> int:
        return self.factor_names.index(name)

    def draw_correlated(
        self,
        n_trials: int,
        n_steps: int,
        rng: Optional[np.random.Generator] = None,
    ) -> np.ndarray:
        """
        Draw correlated standard-normal variates.

        Returns
        -------
        z : array, shape (n_trials, n_steps, n_factors)
            Correlated standard-normal variates.
            z[:, :, i] corresponds to factor factor_names[i].
        """
        if rng is None:
            rng = np.random.default_rng()

        n_factors = len(self.factor_names)
        z_indep = rng.standard_normal((n_trials, n_steps, n_factors))

        # Apply Cholesky: correlated = indep @ L^T
        z_corr = z_indep @ self._chol.T  # (n_trials, n_steps, n_factors)
        return z_corr

    def validate(self) -> dict:
        """Run basic sanity checks on the correlation matrix."""
        eigvals = np.linalg.eigvalsh(self._corr)
        results = {
            "symmetric": np.allclose(self._corr, self._corr.T),
            "unit_diagonal": np.allclose(np.diag(self._corr), 1.0),
            "positive_definite": bool(np.all(eigvals > 0)),
            "min_eigenvalue": float(eigvals.min()),
            "all_in_[-1,1]": bool(np.all(np.abs(self._corr) <= 1.0 + 1e-10)),
        }
        return results
