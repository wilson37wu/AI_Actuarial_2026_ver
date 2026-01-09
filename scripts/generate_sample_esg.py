"""
Generate Sample ESG Scenario File for Testing

Creates a synthetic ESG scenario file with realistic structure for testing
the asset share projection and non-guaranteed dividend calculation.

Output format matches expected ESG file structure with columns:
- Trial, Timestep
- Government ZCB prices by tenor
- Corporate ZCB prices by rating and tenor
- Equity total returns and dividend yields
- Cash returns

Performance optimized to avoid DataFrame fragmentation warnings.
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def generate_sample_esg(
    n_trials: int = 100,
    n_timesteps: int = 360,  # 30 years monthly
    max_tenor: int = 30,
    ratings: list = None,
    output_path: str = None,
    seed: int = 42,
):
    """
    Generate sample ESG scenario file.

    Parameters
    ----------
    n_trials : int
        Number of scenario trials
    n_timesteps : int
        Number of timesteps (e.g., 360 for 30 years monthly)
    max_tenor : int
        Maximum bond tenor to generate
    ratings : list
        Credit ratings to include
    output_path : str
        Output file path (CSV or Parquet)
    seed : int
        Random seed for reproducibility
    """
    if ratings is None:
        ratings = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]

    if output_path is None:
        output_path = PROJECT_ROOT / "data" / "esg" / "sample_scenarios.parquet"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    np.random.seed(seed)

    print("Generating sample ESG scenarios:")
    print(f"  Trials: {n_trials}")
    print(f"  Timesteps: {n_timesteps}")
    print(f"  Max tenor: {max_tenor}")
    print(f"  Ratings: {ratings}")

    # Total rows
    n_steps = n_timesteps + 1  # Include timestep 0
    n_rows = n_trials * n_steps

    # Create base index arrays (vectorized)
    trials = np.repeat(np.arange(1, n_trials + 1), n_steps)
    timesteps = np.tile(np.arange(0, n_steps), n_trials)

    base_df = pd.DataFrame({"Trial": trials, "Timestep": timesteps})
    print(f"  Base rows: {len(base_df):,}")

    # Dictionary to hold all scenario columns
    col_dict = {}

    # Generate interest rate scenarios (stochastic)
    # Use Vasicek-like model for short rate
    t0 = time.time()
    print("  Generating interest rate scenarios...")

    # Pre-allocate arrays for government ZCB prices: shape (n_trials, n_steps, max_tenor)
    govt_zcb = np.zeros((n_trials, n_steps, max_tenor))

    for trial_idx in range(n_trials):
        # Initial short rate
        r0 = 0.03 + np.random.normal(0, 0.005)

        # Mean reversion parameters
        kappa = 0.3  # Speed of mean reversion
        theta = 0.04  # Long-term mean
        sigma = 0.02  # Volatility

        # Generate short rate path (vectorized)
        short_rates = np.zeros(n_steps)
        short_rates[0] = r0

        for t in range(1, n_steps):
            dr = (
                kappa * (theta - short_rates[t - 1]) * (1 / 12)
                + sigma * np.sqrt(1 / 12) * np.random.normal()
            )
            short_rates[t] = max(0.001, short_rates[t - 1] + dr)  # Floor at 0.1%

        # Generate ZCB prices for all tenors at once
        for tenor in range(1, max_tenor + 1):
            # Approximate yield curve with slight upward slope
            yields = short_rates + 0.001 * tenor + np.random.normal(0, 0.002, n_steps)
            prices = np.exp(-yields * tenor)
            govt_zcb[trial_idx, :, tenor - 1] = prices

    # Reshape and add to col_dict
    for tenor in range(1, max_tenor + 1):
        col_name = f"ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)"
        # Extract tenor slice and reshape to 1D (trial-major order)
        col_dict[col_name] = govt_zcb[:, :, tenor - 1].reshape(-1)

    print(f"    Government ZCB: {time.time() - t0:.2f}s")

    # Generate corporate bond ZCB prices (with credit spreads)
    t0 = time.time()
    print("  Generating corporate bond scenarios...")

    credit_spreads = {
        "AAA": 0.0005,
        "AA": 0.001,
        "A": 0.0015,
        "BBB": 0.0025,
        "BB": 0.004,
        "B": 0.006,
        "CCC": 0.010,
    }

    for rating in ratings:
        spread = credit_spreads.get(rating, 0.002)

        for tenor in range(1, max_tenor + 1):
            credit_col = f"ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)"

            # Corporate ZCB = Govt ZCB * exp(-spread * tenor)
            # Use already computed govt_zcb array
            govt_prices = govt_zcb[:, :, tenor - 1]  # Shape (n_trials, n_steps)

            # Stochastic spread variation for all trials at once
            spread_noise = np.random.normal(0, spread * 0.2, (n_trials, n_steps))
            spread_path = spread + spread_noise
            spread_path = np.maximum(0, spread_path)  # No negative spreads

            credit_prices = govt_prices * np.exp(-spread_path * tenor)

            # Reshape to 1D and add to col_dict
            col_dict[credit_col] = credit_prices.reshape(-1)

    print(f"    Corporate ZCB: {time.time() - t0:.2f}s")

    # Generate equity scenarios
    t0 = time.time()
    print("  Generating equity scenarios...")

    equity_tickers = ["E_CNY"]  # Can add 'P_CNY' for property

    for ticker in equity_tickers:
        total_return_col = f"ESG.Assets.EquityAssets.{ticker}.TotalReturn"
        div_yield_col = f"ESG.Assets.EquityAssets.{ticker}.DividendYield.Value"

        # Equity returns: GBM-like with mean 8%, vol 20%
        mu = 0.08 / 12  # Monthly drift
        sigma = 0.20 / np.sqrt(12)  # Monthly vol

        # Pre-allocate arrays: shape (n_trials, n_steps)
        equity_returns = np.ones((n_trials, n_steps))
        div_yields = np.zeros((n_trials, n_steps))

        for trial_idx in range(n_trials):
            # Returns: t=0 is 1.0, t>0 is exp(mu + sigma*N(0,1))
            equity_returns[trial_idx, 0] = 1.0
            equity_returns[trial_idx, 1:] = np.exp(mu + sigma * np.random.normal(size=n_steps - 1))

            # Dividend yield: mean 3%, some variation
            div_yields[trial_idx, :] = 0.03 + np.random.normal(0, 0.005, n_steps)
            div_yields[trial_idx, :] = np.maximum(0.01, div_yields[trial_idx, :])  # Floor at 1%

        # Reshape to 1D and add to col_dict
        col_dict[total_return_col] = equity_returns.reshape(-1)
        col_dict[div_yield_col] = div_yields.reshape(-1)

    print(f"    Equity: {time.time() - t0:.2f}s")

    # Generate cash returns
    t0 = time.time()
    print("  Generating cash return scenarios...")

    cash_col = "ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn"

    # Cash return follows short rate with small spread
    # Use already computed 1Y govt ZCB prices
    govt_1y_prices = govt_zcb[:, :, 0]  # Shape (n_trials, n_steps), tenor index 0 = 1Y

    # Implied 1-year rate from ZCB price
    implied_rates = -np.log(govt_1y_prices)

    # Cash return = 1 + short_rate/12 (monthly) + noise
    cash_noise = np.random.normal(0, 0.0001, (n_trials, n_steps))
    cash_returns = 1 + implied_rates / 12 + cash_noise

    # Reshape to 1D and add to col_dict
    col_dict[cash_col] = cash_returns.reshape(-1)

    print(f"    Cash: {time.time() - t0:.2f}s")

    # Build final DataFrame by concatenating base_df with all scenario columns
    print("\n  Building final DataFrame...")
    t0 = time.time()

    scenario_df = pd.DataFrame(col_dict)
    df = pd.concat([base_df, scenario_df], axis=1)

    print(f"    DataFrame construction: {time.time() - t0:.2f}s")

    # Save to file
    print(f"\nSaving to: {output_path}")
    print(f"  Total columns: {len(df.columns)}")
    print(f"  Total rows: {len(df):,}")
    print(f"  File size estimate: {df.memory_usage(deep=True).sum() / 1024**2:.1f} MB")

    t0 = time.time()
    if output_path.suffix.lower() == ".parquet":
        df.to_parquet(output_path, index=False)
    else:
        df.to_csv(output_path, index=False)
    print(f"  Write time: {time.time() - t0:.2f}s")

    print("\n✓ Sample ESG file created successfully")

    # Display sample
    print("\nSample data (first 5 rows):")
    print(df.head())

    print("\nColumn summary:")
    print(f"  Government ZCB columns: {sum(1 for c in df.columns if 'Govt' in c)}")
    print(f"  Corporate ZCB columns: {sum(1 for c in df.columns if any(r in c for r in ratings))}")
    print(f"  Equity columns: {sum(1 for c in df.columns if 'Equity' in c)}")
    print(f"  Cash columns: {sum(1 for c in df.columns if 'Cash' in c)}")

    return df


def main():
    parser = argparse.ArgumentParser(description="Generate sample ESG scenario file for testing")

    parser.add_argument(
        "--n_trials", type=int, default=100, help="Number of scenario trials (default: 100)"
    )

    parser.add_argument(
        "--n_timesteps",
        type=int,
        default=360,
        help="Number of timesteps (default: 360 for 30 years monthly)",
    )

    parser.add_argument(
        "--max_tenor", type=int, default=30, help="Maximum bond tenor in years (default: 30)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (default: data/esg/sample_scenarios.parquet)",
    )

    parser.add_argument(
        "--format",
        type=str,
        choices=["csv", "parquet"],
        default="parquet",
        help="Output format (default: parquet)",
    )

    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")

    args = parser.parse_args()

    # Determine output path
    if args.output is None:
        output_path = PROJECT_ROOT / "data" / "esg" / f"sample_scenarios.{args.format}"
    else:
        output_path = args.output

    # Generate
    generate_sample_esg(
        n_trials=args.n_trials,
        n_timesteps=args.n_timesteps,
        max_tenor=args.max_tenor,
        output_path=output_path,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
