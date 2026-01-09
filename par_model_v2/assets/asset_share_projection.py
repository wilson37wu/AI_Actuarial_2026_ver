"""
Asset Share Projection with Non-Guaranteed Dividend Calculation

Implements asset share accounting for PAR policies including:
- Investment return application from ESG scenarios
- Shareholder Deficit Account (SDA) for negative asset share scenarios
- 70/30 profit sharing with lifetime cap enforcement
- Non-guaranteed dividend calculation

Accounting Conventions
----------------------
1. Dividend Mode: "accumulate" - dividends increase RB, paid at maturity/death
2. Lifetime Cap: Shareholder cumulative share ≤ 30% of total cumulative distributions
3. Excess Surplus: Remains in asset share as buffer
4. SDA Repayment: First priority before any profit sharing
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass
class DividendRules:
    """
    Configuration for non-guaranteed dividend calculation.

    Parameters
    ----------
    policyholder_share : float, default=0.70
        Policyholder share of distributable surplus (70%)
    shareholder_share : float, default=0.30
        Shareholder share of distributable surplus (30%)
    enforce_lifetime_cap : bool, default=True
        If True, enforce lifetime 30% cap on shareholder distributions
    dividend_mode : str, default='accumulate'
        How dividends are handled:
        - 'accumulate': Add to RB/terminal bonus, paid at maturity/death
        - 'pay': Pay immediately, reduce asset share
    required_reserve_buffer : float, default=0.0
        Minimum asset share buffer before surplus is distributable
        (as fraction of sum assured, e.g., 0.05 = 5% of SA)
    enable_deficit_account : bool, default=True
        If True, use Shareholder Deficit Account for negative asset share
    """

    policyholder_share: float = 0.70
    shareholder_share: float = 0.30
    enforce_lifetime_cap: bool = True
    dividend_mode: str = "accumulate"  # 'accumulate' or 'pay'
    required_reserve_buffer: float = 0.0
    enable_deficit_account: bool = True

    def validate(self):
        """Validate configuration."""
        if not np.isclose(self.policyholder_share + self.shareholder_share, 1.0, atol=1e-6):
            raise ValueError(
                f"Shares must sum to 1.0, got {self.policyholder_share + self.shareholder_share}"
            )

        if self.dividend_mode not in ["accumulate", "pay"]:
            raise ValueError(
                f"dividend_mode must be 'accumulate' or 'pay', got {self.dividend_mode}"
            )

        if self.required_reserve_buffer < 0:
            raise ValueError("required_reserve_buffer must be >= 0")


@dataclass
class AssetShareState:
    """
    State variables for asset share projection at a single timestep.

    All monetary values are in policy currency units.
    """

    # Core asset share
    asset_share: float = 0.0

    # Deficit accounting
    shareholder_deficit: float = 0.0  # SDA balance

    # Cumulative distributions (for lifetime cap enforcement)
    cum_policyholder_dividends: float = 0.0
    cum_shareholder_distributions: float = 0.0

    # Period flows
    period_premium: float = 0.0
    period_expenses: float = 0.0
    period_guaranteed_benefit: float = 0.0
    period_investment_return: float = 0.0
    period_ng_dividend: float = 0.0  # Non-guaranteed dividend

    # Accumulated RB (if dividend_mode='accumulate')
    accumulated_rb: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary for output."""
        return {
            "asset_share": self.asset_share,
            "shareholder_deficit": self.shareholder_deficit,
            "cum_policyholder_dividends": self.cum_policyholder_dividends,
            "cum_shareholder_distributions": self.cum_shareholder_distributions,
            "period_premium": self.period_premium,
            "period_expenses": self.period_expenses,
            "period_guaranteed_benefit": self.period_guaranteed_benefit,
            "period_investment_return": self.period_investment_return,
            "period_ng_dividend": self.period_ng_dividend,
            "accumulated_rb": self.accumulated_rb,
        }


