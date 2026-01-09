"""
Generate Sample Assumption Tables for Deterministic Liability Valuation

Creates realistic toy CSV tables under data/assumptions/ with schemas
matching the AssumptionProvider requirements.

Usage:
    python scripts/generate_sample_assumptions.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

np.random.seed(42)

OUTPUT_DIR = PROJECT_ROOT / "data" / "assumptions"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def generate_mortality_qx():
    """Generate mortality_qx.csv with age-based mortality rates."""

    products = ["WL", "PEN"]
    issue_years = [2016, 2018, 2020, 2022, 2024, "ALL"]
    genders = ["M", "F", "U"]
    uw_classes = ["STD", "SUB", "ALL"]
    policy_statuses = ["INFORCE", "PAIDUP", "ALL"]
    sa_bands = ["SA_0_100K", "SA_100K_300K", "SA_300K_1M", "SA_1M_PLUS"]
    prem_bands = ["PREM_0_10K", "PREM_10K_30K", "PREM_30K_100K", "PREM_100K_PLUS"]

    ages = list(range(20, 101))

    rows = []
    table_counter = 1

    for product in products:
        for issue_year in [2020, "ALL"]:
            for gender in ["M", "F", "ALL"]:
                for uw_class in ["STD", "ALL"]:
                    for status in ["INFORCE", "ALL"]:
                        for sa_band in ["SA_0_100K", "ALL"]:
                            for prem_band in ["PREM_0_10K", "ALL"]:
                                table_id = f"MORT_BASE_V1_T{table_counter}"
                                table_counter += 1

                                for age in ages:
                                    base_qx = 0.0001 * np.exp((age - 20) * 0.08)

                                    if gender == "M":
                                        qx = base_qx * 1.15
                                    elif gender == "F":
                                        qx = base_qx * 0.85
                                    else:
                                        qx = base_qx

                                    if uw_class == "SUB":
                                        qx *= 1.5

                                    if status == "PAIDUP":
                                        qx *= 0.95

                                    qx = min(qx, 1.0)

                                    rows.append(
                                        {
                                            "table_id": table_id,
                                            "product_code": product,
                                            "issue_year": issue_year,
                                            "gender": gender,
                                            "uw_class": uw_class,
                                            "policy_status": status,
                                            "sa_band": sa_band,
                                            "prem_band": prem_band,
                                            "attained_age": age,
                                            "qx_annual": round(qx, 8),
                                        }
                                    )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "mortality_qx.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert df["qx_annual"].between(0, 1).all(), "qx out of bounds"
    return df


def generate_lapse():
    """Generate lapse.csv with policy-year-based lapse rates."""

    products = ["WL", "PEN"]
    issue_years = [2020, "ALL"]
    genders = ["M", "F", "ALL"]
    uw_classes = ["STD", "ALL"]
    policy_statuses = ["INFORCE", "PAIDUP", "ALL"]
    sa_bands = ["SA_0_100K", "ALL"]
    prem_bands = ["PREM_0_10K", "ALL"]

    policy_years = list(range(1, 31)) + [50, 100, 130]

    rows = []
    table_counter = 1

    for product in products:
        for issue_year in issue_years:
            for gender in genders:
                for uw_class in uw_classes:
                    for status in policy_statuses:
                        for sa_band in sa_bands:
                            for prem_band in prem_bands:
                                table_id = f"LAPSE_BASE_V1_T{table_counter}"
                                table_counter += 1

                                for py in policy_years:
                                    if py == 1:
                                        base_lapse = 0.15
                                    elif py <= 3:
                                        base_lapse = 0.10
                                    elif py <= 5:
                                        base_lapse = 0.06
                                    elif py <= 10:
                                        base_lapse = 0.04
                                    elif py <= 20:
                                        base_lapse = 0.02
                                    else:
                                        base_lapse = 0.01

                                    if product == "PEN":
                                        base_lapse *= 0.7

                                    if status == "PAIDUP":
                                        base_lapse *= 0.5

                                    lapse = min(base_lapse, 1.0)

                                    rows.append(
                                        {
                                            "table_id": table_id,
                                            "product_code": product,
                                            "issue_year": issue_year,
                                            "gender": gender,
                                            "uw_class": uw_class,
                                            "policy_status": status,
                                            "sa_band": sa_band,
                                            "prem_band": prem_band,
                                            "policy_year": py,
                                            "lapse_annual": round(lapse, 6),
                                        }
                                    )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "lapse.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert df["lapse_annual"].between(0, 1).all(), "lapse out of bounds"
    return df


def generate_expenses():
    """Generate expenses.csv with fixed and percentage-based expenses."""

    products = ["WL", "PEN"]
    policy_statuses = ["INFORCE", "PAIDUP", "ALL"]

    policy_years = list(range(1, 31)) + [50, 100, 130]

    rows = []
    table_counter = 1

    for product in products:
        for status in policy_statuses:
            table_id = f"EXP_BASE_V1_T{table_counter}"
            table_counter += 1

            for py in policy_years:
                if py == 1:
                    fixed = 500.0
                    pct = 0.10
                elif py <= 5:
                    fixed = 150.0
                    pct = 0.05
                elif py <= 10:
                    fixed = 100.0
                    pct = 0.03
                elif py <= 20:
                    fixed = 75.0
                    pct = 0.02
                else:
                    fixed = 50.0
                    pct = 0.01

                if product == "PEN":
                    fixed *= 0.8
                    pct *= 0.8

                if status == "PAIDUP":
                    fixed *= 0.5
                    pct = 0.0

                rows.append(
                    {
                        "table_id": table_id,
                        "product_code": product,
                        "policy_status": status,
                        "policy_year": py,
                        "expense_fixed_monthly": round(fixed / 12, 2),
                        "expense_pct_premium": round(pct, 4),
                    }
                )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "expenses.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert (df["expense_pct_premium"] >= 0).all(), "expense_pct negative"
    return df


def generate_bonus_rb():
    """Generate bonus_rb.csv with reversionary bonus growth rates."""

    products = ["WL", "PEN"]
    policy_years = list(range(1, 31)) + [50, 100, 130]

    rows = []
    table_counter = 1

    for product in products:
        table_id = f"BONUS_BASE_V1_T{table_counter}"
        table_counter += 1

        for py in policy_years:
            if product == "WL":
                if py <= 5:
                    rb_growth = 0.030
                elif py <= 10:
                    rb_growth = 0.025
                elif py <= 20:
                    rb_growth = 0.020
                elif py <= 50:
                    rb_growth = 0.015
                else:
                    rb_growth = 0.010
            else:
                rb_growth = 0.0

            rows.append(
                {
                    "table_id": table_id,
                    "product_code": product,
                    "policy_year": py,
                    "rb_growth_annual": round(rb_growth, 6),
                }
            )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "bonus_rb.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert df["rb_growth_annual"].between(0, 0.1).all(), "rb_growth out of bounds"
    return df


def generate_discount_curve():
    """Generate discount_curve.csv with annual tenors up to 130 years."""

    curve_id = "DISC_BASE_V1"
    tenor_years = list(range(0, 131))

    rows = []

    for tenor in tenor_years:
        if tenor == 0:
            rate = 0.025
        elif tenor <= 1:
            rate = 0.028
        elif tenor <= 5:
            rate = 0.030 + (tenor - 1) * 0.002
        elif tenor <= 10:
            rate = 0.038 + (tenor - 5) * 0.001
        elif tenor <= 20:
            rate = 0.043 + (tenor - 10) * 0.0005
        elif tenor <= 30:
            rate = 0.048 + (tenor - 20) * 0.0002
        else:
            rate = 0.050

        rows.append(
            {"curve_id": curve_id, "tenor_years": tenor, "zero_rate_annual": round(rate, 6)}
        )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "discount_curve.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert df["zero_rate_annual"].between(0, 0.15).all(), "discount rate out of bounds"
    return df


def generate_investment_return():
    """Generate investment_return.csv with annual tenors up to 130 years."""

    curve_id = "INV_BASE_V1"
    tenor_years = list(range(0, 131))

    rows = []

    for tenor in tenor_years:
        if tenor <= 5:
            rate = 0.045
        elif tenor <= 10:
            rate = 0.050
        elif tenor <= 20:
            rate = 0.055
        elif tenor <= 30:
            rate = 0.058
        else:
            rate = 0.060

        rows.append(
            {"curve_id": curve_id, "tenor_years": tenor, "return_rate_annual": round(rate, 6)}
        )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "investment_return.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    assert df["return_rate_annual"].between(0, 0.15).all(), "investment return out of bounds"
    return df


def generate_strategic_asset_allocation():
    """Generate strategic_asset_allocation.csv with time-varying SAA."""

    rows = []

    # Define SAA profiles for different policy durations
    # Early years: higher equity
    # Later years: higher bonds

    policy_years = [1, 5, 10, 15, 20, 25, 30]

    for py in policy_years:
        # Equity allocation decreases with duration
        if py <= 5:
            w_equity = 0.35
            w_govt = 0.35
            w_credit = 0.25
            w_cash = 0.05
        elif py <= 10:
            w_equity = 0.30
            w_govt = 0.40
            w_credit = 0.25
            w_cash = 0.05
        elif py <= 20:
            w_equity = 0.20
            w_govt = 0.50
            w_credit = 0.25
            w_cash = 0.05
        else:
            w_equity = 0.15
            w_govt = 0.55
            w_credit = 0.25
            w_cash = 0.05

        # Create rows for each asset class
        rows.append(
            {
                "product_code": "ALL",
                "policy_year": py,
                "calendar_year": 0,  # Wildcard
                "fund_id": "PAR",
                "asset_class": "Govt",
                "target_weight": w_govt,
            }
        )
        rows.append(
            {
                "product_code": "ALL",
                "policy_year": py,
                "calendar_year": 0,
                "fund_id": "PAR",
                "asset_class": "Credit_A",
                "target_weight": w_credit,
            }
        )
        rows.append(
            {
                "product_code": "ALL",
                "policy_year": py,
                "calendar_year": 0,
                "fund_id": "PAR",
                "asset_class": "Equity",
                "target_weight": w_equity,
            }
        )
        rows.append(
            {
                "product_code": "ALL",
                "policy_year": py,
                "calendar_year": 0,
                "fund_id": "PAR",
                "asset_class": "Cash",
                "target_weight": w_cash,
            }
        )

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "strategic_asset_allocation.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    # Validate weights sum to 1 within each group
    weight_sums = df.groupby(["product_code", "policy_year", "fund_id"])["target_weight"].sum()
    assert np.allclose(weight_sums, 1.0, atol=1e-6), "SAA weights do not sum to 1.0"

    return df


def generate_initial_fund_assets():
    """Generate initial_fund_assets.csv with starting PAR fund assets."""

    # Example: PAR fund starts with 2.3M in assets
    # Allocation roughly follows balanced SAA

    rows = [
        {
            "fund_id": "PAR",
            "valuation_date": "2024-01-01",
            "asset_class": "Govt",
            "market_value": 900000,
            "book_value": 880000,
            "duration": 8.5,
            "average_rating": "",
        },
        {
            "fund_id": "PAR",
            "valuation_date": "2024-01-01",
            "asset_class": "Credit_A",
            "market_value": 575000,
            "book_value": 570000,
            "duration": 6.2,
            "average_rating": "A",
        },
        {
            "fund_id": "PAR",
            "valuation_date": "2024-01-01",
            "asset_class": "Equity",
            "market_value": 700000,
            "book_value": 700000,
            "duration": 0.0,
            "average_rating": "",
        },
        {
            "fund_id": "PAR",
            "valuation_date": "2024-01-01",
            "asset_class": "Cash",
            "market_value": 125000,
            "book_value": 125000,
            "duration": 0.0,
            "average_rating": "",
        },
    ]

    df = pd.DataFrame(rows)
    output_path = OUTPUT_DIR / "initial_fund_assets.csv"
    df.to_csv(output_path, index=False)
    print(f"Created {output_path} with {len(df):,} rows")

    total_mv = df["market_value"].sum()
    print(f"  Total initial fund assets: ${total_mv:,.0f}")

    return df


def main():
    """Generate all assumption tables."""

    print("=" * 70)
    print("Generating Sample Assumption Tables")
    print("=" * 70)
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    generate_mortality_qx()
    generate_lapse()
    generate_expenses()
    generate_bonus_rb()
    generate_discount_curve()
    generate_investment_return()
    generate_strategic_asset_allocation()
    generate_initial_fund_assets()

    print()
    print("=" * 70)
    print("All assumption tables generated successfully!")
    print("=" * 70)
    print()
    print("Files created:")
    for csv_file in sorted(OUTPUT_DIR.glob("*.csv")):
        size_kb = csv_file.stat().st_size / 1024
        print(f"  {csv_file.name:30s} ({size_kb:>8.1f} KB)")


if __name__ == "__main__":
    main()
