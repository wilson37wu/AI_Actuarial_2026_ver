"""Synthetic model point generator for par fund products.

This module generates a large portfolio of synthetic policies (≥ 1M) with a
predefined product mix and distributions for key attributes. The output is
written to a Parquet file for downstream actuarial modelling.

The implementation is intentionally simple and fully vectorised with NumPy to
be fast for large portfolios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class PortfolioSpec:
    """Specification of the synthetic portfolio to generate."""

    n_policies: int = 1_000_000
    wl_share: float = 0.60  # whole life
    pension_share: float = 0.40  # deferred pension

    # Issue year cohort parameters (2016-2025)
    issue_year_start: int = 2016
    issue_year_end: int = 2025
    issue_year_weights: Optional[Dict[int, float]] = None  # If None, use default

    # Demographic distributions
    gender_probs: Dict[str, float] = field(default_factory=lambda: {"M": 0.55, "F": 0.45})
    uw_class_probs: Dict[str, float] = field(default_factory=lambda: {"STD": 0.90, "SUB": 0.10})
    policy_status_probs: Dict[str, float] = field(
        default_factory=lambda: {"INFORCE": 0.97, "PAIDUP": 0.03}
    )

    # Premium term choices
    premium_term_choices_wl: List[int] = field(default_factory=lambda: [5, 10, 15, 20])
    premium_term_probs_wl: List[float] = field(default_factory=lambda: [0.15, 0.30, 0.35, 0.20])
    include_pay_to_99: bool = False
    pay_to_99_prob: float = 0.10  # If included, adjust other probs proportionally

    premium_term_choices_pen: List[int] = field(default_factory=lambda: [10, 15, 20, 25, 30])
    premium_term_probs_pen: List[float] = field(
        default_factory=lambda: [0.15, 0.25, 0.30, 0.20, 0.10]
    )
    include_pay_to_retirement: bool = False
    pay_to_retirement_prob: float = 0.15  # If included

    # Premium calculation factor for pension
    pension_contrib_factor: float = 1.0

    # Banding definitions
    sa_band_edges: List[float] = field(
        default_factory=lambda: [0, 100_000, 300_000, 1_000_000, float("inf")]
    )
    sa_band_labels: List[str] = field(
        default_factory=lambda: ["SA_0_100K", "SA_100K_300K", "SA_300K_1M", "SA_1M_PLUS"]
    )
    prem_band_edges: List[float] = field(
        default_factory=lambda: [0, 10_000, 30_000, 100_000, float("inf")]
    )
    prem_band_labels: List[str] = field(
        default_factory=lambda: ["PREM_0_10K", "PREM_10K_30K", "PREM_30K_100K", "PREM_100K_PLUS"]
    )

    # Random seed for reproducibility
    seed: Optional[int] = None


def _sample_product_codes(spec: PortfolioSpec) -> np.ndarray:
    n = int(spec.n_policies)
    probs = np.array([spec.wl_share, spec.pension_share], dtype=float)
    probs = probs / probs.sum()
    products = np.array(["WL", "PEN"], dtype=object)
    return np.random.choice(products, size=n, p=probs)


def _sample_issue_ages(n: int) -> np.ndarray:
    """Sample issue ages with a plausible distribution.

    For simplicity, use a mixture of adult ages with more mass in mid-range.
    """

    # Triangular-like distribution between 20 and 65 with mode at 40
    u = np.random.rand(n)
    age = np.where(
        u < 0.5, 20 + np.sqrt(u * 0.5) * (40 - 20) * 2, 40 + (1 - np.sqrt((1 - u) * 2)) * (65 - 40)
    )
    return age.astype(int)


def _sample_issue_years_weighted(
    n: int, years: List[int], weights: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Sample issue years with specified weights.

    Parameters
    ----------
    n : int
        Number of samples
    years : List[int]
        List of issue years
    weights : np.ndarray
        Probability weights (must sum to 1)
    rng : np.random.Generator
        Random number generator

    Returns
    -------
    np.ndarray
        Sampled issue years
    """
    # Normalize weights to ensure they sum to 1
    weights = np.array(weights, dtype=float)
    weights = weights / weights.sum()
    return rng.choice(years, size=n, p=weights)


def _sample_sa_bands(n: int) -> np.ndarray:
    """Sample sum assured using rough bands (in thousands)."""

    bands = np.array([50_000, 100_000, 200_000, 500_000, 1_000_000], dtype=float)
    probs = np.array([0.25, 0.35, 0.25, 0.10, 0.05], dtype=float)
    probs = probs / probs.sum()
    idx = np.random.choice(len(bands), size=n, p=probs)
    # Add some randomness within band (±20%)
    jitter = 0.8 + 0.4 * np.random.rand(n)
    return (bands[idx] * jitter).astype(float)