class AssetShareProjector:
    """
    Project asset share and non-guaranteed dividends for a single policy.

    Parameters
    ----------
    dividend_rules : DividendRules
        Dividend calculation rules

    Examples
    --------
    >>> rules = DividendRules()
    >>> projector = AssetShareProjector(rules)
    >>> state = AssetShareState(asset_share=10000)
    >>> new_state = projector.step(
    ...     state=state,
    ...     premium=1000,
    ...     expenses=50,
    ...     guaranteed_benefit=0,
    ...     investment_return_factor=1.05,
    ...     sum_assured=100000,
    ... )
    """

    def __init__(self, dividend_rules: DividendRules):
        self.rules = dividend_rules
        self.rules.validate()

    def step(
        self,
        state: AssetShareState,
        premium: float,
        expenses: float,
        guaranteed_benefit: float,
        investment_return_factor: float,
        sum_assured: float,
    ) -> AssetShareState:
        """
        Execute one timestep of asset share projection.

        Parameters
        ----------
        state : AssetShareState
            Current state (will not be modified)
        premium : float
            Premium inflow for this period
        expenses : float
            Expenses and charges for this period
        guaranteed_benefit : float
            Guaranteed benefit payment (death/maturity/surrender)
        investment_return_factor : float
            Gross return factor (e.g., 1.05 for 5% return)
        sum_assured : float
            Sum assured (for reserve buffer calculation)

        Returns
        -------
        AssetShareState
            New state after this timestep

        Notes
        -----
        Accounting sequence:
        1. Add premium, deduct expenses
        2. Apply investment return
        3. Pay guaranteed benefits
        4. Handle negative asset share (SDA)
        5. Calculate distributable surplus
        6. Repay SDA if applicable
        7. Distribute remaining surplus (70/30 with lifetime cap)
        8. Credit/pay non-guaranteed dividend
        """
        # Create new state (copy cumulative trackers)
        new_state = AssetShareState(
            asset_share=state.asset_share,
            shareholder_deficit=state.shareholder_deficit,
            cum_policyholder_dividends=state.cum_policyholder_dividends,
            cum_shareholder_distributions=state.cum_shareholder_distributions,
            accumulated_rb=state.accumulated_rb,
        )

        # Record period flows
        new_state.period_premium = premium
        new_state.period_expenses = expenses
        new_state.period_guaranteed_benefit = guaranteed_benefit

        # Step 1: Add premium, deduct expenses
        new_state.asset_share += premium - expenses

        # Step 2: Apply investment return
        if new_state.asset_share > 0:
            investment_gain = new_state.asset_share * (investment_return_factor - 1.0)
            new_state.period_investment_return = investment_gain
            new_state.asset_share *= investment_return_factor
        else:
            new_state.period_investment_return = 0.0

        # Step 3: Pay guaranteed benefits
        new_state.asset_share -= guaranteed_benefit

        # Step 4: Handle negative asset share (Shareholder Deficit Account)
        if new_state.asset_share < 0 and self.rules.enable_deficit_account:
            deficit = -new_state.asset_share
            new_state.shareholder_deficit += deficit
            new_state.asset_share = 0.0

        # Step 5: Calculate distributable surplus
        required_buffer = self.rules.required_reserve_buffer * sum_assured
        distributable_surplus = max(0.0, new_state.asset_share - required_buffer)

        if distributable_surplus <= 0:
            # No surplus to distribute
            new_state.period_ng_dividend = 0.0
            return new_state

        # Step 6: Repay Shareholder Deficit Account (first priority)
        if new_state.shareholder_deficit > 0:
            repayment = min(distributable_surplus, new_state.shareholder_deficit)
            new_state.shareholder_deficit -= repayment
            distributable_surplus -= repayment
            # Repayment is shareholder cashflow (recouping past support)
            # Do NOT count toward lifetime cap (it's recovery, not profit share)

        if distributable_surplus <= 0:
            # All surplus used for SDA repayment
            new_state.period_ng_dividend = 0.0
            return new_state

        # Step 7: Distribute remaining surplus (70/30 with lifetime cap)
        ph_share_raw = distributable_surplus * self.rules.policyholder_share
        sh_share_raw = distributable_surplus * self.rules.shareholder_share

        # Enforce lifetime cap if enabled
        if self.rules.enforce_lifetime_cap:
            # Lifetime cap: cum_sh ≤ 0.30 * (cum_ph + cum_sh)
            # Equivalently: cum_sh ≤ 0.30/0.70 * cum_ph
            # Or: cum_sh / cum_ph ≤ 0.30/0.70 = 0.4286

            # Calculate what shareholder can take without breaching cap
            cum_ph_after = new_state.cum_policyholder_dividends + ph_share_raw
            cum_sh_after = new_state.cum_shareholder_distributions + sh_share_raw

            # Check if cap would be breached
            max_sh_allowed = 0.30 * (cum_ph_after + cum_sh_after)

            if cum_sh_after > max_sh_allowed:
                # Reduce shareholder share to stay at cap
                sh_share_actual = max(0.0, max_sh_allowed - new_state.cum_shareholder_distributions)

                # Reallocate excess to policyholder (or leave in asset share)
                excess = sh_share_raw - sh_share_actual
                ph_share_actual = ph_share_raw + excess  # Give excess to policyholder

                # Alternative: leave excess in asset share as buffer
                # ph_share_actual = ph_share_raw
                # (excess remains in asset_share)
            else:
                ph_share_actual = ph_share_raw
                sh_share_actual = sh_share_raw
        else:
            ph_share_actual = ph_share_raw
            sh_share_actual = sh_share_raw

        # Update cumulative trackers
        new_state.cum_policyholder_dividends += ph_share_actual
        new_state.cum_shareholder_distributions += sh_share_actual

        # Step 8: Credit/pay non-guaranteed dividend
        new_state.period_ng_dividend = ph_share_actual

        if self.rules.dividend_mode == "accumulate":
            # Add to accumulated RB (paid at maturity/death)
            new_state.accumulated_rb += ph_share_actual
            # Asset share stays as is (dividend not paid out yet)
        else:  # 'pay'
            # Pay immediately, reduce asset share
            new_state.asset_share -= ph_share_actual

        # Shareholder distribution reduces asset share
        new_state.asset_share -= sh_share_actual

        return new_state

    def project_policy(
        self,
        initial_state: AssetShareState,
        premiums: np.ndarray,
        expenses: np.ndarray,
        guaranteed_benefits: np.ndarray,
        investment_returns: np.ndarray,
        sum_assured: float,
    ) -> Tuple[List[AssetShareState], pd.DataFrame]:
        """
        Project asset share for entire policy lifetime.

        Parameters
        ----------
        initial_state : AssetShareState
            Starting state (typically asset_share=0)
        premiums : np.ndarray
            Premium inflows by timestep
        expenses : np.ndarray
            Expenses by timestep
        guaranteed_benefits : np.ndarray
            Guaranteed benefit payments by timestep
        investment_returns : np.ndarray
            Investment return factors by timestep
        sum_assured : float
            Sum assured

        Returns
        -------
        states : list of AssetShareState
            State at each timestep (including initial)
        df : pd.DataFrame
            DataFrame with all state variables by timestep
        """
        n_steps = len(premiums)
        states = [initial_state]

        current_state = initial_state

        for t in range(n_steps):
            new_state = self.step(
                state=current_state,
                premium=premiums[t],
                expenses=expenses[t],
                guaranteed_benefit=guaranteed_benefits[t],
                investment_return_factor=investment_returns[t],
                sum_assured=sum_assured,
            )
            states.append(new_state)
            current_state = new_state

        # Convert to DataFrame
        df = pd.DataFrame([s.to_dict() for s in states])
        df.insert(0, "timestep", range(len(states)))

        return states, df


def calculate_lifetime_shareholder_ratio(
    cum_policyholder: float,
    cum_shareholder: float,
) -> float:
    """
    Calculate lifetime shareholder distribution ratio.

    Returns shareholder share as percentage of total distributions.

    Parameters
    ----------
    cum_policyholder : float
        Cumulative policyholder dividends
    cum_shareholder : float
        Cumulative shareholder distributions

    Returns
    -------
    float
        Shareholder ratio (e.g., 0.30 for 30%)
        Returns 0.0 if no distributions occurred
    """
    total = cum_policyholder + cum_shareholder
    if total <= 0:
        return 0.0
    return cum_shareholder / total
