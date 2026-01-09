"""
Fund Portfolio Management with Strategic Asset Allocation

Implements policy-level or fund-level portfolio management including:
- Asset bucket tracking (Govt, Credit, Equity, Cash)
- ESG-driven return application
- Net cashflow handling (buy/sell decisions)
- Rebalancing toward SAA targets
- Transaction cost modeling
- Trade logging for audit

Modelling Approach
------------------
Policy-level: Each policy has its own mini-portfolio
- Simpler implementation
- Consistent with asset share per policy
- Easier to parallelize
- Fund-level view obtained by aggregation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class AssetClass(Enum):
    """Asset class identifiers."""

    GOVT = "Govt"
    CREDIT_AAA = "Credit_AAA"
    CREDIT_AA = "Credit_AA"
    CREDIT_A = "Credit_A"
    CREDIT_BBB = "Credit_BBB"
    CREDIT_BB = "Credit_BB"
    CREDIT_B = "Credit_B"
    EQUITY = "Equity"
    CASH = "Cash"

    @classmethod
    def from_string(cls, s: str) -> AssetClass:
        """Convert string to AssetClass enum."""
        mapping = {e.value: e for e in cls}
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Unknown asset class: {s}")

    @property
    def is_credit(self) -> bool:
        """Check if this is a credit asset."""
        return self.value.startswith("Credit_")

    @property
    def credit_rating(self) -> Optional[str]:
        """Extract credit rating if applicable."""
        if self.is_credit:
            return self.value.split("_")[1]
        return None


@dataclass
class TradingPolicy:
    """
    Configuration for trading and rebalancing decisions.

    Parameters
    ----------
    rebalance_frequency : str
        'each_step', 'annual', or 'none'
    sell_order : list of AssetClass
        Order in which to sell assets when raising cash
        Default: [Cash, Govt, Credit, Equity]
    transaction_cost_bps : float
        Transaction cost in basis points (e.g., 10 = 0.10%)
    allow_shorting : bool
        If False, cannot sell more than current holdings
    rebalance_threshold : float
        Only rebalance if drift from target exceeds this (e.g., 0.05 = 5%)
    """

    rebalance_frequency: str = "each_step"
    sell_order: List[AssetClass] = field(
        default_factory=lambda: [
            AssetClass.CASH,
            AssetClass.GOVT,
            AssetClass.CREDIT_A,
            AssetClass.EQUITY,
        ]
    )
    transaction_cost_bps: float = 5.0
    allow_shorting: bool = False
    rebalance_threshold: float = 0.0  # 0 = always rebalance

    def validate(self):
        """Validate configuration."""
        valid_freq = ["each_step", "annual", "none"]
        if self.rebalance_frequency not in valid_freq:
            raise ValueError(
                f"rebalance_frequency must be one of {valid_freq}, got {self.rebalance_frequency}"
            )

        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be >= 0")


@dataclass
class TradeRecord:
    """Record of a single trade action."""

    timestep: int
    asset_class: AssetClass
    trade_amount: float  # Positive = buy, negative = sell
    transaction_cost: float
    reason: str  # 'cashflow', 'rebalance', 'initial'


@dataclass
class PortfolioSnapshot:
    """Snapshot of portfolio state at a point in time."""

    timestep: int
    market_values: Dict[AssetClass, float]
    total_mv: float
    weights: Dict[AssetClass, float]
    target_weights: Dict[AssetClass, float]
    net_cashflow: float
    trades: List[TradeRecord]
    shareholder_deficit: float

    def to_dict(self) -> Dict:
        """Convert to dictionary for output."""
        return {
            "timestep": self.timestep,
            "total_mv": self.total_mv,
            "net_cashflow": self.net_cashflow,
            "shareholder_deficit": self.shareholder_deficit,
            **{f"mv_{ac.value}": self.market_values.get(ac, 0.0) for ac in AssetClass},
            **{f"weight_{ac.value}": self.weights.get(ac, 0.0) for ac in AssetClass},
            **{f"target_{ac.value}": self.target_weights.get(ac, 0.0) for ac in AssetClass},
            "n_trades": len(self.trades),
            "total_trade_cost": sum(t.transaction_cost for t in self.trades),
        }


class FundPortfolio:
    """
    Manages a portfolio of assets for PAR fund backing.

    Tracks market values by asset class, applies returns from ESG scenarios,
    handles net cashflows via trading, and rebalances toward SAA targets.

    Parameters
    ----------
    initial_assets : dict, optional
        Initial market values by AssetClass
        If None, starts with zero assets
    trading_policy : TradingPolicy, optional
        Trading and rebalancing configuration

    Examples
    --------
    >>> portfolio = FundPortfolio(
    ...     initial_assets={AssetClass.CASH: 10000},
    ...     trading_policy=TradingPolicy(rebalance_frequency='annual')
    ... )
    >>> portfolio.apply_returns(esg_provider, trial=1, timestep=1)
    >>> portfolio.apply_net_cashflow(
    ...     net_cf=1000,
    ...     saa_weights={AssetClass.EQUITY: 0.5, AssetClass.CASH: 0.5},
    ...     timestep=1
    ... )
    """

    def __init__(
        self,
        initial_assets: Optional[Dict[AssetClass, float]] = None,
        trading_policy: Optional[TradingPolicy] = None,
    ):
        self.market_values = initial_assets or {}
        self.trading_policy = trading_policy or TradingPolicy()
        self.trading_policy.validate()

        # State tracking
        self.current_timestep = 0
        self.shareholder_deficit = 0.0
        self.trade_history: List[TradeRecord] = []
        self.snapshots: List[PortfolioSnapshot] = []

        # Ensure all asset classes have entries (even if 0)
        for ac in AssetClass:
            if ac not in self.market_values:
                self.market_values[ac] = 0.0

    @property
    def total_market_value(self) -> float:
        """Total portfolio market value."""
        return sum(self.market_values.values())

    def get_weights(self) -> Dict[AssetClass, float]:
        """Current portfolio weights."""
        total = self.total_market_value
        if total <= 0:
            return {ac: 0.0 for ac in AssetClass}
        return {ac: mv / total for ac, mv in self.market_values.items()}

    def apply_returns(
        self,
        returns_by_asset: Dict[AssetClass, float],
        timestep: int,
    ):
        """
        Apply investment returns to all asset buckets.

        Parameters
        ----------
        returns_by_asset : dict
            Gross return factors by AssetClass (e.g., 1.05 for 5% return)
        timestep : int
            Current timestep
        """
        self.current_timestep = timestep

        for ac in AssetClass:
            if ac in returns_by_asset and self.market_values[ac] > 0:
                return_factor = returns_by_asset[ac]
                self.market_values[ac] *= return_factor

    def apply_net_cashflow(
        self,
        net_cashflow: float,
        saa_weights: Dict[AssetClass, float],
        timestep: int,
    ) -> Tuple[float, List[TradeRecord]]:
        """
        Apply net cashflow and rebalance toward SAA targets.

        Parameters
        ----------
        net_cashflow : float
            Net cashflow (positive = inflow, negative = outflow)
            = Premiums - Benefits - Expenses - Dividends
        saa_weights : dict
            Target allocation weights by AssetClass (must sum to 1.0)
        timestep : int
            Current timestep

        Returns
        -------
        deficit_created : float
            Amount of deficit created if assets insufficient (>= 0)
        trades : list of TradeRecord
            Trades executed this period
        """
        self.current_timestep = timestep
        trades = []

        # Validate SAA weights
        total_weight = sum(saa_weights.values())
        if not np.isclose(total_weight, 1.0, atol=1e-6):
            raise ValueError(f"SAA weights must sum to 1.0, got {total_weight}")

        # Current total MV before cashflow
        mv_before = self.total_market_value

        # Target total MV after cashflow
        target_total = mv_before + net_cashflow

        if target_total < 0:
            # Insufficient assets to meet outflow
            # Liquidate everything and create deficit
            deficit_created = -target_total

            # Sell all assets
            for ac in AssetClass:
                if self.market_values[ac] > 0:
                    trade = TradeRecord(
                        timestep=timestep,
                        asset_class=ac,
                        trade_amount=-self.market_values[ac],
                        transaction_cost=0.0,  # No cost when forced liquidation
                        reason="deficit_liquidation",
                    )
                    trades.append(trade)
                    self.market_values[ac] = 0.0

            self.shareholder_deficit += deficit_created
            self.trade_history.extend(trades)
            return deficit_created, trades

        # Compute target MVs by asset class
        target_mvs = {ac: target_total * weight for ac, weight in saa_weights.items()}

        # Compute required trades
        required_trades = {
            ac: target_mvs.get(ac, 0.0) - self.market_values[ac] for ac in AssetClass
        }

        # Execute trades with constraints
        trades = self._execute_trades(required_trades, timestep)

        self.trade_history.extend(trades)
        return 0.0, trades

    def _execute_trades(
        self,
        required_trades: Dict[AssetClass, float],
        timestep: int,
    ) -> List[TradeRecord]:
        """
        Execute trades with constraints (no shorting, sell order).

        Parameters
        ----------
        required_trades : dict
            Required trade amounts by AssetClass
            Positive = buy, negative = sell
        timestep : int
            Current timestep

        Returns
        -------
        list of TradeRecord
            Executed trades
        """
        trades = []

        # Separate buys and sells
        sells = {ac: amt for ac, amt in required_trades.items() if amt < 0}
        buys = {ac: amt for ac, amt in required_trades.items() if amt > 0}

        # Execute sells first (to raise cash for buys)
        for ac in self.trading_policy.sell_order:
            if ac not in sells:
                continue

            sell_amount = -sells[ac]  # Make positive
            available = self.market_values[ac]

            if not self.trading_policy.allow_shorting:
                sell_amount = min(sell_amount, available)

            if sell_amount > 0:
                # Apply transaction cost
                tc = sell_amount * self.trading_policy.transaction_cost_bps / 10000

                trade = TradeRecord(
                    timestep=timestep,
                    asset_class=ac,
                    trade_amount=-sell_amount,
                    transaction_cost=tc,
                    reason="rebalance_sell",
                )
                trades.append(trade)

                # Update market value
                self.market_values[ac] -= sell_amount

        # Execute buys
        for ac, buy_amount in buys.items():
            if buy_amount > 0:
                # Apply transaction cost
                tc = buy_amount * self.trading_policy.transaction_cost_bps / 10000

                trade = TradeRecord(
                    timestep=timestep,
                    asset_class=ac,
                    trade_amount=buy_amount,
                    transaction_cost=tc,
                    reason="rebalance_buy",
                )
                trades.append(trade)

                # Update market value (net of transaction cost)
                self.market_values[ac] += buy_amount - tc

        return trades

    def snapshot(
        self,
        timestep: int,
        saa_weights: Dict[AssetClass, float],
        net_cashflow: float = 0.0,
    ) -> PortfolioSnapshot:
        """
        Create a snapshot of current portfolio state.

        Parameters
        ----------
        timestep : int
            Current timestep
        saa_weights : dict
            Target SAA weights
        net_cashflow : float
            Net cashflow this period

        Returns
        -------
        PortfolioSnapshot
        """
        snap = PortfolioSnapshot(
            timestep=timestep,
            market_values=self.market_values.copy(),
            total_mv=self.total_market_value,
            weights=self.get_weights(),
            target_weights=saa_weights,
            net_cashflow=net_cashflow,
            trades=[t for t in self.trade_history if t.timestep == timestep],
            shareholder_deficit=self.shareholder_deficit,
        )

        self.snapshots.append(snap)
        return snap

    def get_history_dataframe(self) -> pd.DataFrame:
        """
        Get portfolio history as DataFrame.

        Returns
        -------
        pd.DataFrame
            Portfolio snapshots over time
        """
        if not self.snapshots:
            return pd.DataFrame()

        return pd.DataFrame([s.to_dict() for s in self.snapshots])

    def get_trade_dataframe(self) -> pd.DataFrame:
        """
        Get trade history as DataFrame.

        Returns
        -------
        pd.DataFrame
            All trades executed
        """
        if not self.trade_history:
            return pd.DataFrame()

        records = []
        for trade in self.trade_history:
            records.append(
                {
                    "timestep": trade.timestep,
                    "asset_class": trade.asset_class.value,
                    "trade_amount": trade.trade_amount,
                    "transaction_cost": trade.transaction_cost,
                    "reason": trade.reason,
                }
            )

        return pd.DataFrame(records)


def load_initial_assets(
    file_path: str,
    fund_id: str = "PAR",
) -> Dict[AssetClass, float]:
    """
    Load initial fund assets from CSV file.

    Parameters
    ----------
    file_path : str
        Path to initial_fund_assets.csv
    fund_id : str
        Fund identifier to filter

    Returns
    -------
    dict
        Initial market values by AssetClass
    """
    df = pd.read_csv(file_path)

    # Filter by fund_id
    df = df[df["fund_id"] == fund_id].copy()

    if len(df) == 0:
        raise ValueError(f"No assets found for fund_id={fund_id}")

    # Convert to dict
    initial_assets = {}
    for _, row in df.iterrows():
        ac = AssetClass.from_string(row["asset_class"])
        mv = float(row["market_value"])
        initial_assets[ac] = mv

    return initial_assets