def _sample_premium_term(
    n: int,
    product_codes: np.ndarray,
    issue_ages: np.ndarray,
    retirement_ages: np.ndarray,
    spec: PortfolioSpec,
    rng: np.random.Generator,
) -> np.ndarray:
    """Sample premium term in years, depending on product type.

    Supports special values:
    - For WL: PAY_TO_99 (premium term = 99 - issue_age)
    - For PEN: PAY_TO_RETIREMENT (premium term = retirement_age - issue_age)
    """

    term = np.empty(n, dtype=int)

    # WL: shorter premium terms typical
    mask_wl = product_codes == "WL"
    n_wl = int(mask_wl.sum())
    if n_wl > 0:
        choices = spec.premium_term_choices_wl.copy()
        probs = np.array(spec.premium_term_probs_wl, dtype=float)

        if spec.include_pay_to_99:
            # Add PAY_TO_99 option and adjust probabilities
            choices.append(-99)  # Special marker
            probs = probs * (1 - spec.pay_to_99_prob)
            probs = np.append(probs, spec.pay_to_99_prob)

        probs = probs / probs.sum()
        sampled = rng.choice(choices, size=n_wl, p=probs)

        # Replace -99 with actual pay-to-99 terms
        mask_pay_to_99 = sampled == -99
        if mask_pay_to_99.any():
            ages_wl = issue_ages[mask_wl]
            sampled[mask_pay_to_99] = np.maximum(1, 99 - ages_wl[mask_pay_to_99])

        term[mask_wl] = sampled

    # Pension: longer or to-retirement premium terms
    mask_pen = product_codes == "PEN"
    n_pen = int(mask_pen.sum())
    if n_pen > 0:
        choices = spec.premium_term_choices_pen.copy()
        probs = np.array(spec.premium_term_probs_pen, dtype=float)

        if spec.include_pay_to_retirement:
            # Add PAY_TO_RETIREMENT option
            choices.append(-999)  # Special marker
            probs = probs * (1 - spec.pay_to_retirement_prob)
            probs = np.append(probs, spec.pay_to_retirement_prob)

        probs = probs / probs.sum()
        sampled = rng.choice(choices, size=n_pen, p=probs)

        # Replace -999 with actual pay-to-retirement terms
        mask_pay_to_ret = sampled == -999
        if mask_pay_to_ret.any():
            ages_pen = issue_ages[mask_pen]
            ret_ages_pen = retirement_ages[mask_pen]
            sampled[mask_pay_to_ret] = np.maximum(
                1, ret_ages_pen[mask_pay_to_ret] - ages_pen[mask_pay_to_ret]
            )

        term[mask_pen] = sampled

    return term


def _sample_rb_accum_sa(n: int, sa: np.ndarray, product_codes: np.ndarray) -> np.ndarray:
    """Sample reversionary bonus accumulated SA as a fraction of base SA."""

    base_ratio = np.random.beta(a=2.0, b=5.0, size=n)  # mostly < 0.5
    # Pension typically has lower accumulated RB
    factor = np.where(product_codes == "PEN", 0.5, 1.0)
    ratio = base_ratio * factor
    return sa * ratio


def _sample_retirement_age(n: int, rng: np.random.Generator) -> np.ndarray:
    """Sample retirement ages (for pension) between 55 and 70."""

    return rng.integers(55, 71, size=n)


def _sample_gender(n: int, probs: Dict[str, float], rng: np.random.Generator) -> np.ndarray:
    """Sample gender with specified probabilities."""
    genders = list(probs.keys())
    weights = np.array([probs[g] for g in genders], dtype=float)
    weights = weights / weights.sum()
    return rng.choice(genders, size=n, p=weights)


def _sample_uw_class(n: int, probs: Dict[str, float], rng: np.random.Generator) -> np.ndarray:
    """Sample underwriting class with specified probabilities."""
    classes = list(probs.keys())
    weights = np.array([probs[c] for c in classes], dtype=float)
    weights = weights / weights.sum()
    return rng.choice(classes, size=n, p=weights)


def _sample_policy_status(n: int, probs: Dict[str, float], rng: np.random.Generator) -> np.ndarray:
    """Sample policy status with specified probabilities."""
    statuses = list(probs.keys())
    weights = np.array([probs[s] for s in statuses], dtype=float)
    weights = weights / weights.sum()
    return rng.choice(statuses, size=n, p=weights)


def _calculate_annual_premium(
    product_codes: np.ndarray,
    sum_assured: np.ndarray,
    premium_terms: np.ndarray,
    spec: PortfolioSpec,
) -> np.ndarray:
    """Calculate annual premium based on product type.

    - WL: Simple level annual premium = sum_assured / premium_term
    - PEN: Monthly contribution model = (sum_assured / premium_term) / 12 * factor, then annualize
    """
    annual_prem = np.zeros(len(product_codes), dtype=float)

    # WL: level annual premium
    mask_wl = product_codes == "WL"
    if mask_wl.any():
        annual_prem[mask_wl] = sum_assured[mask_wl] / np.maximum(1, premium_terms[mask_wl])

    # PEN: monthly contribution model
    mask_pen = product_codes == "PEN"
    if mask_pen.any():
        monthly_contrib = (
            (sum_assured[mask_pen] / np.maximum(1, premium_terms[mask_pen]))
            / 12
            * spec.pension_contrib_factor
        )
        annual_prem[mask_pen] = monthly_contrib * 12

    return annual_prem


