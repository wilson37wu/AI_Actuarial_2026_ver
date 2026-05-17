"""
Generate Global ESG Scenario File

Produces a multi-currency, multi-asset ESG Parquet file using:
  - Hull-White 1-factor interest rate model per currency
  - Risk-neutral GBM equity model correlated with rates
  - Cholesky correlation structure

Usage examples:

  # Quick test run (100 trials, 10 years, USD + CNY only)
  python scripts/generate_global_esg.py --n_trials 100 --n_years 10 --currencies USD CNY

  # Production run (1000 trials, 30 years, all currencies)
  python scripts/generate_global_esg.py --n_trials 1000 --n_years 30

  # Custom output path
  python scripts/generate_global_esg.py --output data/esg/global_2026.parquet
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from par_model_v2.esg.global_esg import generate_global_esg


def main():
    parser = argparse.ArgumentParser(
        description="Generate global multi-currency ESG scenarios",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--n_trials", type=int, default=500,
                        help="Number of Monte Carlo trials")
    parser.add_argument("--n_years", type=int, default=30,
                        help="Projection horizon in years")
    parser.add_argument(
        "--currencies", nargs="+",
        default=["USD", "EUR", "GBP", "JPY", "CNY"],
        choices=["USD", "EUR", "GBP", "JPY", "CNY"],
        help="Currencies to model",
    )
    parser.add_argument(
        "--equity", nargs="+",
        default=["E_USD", "E_EUR", "E_GBP", "E_JPY", "E_CNY"],
        choices=["E_USD", "E_EUR", "E_GBP", "E_JPY", "E_CNY"],
        help="Equity indices to model",
    )
    parser.add_argument("--output", type=str, default=None,
                        help="Output Parquet file path")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")

    args = parser.parse_args()

    output = args.output or str(PROJECT_ROOT / "data" / "esg" / "global_scenarios.parquet")

    generate_global_esg(
        output_path=output,
        n_trials=args.n_trials,
        n_years=args.n_years,
        currencies=args.currencies,
        equity_tickers=args.equity,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
