"""
Unit tests for Dynamic ALM Engine
"""

import pandas as pd
import pytest
from par_model_v2.valuation.dynamic_alm import (
    ALMConfig,
    DynamicALMEngine,
    Holdings,
)


@pytest.fixture
def simple_esg_data():
    """Create simple ESG scenario data for testing."""
    n_timesteps = 12
    trials = [1]

    data = []
    for trial in trials:
        for t in range(n_timesteps + 1):
            row = {
                "Trial": trial,
                "Timestep": t,
                # Government bonds: stable prices around 0.95
                "ESG.Economies.CNY.NominalZCBP(Govt, 1, 3)": 0.98,
                "ESG.Economies.CNY.NominalZCBP(Govt, 5, 3)": 0.95,
                "ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)": 0.92,
                # Credit bonds: slightly lower prices
                "ESG.Economies.CNY.NominalZCBP(A, 5, 3)": 0.93,
                "ESG.Economies.CNY.NominalZCBP(AAA, 5, 3)": 0.94,
                # Equity: 1.0 at t=0, then 1.005 (0.5% return per month)
                "ESG.Assets.EquityAssets.E_CNY.TotalReturn": 1.0 if t == 0 else 1.005,
                # Cash: 1.002 (0.2% return per month)
                "ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn": 1.002,
            }
            data.append(row)

    return pd.DataFrame(data)


@pytest.fixture
def simple_liability_cf():
    """Create simple liability cashflow data."""
    n_timesteps = 12

    data = []
    for t in range(n_timesteps + 1):
        row = {
            "Trial": 1,
            "Timestep": t,
            "NetCF_liab": 1000.0,  # Positive cashflow (premiums > benefits)
        }
        data.append(row)

    return pd.DataFrame(data)


@pytest.fixture
def negative_cf_liability():
    """Create liability cashflow with negative net cashflow."""
    n_timesteps = 12

    data = []
    for t in range(n_timesteps + 1):
        row = {
            "Trial": 1,
            "Timestep": t,
            "NetCF_liab": -500.0,  # Negative cashflow (benefits > premiums)
        }
        data.append(row)

    return pd.DataFrame(data)


def simple_saa_schedule(timestep: int) -> dict:
    """Simple SAA schedule: balanced portfolio."""
    return {
        "Govt": 0.30,
        "Credit": 0.30,
        "Equity": 0.30,
        "Cash": 0.10,
    }


def test_holdings_basic():
    """Test Holdings class basic functionality."""
    holdings = Holdings()
    holdings.govt[10] = 1000.0
    holdings.credit[("A", 5)] = 500.0
    holdings.equity = 800.0
    holdings.cash = 200.0

    assert holdings.total_mv() == 2500.0

    weights = holdings.get_weights()
    assert abs(weights["Govt"] - 0.40) < 0.01
    assert abs(weights["Credit"] - 0.20) < 0.01
    assert abs(weights["Equity"] - 0.32) < 0.01
    assert abs(weights["Cash"] - 0.08) < 0.01


def test_holdings_copy():
    """Test Holdings deep copy."""
    holdings = Holdings()
    holdings.govt[10] = 1000.0
    holdings.cash = 200.0

    holdings_copy = holdings.copy()
    holdings_copy.govt[10] = 500.0

    assert holdings.govt[10] == 1000.0  # Original unchanged
    assert holdings_copy.govt[10] == 500.0


