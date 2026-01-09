"""Run deterministic liability valuation on a synthetic portfolio.

This script provides a command-line interface to perform deterministic GPV
calculations and generate detailed monthly cash-flow schedules for an entire
portfolio of insurance policies.

Usage
-----
Development usage (uses defaults):

    python scripts/run_liability_valuation.py

With sample size for testing:

    python scripts/run_liability_valuation.py --sample_size 1000

Explicit paths:

    python scripts/run_liability_valuation.py \\
        --portfolio data/inforce/synthetic_portfolio.parquet \\
        --output_dir data/liability_results

Custom valuation assumptions:

    python scripts/run_liability_valuation.py \\
        --portfolio data/inforce/synthetic_portfolio.parquet \\
        --output_dir data/liability_results \\
        --discount_rate 0.03 \\
        --expense_loading 0.05 \\
        --rb_growth_rate 0.02 \\
        --surrender_rate 0.01 \\
        --save_cashflows

Defaults
--------
If not specified:
- portfolio: data/inforce/synthetic_portfolio.parquet
- output_dir: data/liability_results

Outputs
-------
The script generates the following files in the output directory:

- portfolio_with_gpv.parquet: Enriched portfolio with per-policy GPV columns
- aggregate_cashflows.csv: Monthly aggregate cash flows by category
- gpv_summary.json: Summary statistics (total PV premiums, benefits, GPV)
- policy_cashflows.parquet: Per-policy cash-flow schedules (if --save_cashflows)

Requirements
------------
- pandas
- pyarrow (for Parquet support)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from par_model_v2.liabilities.deterministic_liability import value_portfolio


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Run deterministic liability valuation on a synthetic portfolio",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--portfolio",
        type=str,
        default=None,
        help="Path to portfolio Parquet file (default: data/inforce/synthetic_portfolio.parquet)",
    )

    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Output directory for valuation results (default: data/liability_results)",
    )

    parser.add_argument(
        "--discount_rate",
        type=float,
        default=0.03,
        help="Annual discount rate (e.g., 0.03 for 3%%)",
    )

    parser.add_argument(
        "--expense_loading",
        type=float,
        default=0.05,
        help="Expense loading as fraction of premiums (e.g., 0.05 for 5%%)",
    )

    parser.add_argument(
        "--rb_growth_rate",
        type=float,
        default=0.02,
        help="Annual reversionary bonus growth rate (e.g., 0.02 for 2%%)",
    )

    parser.add_argument(
        "--surrender_rate",
        type=float,
        default=0.01,
        help="Annual surrender/lapse rate (e.g., 0.01 for 1%%)",
    )

    parser.add_argument(
        "--valuation_year",
        type=int,
        default=2025,
        help="Valuation year",
    )

    parser.add_argument(
        "--max_projection_years",
        type=int,
        default=100,
        help="Maximum projection horizon in years",
    )

    parser.add_argument(
        "--save_cashflows",
        action="store_true",
        help="Save per-policy cash-flow schedules to Parquet",
    )

    parser.add_argument(
        "--sample_size",
        type=int,
        default=None,
        help="Optional: process only first N policies (for testing)",
    )

    return parser.parse_args()


def validate_portfolio(portfolio_path: Path) -> None:
    """Validate that portfolio file exists and is readable."""

    if not portfolio_path.exists():
        raise FileNotFoundError(f"Portfolio file not found: {portfolio_path}")

    if not portfolio_path.is_file():
        raise ValueError(f"Portfolio path is not a file: {portfolio_path}")

    if portfolio_path.suffix.lower() != ".parquet":
        raise ValueError(f"Portfolio file must be Parquet format: {portfolio_path}")


def main() -> None:
    """Main entry point for liability valuation script."""

    args = parse_args()

    # Apply defaults if not provided
    if args.portfolio is None:
        portfolio_path = PROJECT_ROOT / "data" / "inforce" / "synthetic_portfolio.parquet"
        using_default_portfolio = True
    else:
        portfolio_path = Path(args.portfolio)
        using_default_portfolio = False

    if args.output_dir is None:
        output_dir = PROJECT_ROOT / "data" / "liability_results"
        using_default_output = True
    else:
        output_dir = Path(args.output_dir)
        using_default_output = False

    print("=" * 70)
    print("Deterministic Liability Valuation")
    print("=" * 70)

    # Display path information
    print("\nConfiguration:")
    if using_default_portfolio:
        print("  Using default portfolio:")
        print(f"    {portfolio_path.relative_to(PROJECT_ROOT)}")
    else:
        print("  Using specified portfolio:")
        print(f"    {portfolio_path}")

    if using_default_output:
        print("  Using default output directory:")
        print(f"    {output_dir.relative_to(PROJECT_ROOT)}")
    else:
        print("  Using specified output directory:")
        print(f"    {output_dir}")

    # Validate portfolio exists
    if not portfolio_path.exists():
        print(f"\nError: Portfolio file not found at {portfolio_path}")
        print("\nPlease run the model point generator first:")
        print("  python scripts/run_mp_generator.py")
        print("\nOr specify a portfolio file explicitly:")
        print("  python scripts/run_liability_valuation.py --portfolio <path>")
        sys.exit(1)

    try:
        validate_portfolio(portfolio_path)
    except ValueError as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Load portfolio
    print("\nLoading portfolio...")
    try:
        df_portfolio = pd.read_parquet(portfolio_path)
    except Exception as e:
        print(f"\nError reading portfolio file: {e}")
        sys.exit(1)

    print(f"  Loaded {len(df_portfolio):,} policies")

    # Large portfolio warning
    if len(df_portfolio) > 100_000 and args.sample_size is None:
        print("\n" + "!" * 70)
        print("WARNING: Large portfolio detected")
        print("!" * 70)
        print(f"  Portfolio contains {len(df_portfolio):,} policies.")
        print("  Consider using --sample_size for testing, e.g.:")
        print("    python scripts/run_liability_valuation.py --sample_size 1000")
        print("\n  For production runs on large portfolios, use:")
        print("    python scripts/run_liability_distributed.py")
        print("!" * 70)
        print()

    # Optional: sample for testing
    if args.sample_size:
        print(f"  Sampling first {args.sample_size:,} policies for testing")
        df_portfolio = df_portfolio.head(args.sample_size)

    # Display portfolio summary
    print("\nPortfolio Summary:")
    print("  Product mix:")
    product_counts = df_portfolio["product_code"].value_counts()
    for product, count in product_counts.items():
        pct = 100 * count / len(df_portfolio)
        print(f"    {product}: {count:,} ({pct:.1f}%)")

    print("\n  Sum Assured:")
    print(f"    Mean:   ${df_portfolio['sum_assured'].mean():,.2f}")
    print(f"    Median: ${df_portfolio['sum_assured'].median():,.2f}")
    print(f"    Total:  ${df_portfolio['sum_assured'].sum():,.2f}")

    # Run valuation
    print("\n" + "-" * 70)
    print("Running Valuation...")
    print("-" * 70)
    print(f"  Discount rate:      {args.discount_rate:.2%}")
    print(f"  Expense loading:    {args.expense_loading:.2%}")
    print(f"  RB growth rate:     {args.rb_growth_rate:.2%}")
    print(f"  Surrender rate:     {args.surrender_rate:.2%}")
    print(f"  Valuation year:     {args.valuation_year}")
    print(f"  Save cash flows:    {args.save_cashflows}")

    try:
        df_result, aggregate_cf, summary = value_portfolio(
            df_portfolio,
            discount_rate=args.discount_rate,
            expense_loading=args.expense_loading,
            rb_growth_rate=args.rb_growth_rate,
            surrender_rate=args.surrender_rate,
            valuation_year=args.valuation_year,
            max_projection_years=args.max_projection_years,
            output_dir=str(output_dir),
            save_cashflows=args.save_cashflows,
        )
    except Exception as e:
        print(f"\nError during valuation: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # Display results
    print("\n" + "=" * 70)
    print("Valuation Results")
    print("=" * 70)

    print("\nAggregate Present Values:")
    print(f"  Total PV Premiums:  ${summary['total_pv_premiums']:>15,.2f}")
    print(f"  Total PV Benefits:  ${summary['total_pv_benefits']:>15,.2f}")
    print(f"  Total GPV:          ${summary['total_gpv']:>15,.2f}")
    print(f"  Number of Policies: {summary['n_policies']:>15,}")

    # Per-policy statistics
    print("\nPer-Policy GPV Statistics:")
    print(f"  Mean GPV:           ${df_result['gpv_policy'].mean():>15,.2f}")
    print(f"  Median GPV:         ${df_result['gpv_policy'].median():>15,.2f}")
    print(f"  Min GPV:            ${df_result['gpv_policy'].min():>15,.2f}")
    print(f"  Max GPV:            ${df_result['gpv_policy'].max():>15,.2f}")

    # Aggregate cash flow summary (first year)
    if not aggregate_cf.empty:
        first_year = aggregate_cf.head(12)
        print("\nAggregate Cash Flows (First Year):")
        print(f"  Total Premium:      ${first_year['total_premium'].sum():>15,.2f}")
        print(f"  Total Expense:      ${first_year['total_expense'].sum():>15,.2f}")
        print(f"  Total Surrender:    ${first_year['total_surrender'].sum():>15,.2f}")
        print(f"  Total Death:        ${first_year['total_death'].sum():>15,.2f}")
        print(f"  Total Guaranteed:   ${first_year['total_guaranteed'].sum():>15,.2f}")
        print(f"  Total Non-Guar:     ${first_year['total_non_guaranteed'].sum():>15,.2f}")

    # Output files
    print("\n" + "=" * 70)
    print("Output Files")
    print("=" * 70)
    print(f"\nResults saved to: {output_dir.resolve()}/")
    print("  - portfolio_with_gpv.parquet")
    print("  - aggregate_cashflows.csv")
    print("  - gpv_summary.json")
    if args.save_cashflows:
        print("  - policy_cashflows.parquet")

    print("\n" + "=" * 70)
    print("Valuation Complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
