"""Deterministic liability valuation for par fund products.

This module calculates the Guaranteed Present Value (GPV) of participating
insurance policies using deterministic assumptions for interest rates and
mortality. It supports whole-life and deferred pension products.

The GPV represents the present value of guaranteed benefits minus premiums,
calculated under a deterministic scenario (no stochastic variation).

Key Features
------------
- Per-policy GPV calculation with PV breakdown
- Portfolio-level batch processing with aggregated results
- Monthly cash-flow schedules per policy
- Consolidated monthly cash flows across entire portfolio

Usage Example
-------------
>>> import pandas as pd
>>> from par_model_v2.liabilities import value_portfolio
>>>
>>> df = pd.read_parquet('synthetic_portfolio.parquet')
>>> df_result, aggregate_cf, summary = value_portfolio(
...     df, discount_rate=0.03, output_dir='results'
... )
>>> print(f"Total GPV: {summary['total_gpv']:,.2f}")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from par_model_v2.assumptions import AssumptionProvider, get_premium_band, get_sa_band
from par_model_v2.assumptions.banding import derive_annual_premium

# ---------------------------------------------------------------------------
# Mortality utilities
# ---------------------------------------------------------------------------


def default_mortality_qx(age: float) -> float:
    """Default mortality rate approximation using exponential force.

    Uses the formula: q_x = 0.0005 × exp(0.08 × (x - 20)) for ages ≥ 20.
    For ages < 20, uses a flat low rate.

    Parameters
    ----------
    age:
        Attained age in years.

    Returns
    -------
    float
        Annual mortality rate q_x (probability of death within one year).
    """

    if age < 20:
        return 0.0001
    return min(0.0005 * np.exp(0.08 * (age - 20)), 1.0)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DeterministicAssumptions:
    """Deterministic valuation assumptions.

    Attributes
    ----------
    discount_rate:
        Annual discount rate for present value calculations (e.g., 0.03 for 3%).
    expense_loading:
        Expense loading as a fraction of premiums (e.g., 0.05 for 5%).
    rb_growth_rate:
        Annual growth rate for reversionary bonus accumulation (e.g., 0.02 for 2%).
    surrender_rate:
        Annual surrender/lapse rate (e.g., 0.01 for 1% p.a.).
    mortality_function:
        Callable that returns q_x given age. If None, uses ``default_mortality_qx``.
    """

    discount_rate: float = 0.03
    expense_loading: float = 0.05
    rb_growth_rate: float = 0.02
    surrender_rate: float = 0.01
    mortality_function: Optional[Callable[[float], float]] = None

    def __post_init__(self) -> None:
        if self.mortality_function is None:
            self.mortality_function = default_mortality_qx


# ---------------------------------------------------------------------------
# GPV calculation
# ---------------------------------------------------------------------------


def calculate_gpv(
    policy_row: Dict,
    discount_rate: float = 0.03,
    mortality_function: Optional[Callable[[float], float]] = None,
    expense_loading: float = 0.05,
    rb_growth_rate: float = 0.02,
    valuation_year: int = 2025,
    max_projection_years: int = 100,
    provider: Optional[AssumptionProvider] = None,
) -> Dict[str, float]:
    """Calculate Guaranteed Present Value (GPV) for a single policy.

    This function projects premiums and benefits deterministically and computes
    their present values. The GPV is defined as:

        GPV = PV(Benefits) - PV(Premiums)

    Parameters
    ----------
    policy_row:
        Dictionary or dict-like object with policy attributes:
        - ``product_code``: "WL" or "PEN"
        - ``issue_age``: Age at issue
        - ``sum_assured``: Base sum assured
        - ``premium_term``: Premium payment term in years
        - ``issue_year``: Year of issue
        - ``rb_accum_sa``: Accumulated reversionary bonus SA at valuation
        - ``retirement_age``: Retirement age (for pension products)
    discount_rate:
        Annual discount rate (e.g., 0.03 for 3% p.a.).
    mortality_function:
        Optional callable returning q_x given age. If None, uses
        ``default_mortality_qx``.
    expense_loading:
        Expense loading as a fraction of premiums.
    rb_growth_rate:
        Annual growth rate for reversionary bonus SA.
    valuation_year:
        Valuation date year (default 2025).
    max_projection_years:
        Maximum projection horizon in years (default 100).

    Returns
    -------
    dict
        Dictionary with keys:
        - ``PV_premiums``: Present value of premiums
        - ``PV_benefits``: Present value of benefits
        - ``GPV``: Guaranteed present value (PV_benefits - PV_premiums)
        - ``duration_at_val``: Policy duration at valuation
        - ``age_at_val``: Attained age at valuation
    """

    # Extract policy fields
    product_code = str(policy_row.get("product_code", "WL")).upper()
    issue_age = int(policy_row.get("issue_age", 30))
    sum_assured = float(policy_row.get("sum_assured", 100_000))
    premium_term = int(policy_row.get("premium_term", 20))
    issue_year = int(policy_row.get("issue_year", 2020))
    rb_accum_sa = float(policy_row.get("rb_accum_sa", 0.0))
    retirement_age = int(policy_row.get("retirement_age", 65))

    # Set up assumptions (backward compatible)
    if provider is None:
        assumptions = DeterministicAssumptions(
            discount_rate=discount_rate,
            expense_loading=expense_loading,
            rb_growth_rate=rb_growth_rate,
            mortality_function=mortality_function,
        )
        use_tables = False
    else:
        assumptions = None
        use_tables = True

    # Duration and attained age at valuation
    duration = max(0, valuation_year - issue_year)
    age_at_val = issue_age + duration

    # Determine projection horizon
    if product_code == "PEN":
        horizon = min(max_projection_years, retirement_age - age_at_val + 5)
    else:
        horizon = min(max_projection_years, 120 - age_at_val)

    if horizon <= 0:
        # Policy has matured or expired
        return {
            "PV_premiums": 0.0,
            "PV_benefits": 0.0,
            "GPV": 0.0,
            "duration_at_val": duration,
            "age_at_val": age_at_val,
        }

    # Initialize accumulators
    pv_premiums = 0.0
    pv_benefits = 0.0

    # Survival probability accumulator
    survival_prob = 1.0

    # Premium per year (simplified: assume level premium = SA / premium_term)
    # In practice, you'd use actuarial premium formulas
    annual_premium = sum_assured / max(1, premium_term) if premium_term > 0 else 0.0

    # Accumulated contributions for pension (with interest)
    pension_fund = 0.0

    for t in range(horizon):
        age = age_at_val + t
        policy_year = duration + t

        # Mortality rate and discount factor
        if use_tables:
            qx, _ = provider.get_mortality_qx(policy_row, int(age))
            discount_factor = provider.get_discount_factor(t * 12) ** (1.0 / 12.0) ** t
        else:
            qx = assumptions.mortality_function(age)
            discount_factor = (1.0 / (1.0 + assumptions.discount_rate)) ** t

        qx = min(max(qx, 0.0), 1.0)
        px = 1.0 - qx

        # Premium cash flow (if still in premium term)
        if policy_year < premium_term:
            gross_premium = annual_premium

            if use_tables:
                _, expense_pct, _ = provider.get_expenses(policy_row, int(policy_year) + 1)
                net_premium = gross_premium * (1.0 - expense_pct)
                inv_return = provider.get_investment_return(t * 12) * 12.0
            else:
                net_premium = gross_premium * (1.0 - assumptions.expense_loading)
                inv_return = assumptions.discount_rate

            pv_premiums += survival_prob * gross_premium * discount_factor

            # For pension, accumulate contributions
            if product_code == "PEN":
                pension_fund = (pension_fund + net_premium) * (1.0 + inv_return)
        else:
            gross_premium = 0.0
            net_premium = 0.0

        # Benefit cash flow
        benefit = 0.0

        if product_code == "WL":
            # Whole-life: death benefit = SA + accumulated RB
            # RB grows at rb_growth_rate per year
            if use_tables:
                rb_growth, _ = provider.get_rb_growth(policy_row, int(policy_year) + 1)
            else:
                rb_growth = assumptions.rb_growth_rate

            rb_at_t = rb_accum_sa * ((1.0 + rb_growth) ** t)
            death_benefit = sum_assured + rb_at_t
            benefit = qx * death_benefit

        elif product_code == "PEN":
            # Pension: lump sum at retirement
            if age >= retirement_age and t > 0:
                # Pay out accumulated fund at retirement (one-time)
                # Check if this is the retirement year
                if age_at_val + t - 1 < retirement_age <= age_at_val + t:
                    benefit = pension_fund
                    pension_fund = 0.0  # reset after payout

        pv_benefits += survival_prob * benefit * discount_factor

        # Update survival probability for next year
        survival_prob *= px

        # Stop if survival probability is negligible
        if survival_prob < 1e-6:
            break

    gpv = pv_benefits - pv_premiums

    return {
        "PV_premiums": pv_premiums,
        "PV_benefits": pv_benefits,
        "GPV": gpv,
        "duration_at_val": duration,
        "age_at_val": age_at_val,
    }


# ---------------------------------------------------------------------------
# Batch calculation
# ---------------------------------------------------------------------------


def calculate_gpv_batch(
    policies: list[Dict],
    discount_rate: float = 0.03,
    mortality_function: Optional[Callable[[float], float]] = None,
    expense_loading: float = 0.05,
    rb_growth_rate: float = 0.02,
    valuation_year: int = 2025,
    max_projection_years: int = 100,
    provider: Optional[AssumptionProvider] = None,
) -> list[Dict[str, float]]:
    """Calculate GPV for a batch of policies.

    Parameters
    ----------
    policies:
        List of policy dictionaries (same format as ``calculate_gpv``).
    discount_rate, mortality_function, expense_loading, rb_growth_rate,
    valuation_year, max_projection_years:
        Same as ``calculate_gpv``.

    Returns
    -------
    list
        List of GPV result dictionaries, one per policy.
    """

    results = []
    for policy in policies:
        result = calculate_gpv(
            policy,
            discount_rate=discount_rate,
            mortality_function=mortality_function,
            expense_loading=expense_loading,
            rb_growth_rate=rb_growth_rate,
            valuation_year=valuation_year,
            max_projection_years=max_projection_years,
            provider=provider,
        )
        results.append(result)
    return results


def generate_monthly_cashflows(
    policy_row: Dict,
    discount_rate: float = 0.03,
    mortality_function: Optional[Callable[[float], float]] = None,
    expense_loading: float = 0.05,
    rb_growth_rate: float = 0.02,
    surrender_rate: float = 0.01,
    valuation_year: int = 2025,
    max_projection_years: int = 100,
    provider: Optional[AssumptionProvider] = None,
) -> pd.DataFrame:
    """Generate detailed monthly cash-flow schedule for a single policy.

    Parameters
    ----------
    policy_row:
        Dictionary with policy attributes (same as ``calculate_gpv``).
    discount_rate, mortality_function, expense_loading, rb_growth_rate:
        Same as ``calculate_gpv``.
    surrender_rate:
        Annual surrender/lapse rate (e.g., 0.01 for 1% p.a.).
    valuation_year, max_projection_years:
        Same as ``calculate_gpv``.

    Returns
    -------
    pandas.DataFrame
        Monthly cash-flow schedule with detailed categories:
        - ``policy_id``: Policy identifier
        - ``month_index``: Month index from valuation (0, 1, 2, ...)
        - ``date``: Calendar date (year-month)
        - ``premium``: Premium inflow
        - ``expense``: Expense outflow
        - ``surrender``: Surrender benefit outflow
        - ``death``: Death benefit outflow
        - ``guaranteed_benefit``: Guaranteed benefit component
        - ``non_guaranteed_benefit``: Non-guaranteed benefit (RB) component
    """

    # Extract policy fields
    policy_id = policy_row.get("policy_id", "UNKNOWN")
    product_code = str(policy_row.get("product_code", "WL")).upper()
    issue_age = int(policy_row.get("issue_age", 30))
    sum_assured = float(policy_row.get("sum_assured", 100_000))
    premium_term = int(policy_row.get("premium_term", 20))
    issue_year = int(policy_row.get("issue_year", 2020))
    rb_accum_sa = float(policy_row.get("rb_accum_sa", 0.0))
    retirement_age = int(policy_row.get("retirement_age", 65))

    # Set up assumptions
    use_tables = provider is not None
    if not use_tables and mortality_function is None:
        mortality_function = default_mortality_qx

    # Duration and attained age at valuation
    duration = max(0, valuation_year - issue_year)
    age_at_val = issue_age + duration

    # Determine projection horizon in years
    if product_code == "PEN":
        horizon_years = min(max_projection_years, retirement_age - age_at_val + 5)
    else:
        horizon_years = min(max_projection_years, 120 - age_at_val)

    if horizon_years <= 0:
        return pd.DataFrame(
            columns=["policy_id", "month_index", "date", "cash_in", "cash_out", "net_cash_flow"]
        )

    # Convert to months
    horizon_months = horizon_years * 12

    # Annual premium (simplified)
    annual_premium = sum_assured / max(1, premium_term) if premium_term > 0 else 0.0
    monthly_premium = annual_premium / 12.0

    # Initialize detailed cash-flow arrays
    months = np.arange(horizon_months)
    premium = np.zeros(horizon_months, dtype=float)
    expense = np.zeros(horizon_months, dtype=float)
    surrender = np.zeros(horizon_months, dtype=float)
    death = np.zeros(horizon_months, dtype=float)
    guaranteed_benefit = np.zeros(horizon_months, dtype=float)
    non_guaranteed_benefit = np.zeros(horizon_months, dtype=float)

    # Pension fund accumulator and policy value tracker
    pension_fund = 0.0
    policy_value = 0.0  # For surrender value calculation
    in_force_prob = 1.0  # Probability policy is still in force

    for m in months:
        year_frac = m / 12.0
        age = age_at_val + year_frac
        policy_year = duration + year_frac

        # Monthly mortality and surrender probabilities
        if use_tables:
            qx_annual, _ = provider.get_mortality_qx(policy_row, int(age))
            lapse_annual, _ = provider.get_lapse_rate(policy_row, int(policy_year) + 1)
            surrender_rate_monthly = lapse_annual / 12.0
        else:
            qx_annual = mortality_function(int(age))
            surrender_rate_monthly = surrender_rate / 12.0

        qx_monthly = qx_annual / 12.0

        # Premium cash flow (inflow)
        if policy_year < premium_term:
            gross_premium_m = monthly_premium

            if use_tables:
                exp_fixed, exp_pct, _ = provider.get_expenses(policy_row, int(policy_year) + 1)
                net_premium_m = gross_premium_m * (1.0 - exp_pct)
                expense[m] = (exp_fixed + gross_premium_m * exp_pct) * in_force_prob
                inv_return_m = provider.get_investment_return(m)
            else:
                net_premium_m = gross_premium_m * (1.0 - expense_loading)
                expense[m] = gross_premium_m * expense_loading * in_force_prob
                inv_return_m = discount_rate / 12.0

            premium[m] = gross_premium_m * in_force_prob

            # Update policy value
            policy_value += net_premium_m

            # For pension, accumulate fund
            if product_code == "PEN":
                pension_fund = (pension_fund + net_premium_m) * (1.0 + inv_return_m)

        # Surrender (outflow)
        if m > 0:  # No surrender at t=0
            surrender_value = policy_value * 0.9  # 90% of accumulated value
            surrender[m] = surrender_rate_monthly * in_force_prob * surrender_value

        # Death and benefit cash flows
        if product_code == "WL":
            # Death benefit components
            year_idx = int(year_frac)

            if use_tables:
                rb_growth, _ = provider.get_rb_growth(policy_row, int(policy_year) + 1)
            else:
                rb_growth = rb_growth_rate

            rb_at_t = rb_accum_sa * ((1.0 + rb_growth) ** year_idx)

            # Guaranteed: base sum assured
            guaranteed_death = sum_assured
            # Non-guaranteed: accumulated RB
            non_guaranteed_death = rb_at_t

            total_death_benefit = guaranteed_death + non_guaranteed_death
            death[m] = qx_monthly * in_force_prob * total_death_benefit
            guaranteed_benefit[m] = qx_monthly * in_force_prob * guaranteed_death
            non_guaranteed_benefit[m] = qx_monthly * in_force_prob * non_guaranteed_death

        elif product_code == "PEN":
            # Retirement conversion (guaranteed lump sum)
            age_int = int(age)
            if age_int == retirement_age and m > 0:
                prev_age = int(age_at_val + (m - 1) / 12.0)
                if prev_age < retirement_age:
                    retirement_benefit = pension_fund
                    guaranteed_benefit[m] = in_force_prob * retirement_benefit
                    death[m] = 0.0  # No death benefit at retirement
                    pension_fund = 0.0

        # Update in-force probability (decrements due to death and surrender)
        in_force_prob *= 1.0 - qx_monthly - surrender_rate_monthly
        in_force_prob = max(in_force_prob, 0.0)

        # Stop if in-force probability is negligible
        if in_force_prob < 1e-6:
            break

    # Build DataFrame with detailed categories
    dates = pd.date_range(
        start=f"{valuation_year}-01-01",
        periods=horizon_months,
        freq="MS",
    )

    df = pd.DataFrame(
        {
            "policy_id": policy_id,
            "month_index": months,
            "date": dates,
            "premium": premium,
            "expense": expense,
            "surrender": surrender,
            "death": death,
            "guaranteed_benefit": guaranteed_benefit,
            "non_guaranteed_benefit": non_guaranteed_benefit,
        }
    )

    return df


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _enrich_policy_attributes(df: pd.DataFrame, valuation_year: int) -> pd.DataFrame:
    """
    Enrich policy DataFrame with derived attributes for assumption table lookup.

    Adds columns for:
    - gender (default 'U' if missing)
    - uw_class (default 'STD' if missing)
    - policy_status (default 'INFORCE' if missing)
    - annual_premium (derived if missing)
    - sa_band (derived from sum_assured)
    - prem_band (derived from annual_premium)

    Parameters
    ----------
    df : pd.DataFrame
        Policy DataFrame
    valuation_year : int
        Valuation year

    Returns
    -------
    pd.DataFrame
        Enriched DataFrame with additional columns
    """
    df = df.copy()

    # Add gender if missing
    if "gender" not in df.columns:
        df["gender"] = "U"
    else:
        df["gender"] = df["gender"].fillna("U")

    # Add uw_class if missing
    if "uw_class" not in df.columns:
        df["uw_class"] = "STD"
    else:
        df["uw_class"] = df["uw_class"].fillna("STD")

    # Add policy_status if missing
    if "policy_status" not in df.columns:
        df["policy_status"] = "INFORCE"
    else:
        df["policy_status"] = df["policy_status"].fillna("INFORCE")

    # Derive annual_premium if missing
    if "annual_premium" not in df.columns:
        df["annual_premium"] = df.apply(
            lambda row: derive_annual_premium(
                row.get("sum_assured", 100_000),
                row.get("premium_term", 20),
                row.get("product_code", "WL"),
            ),
            axis=1,
        )

    # Derive SA band
    df["sa_band"] = df["sum_assured"].apply(get_sa_band)

    # Derive premium band
    df["prem_band"] = df["annual_premium"].apply(get_premium_band)

    return df


# ---------------------------------------------------------------------------
# Portfolio-level valuation
# ---------------------------------------------------------------------------


def value_portfolio(
    df_policies: pd.DataFrame,
    discount_rate: float = 0.03,
    mortality_function: Optional[Callable[[float], float]] = None,
    expense_loading: float = 0.05,
    rb_growth_rate: float = 0.02,
    surrender_rate: float = 0.01,
    valuation_year: int = 2025,
    max_projection_years: int = 100,
    output_dir: Optional[str] = None,
    save_cashflows: bool = False,
    provider: Optional[AssumptionProvider] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """Calculate GPV and detailed cash flows for an entire portfolio.

    Parameters
    ----------
    df_policies:
        DataFrame of policies with required columns (product_code, issue_age,
        sum_assured, premium_term, issue_year, rb_accum_sa, retirement_age).
    discount_rate, mortality_function, expense_loading, rb_growth_rate:
        Same as ``calculate_gpv``.
    surrender_rate:
        Annual surrender/lapse rate (e.g., 0.01 for 1% p.a.).
    valuation_year, max_projection_years:
        Same as ``calculate_gpv``.
    output_dir:
        Optional directory to save output files (enriched portfolio, aggregate
        cash flows, summary JSON).
    save_cashflows:
        If True, save per-policy cash-flow schedules to Parquet.

    Returns
    -------
    tuple
        - ``df_result``: Input DataFrame with added columns (pv_premiums,
          pv_benefits, gpv_policy).
        - ``aggregate_cf``: Consolidated monthly cash flows across portfolio
          with detailed categories (month_index, date, total_premium,
          total_expense, total_surrender, total_death, total_guaranteed,
          total_non_guaranteed).
        - ``summary``: Dictionary with keys (total_pv_premiums, total_pv_benefits,
          total_gpv, n_policies).
    """

    if df_policies.empty:
        raise ValueError("df_policies is empty")

    # Ensure policy_id exists
    if "policy_id" not in df_policies.columns:
        df_policies = df_policies.copy()
        df_policies["policy_id"] = np.arange(1, len(df_policies) + 1)

    df_result = df_policies.copy()

    # Enrich policies with derived attributes for table lookup
    df_result = _enrich_policy_attributes(df_result, valuation_year)

    # Per-policy GPV calculation
    gpv_results = []
    all_cashflows = []

    for idx, row in df_result.iterrows():
        policy_dict = row.to_dict()

        # Calculate GPV
        gpv = calculate_gpv(
            policy_dict,
            discount_rate=discount_rate,
            mortality_function=mortality_function,
            expense_loading=expense_loading,
            rb_growth_rate=rb_growth_rate,
            valuation_year=valuation_year,
            max_projection_years=max_projection_years,
            provider=provider,
        )
        gpv_results.append(gpv)

        # Generate monthly cash flows with detailed categories
        cf = generate_monthly_cashflows(
            policy_dict,
            discount_rate=discount_rate,
            mortality_function=mortality_function,
            expense_loading=expense_loading,
            rb_growth_rate=rb_growth_rate,
            surrender_rate=surrender_rate,
            valuation_year=valuation_year,
            max_projection_years=max_projection_years,
            provider=provider,
        )
        all_cashflows.append(cf)

    # Add GPV columns to result DataFrame
    gpv_df = pd.DataFrame(gpv_results)
    df_result["pv_premiums"] = gpv_df["PV_premiums"].values
    df_result["pv_benefits"] = gpv_df["PV_benefits"].values
    df_result["gpv_policy"] = gpv_df["GPV"].values

    # Aggregate cash flows by category
    if all_cashflows:
        df_all_cf = pd.concat(all_cashflows, axis=0, ignore_index=True)

        aggregate_cf = (
            df_all_cf.groupby(["month_index", "date"])
            .agg(
                {
                    "premium": "sum",
                    "expense": "sum",
                    "surrender": "sum",
                    "death": "sum",
                    "guaranteed_benefit": "sum",
                    "non_guaranteed_benefit": "sum",
                }
            )
            .reset_index()
        )
        aggregate_cf.rename(
            columns={
                "premium": "total_premium",
                "expense": "total_expense",
                "surrender": "total_surrender",
                "death": "total_death",
                "guaranteed_benefit": "total_guaranteed",
                "non_guaranteed_benefit": "total_non_guaranteed",
            },
            inplace=True,
        )
        aggregate_cf.sort_values("month_index", inplace=True)
        aggregate_cf.reset_index(drop=True, inplace=True)
    else:
        aggregate_cf = pd.DataFrame(
            columns=[
                "month_index",
                "date",
                "total_premium",
                "total_expense",
                "total_surrender",
                "total_death",
                "total_guaranteed",
                "total_non_guaranteed",
            ]
        )

    # Summary statistics
    summary = {
        "total_pv_premiums": float(df_result["pv_premiums"].sum()),
        "total_pv_benefits": float(df_result["pv_benefits"].sum()),
        "total_gpv": float(df_result["gpv_policy"].sum()),
        "n_policies": len(df_result),
    }

    # Save outputs if requested
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Enriched portfolio
        portfolio_path = output_path / "portfolio_with_gpv.parquet"
        df_result.to_parquet(portfolio_path, index=False)

        # Aggregate cash flows
        cf_path = output_path / "aggregate_cashflows.csv"
        aggregate_cf.to_csv(cf_path, index=False)

        # Summary JSON
        import json

        summary_path = output_path / "gpv_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        # Per-policy cash flows (optional)
        if save_cashflows and all_cashflows:
            cf_all_path = output_path / "policy_cashflows.parquet"
            df_all_cf.to_parquet(cf_all_path, index=False)

    return df_result, aggregate_cf, summary


__all__ = [
    "default_mortality_qx",
    "DeterministicAssumptions",
    "calculate_gpv",
    "calculate_gpv_batch",
    "generate_monthly_cashflows",
    "value_portfolio",
]


# ---------------------------------------------------------------------------
# Testing / demonstration
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("=" * 70)
    print("Deterministic GPV Calculation - Table-Driven Assumptions Demo")
    print("=" * 70)

    # Try to load assumption provider
    project_root = Path(__file__).resolve().parents[2]
    assumption_dir = project_root / "data" / "assumptions"

    provider = None
    if assumption_dir.exists():
        try:
            print(f"\nLoading assumptions from: {assumption_dir}")
            provider = AssumptionProvider(assumption_dir)
            print("✓ Assumptions loaded successfully!")

            # Display assumption summary
            summary_info = provider.get_summary_info()
            print("\nAssumption Tables Loaded:")
            print(f"  Mortality tables:     {summary_info['mortality_tables']}")
            print(f"  Lapse tables:         {summary_info['lapse_tables']}")
            print(f"  Expense tables:       {summary_info['expense_tables']}")
            print(f"  Bonus tables:         {summary_info['bonus_tables']}")
            print(f"  Discount curves:      {summary_info['discount_curves']}")
            print(f"  Investment curves:    {summary_info['investment_curves']}")
            print(f"  Discount rate (1Y):   {summary_info['discount_rate_1y']:.4f}")
            print(f"  Discount rate (10Y):  {summary_info['discount_rate_10y']:.4f}")
        except Exception as e:
            print(f"⚠ Could not load assumptions: {e}")
            print("  Falling back to hardcoded assumptions")
            provider = None
    else:
        print(f"\n⚠ Assumption directory not found: {assumption_dir}")
        print("  Using hardcoded assumptions")
        print("  Run: python scripts/generate_sample_assumptions.py")

    # Sample whole-life policy
    wl_policy = {
        "policy_id": "WL001",
        "product_code": "WL",
        "issue_age": 35,
        "sum_assured": 500_000,
        "premium_term": 20,
        "issue_year": 2015,
        "rb_accum_sa": 50_000,
        "retirement_age": 65,
    }

    # Sample pension policy
    pen_policy = {
        "policy_id": "PEN001",
        "product_code": "PEN",
        "issue_age": 30,
        "sum_assured": 300_000,
        "premium_term": 25,
        "issue_year": 2010,
        "rb_accum_sa": 0,
        "retirement_age": 60,
    }

    print("\n" + "=" * 70)
    print("PART 1: Individual Policy GPV")
    print("=" * 70)

    print("\n1. Whole-Life Policy")
    print("-" * 70)
    for k, v in wl_policy.items():
        print(f"  {k:20s}: {v}")

    wl_result = calculate_gpv(
        wl_policy, discount_rate=0.03, expense_loading=0.05, provider=provider
    )
    print("\nGPV Results:")
    for k, v in wl_result.items():
        if isinstance(v, float):
            print(f"  {k:20s}: {v:,.2f}")
        else:
            print(f"  {k:20s}: {v}")

    print("\n2. Pension Policy")
    print("-" * 70)
    for k, v in pen_policy.items():
        print(f"  {k:20s}: {v}")

    pen_result = calculate_gpv(
        pen_policy, discount_rate=0.03, expense_loading=0.05, provider=provider
    )
    print("\nGPV Results:")
    for k, v in pen_result.items():
        if isinstance(v, float):
            print(f"  {k:20s}: {v:,.2f}")
        else:
            print(f"  {k:20s}: {v}")

    print("\n" + "=" * 70)
    print("PART 2: Detailed Monthly Cash-Flow Schedules")
    print("=" * 70)

    print("\n1. WL Policy - First 12 months (detailed categories):")
    wl_cf = generate_monthly_cashflows(
        wl_policy, discount_rate=0.03, surrender_rate=0.01, provider=provider
    )
    print(wl_cf.head(12).to_string(index=False))

    print("\n   Cash flow totals (first year):")
    first_year = wl_cf.head(12)
    print(f"   Total Premium:              {first_year['premium'].sum():>12,.2f}")
    print(f"   Total Expense:              {first_year['expense'].sum():>12,.2f}")
    print(f"   Total Surrender:            {first_year['surrender'].sum():>12,.2f}")
    print(f"   Total Death:                {first_year['death'].sum():>12,.2f}")
    print(f"   Total Guaranteed Benefit:   {first_year['guaranteed_benefit'].sum():>12,.2f}")
    print(f"   Total Non-Guaranteed (RB):  {first_year['non_guaranteed_benefit'].sum():>12,.2f}")

    print("\n2. Pension Policy - First 12 months (detailed categories):")
    pen_cf = generate_monthly_cashflows(
        pen_policy, discount_rate=0.03, surrender_rate=0.01, provider=provider
    )
    print(pen_cf.head(12).to_string(index=False))

    print("\n   Cash flow totals (first year):")
    first_year_pen = pen_cf.head(12)
    print(f"   Total Premium:              {first_year_pen['premium'].sum():>12,.2f}")
    print(f"   Total Expense:              {first_year_pen['expense'].sum():>12,.2f}")
    print(f"   Total Surrender:            {first_year_pen['surrender'].sum():>12,.2f}")
    print(f"   Total Guaranteed Benefit:   {first_year_pen['guaranteed_benefit'].sum():>12,.2f}")

    print("\n" + "=" * 70)
    print("PART 3: Portfolio-Level Valuation")
    print("=" * 70)

    # Create a small portfolio
    df_portfolio = pd.DataFrame([wl_policy, pen_policy])

    # Add a few more policies for demonstration
    extra_policies = [
        {
            "policy_id": "WL002",
            "product_code": "WL",
            "issue_age": 40,
            "sum_assured": 250_000,
            "premium_term": 15,
            "issue_year": 2018,
            "rb_accum_sa": 25_000,
            "retirement_age": 65,
        },
        {
            "policy_id": "PEN002",
            "product_code": "PEN",
            "issue_age": 35,
            "sum_assured": 400_000,
            "premium_term": 20,
            "issue_year": 2015,
            "rb_accum_sa": 0,
            "retirement_age": 65,
        },
    ]
    df_portfolio = pd.concat(
        [df_portfolio, pd.DataFrame(extra_policies)], axis=0, ignore_index=True
    )

    print(f"\nPortfolio size: {len(df_portfolio)} policies")
    print("\nProduct mix:")
    print(df_portfolio["product_code"].value_counts())

    # Determine output directory relative to project root
    output_dir = project_root / "data" / "liability_results"

    # Value the portfolio with detailed cash flows
    df_result, aggregate_cf, summary = value_portfolio(
        df_portfolio,
        discount_rate=0.03,
        surrender_rate=0.01,
        output_dir=str(output_dir),
        save_cashflows=True,
        provider=provider,
    )

    print("\n" + "-" * 70)
    print("Portfolio GPV Results (per policy):")
    print("-" * 70)
    print(
        df_result[
            ["policy_id", "product_code", "pv_premiums", "pv_benefits", "gpv_policy"]
        ].to_string(index=False)
    )

    print("\n" + "-" * 70)
    print("Aggregate Summary:")
    print("-" * 70)
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k:25s}: {v:,.2f}")
        else:
            print(f"  {k:25s}: {v}")

    print("\n" + "-" * 70)
    print("Aggregate Monthly Cash Flows by Category (first 12 months):")
    print("-" * 70)
    print(aggregate_cf.head(12).to_string(index=False))

    print("\n" + "-" * 70)
    print("Aggregate Totals (first year):")
    print("-" * 70)
    first_year_agg = aggregate_cf.head(12)
    print(f"  Total Premium:              {first_year_agg['total_premium'].sum():>15,.2f}")
    print(f"  Total Expense:              {first_year_agg['total_expense'].sum():>15,.2f}")
    print(f"  Total Surrender:            {first_year_agg['total_surrender'].sum():>15,.2f}")
    print(f"  Total Death:                {first_year_agg['total_death'].sum():>15,.2f}")
    print(f"  Total Guaranteed:           {first_year_agg['total_guaranteed'].sum():>15,.2f}")
    print(f"  Total Non-Guaranteed (RB):  {first_year_agg['total_non_guaranteed'].sum():>15,.2f}")
    print(
        f"  Net Cash Flow:              {(first_year_agg['total_premium'].sum() - first_year_agg['total_expense'].sum() - first_year_agg['total_surrender'].sum() - first_year_agg['total_death'].sum() - first_year_agg['total_guaranteed'].sum() - first_year_agg['total_non_guaranteed'].sum()):>15,.2f}"
    )

    print("\n" + "=" * 70)
    print("Testing complete!")
    print(f"\nOutput files saved to: {output_dir}/")
    print("  - portfolio_with_gpv.parquet")
    print("  - aggregate_cashflows.csv")
    print("  - gpv_summary.json")
    print("  - policy_cashflows.parquet")
    print("=" * 70)