def _assign_band(values: np.ndarray, edges: List[float], labels: List[str]) -> np.ndarray:
    """Assign band labels based on value ranges.

    Parameters
    ----------
    values : np.ndarray
        Values to categorize
    edges : List[float]
        Bin edges (must have len = len(labels) + 1)
    labels : List[str]
        Labels for each bin

    Returns
    -------
    np.ndarray
        Array of band labels
    """
    bins = np.digitize(values, edges[1:])  # Skip first edge (0)
    bins = np.clip(bins, 0, len(labels) - 1)  # Ensure within bounds
    return np.array([labels[i] for i in bins], dtype=object)


def _get_default_issue_year_weights(years: List[int]) -> np.ndarray:
    """Get default issue year weights ensuring 2022-2025 sum to 0.50.

    Distribution:
    2016: 0.05, 2017: 0.05, 2018: 0.06, 2019: 0.07, 2020: 0.08, 2021: 0.19
    2022: 0.12, 2023: 0.13, 2024: 0.12, 2025: 0.13
    (2022-2025 sum = 0.50, earlier years sum = 0.50)
    """
    weights_map = {
        2016: 0.05,
        2017: 0.05,
        2018: 0.06,
        2019: 0.07,
        2020: 0.08,
        2021: 0.19,
        2022: 0.12,
        2023: 0.13,
        2024: 0.12,
        2025: 0.13,
    }

    weights = np.array([weights_map.get(year, 0.0) for year in years], dtype=float)
    # Normalize to ensure sum = 1
    weights = weights / weights.sum()
    return weights


def generate_synthetic_policies(spec: PortfolioSpec, parquet_path: str) -> pd.DataFrame:
    """Generate a synthetic portfolio and write it to Parquet.

    Parameters
    ----------
    spec:
        Portfolio specification, including portfolio size and product mix.
    parquet_path:
        File path where the generated portfolio will be written as Parquet.

    Returns
    -------
    pd.DataFrame
        DataFrame of individual policies (also written to Parquet).
    """

    # Initialize random number generator for reproducibility
    rng = np.random.default_rng(spec.seed)

    # Set legacy random seed for backward compatibility with old functions
    if spec.seed is not None:
        np.random.seed(spec.seed)

    n = int(spec.n_policies)

    # Sample existing attributes
    product_code = _sample_product_codes(spec)
    issue_age = _sample_issue_ages(n)

    # Sample issue years with cohort weighting
    years = list(range(spec.issue_year_start, spec.issue_year_end + 1))
    if spec.issue_year_weights is not None:
        weights = np.array([spec.issue_year_weights.get(y, 0.0) for y in years], dtype=float)
    else:
        weights = _get_default_issue_year_weights(years)
    issue_year = _sample_issue_years_weighted(n, years, weights, rng)

    sa = _sample_sa_bands(n)
    retirement_age = _sample_retirement_age(n, rng)

    # Sample premium term (needs issue_age and retirement_age for special cases)
    premium_term = _sample_premium_term(n, product_code, issue_age, retirement_age, spec, rng)

    rb_accum_sa = _sample_rb_accum_sa(n, sa, product_code)

    # Duration at valuation date (assume valuation year 2025)
    valuation_year = 2025
    duration = np.maximum(0, valuation_year - issue_year)

    # Time to retirement for pension products; zero for WL
    time_to_retirement = np.maximum(0, retirement_age - (issue_age + duration))
    time_to_retirement = np.where(product_code == "PEN", time_to_retirement, 0)

    # Sample new demographic attributes
    gender = _sample_gender(n, spec.gender_probs, rng)
    uw_class = _sample_uw_class(n, spec.uw_class_probs, rng)
    policy_status = _sample_policy_status(n, spec.policy_status_probs, rng)

    # Calculate annual premium
    annual_premium = _calculate_annual_premium(product_code, sa, premium_term, spec)

    # Assign bands
    sa_band = _assign_band(sa, spec.sa_band_edges, spec.sa_band_labels)
    premium_band = _assign_band(annual_premium, spec.prem_band_edges, spec.prem_band_labels)

    df = pd.DataFrame(
        {
            "policy_id": np.arange(1, n + 1, dtype=np.int64),
            "product_code": product_code,
            "issue_age": issue_age,
            "issue_year": issue_year,
            "duration": duration,
            "sum_assured": sa,
            "premium_term": premium_term,
            "rb_accum_sa": rb_accum_sa,
            "retirement_age": retirement_age,
            "time_to_retirement": time_to_retirement,
            # New columns for table-driven assumptions
            "gender": gender,
            "uw_class": uw_class,
            "policy_status": policy_status,
            "annual_premium": annual_premium,
            "sa_band": sa_band,
            "premium_band": premium_band,
        }
    )

    # Write to Parquet for downstream use
    df.to_parquet(parquet_path, index=False)
    return df


__all__ = ["PortfolioSpec", "generate_synthetic_policies"]
