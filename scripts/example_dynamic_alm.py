"""
Example: Dynamic ALM Engine Usage

Demonstrates how to use the Dynamic ALM Engine to link liability cashflows
with asset portfolio projection under ESG scenarios.
"""

import sys
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from par_model_v2.valuation.dynamic_alm import (
    ALMConfig,
    DynamicALMEngine,
    Holdings,
)

# Project root


def create_sample_liability_cashflows(n_trials: int = 3, n_timesteps: int = 24) -> pd.DataFrame:
    """Create sample liability cashflows for demonstration."""
    data = []

    for trial in range(1, n_trials + 1):
        for t in range(n_timesteps + 1):
            # Simulate declining net cashflow over time
            # Early years: positive (premiums > benefits)
            # Later years: negative (benefits > premiums)
            if t < 12:
                net_cf = 1000.0 - t * 50  # Declining from 1000 to 450
            else:
                net_cf = -200.0 - (t - 12) * 30  # Increasing outflows

            data.append(
                {
                    "Trial": trial,
                    "Timestep": t,
                    "NetCF_liab": net_cf,
                }
            )

    return pd.DataFrame(data)


def simple_saa_schedule(timestep: int) -> dict:
    """
    Simple SAA schedule with glide path.

    Early years: Higher equity allocation
    Later years: Higher bond allocation
    """
    # Determine policy year (assuming monthly timesteps)
    policy_year = timestep // 12

    if policy_year < 5:
        # Early years: Growth-oriented
        return {
            "Govt": 0.20,
            "Credit": 0.20,
            "Equity": 0.50,
            "Cash": 0.10,
        }
    elif policy_year < 15:
        # Mid years: Balanced
        return {
            "Govt": 0.30,
            "Credit": 0.30,
            "Equity": 0.30,
            "Cash": 0.10,
        }
    else:
        # Later years: Conservative
        return {
            "Govt": 0.40,
            "Credit": 0.35,
            "Equity": 0.15,
            "Cash": 0.10,
        }


