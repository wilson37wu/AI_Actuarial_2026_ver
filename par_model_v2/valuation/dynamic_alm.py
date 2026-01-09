"""
Dynamic Asset-Liability Management (ALM) Engine - MVP

Integrates liability cashflows with asset portfolio projection under ESG scenarios.
Implements per-timestep recursion with:
- ESG-driven asset returns
- Liability net cashflow application
- Deterministic buy/sell decisions
- Rebalancing to SAA targets

Sign Convention:
- NetCF_liab > 0: Premiums exceed benefits (cash inflow to fund)
- NetCF_liab < 0: Benefits exceed premiums (cash outflow from fund)
- Asset holdings: Always >= 0 (no shorting)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class ALMConfig:
    """Configuration for Dynamic ALM engine."""

    # Rebalancing
    rebalance_frequency: str = "each_step"  # 'each_step', 'annual', 'none'
    rebalance_threshold: float = 0.05  # Weight drift tolerance (if frequency='threshold')

    # Cash management
    target_cash_buffer: float = 0.02  # Target cash as % of total MV
    min_cash_buffer: float = 0.01  # Minimum cash as % of total MV
    max_cash_buffer: float = 0.10  # Maximum cash as % of total MV

    # Transaction costs (bps)
    tc_govt_short: float = 2.0  # 1Y-5Y
    tc_govt_mid: float = 3.0  # 5Y-10Y
    tc_govt_long: float = 5.0  # 10Y+
    tc_credit_aaa: float = 5.0
    tc_credit_aa: float = 7.0
    tc_credit_a: float = 10.0
    tc_credit_bbb: float = 15.0
    tc_credit_bb: float = 25.0
    tc_credit_b: float = 40.0
    tc_credit_ccc: float = 60.0
    tc_equity: float = 15.0
    tc_cash: float = 0.0

    # Sell order priority (lower = sell first)
    sell_order_govt: Dict[int, int] = field(
        default_factory=lambda: {1: 1, 2: 2, 3: 3, 5: 4, 7: 5, 10: 6, 15: 7, 20: 8, 30: 9}
    )

    def get_tc_bps(
        self, asset_type: str, tenor: Optional[int] = None, rating: Optional[str] = None
    ) -> float:
        """Get transaction cost in bps for an asset bucket."""
        if asset_type == "Govt":
            if tenor is None:
                return self.tc_govt_mid
            if tenor <= 5:
                return self.tc_govt_short
            elif tenor <= 10:
                return self.tc_govt_mid
            else:
                return self.tc_govt_long
        elif asset_type == "Credit":
            rating_map = {
                "AAA": self.tc_credit_aaa,
                "AA": self.tc_credit_aa,
                "A": self.tc_credit_a,
                "BBB": self.tc_credit_bbb,
                "BB": self.tc_credit_bb,
                "B": self.tc_credit_b,
                "CCC": self.tc_credit_ccc,
            }
            return rating_map.get(rating, self.tc_credit_a)
        elif asset_type == "Equity":
            return self.tc_equity
        elif asset_type == "Cash":
            return self.tc_cash
        else:
            return 10.0  # Default


@dataclass
class Holdings:
    """Asset holdings representation."""

    govt: Dict[int, float] = field(default_factory=dict)  # {tenor: MV}
    credit: Dict[Tuple[str, int], float] = field(default_factory=dict)  # {(rating, tenor): MV}
    equity: float = 0.0
    cash: float = 0.0

    def total_mv(self) -> float:
        """Calculate total market value."""
        govt_total = sum(self.govt.values())
        credit_total = sum(self.credit.values())
        return govt_total + credit_total + self.equity + self.cash

    def get_weights(self) -> Dict[str, float]:
        """Get asset class weights."""
        total = self.total_mv()
        if total <= 0:
            return {"Govt": 0.0, "Credit": 0.0, "Equity": 0.0, "Cash": 0.0}

        govt_total = sum(self.govt.values())
        credit_total = sum(self.credit.values())

        return {
            "Govt": govt_total / total,
            "Credit": credit_total / total,
            "Equity": self.equity / total,
            "Cash": self.cash / total,
        }

    def copy(self) -> "Holdings":
        """Create a deep copy."""
        return Holdings(
            govt=self.govt.copy(),
            credit=self.credit.copy(),
            equity=self.equity,
            cash=self.cash,
        )


@dataclass
class TradeRecord:
    """Record of a single trade."""

    trial: int
    timestep: int
    action: str  # 'BUY' or 'SELL'
    asset_type: str  # 'Govt', 'Credit', 'Equity', 'Cash'
    bucket: str  # e.g., 'Govt_10Y', 'Credit_A_5Y', 'Equity', 'Cash'
    amount_gross: float  # Absolute value of trade
    tc_bps: float  # Transaction cost in bps
    tc_amount: float  # Transaction cost in currency
    amount_net: float  # Net proceeds (sell) or net cost (buy)
    reason: str  # 'FUNDING', 'REBALANCE', 'INITIAL'


@dataclass
class ALMProjectionResult:
    """Result container for ALM projection."""

    fund_history: List[Dict[str, Any]] = field(default_factory=list)
    trade_history: List[TradeRecord] = field(default_factory=list)
    reconciliation: List[Dict[str, Any]] = field(default_factory=list)

    def to_dataframes(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Convert to DataFrames."""
        fund_df = pd.DataFrame(self.fund_history) if self.fund_history else pd.DataFrame()

        trade_df = (
            pd.DataFrame(
                [
                    {
                        "Trial": t.trial,
                        "Timestep": t.timestep,
                        "Action": t.action,
                        "AssetType": t.asset_type,
                        "Bucket": t.bucket,
                        "AmountGross": t.amount_gross,
                        "TC_bps": t.tc_bps,
                        "TC_Amount": t.tc_amount,
                        "AmountNet": t.amount_net,
                        "Reason": t.reason,
                    }
                    for t in self.trade_history
                ]
            )
            if self.trade_history
            else pd.DataFrame()
        )

        recon_df = pd.DataFrame(self.reconciliation) if self.reconciliation else pd.DataFrame()

        return fund_df, trade_df, recon_df


