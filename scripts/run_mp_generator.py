"""Generate synthetic model point portfolio and save to Parquet.

This script provides a command-line interface to generate a large synthetic
portfolio of insurance policies using the model point generator. The portfolio
is written to a Parquet file for downstream actuarial modeling.

Usage
-----
Basic usage with defaults (1M policies, 60% WL / 40% pension):

    python scripts/run_mp_generator.py

Custom portfolio size and product mix:

    python scripts/run_mp_generator.py --n_policies 2000000 --wl_share 0.55 --pension_share 0.45

Specify output path:

    python scripts/run_mp_generator.py --output data/custom_portfolio.parquet

Environment Variables
---------------------
OUTPUT_DIR: Base directory for output files (from .env). Defaults to 'data/inforce'.

Requirements
------------
- numpy
- pandas
- pyarrow (for Parquet support)
- python-dotenv (for .env file loading)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    print("Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    load_dotenv = None

import os

from par_model_v2.model_points.mp_generator import (
    PortfolioSpec,
    generate_synthetic_policies,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Generate synthetic insurance portfolio and save to Parquet",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--n_policies",
        type=int,
        default=1_000_000,
        help="Number of policies to generate",
    )

    parser.add_argument(
        "--wl_share",
        type=float,
        default=0.60,
        help="Fraction of whole life policies (0.0 to 1.0)",
    )

    parser.add_argument(
        "--pension_share",
        type=float,
        default=0.40,
        help="Fraction of deferred pension policies (0.0 to 1.0)",
    )

    # Issue year cohort parameters
    parser.add_argument(
        "--issue_year_start",
        type=int,
        default=2016,
        help="Start year for issue year range",
    )

    parser.add_argument(
        "--issue_year_end",
        type=int,
        default=2025,
        help="End year for issue year range",
    )

    parser.add_argument(
        "--recent_years_weight",
        type=float,
        default=0.50,
        help="Target weight for recent years (2022-2025)",
    )

    # Demographic distributions
    parser.add_argument(
        "--gender_m",
        type=float,
        default=0.55,
        help="Probability of male gender (F will be 1 - M)",
    )

    parser.add_argument(
        "--uw_std",
        type=float,
        default=0.90,
        help="Probability of standard underwriting class (SUB will be 1 - STD)",
    )

    parser.add_argument(
        "--paidup_share",
        type=float,
        default=0.03,
        help="Fraction of paid-up policies (INFORCE will be 1 - PAIDUP)",
    )

    # Special premium term options
    parser.add_argument(
        "--include_pay_to_99",
        action="store_true",
        help="Include pay-to-99 option for WL policies",
    )

    parser.add_argument(
        "--include_pay_to_retirement",
        action="store_true",
        help="Include pay-to-retirement option for pension policies",
    )

    # Random seed
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output Parquet file path (if not specified, uses OUTPUT_DIR from .env or default)",
    )

    return parser.parse_args()


def get_output_path(args: argparse.Namespace) -> Path:
    """Determine output path from CLI args or environment variables."""

    if args.output:
        return Path(args.output)

    # Load .env file if available
    if load_dotenv is not None:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)

    # Get output directory from environment or use default
    output_dir = os.getenv("OUTPUT_DIR", "data/inforce")
    output_dir_path = PROJECT_ROOT / output_dir
    output_dir_path.mkdir(parents=True, exist_ok=True)

    return output_dir_path / "synthetic_portfolio.parquet"


def main() -> None:
    """Main entry point for the MP generator script."""

    args = parse_args()

    # Validate product mix
    total_share = args.wl_share + args.pension_share
    if not (0.99 <= total_share <= 1.01):
        print(f"Error: wl_share + pension_share must sum to 1.0 (got {total_share:.3f})")
        sys.exit(1)

    # Validate probabilities
    if not (0.0 <= args.gender_m <= 1.0):
        print(f"Error: gender_m must be between 0 and 1 (got {args.gender_m})")
        sys.exit(1)
    if not (0.0 <= args.uw_std <= 1.0):
        print(f"Error: uw_std must be between 0 and 1 (got {args.uw_std})")
        sys.exit(1)
    if not (0.0 <= args.paidup_share <= 1.0):
        print(f"Error: paidup_share must be between 0 and 1 (got {args.paidup_share})")
        sys.exit(1)

    # Create portfolio specification
    spec = PortfolioSpec(
        n_policies=args.n_policies,
        wl_share=args.wl_share,
        pension_share=args.pension_share,
        issue_year_start=args.issue_year_start,
        issue_year_end=args.issue_year_end,
        gender_probs={"M": args.gender_m, "F": 1.0 - args.gender_m},
        uw_class_probs={"STD": args.uw_std, "SUB": 1.0 - args.uw_std},
        policy_status_probs={"INFORCE": 1.0 - args.paidup_share, "PAIDUP": args.paidup_share},
        include_pay_to_99=args.include_pay_to_99,
        include_pay_to_retirement=args.include_pay_to_retirement,
        seed=args.seed,
    )

    # Determine output path
    output_path = get_output_path(args)

    print("=" * 70)
    print("Generating Synthetic Portfolio")
    print("=" * 70)
    print(f"\n  Policies: {spec.n_policies:,}")
    print(f"  Product mix: {spec.wl_share:.1%} WL / {spec.pension_share:.1%} Pension")
    print(f"  Issue years: {spec.issue_year_start}-{spec.issue_year_end}")
    print(f"  Random seed: {spec.seed if spec.seed is not None else 'None (random)'}")
    print("\n  Demographic distributions:")
    print(f"    Gender: M={args.gender_m:.1%}, F={1 - args.gender_m:.1%}")
    print(f"    UW Class: STD={args.uw_std:.1%}, SUB={1 - args.uw_std:.1%}")
    print(f"    Status: INFORCE={1 - args.paidup_share:.1%}, PAIDUP={args.paidup_share:.1%}")
    print("\n  Special options:")
    print(f"    Pay-to-99 (WL): {'Yes' if args.include_pay_to_99 else 'No'}")
    print(f"    Pay-to-retirement (PEN): {'Yes' if args.include_pay_to_retirement else 'No'}")
    print(f"\n  Output: {output_path}")
    print()

    # Generate and save portfolio
    df = generate_synthetic_policies(spec, str(output_path))

    print("\n" + "=" * 70)
    print("Portfolio Generation Complete")
    print("=" * 70)
    print(f"\nGenerated {len(df):,} policies")
    print(f"Saved to: {output_path.resolve()}")

    print("\n" + "-" * 70)
    print("Summary Statistics")
    print("-" * 70)

    print("\n  Product distribution:")
    product_counts = df["product_code"].value_counts()
    for product, count in product_counts.items():
        pct = 100 * count / len(df)
        print(f"    {product}: {count:,} ({pct:.1f}%)")

    print("\n  Issue year distribution:")
    issue_year_counts = df["issue_year"].value_counts().sort_index()
    recent_years = [2022, 2023, 2024, 2025]
    recent_count = sum(issue_year_counts.get(y, 0) for y in recent_years)
    recent_pct = 100 * recent_count / len(df)
    print(f"    2022-2025: {recent_count:,} ({recent_pct:.1f}%)")
    for year in sorted(issue_year_counts.index):
        count = issue_year_counts[year]
        pct = 100 * count / len(df)
        print(f"    {year}: {count:,} ({pct:.1f}%)")

    print("\n  Gender distribution:")
    gender_counts = df["gender"].value_counts()
    for gender, count in sorted(gender_counts.items()):
        pct = 100 * count / len(df)
        print(f"    {gender}: {count:,} ({pct:.1f}%)")

    print("\n  Underwriting class distribution:")
    uw_counts = df["uw_class"].value_counts()
    for uw, count in sorted(uw_counts.items()):
        pct = 100 * count / len(df)
        print(f"    {uw}: {count:,} ({pct:.1f}%)")

    print("\n  Policy status distribution:")
    status_counts = df["policy_status"].value_counts()
    for status, count in sorted(status_counts.items()):
        pct = 100 * count / len(df)
        print(f"    {status}: {count:,} ({pct:.1f}%)")

    print("\n  Sum assured band distribution:")
    sa_band_counts = df["sa_band"].value_counts()
    for band in ["SA_0_100K", "SA_100K_300K", "SA_300K_1M", "SA_1M_PLUS"]:
        count = sa_band_counts.get(band, 0)
        pct = 100 * count / len(df) if len(df) > 0 else 0
        print(f"    {band}: {count:,} ({pct:.1f}%)")

    print("\n  Premium band distribution:")
    prem_band_counts = df["premium_band"].value_counts()
    for band in ["PREM_0_10K", "PREM_10K_30K", "PREM_30K_100K", "PREM_100K_PLUS"]:
        count = prem_band_counts.get(band, 0)
        pct = 100 * count / len(df) if len(df) > 0 else 0
        print(f"    {band}: {count:,} ({pct:.1f}%)")

    print("\n  Sum assured statistics:")
    print(f"    Mean: ${df['sum_assured'].mean():,.2f}")
    print(f"    Median: ${df['sum_assured'].median():,.2f}")
    print(f"    Min: ${df['sum_assured'].min():,.2f}")
    print(f"    Max: ${df['sum_assured'].max():,.2f}")

    print("\n  Annual premium statistics:")
    print(f"    Mean: ${df['annual_premium'].mean():,.2f}")
    print(f"    Median: ${df['annual_premium'].median():,.2f}")
    print(f"    Min: ${df['annual_premium'].min():,.2f}")
    print(f"    Max: ${df['annual_premium'].max():,.2f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