def main():
    """Run example Dynamic ALM projection."""

    print("=" * 80)
    print("Dynamic ALM Engine - Example Usage")
    print("=" * 80)

    # Step 1: Load ESG scenarios
    print("\n1. Loading ESG scenarios...")
    esg_path = PROJECT_ROOT / "data" / "esg" / "sample_scenarios.parquet"

    if not esg_path.exists():
        print(f"   ERROR: ESG file not found at {esg_path}")
        print("   Please run: python scripts/generate_sample_esg.py")
        return

    esg_df = pd.read_parquet(esg_path)
    print(f"   Loaded {len(esg_df):,} rows")
    print(f"   Trials: {esg_df['Trial'].nunique()}")
    print(f"   Timesteps: {esg_df['Timestep'].nunique()}")

    # Step 2: Create liability cashflows
    print("\n2. Creating sample liability cashflows...")
    n_trials = min(3, esg_df["Trial"].nunique())  # Use first 3 trials
    n_timesteps = min(24, esg_df["Timestep"].max())  # Use first 24 months

    liability_cf_df = create_sample_liability_cashflows(n_trials, n_timesteps)
    print(f"   Created {len(liability_cf_df):,} cashflow records")

    # Step 3: Set up initial assets
    print("\n3. Setting up initial Par fund assets...")
    initial_assets = Holdings()
    initial_assets.govt[10] = 5000.0  # 5,000 in 10Y government bonds
    initial_assets.credit[("A", 5)] = 3000.0  # 3,000 in A-rated 5Y credit
    initial_assets.equity = 4000.0  # 4,000 in equity
    initial_assets.cash = 1000.0  # 1,000 in cash

    total_initial = initial_assets.total_mv()
    print(f"   Total initial MV: {total_initial:,.0f}")
    print(f"   Weights: {initial_assets.get_weights()}")

    # Step 4: Configure ALM engine
    print("\n4. Configuring Dynamic ALM engine...")
    config = ALMConfig(
        rebalance_frequency="annual",  # Rebalance once per year
        target_cash_buffer=0.03,  # 3% cash target
        min_cash_buffer=0.01,  # 1% cash minimum
    )

    engine = DynamicALMEngine(config)
    print(f"   Rebalance frequency: {config.rebalance_frequency}")
    print(f"   Cash buffer: {config.min_cash_buffer:.1%} - {config.target_cash_buffer:.1%}")

    # Step 5: Project single trial
    print("\n5. Projecting Trial 1...")
    result_trial1 = engine.project_trial(
        trial=1,
        liability_cf_df=liability_cf_df,
        esg_df=esg_df,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result_trial1.to_dataframes()

    print(f"   Fund history: {len(fund_df)} timesteps")
    print(f"   Trades executed: {len(trade_df)}")
    print(f"   Reconciliation checks: {len(recon_df)}")

    # Step 6: Analyze results
    print("\n6. Analyzing Trial 1 results...")
    print("\n   Fund Evolution:")
    print(f"   {'Timestep':<10} {'MV_total':>12} {'NetCF_liab':>12} {'TC':>10} {'Status':>10}")
    print("   " + "-" * 60)

    for idx in [0, 6, 12, 18, 24]:
        if idx < len(fund_df):
            row = fund_df.iloc[idx]
            print(
                f"   {row['Timestep']:<10} {row['MV_total']:>12,.0f} "
                f"{row['NetCF_liab']:>12,.0f} {row['TransactionCosts']:>10,.2f} "
                f"{'OK' if row['WeightDriftMax'] < 0.1 else 'DRIFT':>10}"
            )

    # Final state
    final_row = fund_df.iloc[-1]
    print(f"\n   Final MV: {final_row['MV_total']:,.0f}")
    print("   Final weights:")
    for asset_class in ["Govt", "Credit", "Equity", "Cash"]:
        weight = final_row[f"MV_{asset_class.lower()}"] / final_row["MV_total"]
        print(f"     {asset_class}: {weight:>6.1%}")

    # Trade summary
    if len(trade_df) > 0:
        print("\n   Trade Summary:")
        print(f"     Total trades: {len(trade_df)}")
        print(f"     BUY trades: {(trade_df['Action'] == 'BUY').sum()}")
        print(f"     SELL trades: {(trade_df['Action'] == 'SELL').sum()}")
        print(f"     Total TC: {trade_df['TC_Amount'].sum():,.2f}")

        print("\n   Sample trades:")
        print(f"   {'T':<4} {'Action':<6} {'Bucket':<15} {'Amount':>12} {'TC':>8} {'Reason':<10}")
        print("   " + "-" * 65)
        for _, trade in trade_df.head(5).iterrows():
            print(
                f"   {trade['Timestep']:<4} {trade['Action']:<6} "
                f"{trade['Bucket']:<15} {trade['AmountGross']:>12,.0f} "
                f"{trade['TC_Amount']:>8,.2f} {trade['Reason']:<10}"
            )

    # Reconciliation
    if len(recon_df) > 0:
        ok_count = (recon_df["Status"] == "OK").sum()
        print(f"\n   Reconciliation: {ok_count}/{len(recon_df)} checks passed")

    # Step 7: Project all trials
    print("\n7. Projecting all trials...")
    result_all = engine.project_portfolio(
        liability_cf_df=liability_cf_df,
        esg_df=esg_df,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
        n_trials=n_trials,
        parallel=False,
    )

    fund_all_df, trade_all_df, recon_all_df = result_all.to_dataframes()

    print(f"   Total fund records: {len(fund_all_df):,}")
    print(f"   Total trades: {len(trade_all_df):,}")

    # Summary statistics across trials
    print("\n   Summary Statistics (Final Timestep):")
    final_timestep = fund_all_df["Timestep"].max()
    final_data = fund_all_df[fund_all_df["Timestep"] == final_timestep]

    print(f"   {'Metric':<20} {'Mean':>12} {'Std':>12} {'Min':>12} {'Max':>12}")
    print("   " + "-" * 70)

    for col in ["MV_total", "MV_govt", "MV_credit", "MV_equity", "MV_cash"]:
        stats = final_data[col].describe()
        print(
            f"   {col:<20} {stats['mean']:>12,.0f} {stats['std']:>12,.0f} "
            f"{stats['min']:>12,.0f} {stats['max']:>12,.0f}"
        )

    # Step 8: Save results
    print("\n8. Saving results...")
    output_dir = PROJECT_ROOT / "output" / "dynamic_alm"
    output_dir.mkdir(parents=True, exist_ok=True)

    fund_all_df.to_csv(output_dir / "fund_history.csv", index=False)
    trade_all_df.to_csv(output_dir / "trade_history.csv", index=False)
    recon_all_df.to_csv(output_dir / "reconciliation.csv", index=False)

    print(f"   Saved to: {output_dir}")
    print(f"     - fund_history.csv ({len(fund_all_df):,} rows)")
    print(f"     - trade_history.csv ({len(trade_all_df):,} rows)")
    print(f"     - reconciliation.csv ({len(recon_all_df):,} rows)")

    print("\n" + "=" * 80)
    print("Dynamic ALM projection completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    main()
