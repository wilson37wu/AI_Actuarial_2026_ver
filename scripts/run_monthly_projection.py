"""
Demo: Monthly Projection for PAR Endowment (5Y / 10Y / 20Y)
============================================================

Runs the full monthly projection for three par endowment policy terms and
prints summary tables for liability CFs, asset CFs by class, and asset share.

Usage:
    PYTHONPATH=. python scripts/run_monthly_projection.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
pd.options.display.float_format = "{:,.2f}".format
pd.options.display.max_columns = 20
pd.options.display.width = 120

from par_model_v2.projection import (
    AssetPosition, ParEndowmentProduct, run_full_projection
)

# ---------------------------------------------------------------------------
# Fund positions (from data/assumptions/initial_fund_assets.csv)
# ---------------------------------------------------------------------------
FUND_POSITIONS = [
    AssetPosition("Govt",     900_000, 880_000, duration_years=8.5,
                  annual_yield=0.032, annual_capital_growth=0.0,
                  average_maturity_years=8.5, credit_rating=""),
    AssetPosition("Credit_A", 575_000, 570_000, duration_years=6.2,
                  annual_yield=0.038, annual_capital_growth=0.0,
                  average_maturity_years=6.2, credit_rating="A"),
    AssetPosition("Equity",   700_000, 700_000, duration_years=0.0,
                  annual_yield=0.025, annual_capital_growth=0.06,
                  average_maturity_years=0.0, credit_rating=""),
    AssetPosition("Cash",     125_000, 125_000, duration_years=0.0,
                  annual_yield=0.020, annual_capital_growth=0.0,
                  average_maturity_years=0.0, credit_rating=""),
]

# Scale fund to approximate single-policy level
SCALE = 1.0 / 100.0   # 1% of total fund per representative policy
SCALED_POSITIONS = [
    AssetPosition(p.asset_class, p.market_value * SCALE, p.book_value * SCALE,
                  p.duration_years, p.annual_yield, p.annual_capital_growth,
                  p.average_maturity_years, p.credit_rating)
    for p in FUND_POSITIONS
]


def run_and_print(term_years: int):
    banner = f"{'='*70}\nPAR Endowment — {term_years}-Year Term  "
    banner += f"({term_years * 12} monthly timesteps)\n{'='*70}"
    print(banner)

    product = ParEndowmentProduct(
        term_years=term_years,
        issue_age=35,
        gender="M",
        sum_assured=100_000.0,
        annual_premium=5_000.0,
        rb_rate_annual=0.030,
        terminal_bonus_pct=0.50,
        surrender_value_pct=0.90,
        initial_rb_accum=0.0,
    )

    result = run_full_projection(
        product,
        SCALED_POSITIONS,
        discount_rate_annual=0.035,
        acquisition_expense_pct=0.08,
        renewal_expense_pct=0.04,
        renewal_expense_fixed_monthly=12.50,
        policyholder_share=0.70,
        shareholder_share=0.30,
    )

    # --- Summary ---
    s = result.summary()
    print("\n[Summary]")
    for k, v in s.items():
        print(f"  {k:<35s}: {v:>15,.2f}" if isinstance(v, float) else f"  {k:<35s}: {v}")

    # --- Liability CFs (first 12 months + last month) ---
    print(f"\n[Liability Cashflows — first 12 months]")
    cols = ["month", "in_force_prob", "premium", "acq_expense", "renewal_expense",
            "death_benefit_guar", "death_benefit_ng", "surrender_benefit",
            "maturity_benefit_guar", "maturity_benefit_ng", "discount_factor"]
    print(result.liability.cashflows[cols].head(12).to_string(index=False))
    print(f"\n[Liability Cashflows — maturity month {term_years*12}]")
    print(result.liability.cashflows[cols].tail(1).to_string(index=False))

    print(f"\n  PV Premiums:            {result.liability.pv_premiums:>15,.2f}")
    print(f"  PV Guaranteed Benefits: {result.liability.pv_guaranteed_benefits:>15,.2f}")
    print(f"  PV Non-Guar Benefits:   {result.liability.pv_non_guaranteed_benefits:>15,.2f}")
    print(f"  PV Surrender Benefits:  {result.liability.pv_surrender_benefits:>15,.2f}")
    print(f"  PV Expenses:            {result.liability.pv_expenses:>15,.2f}")
    print(f"  PV Net Liability:       {result.liability.pv_net_liability:>15,.2f}")

    # --- Asset CFs by class ---
    print(f"\n[Asset Cashflows by Class — first 12 months]")
    acols = ["month", "Govt_coupon", "Credit_coupon", "Equity_dividend",
             "Cash_interest", "total_income", "running_fund_mv"]
    print(result.assets.cashflows[acols].head(12).to_string(index=False))
    print(f"\n[Asset Income Summary by Class]")
    print(result.assets.by_class_summary.to_string(index=False))

    # --- Asset Share ---
    print(f"\n[Asset Share Projection — first 12 months]")
    ascols = ["month", "asset_share_bom", "premium", "investment_return",
              "death_outgo_guar", "death_outgo_ng", "surrender_outgo",
              "shareholder_dist", "policyholder_dist", "asset_share_eom"]
    print(result.asset_share.projection[ascols].head(12).to_string(index=False))

    print(f"\n  Asset Share at Maturity: {result.asset_share.asset_share_at_maturity:>15,.2f}")
    print(f"  Total Shareholder Dist:  {result.asset_share.total_shareholder_dist:>15,.2f}")
    print(f"  Total Policyholder Dist: {result.asset_share.total_policyholder_dist:>15,.2f}")
    print()


if __name__ == "__main__":
    for term in (5, 10, 20):
        run_and_print(term)
    print("Done. All three terms projected successfully.")