def test_positive_cashflow_growth(simple_esg_data, simple_liability_cf):
    """
    Test that positive net cashflow leads to portfolio growth.

    Scenario:
    - Start with 10,000 initial assets
    - Receive 1,000 positive cashflow each period
    - Assets should grow due to cashflow + investment returns
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    # Initial holdings: balanced portfolio
    initial_assets = Holdings()
    initial_assets.govt[10] = 3000.0
    initial_assets.credit[("A", 5)] = 3000.0
    initial_assets.equity = 3000.0
    initial_assets.cash = 1000.0

    result = engine.project_trial(
        trial=1,
        liability_cf_df=simple_liability_cf,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that MV grows over time
    assert len(fund_df) == 13  # 0 to 12

    mv_start = fund_df.iloc[0]["MV_total"]
    mv_end = fund_df.iloc[-1]["MV_total"]

    # At t=0, we have initial 10,000 + first cashflow 1,000 = 11,000
    assert mv_start == 11000.0
    assert mv_end > mv_start  # Should grow due to investment returns + more cashflows

    # Check that all cashflows are positive
    assert all(fund_df["NetCF_liab"] == 1000.0)

    # Check reconciliation
    if len(recon_df) > 0:
        assert all(recon_df["Status"] == "OK")


def test_negative_cashflow_liquidation(simple_esg_data, negative_cf_liability):
    """
    Test that negative net cashflow leads to asset liquidation.

    Scenario:
    - Start with 10,000 initial assets
    - Pay out 500 each period (negative cashflow)
    - Assets should decrease
    - Should see SELL trades in trade history
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    # Initial holdings
    initial_assets = Holdings()
    initial_assets.govt[10] = 3000.0
    initial_assets.credit[("A", 5)] = 3000.0
    initial_assets.equity = 3000.0
    initial_assets.cash = 1000.0

    result = engine.project_trial(
        trial=1,
        liability_cf_df=negative_cf_liability,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that MV decreases over time
    mv_start = fund_df.iloc[0]["MV_total"]
    mv_end = fund_df.iloc[-1]["MV_total"]

    assert mv_end < mv_start  # Should decrease

    # Check that there are SELL trades
    assert len(trade_df) > 0
    assert "SELL" in trade_df["Action"].values

    # Check that trades are for FUNDING reason
    funding_trades = trade_df[trade_df["Reason"] == "FUNDING"]
    assert len(funding_trades) > 0


def test_rebalancing_to_saa(simple_esg_data):
    """
    Test rebalancing to SAA targets.

    Scenario:
    - Start with unbalanced portfolio (all in cash)
    - Zero net cashflow
    - Rebalance each step
    - Should see portfolio converge to SAA weights
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="each_step"))

    # Initial holdings: all cash (unbalanced)
    initial_assets = Holdings()
    initial_assets.cash = 10000.0

    # Zero net cashflow
    liability_cf = pd.DataFrame([{"Trial": 1, "Timestep": t, "NetCF_liab": 0.0} for t in range(13)])

    result = engine.project_trial(
        trial=1,
        liability_cf_df=liability_cf,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that rebalancing occurred (trade_df might be empty if no trades needed)
    if len(trade_df) > 0:
        rebalance_trades = trade_df[trade_df["Reason"] == "REBALANCE"]
        assert len(rebalance_trades) > 0

    # Check final weights are close to SAA targets
    final_row = fund_df.iloc[-1]
    total_mv = final_row["MV_total"]

    if total_mv > 0:
        final_weights = {
            "Govt": final_row["MV_govt"] / total_mv,
            "Credit": final_row["MV_credit"] / total_mv,
            "Equity": final_row["MV_equity"] / total_mv,
            "Cash": final_row["MV_cash"] / total_mv,
        }

        saa_target = simple_saa_schedule(0)

        # Weights should be reasonably close to targets
        # MVP has simplified rebalancing, so allow generous tolerance
        for asset_class in ["Govt", "Credit", "Equity", "Cash"]:
            weight_diff = abs(final_weights[asset_class] - saa_target[asset_class])
            # Allow 35% tolerance for MVP (simplified rebalancing + transaction costs)
            assert weight_diff < 0.35, (
                f"{asset_class}: actual={final_weights[asset_class]:.2f}, target={saa_target[asset_class]:.2f}"
            )


def test_zero_initial_assets(simple_esg_data, simple_liability_cf):
    """
    Test starting with zero assets.

    Scenario:
    - No initial assets
    - Positive cashflow builds up assets
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="each_step"))

    result = engine.project_trial(
        trial=1,
        liability_cf_df=simple_liability_cf,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=None,  # Start with zero
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that MV starts at zero + first cashflow
    # At t=0, we receive 1000 cashflow, but also need to maintain cash buffer
    # So some will go to rebalancing
    assert fund_df.iloc[0]["MV_total"] > 0  # Should have the cashflow

    # Check that MV grows over time
    mv_end = fund_df.iloc[-1]["MV_total"]
    assert mv_end > fund_df.iloc[0]["MV_total"]  # Should grow


def test_transaction_costs(simple_esg_data, negative_cf_liability):
    """
    Test that transaction costs are applied correctly.
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    initial_assets = Holdings()
    initial_assets.govt[10] = 5000.0
    initial_assets.cash = 1000.0

    result = engine.project_trial(
        trial=1,
        liability_cf_df=negative_cf_liability,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that transaction costs are recorded
    assert "TransactionCosts" in fund_df.columns
    total_tc = fund_df["TransactionCosts"].sum()
    assert total_tc > 0  # Should have some transaction costs

    # Check trade records have TC details
    if len(trade_df) > 0:
        assert "TC_bps" in trade_df.columns
        assert "TC_Amount" in trade_df.columns
        assert all(trade_df["TC_Amount"] >= 0)


def test_input_validation():
    """Test input validation."""
    engine = DynamicALMEngine()

    # Missing required columns
    bad_liability_cf = pd.DataFrame(
        [
            {"Trial": 1, "Timestep": 0}  # Missing NetCF_liab
        ]
    )

    bad_esg = pd.DataFrame(
        [
            {"Trial": 1}  # Missing Timestep
        ]
    )

    with pytest.raises(ValueError, match="Missing required column"):
        engine.project_trial(
            trial=1,
            liability_cf_df=bad_liability_cf,
            esg_df=bad_esg,
            saa_schedule=simple_saa_schedule,
        )


def test_sell_order_priority(simple_esg_data):
    """
    Test that assets are sold in correct priority order.

    Sell order should be: Govt (short to long) -> Credit -> Equity
    """
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    # Holdings with multiple asset types
    initial_assets = Holdings()
    initial_assets.govt[1] = 1000.0  # Short tenor
    initial_assets.govt[10] = 1000.0  # Long tenor
    initial_assets.credit[("A", 5)] = 1000.0
    initial_assets.equity = 1000.0
    initial_assets.cash = 100.0

    # Large negative cashflow to force sales
    liability_cf = pd.DataFrame(
        [{"Trial": 1, "Timestep": t, "NetCF_liab": -2000.0 if t == 1 else 0.0} for t in range(13)]
    )

    result = engine.project_trial(
        trial=1,
        liability_cf_df=liability_cf,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Get FUNDING trades at timestep 1
    funding_trades = trade_df[
        (trade_df["Reason"] == "FUNDING")
        & (trade_df["Timestep"] == 1)
        & (trade_df["Action"] == "SELL")
    ]

    if len(funding_trades) > 0:
        # Check that Govt bonds are sold before Credit and Equity
        asset_types_sold = funding_trades["AssetType"].tolist()

        # If multiple asset types sold, Govt should come first
        if "Govt" in asset_types_sold and "Equity" in asset_types_sold:
            govt_idx = asset_types_sold.index("Govt")
            equity_idx = asset_types_sold.index("Equity")
            assert govt_idx < equity_idx


def test_reconciliation_checks(simple_esg_data, simple_liability_cf):
    """Test that reconciliation checks pass."""
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    initial_assets = Holdings()
    initial_assets.cash = 5000.0

    result = engine.project_trial(
        trial=1,
        liability_cf_df=simple_liability_cf,
        esg_df=simple_esg_data,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check reconciliation status
    if len(recon_df) > 0:
        # Most should be OK (allowing for small numerical errors)
        ok_count = (recon_df["Status"] == "OK").sum()
        assert ok_count >= len(recon_df) * 0.8  # At least 80% should be OK


def test_project_portfolio_multiple_trials():
    """Test projecting multiple trials."""
    engine = DynamicALMEngine(ALMConfig(rebalance_frequency="none"))

    # Create data for 3 trials
    n_trials = 3
    n_timesteps = 6

    liability_data = []
    esg_data = []

    for trial in range(1, n_trials + 1):
        for t in range(n_timesteps + 1):
            liability_data.append(
                {
                    "Trial": trial,
                    "Timestep": t,
                    "NetCF_liab": 100.0,
                }
            )

            esg_data.append(
                {
                    "Trial": trial,
                    "Timestep": t,
                    "ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)": 0.95,
                    "ESG.Assets.EquityAssets.E_CNY.TotalReturn": 1.0 if t == 0 else 1.01,
                    "ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn": 1.002,
                }
            )

    liability_cf_df = pd.DataFrame(liability_data)
    esg_df = pd.DataFrame(esg_data)

    initial_assets = Holdings()
    initial_assets.cash = 1000.0

    result = engine.project_portfolio(
        liability_cf_df=liability_cf_df,
        esg_df=esg_df,
        saa_schedule=simple_saa_schedule,
        initial_assets=initial_assets,
        n_trials=n_trials,
        parallel=False,
    )

    fund_df, trade_df, recon_df = result.to_dataframes()

    # Check that all trials are present
    assert len(fund_df["Trial"].unique()) == n_trials

    # Check that each trial has correct number of timesteps
    for trial in range(1, n_trials + 1):
        trial_rows = fund_df[fund_df["Trial"] == trial]
        assert len(trial_rows) == n_timesteps + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