class DynamicALMEngine:
    """
    Dynamic ALM Engine - MVP Implementation

    Links liability cashflows with asset portfolio evolution under ESG scenarios.
    """

    def __init__(self, config: Optional[ALMConfig] = None):
        """
        Initialize Dynamic ALM Engine.

        Parameters
        ----------
        config : ALMConfig, optional
            Configuration parameters. Uses defaults if not provided.
        """
        self.config = config or ALMConfig()

    def project_trial(
        self,
        trial: int,
        liability_cf_df: pd.DataFrame,
        esg_df: pd.DataFrame,
        saa_schedule: Any,  # Function or object with get_weights(timestep) method
        initial_assets: Optional[Holdings] = None,
    ) -> ALMProjectionResult:
        """
        Project a single trial with dynamic asset-liability linkage.

        Parameters
        ----------
        trial : int
            Trial number to project
        liability_cf_df : pd.DataFrame
            Liability cashflows with columns: Trial, Timestep, NetCF_liab
            Optional: DividendsPaid, RequiredReserve, Inforce
        esg_df : pd.DataFrame
            ESG scenario data for this trial with columns:
            - Trial, Timestep
            - ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)
            - ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)
            - ESG.Assets.EquityAssets.E_CNY.TotalReturn
            - ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn
        saa_schedule : callable or object
            Function/object that returns SAA weights dict for a given timestep
        initial_assets : Holdings, optional
            Starting asset holdings. If None, starts with zero assets.

        Returns
        -------
        ALMProjectionResult
            Projection results with fund history, trades, and reconciliation
        """
        # Validate inputs
        self._validate_inputs(trial, liability_cf_df, esg_df)

        # Filter to this trial
        liab_trial = liability_cf_df[liability_cf_df["Trial"] == trial].sort_values("Timestep")
        esg_trial = esg_df[esg_df["Trial"] == trial].sort_values("Timestep")

        # Initialize result containers
        result = ALMProjectionResult()

        # Initialize holdings
        holdings = initial_assets.copy() if initial_assets else Holdings()

        # Get timesteps
        timesteps = sorted(liab_trial["Timestep"].unique())

        # Main recursion loop
        for i, t in enumerate(timesteps):
            # Get liability cashflow at this timestep
            liab_row = liab_trial[liab_trial["Timestep"] == t].iloc[0]
            net_cf_liab = liab_row.get("NetCF_liab", 0.0)

            # Step A: Apply ESG returns to holdings (only if not first timestep)
            mv_before = holdings.total_mv()
            portfolio_return_effect = 0.0

            if i > 0:  # Apply returns from previous timestep to current
                t_prev = timesteps[i - 1]
                holdings_after_return = self._apply_esg_returns(
                    holdings, trial, t_prev, t, esg_trial
                )
                mv_after_return = holdings_after_return.total_mv()
                portfolio_return_effect = mv_after_return - mv_before if mv_before > 0 else 0.0
            else:
                # First timestep: no return application yet
                holdings_after_return = holdings.copy()

            # Step B: Apply liability net cashflow to cash
            holdings_after_return.cash += net_cf_liab
            mv_after_cf = holdings_after_return.total_mv()

            # Step C: Funding rule if cash is negative or below minimum
            total_mv = mv_after_cf
            target_cash = total_mv * self.config.target_cash_buffer
            min_cash = total_mv * self.config.min_cash_buffer

            tc_total = 0.0

            if holdings_after_return.cash < min_cash:
                # Need to sell assets to restore cash
                cash_needed = target_cash - holdings_after_return.cash
                holdings_after_funding, tc_funding, trades_funding = self._sell_assets(
                    holdings_after_return, cash_needed, trial, t, reason="FUNDING"
                )
                result.trade_history.extend(trades_funding)
                tc_total += tc_funding
            else:
                holdings_after_funding = holdings_after_return

            # Step D: Rebalancing to SAA
            if self._should_rebalance(t):
                # Get SAA target weights
                saa_weights = self._get_saa_weights(saa_schedule, t)

                holdings_after_rebal, tc_rebal, trades_rebal = self._rebalance_to_saa(
                    holdings_after_funding, saa_weights, trial, t
                )
                result.trade_history.extend(trades_rebal)
                tc_total += tc_rebal
            else:
                holdings_after_rebal = holdings_after_funding

            # Final holdings for this timestep
            holdings = holdings_after_rebal
            mv_end = holdings.total_mv()

            # Record fund history
            weights = holdings.get_weights()
            weight_drift_max = self._calculate_weight_drift(
                weights, self._get_saa_weights(saa_schedule, t)
            )

            fund_record = {
                "Trial": trial,
                "Timestep": t,
                "MV_total": mv_end,
                "MV_govt": sum(holdings.govt.values()),
                "MV_credit": sum(holdings.credit.values()),
                "MV_equity": holdings.equity,
                "MV_cash": holdings.cash,
                "NetCF_liab": net_cf_liab,
                "PortfolioReturnEffect": portfolio_return_effect,
                "TransactionCosts": tc_total,
                "WeightDriftMax": weight_drift_max,
            }
            result.fund_history.append(fund_record)

            # Reconciliation check
            if i > 0:
                prev_mv = result.fund_history[i - 1]["MV_total"]
                expected_mv = prev_mv + portfolio_return_effect + net_cf_liab - tc_total
                mv_error = abs(mv_end - expected_mv)

                recon_record = {
                    "Trial": trial,
                    "Timestep": t,
                    "MV_RollforwardError": mv_error,
                    "Status": "OK" if mv_error < 1.0 else "CHECK",
                }
                result.reconciliation.append(recon_record)

        return result

    def _validate_inputs(self, trial: int, liability_cf_df: pd.DataFrame, esg_df: pd.DataFrame):
        """Validate input DataFrames."""
        # Check required columns
        required_liab_cols = ["Trial", "Timestep", "NetCF_liab"]
        for col in required_liab_cols:
            if col not in liability_cf_df.columns:
                raise ValueError(f"Missing required column in liability_cf_df: {col}")

        required_esg_cols = ["Trial", "Timestep"]
        for col in required_esg_cols:
            if col not in esg_df.columns:
                raise ValueError(f"Missing required column in esg_df: {col}")

        # Check trial exists
        if trial not in liability_cf_df["Trial"].values:
            raise ValueError(f"Trial {trial} not found in liability_cf_df")
        if trial not in esg_df["Trial"].values:
            raise ValueError(f"Trial {trial} not found in esg_df")

    def _apply_esg_returns(
        self,
        holdings: Holdings,
        trial: int,
        t: int,
        t_next: int,
        esg_trial: pd.DataFrame,
    ) -> Holdings:
        """Apply ESG returns to holdings from t to t_next."""
        new_holdings = Holdings()

        # Get ESG rows
        esg_t = esg_trial[esg_trial["Timestep"] == t]
        esg_t_next = esg_trial[esg_trial["Timestep"] == t_next]

        if esg_t.empty or esg_t_next.empty:
            # No ESG data, return unchanged
            return holdings.copy()

        esg_t = esg_t.iloc[0]
        esg_t_next = esg_t_next.iloc[0]

        # Government bonds
        for tenor, mv in holdings.govt.items():
            col_name = f"ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)"
            if col_name in esg_t.index and col_name in esg_t_next.index:
                price_t = esg_t[col_name]
                price_t_next = esg_t_next[col_name]
                if price_t > 0:
                    return_factor = price_t_next / price_t
                    new_holdings.govt[tenor] = mv * return_factor
                else:
                    new_holdings.govt[tenor] = mv
            else:
                new_holdings.govt[tenor] = mv

        # Credit bonds
        for (rating, tenor), mv in holdings.credit.items():
            col_name = f"ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)"
            if col_name in esg_t.index and col_name in esg_t_next.index:
                price_t = esg_t[col_name]
                price_t_next = esg_t_next[col_name]
                if price_t > 0:
                    return_factor = price_t_next / price_t
                    new_holdings.credit[(rating, tenor)] = mv * return_factor
                else:
                    new_holdings.credit[(rating, tenor)] = mv
            else:
                new_holdings.credit[(rating, tenor)] = mv

        # Equity
        equity_col = "ESG.Assets.EquityAssets.E_CNY.TotalReturn"
        if equity_col in esg_t_next.index:
            total_return = esg_t_next[equity_col]
            new_holdings.equity = holdings.equity * total_return
        else:
            new_holdings.equity = holdings.equity

        # Cash
        cash_col = "ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn"
        if cash_col in esg_t_next.index:
            cash_return = esg_t_next[cash_col]
            new_holdings.cash = holdings.cash * cash_return
        else:
            new_holdings.cash = holdings.cash

        return new_holdings

    def _sell_assets(
        self,
        holdings: Holdings,
        cash_needed: float,
        trial: int,
        timestep: int,
        reason: str = "FUNDING",
    ) -> Tuple[Holdings, float, List[TradeRecord]]:
        """
        Sell assets to raise cash following deterministic sell order.

        Returns
        -------
        new_holdings : Holdings
            Holdings after sales
        total_tc : float
            Total transaction costs
        trades : List[TradeRecord]
            List of trade records
        """
        new_holdings = holdings.copy()
        trades = []
        total_tc = 0.0
        cash_raised = 0.0

        if cash_needed <= 0:
            return new_holdings, total_tc, trades

        # Sell order: Govt (short to long), Credit (high rating to low, short to long), Equity

        # 1. Sell government bonds (short tenor first)
        govt_tenors_sorted = sorted(new_holdings.govt.keys())
        for tenor in govt_tenors_sorted:
            if cash_raised >= cash_needed:
                break

            mv_available = new_holdings.govt[tenor]
            if mv_available <= 0:
                continue

            amount_to_sell = min(mv_available, cash_needed - cash_raised)
            tc_bps = self.config.get_tc_bps("Govt", tenor=tenor)
            tc = amount_to_sell * tc_bps / 10000
            net_proceeds = amount_to_sell - tc

            new_holdings.govt[tenor] -= amount_to_sell
            cash_raised += net_proceeds
            total_tc += tc

            trades.append(
                TradeRecord(
                    trial=trial,
                    timestep=timestep,
                    action="SELL",
                    asset_type="Govt",
                    bucket=f"Govt_{tenor}Y",
                    amount_gross=amount_to_sell,
                    tc_bps=tc_bps,
                    tc_amount=tc,
                    amount_net=net_proceeds,
                    reason=reason,
                )
            )

        # 2. Sell credit bonds (AAA to CCC, short to long within rating)
        rating_order = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]
        for rating in rating_order:
            if cash_raised >= cash_needed:
                break

            # Get all tenors for this rating
            credit_keys = [(r, t) for (r, t) in new_holdings.credit.keys() if r == rating]
            credit_keys_sorted = sorted(credit_keys, key=lambda x: x[1])  # Sort by tenor

            for key in credit_keys_sorted:
                if cash_raised >= cash_needed:
                    break

                mv_available = new_holdings.credit[key]
                if mv_available <= 0:
                    continue

                amount_to_sell = min(mv_available, cash_needed - cash_raised)
                tc_bps = self.config.get_tc_bps("Credit", rating=key[0])
                tc = amount_to_sell * tc_bps / 10000
                net_proceeds = amount_to_sell - tc

                new_holdings.credit[key] -= amount_to_sell
                cash_raised += net_proceeds
                total_tc += tc

                trades.append(
                    TradeRecord(
                        trial=trial,
                        timestep=timestep,
                        action="SELL",
                        asset_type="Credit",
                        bucket=f"Credit_{key[0]}_{key[1]}Y",
                        amount_gross=amount_to_sell,
                        tc_bps=tc_bps,
                        tc_amount=tc,
                        amount_net=net_proceeds,
                        reason=reason,
                    )
                )

        # 3. Sell equity (last resort)
        if cash_raised < cash_needed and new_holdings.equity > 0:
            amount_to_sell = min(new_holdings.equity, cash_needed - cash_raised)
            tc_bps = self.config.get_tc_bps("Equity")
            tc = amount_to_sell * tc_bps / 10000
            net_proceeds = amount_to_sell - tc

            new_holdings.equity -= amount_to_sell
            cash_raised += net_proceeds
            total_tc += tc

            trades.append(
                TradeRecord(
                    trial=trial,
                    timestep=timestep,
                    action="SELL",
                    asset_type="Equity",
                    bucket="Equity",
                    amount_gross=amount_to_sell,
                    tc_bps=tc_bps,
                    tc_amount=tc,
                    amount_net=net_proceeds,
                    reason=reason,
                )
            )

        # Add net proceeds to cash
        new_holdings.cash += cash_raised

        return new_holdings, total_tc, trades

    def _rebalance_to_saa(
        self,
        holdings: Holdings,
        saa_weights: Dict[str, float],
        trial: int,
        timestep: int,
    ) -> Tuple[Holdings, float, List[TradeRecord]]:
        """
        Rebalance holdings to SAA target weights.

        Simplified MVP: Only rebalance at asset class level (Govt, Credit, Equity, Cash).
        """
        new_holdings = holdings.copy()
        trades = []
        total_tc = 0.0

        total_mv = holdings.total_mv()
        if total_mv <= 0:
            return new_holdings, total_tc, trades

        current_weights = holdings.get_weights()

        # Calculate target MVs
        target_mvs = {asset_class: total_mv * weight for asset_class, weight in saa_weights.items()}

        current_mvs = {
            "Govt": sum(holdings.govt.values()),
            "Credit": sum(holdings.credit.values()),
            "Equity": holdings.equity,
            "Cash": holdings.cash,
        }

        # Calculate trades needed
        trades_needed = {
            asset_class: target_mvs.get(asset_class, 0.0) - current_mvs.get(asset_class, 0.0)
            for asset_class in ["Govt", "Credit", "Equity", "Cash"]
        }

        # Execute sells first, then buys
        # Sells
        for asset_class in ["Govt", "Credit", "Equity", "Cash"]:
            trade_amount = trades_needed[asset_class]
            if trade_amount < 0:  # Sell
                amount_to_sell = abs(trade_amount)

                if asset_class == "Cash":
                    # Reduce cash directly
                    new_holdings.cash -= amount_to_sell
                elif asset_class == "Equity":
                    # Sell equity
                    actual_sell = min(amount_to_sell, new_holdings.equity)
                    tc_bps = self.config.get_tc_bps("Equity")
                    tc = actual_sell * tc_bps / 10000
                    net_proceeds = actual_sell - tc

                    new_holdings.equity -= actual_sell
                    new_holdings.cash += net_proceeds
                    total_tc += tc

                    trades.append(
                        TradeRecord(
                            trial=trial,
                            timestep=timestep,
                            action="SELL",
                            asset_type="Equity",
                            bucket="Equity",
                            amount_gross=actual_sell,
                            tc_bps=tc_bps,
                            tc_amount=tc,
                            amount_net=net_proceeds,
                            reason="REBALANCE",
                        )
                    )
                elif asset_class == "Govt":
                    # Sell govt bonds proportionally
                    govt_total = sum(new_holdings.govt.values())
                    if govt_total > 0:
                        for tenor, mv in list(new_holdings.govt.items()):
                            proportion = mv / govt_total
                            sell_amount = min(amount_to_sell * proportion, mv)

                            tc_bps = self.config.get_tc_bps("Govt", tenor=tenor)
                            tc = sell_amount * tc_bps / 10000
                            net_proceeds = sell_amount - tc

                            new_holdings.govt[tenor] -= sell_amount
                            new_holdings.cash += net_proceeds
                            total_tc += tc

                            trades.append(
                                TradeRecord(
                                    trial=trial,
                                    timestep=timestep,
                                    action="SELL",
                                    asset_type="Govt",
                                    bucket=f"Govt_{tenor}Y",
                                    amount_gross=sell_amount,
                                    tc_bps=tc_bps,
                                    tc_amount=tc,
                                    amount_net=net_proceeds,
                                    reason="REBALANCE",
                                )
                            )
                elif asset_class == "Credit":
                    # Sell credit bonds proportionally
                    credit_total = sum(new_holdings.credit.values())
                    if credit_total > 0:
                        for key, mv in list(new_holdings.credit.items()):
                            proportion = mv / credit_total
                            sell_amount = min(amount_to_sell * proportion, mv)

                            tc_bps = self.config.get_tc_bps("Credit", rating=key[0])
                            tc = sell_amount * tc_bps / 10000
                            net_proceeds = sell_amount - tc

                            new_holdings.credit[key] -= sell_amount
                            new_holdings.cash += net_proceeds
                            total_tc += tc

                            trades.append(
                                TradeRecord(
                                    trial=trial,
                                    timestep=timestep,
                                    action="SELL",
                                    asset_type="Credit",
                                    bucket=f"Credit_{key[0]}_{key[1]}Y",
                                    amount_gross=sell_amount,
                                    tc_bps=tc_bps,
                                    tc_amount=tc,
                                    amount_net=net_proceeds,
                                    reason="REBALANCE",
                                )
                            )

        # Buys
        for asset_class in ["Govt", "Credit", "Equity", "Cash"]:
            trade_amount = trades_needed[asset_class]
            if trade_amount > 0:  # Buy
                amount_to_buy = trade_amount

                if asset_class == "Cash":
                    # Already in cash, no action needed
                    pass
                elif asset_class == "Equity":
                    # Buy equity
                    tc_bps = self.config.get_tc_bps("Equity")
                    tc = amount_to_buy * tc_bps / 10000
                    total_cost = amount_to_buy + tc

                    if new_holdings.cash >= total_cost:
                        new_holdings.equity += amount_to_buy
                        new_holdings.cash -= total_cost
                        total_tc += tc

                        trades.append(
                            TradeRecord(
                                trial=trial,
                                timestep=timestep,
                                action="BUY",
                                asset_type="Equity",
                                bucket="Equity",
                                amount_gross=amount_to_buy,
                                tc_bps=tc_bps,
                                tc_amount=tc,
                                amount_net=total_cost,
                                reason="REBALANCE",
                            )
                        )
                elif asset_class == "Govt":
                    # Buy govt bonds (simplified: buy 10Y tenor)
                    tenor = 10
                    tc_bps = self.config.get_tc_bps("Govt", tenor=tenor)
                    tc = amount_to_buy * tc_bps / 10000
                    total_cost = amount_to_buy + tc

                    if new_holdings.cash >= total_cost:
                        new_holdings.govt[tenor] = new_holdings.govt.get(tenor, 0.0) + amount_to_buy
                        new_holdings.cash -= total_cost
                        total_tc += tc

                        trades.append(
                            TradeRecord(
                                trial=trial,
                                timestep=timestep,
                                action="BUY",
                                asset_type="Govt",
                                bucket=f"Govt_{tenor}Y",
                                amount_gross=amount_to_buy,
                                tc_bps=tc_bps,
                                tc_amount=tc,
                                amount_net=total_cost,
                                reason="REBALANCE",
                            )
                        )
                elif asset_class == "Credit":
                    # Buy credit bonds (simplified: buy A-rated 5Y)
                    rating = "A"
                    tenor = 5
                    tc_bps = self.config.get_tc_bps("Credit", rating=rating)
                    tc = amount_to_buy * tc_bps / 10000
                    total_cost = amount_to_buy + tc

                    if new_holdings.cash >= total_cost:
                        key = (rating, tenor)
                        new_holdings.credit[key] = new_holdings.credit.get(key, 0.0) + amount_to_buy
                        new_holdings.cash -= total_cost
                        total_tc += tc

                        trades.append(
                            TradeRecord(
                                trial=trial,
                                timestep=timestep,
                                action="BUY",
                                asset_type="Credit",
                                bucket=f"Credit_{rating}_{tenor}Y",
                                amount_gross=amount_to_buy,
                                tc_bps=tc_bps,
                                tc_amount=tc,
                                amount_net=total_cost,
                                reason="REBALANCE",
                            )
                        )

        return new_holdings, total_tc, trades

    def _should_rebalance(self, timestep: int) -> bool:
        """Determine if rebalancing should occur at this timestep."""
        if self.config.rebalance_frequency == "none":
            return False
        elif self.config.rebalance_frequency == "each_step":
            return True
        elif self.config.rebalance_frequency == "annual":
            return timestep % 12 == 0
        else:
            return False

    def _get_saa_weights(self, saa_schedule: Any, timestep: int) -> Dict[str, float]:
        """Get SAA weights for a given timestep."""
        if callable(saa_schedule):
            return saa_schedule(timestep)
        elif hasattr(saa_schedule, "get_weights"):
            return saa_schedule.get_weights(timestep)
        else:
            # Default: balanced portfolio
            return {"Govt": 0.30, "Credit": 0.30, "Equity": 0.30, "Cash": 0.10}

    def _calculate_weight_drift(
        self,
        actual_weights: Dict[str, float],
        target_weights: Dict[str, float],
    ) -> float:
        """Calculate maximum weight drift."""
        drifts = []
        for asset_class in ["Govt", "Credit", "Equity", "Cash"]:
            actual = actual_weights.get(asset_class, 0.0)
            target = target_weights.get(asset_class, 0.0)
            drifts.append(abs(actual - target))
        return max(drifts) if drifts else 0.0

    def project_portfolio(
        self,
        liability_cf_df: pd.DataFrame,
        esg_df: pd.DataFrame,
        saa_schedule: Any,
        initial_assets: Optional[Holdings] = None,
        n_trials: Optional[int] = None,
        parallel: bool = False,
    ) -> ALMProjectionResult:
        """
        Project multiple trials.

        Parameters
        ----------
        liability_cf_df : pd.DataFrame
            Liability cashflows for all trials
        esg_df : pd.DataFrame
            ESG scenarios for all trials
        saa_schedule : callable or object
            SAA schedule provider
        initial_assets : Holdings, optional
            Starting assets (same for all trials)
        n_trials : int, optional
            Number of trials to project. If None, uses all trials in data.
        parallel : bool, default False
            Whether to use parallel processing (not implemented in MVP)

        Returns
        -------
        ALMProjectionResult
            Combined results across all trials
        """
        if parallel:
            raise NotImplementedError("Parallel processing not implemented in MVP")

        # Determine trials to project
        if n_trials is None:
            trials = sorted(liability_cf_df["Trial"].unique())
        else:
            trials = list(range(1, n_trials + 1))

        # Combined result
        combined_result = ALMProjectionResult()

        # Project each trial
        for trial in trials:
            trial_result = self.project_trial(
                trial=trial,
                liability_cf_df=liability_cf_df,
                esg_df=esg_df,
                saa_schedule=saa_schedule,
                initial_assets=initial_assets,
            )

            # Append to combined result
            combined_result.fund_history.extend(trial_result.fund_history)
            combined_result.trade_history.extend(trial_result.trade_history)
            combined_result.reconciliation.extend(trial_result.reconciliation)

        return combined_result
