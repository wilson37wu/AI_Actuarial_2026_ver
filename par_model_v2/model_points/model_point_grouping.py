"""Model point grouping for par fund portfolios.

This module defines a configurable grouping framework that collapses a large
in-force portfolio into a smaller set of model points using actuarial keys.

Grouping keys include (as applicable):
- product_code
- issue_year band
- issue_age band
- duration band
- premium_term
- sum assured (SA) band
- time-to-retirement band (for pension)
- reversionary bonus accumulated SA band

The main entry point is ``group_to_model_points``, which takes a detailed
policy DataFrame (such as produced by ``mp_generator.generate_synthetic_policies``)
and returns a grouped model point DataFrame with representative values and
weights. An optional mapping to a "Prophet-style" layout is also provided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class Banding:
    """Generic banding configuration for a numeric field."""

    edges: List[float]

    def apply(self, values: np.ndarray) -> np.ndarray:
        """Return band index for each value based on ``edges``.

        Uses ``np.digitize`` with right=False so that edges are left-inclusive.
        """

        return np.digitize(values, self.edges, right=False)


@dataclass
class GroupingConfig:
    """Configuration for model point grouping.

    Attributes
    ----------
    issue_year_bands:
        Banding for issue year (e.g. [2005, 2010, 2015, 2020, 2025]).
    issue_age_bands:
        Banding for issue age.
    duration_bands:
        Banding for policy duration at valuation.
    sa_bands:
        Banding for sum assured.
    time_to_retirement_bands:
        Banding for time-to-retirement (pension only).
    rb_accum_sa_bands:
        Banding for reversionary bonus accumulated SA.
    """

    issue_year_bands: Banding
    issue_age_bands: Banding
    duration_bands: Banding
    sa_bands: Banding
    time_to_retirement_bands: Banding
    rb_accum_sa_bands: Banding


def _apply_bands(df: pd.DataFrame, cfg: GroupingConfig) -> pd.DataFrame:
    """Add band columns to the detailed portfolio DataFrame."""

    out = df.copy()

    out["issue_year_band"] = cfg.issue_year_bands.apply(out["issue_year"].to_numpy(dtype=float))
    out["issue_age_band"] = cfg.issue_age_bands.apply(out["issue_age"].to_numpy(dtype=float))
    out["duration_band"] = cfg.duration_bands.apply(out["duration"].to_numpy(dtype=float))
    out["sa_band"] = cfg.sa_bands.apply(out["sum_assured"].to_numpy(dtype=float))
    out["time_to_retirement_band"] = cfg.time_to_retirement_bands.apply(
        out["time_to_retirement"].to_numpy(dtype=float)
    )
    out["rb_accum_sa_band"] = cfg.rb_accum_sa_bands.apply(out["rb_accum_sa"].to_numpy(dtype=float))

    return out


def group_to_model_points(
    df_policies: pd.DataFrame,
    cfg: GroupingConfig,
    weight_column: str = "sum_assured",
) -> pd.DataFrame:
    """Group a detailed portfolio into model points.

    Parameters
    ----------
    df_policies:
        Detailed policy DataFrame as produced by ``generate_synthetic_policies``.
    cfg:
        Grouping configuration specifying banding for key fields.
    weight_column:
        Column name used as weight for aggregation (e.g. sum assured).

    Returns
    -------
    pd.DataFrame
        Grouped model point DataFrame with representative values and weights.
    """

    df = _apply_bands(df_policies, cfg)

    # Define grouping keys common to both products
    keys = [
        "product_code",
        "issue_year_band",
        "issue_age_band",
        "duration_band",
        "premium_term",
        "sa_band",
        "rb_accum_sa_band",
    ]

    # time_to_retirement_band only applies to pension, but we can include it
    # generally; for WL it will typically be 0-band only.
    keys.append("time_to_retirement_band")

    # Use groupby with weights to compute representative values
    grouped = df.groupby(keys, observed=True)

    records: List[Dict] = []

    for key_vals, grp in grouped:
        # Ensure key_vals is a tuple aligned with keys
        if not isinstance(key_vals, tuple):
            key_vals = (key_vals,)

        weight = grp[weight_column].to_numpy(dtype=float)
        total_weight = float(weight.sum())
        if total_weight <= 0.0:
            continue

        # Representative numeric fields as weighted means
        rep_issue_age = float(
            (grp["issue_age"].to_numpy(dtype=float) * weight).sum() / total_weight
        )
        rep_issue_year = float(
            (grp["issue_year"].to_numpy(dtype=float) * weight).sum() / total_weight
        )
        rep_duration = float((grp["duration"].to_numpy(dtype=float) * weight).sum() / total_weight)
        rep_sa = float((grp["sum_assured"].to_numpy(dtype=float) * weight).sum() / total_weight)
        rep_prem_term = float(
            (grp["premium_term"].to_numpy(dtype=float) * weight).sum() / total_weight
        )
        rep_rb_accum_sa = float(
            (grp["rb_accum_sa"].to_numpy(dtype=float) * weight).sum() / total_weight
        )
        rep_ttr = float(
            (grp["time_to_retirement"].to_numpy(dtype=float) * weight).sum() / total_weight
        )

        rec: Dict[str, object] = {
            "weight": total_weight,
            "product_code": grp["product_code"].iloc[0],
            "issue_year_band": grp["issue_year_band"].iloc[0],
            "issue_age_band": grp["issue_age_band"].iloc[0],
            "duration_band": grp["duration_band"].iloc[0],
            "premium_term": grp["premium_term"].iloc[0],
            "sa_band": grp["sa_band"].iloc[0],
            "time_to_retirement_band": grp["time_to_retirement_band"].iloc[0],
            "rb_accum_sa_band": grp["rb_accum_sa_band"].iloc[0],
            "rep_issue_age": rep_issue_age,
            "rep_issue_year": rep_issue_year,
            "rep_duration": rep_duration,
            "rep_sum_assured": rep_sa,
            "rep_premium_term": rep_prem_term,
            "rep_rb_accum_sa": rep_rb_accum_sa,
            "rep_time_to_retirement": rep_ttr,
        }

        records.append(rec)

    mp_df = pd.DataFrame.from_records(records)
    return mp_df


def to_prophet_layout(mp_df: pd.DataFrame) -> pd.DataFrame:
    """Map grouped model points to a simplified Prophet-style layout.

    This function renames and/or derives a subset of columns to be more
    compatible with a typical Prophet model point specification. The exact
    mapping will depend on the Prophet setup; here we provide a minimal and
    easily extensible version.
    """

    out = mp_df.copy()

    # Example mapping: these can be customised as needed
    rename_map = {
        "product_code": "PROD_CODE",
        "weight": "SUM_ASSURED_WEIGHT",
        "rep_issue_age": "ISSUE_AGE",
        "rep_issue_year": "ISSUE_YEAR",
        "rep_duration": "DURATION",
        "rep_premium_term": "PREM_TERM",
        "rep_sum_assured": "SA_REP",
        "rep_rb_accum_sa": "RB_ACCUM_SA_REP",
        "rep_time_to_retirement": "TTR_REP",
    }

    for src, dst in rename_map.items():
        if src in out.columns:
            out[dst] = out[src]

    return out


__all__ = ["Banding", "GroupingConfig", "group_to_model_points", "to_prophet_layout"]
