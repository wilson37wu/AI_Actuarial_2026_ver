"""
Asset share engine with 70/30 profit sharing mechanism.

This module implements policy-level asset share projection with:
- 70/30 profit sharing (policyholder/shareholder)
- Shareholder deficit account (SDA) tracking
- Lifetime shareholder cap enforcement
- Reversionary and terminal bonus calculation
- Integration with Dynamic ALM engine
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AssetShareConfig:
    """Configuration for asset share projection."""

    policyholder_share: float = 0.70
    shareholder_share: float = 0.30
    lifetime_shareholder_cap: float = 0.15  # 15% of cumulative premiums
    sda_repayment_priority: bool = True
    smoothing_method: str = "exponential"  # or "target_bonus"
    smoothing_alpha: float = 0.3
    min_bonus_rate: float = 0.0
    max_bonus_rate: float = 0.10
    terminal_bonus_factor: float = 0.5  # 50% of accumulated surplus


@dataclass
class PolicyState:
    """Current state of a policy's asset share."""

    policy_id: str
    timestep: int
    asset_share: float
    cumulative_premiums: float
    cumulative_shareholder_profit: float
    shareholder_deficit: float
    guaranteed_benefit: float
    reversionary_bonus_accumulated: float
    terminal_bonus_pool: float
    is_active: bool = True

    def to_dict(self) -> Dict:
        """Convert to dictionary for DataFrame construction."""
        return {
            "policy_id": self.policy_id,
            "timestep": self.timestep,
            "asset_share": self.asset_share,
            "cumulative_premiums": self.cumulative_premiums,
            "cumulative_shareholder_profit": self.cumulative_shareholder_profit,
            "shareholder_deficit": self.shareholder_deficit,
            "guaranteed_benefit": self.guaranteed_benefit,
            "reversionary_bonus_accumulated": self.reversionary_bonus_accumulated,
            "terminal_bonus_pool": self.terminal_bonus_pool,
            "is_active": self.is_active,
        }


@dataclass
class PolicyCashflow:
    """Cashflows for a single policy at a timestep."""

    policy_id: str
    timestep: int
    premium: float = 0.0
    death_benefit: float = 0.0
    surrender_benefit: float = 0.0
    maturity_benefit: float = 0.0
    expense: float = 0.0
    investment_return: float = 0.0
    surplus_policyholder: float = 0.0
    surplus_shareholder: float = 0.0
    sda_repayment: float = 0.0
    reversionary_bonus: float = 0.0
    terminal_bonus: float = 0.0

    def to_dict(self) -> Dict:
        """Convert to dictionary for DataFrame construction."""
        return {
            "policy_id": self.policy_id,
            "timestep": self.timestep,
            "premium": self.premium,
            "death_benefit": self.death_benefit,
            "surrender_benefit": self.surrender_benefit,
            "maturity_benefit": self.maturity_benefit,
            "expense": self.expense,
            "investment_return": self.investment_return,
            "surplus_policyholder": self.surplus_policyholder,
            "surplus_shareholder": self.surplus_shareholder,
            "sda_repayment": self.sda_repayment,
            "reversionary_bonus": self.reversionary_bonus,
            "terminal_bonus": self.terminal_bonus,
        }


@dataclass
class AssetShareResult:
    """Results from asset share projection."""

    policy_states: List[PolicyState]
    cashflows: List[PolicyCashflow]
    summary_metrics: Dict[str, float]

    def to_dataframes(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Convert results to DataFrames.

        Returns:
            Tuple of (states_df, cashflows_df)
        """
        states_df = pd.DataFrame([s.to_dict() for s in self.policy_states])
        cashflows_df = pd.DataFrame([c.to_dict() for c in self.cashflows])
        return states_df, cashflows_df


class AssetShareEngine:
    """
    Policy-level asset share projection with profit sharing.

    Recursion per timestep:
    1. Apply investment return to asset share
    2. Add premium, subtract expenses
    3. Calculate surplus = asset_share - guaranteed_benefit
    4. If surplus > 0:
       - Repay shareholder deficit first (if SDA repayment priority)
       - Split remaining 70/30 (policyholder/shareholder)
       - Check lifetime shareholder cap
       - Allocate policyholder share to bonus pool
    5. If surplus < 0:
       - Add to shareholder deficit
    6. Update reversionary bonus for survivors
    7. Pay death/surrender/maturity benefits
    8. Update guaranteed benefit with reversionary bonus

    Example:
        >>> config = AssetShareConfig()
        >>> engine = AssetShareEngine(config)
        >>> result = engine.project_policy(policy, esg_trial, assumptions)
    """

    def __init__(self, config: AssetShareConfig):
        """
        Initialize asset share engine.

        Args:
            config: Configuration for profit sharing and bonus calculation
        """
        self.config = config

    def project_policy(
        self,
        policy: pd.Series,
        investment_returns: pd.Series,
        mortality_rates: pd.Series,
        lapse_rates: pd.Series,
        expenses: pd.Series,
        n_timesteps: int,
    ) -> AssetShareResult:
        """
        Project single policy asset share over time.

        Args:
            policy: Policy data (initial values, product type, etc.)
            investment_returns: Investment returns by timestep
            mortality_rates: Mortality rates (qx) by timestep
            lapse_rates: Lapse rates by timestep
            expenses: Expense amounts by timestep
            n_timesteps: Number of timesteps to project

        Returns:
            AssetShareResult with states and cashflows
        """
        # Initialize policy state
        state = PolicyState(
            policy_id=policy["policy_id"],
            timestep=0,
            asset_share=policy.get("initial_asset_share", 0.0),
            cumulative_premiums=0.0,
            cumulative_shareholder_profit=0.0,
            shareholder_deficit=0.0,
            guaranteed_benefit=policy.get("sum_assured", 0.0),
            reversionary_bonus_accumulated=0.0,
            terminal_bonus_pool=0.0,
            is_active=True,
        )

        states = [state]
        cashflows = []

        # Project each timestep
        for t in range(1, n_timesteps + 1):
            if not state.is_active:
                break

            # Create cashflow record
            cf = PolicyCashflow(policy_id=policy["policy_id"], timestep=t)

            # Step 1: Apply investment return
            if t <= len(investment_returns):
                return_rate = investment_returns.iloc[t - 1]
                cf.investment_return = state.asset_share * return_rate
                state.asset_share += cf.investment_return

            # Step 2: Add premium and subtract expenses
            if t <= policy.get("premium_term", n_timesteps):
                cf.premium = policy.get("annual_premium", 0.0) / 12  # Monthly premium
                state.asset_share += cf.premium
                state.cumulative_premiums += cf.premium

            if t <= len(expenses):
                cf.expense = expenses.iloc[t - 1]
                state.asset_share -= cf.expense

            # Step 3: Calculate surplus
            surplus = state.asset_share - state.guaranteed_benefit

            # Step 4: Profit sharing
            if surplus > 0:
                # Repay SDA first if priority enabled
                if self.config.sda_repayment_priority and state.shareholder_deficit > 0:
                    sda_repayment = min(surplus, state.shareholder_deficit)
                    cf.sda_repayment = sda_repayment
                    state.shareholder_deficit -= sda_repayment
                    surplus -= sda_repayment

                # Split remaining surplus 70/30
                if surplus > 0:
                    ph_share = surplus * self.config.policyholder_share
                    sh_share = surplus * self.config.shareholder_share

                    # Check lifetime shareholder cap
                    lifetime_cap = state.cumulative_premiums * self.config.lifetime_shareholder_cap
                    if state.cumulative_shareholder_profit + sh_share > lifetime_cap:
                        excess = (state.cumulative_shareholder_profit + sh_share) - lifetime_cap
                        sh_share -= excess
                        ph_share += excess  # Redirect excess to policyholder

                    cf.surplus_policyholder = ph_share
                    cf.surplus_shareholder = sh_share

                    state.cumulative_shareholder_profit += sh_share
                    state.terminal_bonus_pool += ph_share

            # Step 5: Handle negative surplus (deficit)
            elif surplus < 0:
                state.shareholder_deficit += abs(surplus)

            # Step 6: Calculate and apply reversionary bonus
            if state.is_active and t % 12 == 0:  # Annual bonus declaration
                bonus_rate = self._calculate_bonus_rate(
                    state.terminal_bonus_pool, state.guaranteed_benefit, state.cumulative_premiums
                )

                reversionary_bonus = state.guaranteed_benefit * bonus_rate
                cf.reversionary_bonus = reversionary_bonus
                state.reversionary_bonus_accumulated += reversionary_bonus
                state.guaranteed_benefit += reversionary_bonus

                # Deduct from terminal bonus pool
                state.terminal_bonus_pool = max(0, state.terminal_bonus_pool - reversionary_bonus)

            # Step 7: Check for decrements (death, surrender, maturity)
            if t <= len(mortality_rates):
                qx = mortality_rates.iloc[t - 1]
                if np.random.random() < qx:
                    # Death benefit
                    terminal_bonus = state.terminal_bonus_pool * self.config.terminal_bonus_factor
                    cf.death_benefit = state.guaranteed_benefit + terminal_bonus
                    cf.terminal_bonus = terminal_bonus
                    state.asset_share -= cf.death_benefit
                    state.is_active = False

            if state.is_active and t <= len(lapse_rates):
                lapse_rate = lapse_rates.iloc[t - 1]
                if np.random.random() < lapse_rate:
                    # Surrender benefit (typically lower than death benefit)
                    surrender_value = state.guaranteed_benefit * 0.9  # 90% of guaranteed
                    terminal_bonus = (
                        state.terminal_bonus_pool * self.config.terminal_bonus_factor * 0.5
                    )
                    cf.surrender_benefit = surrender_value + terminal_bonus
                    cf.terminal_bonus = terminal_bonus
                    state.asset_share -= cf.surrender_benefit
                    state.is_active = False

            # Check for maturity
            if state.is_active and t >= policy.get("maturity_term", n_timesteps * 12):
                terminal_bonus = state.terminal_bonus_pool * self.config.terminal_bonus_factor
                cf.maturity_benefit = state.guaranteed_benefit + terminal_bonus
                cf.terminal_bonus = terminal_bonus
                state.asset_share -= cf.maturity_benefit
                state.is_active = False

            # Update state
            state.timestep = t
            states.append(PolicyState(**state.__dict__))
            cashflows.append(cf)

        # Calculate summary metrics
        total_premiums = sum(cf.premium for cf in cashflows)
        total_benefits = sum(
            cf.death_benefit + cf.surrender_benefit + cf.maturity_benefit for cf in cashflows
        )
        total_expenses = sum(cf.expense for cf in cashflows)
        total_sh_profit = state.cumulative_shareholder_profit
        final_sda = state.shareholder_deficit

        summary_metrics = {
            "total_premiums": total_premiums,
            "total_benefits": total_benefits,
            "total_expenses": total_expenses,
            "total_shareholder_profit": total_sh_profit,
            "final_shareholder_deficit": final_sda,
            "final_asset_share": state.asset_share,
            "profit_margin": total_sh_profit / total_premiums if total_premiums > 0 else 0.0,
        }

        return AssetShareResult(
            policy_states=states, cashflows=cashflows, summary_metrics=summary_metrics
        )

    def _calculate_bonus_rate(
        self, bonus_pool: float, guaranteed_benefit: float, cumulative_premiums: float
    ) -> float:
        """
        Calculate reversionary bonus rate based on available pool.

        Args:
            bonus_pool: Available terminal bonus pool
            guaranteed_benefit: Current guaranteed benefit
            cumulative_premiums: Cumulative premiums paid

        Returns:
            Bonus rate (e.g., 0.03 for 3%)
        """
        if guaranteed_benefit <= 0:
            return 0.0

        # Target bonus rate based on pool size
        target_rate = bonus_pool / guaranteed_benefit

        # Apply smoothing
        if self.config.smoothing_method == "exponential":
            # Exponential smoothing (not implemented fully here, would need history)
            smoothed_rate = target_rate * self.config.smoothing_alpha
        else:
            smoothed_rate = target_rate

        # Apply bounds
        bonus_rate = np.clip(smoothed_rate, self.config.min_bonus_rate, self.config.max_bonus_rate)

        return bonus_rate

    def project_portfolio(
        self,
        policies: pd.DataFrame,
        investment_returns_by_trial: Dict[int, pd.Series],
        assumptions_provider,
        n_timesteps: int,
        trial_id: int = 1,
    ) -> pd.DataFrame:
        """
        Project entire portfolio of policies.

        Args:
            policies: DataFrame of policies
            investment_returns_by_trial: Dict mapping trial_id to returns series
            assumptions_provider: Assumption provider for mortality, lapse, expenses
            n_timesteps: Number of timesteps
            trial_id: Trial ID for ESG scenario

        Returns:
            DataFrame with aggregated results
        """
        investment_returns = investment_returns_by_trial.get(trial_id)

        all_states = []
        all_cashflows = []

        for idx, policy in policies.iterrows():
            # Get assumptions for this policy
            mortality_rates = self._get_mortality_series(policy, assumptions_provider, n_timesteps)
            lapse_rates = self._get_lapse_series(policy, assumptions_provider, n_timesteps)
            expenses = self._get_expense_series(policy, assumptions_provider, n_timesteps)

            # Project policy
            result = self.project_policy(
                policy, investment_returns, mortality_rates, lapse_rates, expenses, n_timesteps
            )

            all_states.extend(result.policy_states)
            all_cashflows.extend(result.cashflows)

        # Convert to DataFrames
        states_df = pd.DataFrame([s.to_dict() for s in all_states])
        cashflows_df = pd.DataFrame([c.to_dict() for c in all_cashflows])

        return states_df, cashflows_df

    def _get_mortality_series(
        self, policy: pd.Series, assumptions_provider, n_timesteps: int
    ) -> pd.Series:
        """Get mortality rates for policy over projection period."""
        rates = []
        for t in range(n_timesteps):
            age = policy["age"] + t // 12
            policy_year = t // 12 + 1
            qx = assumptions_provider.get_mortality(
                policy["product"],
                policy["gender"],
                age,
                policy.get("smoker_status", "N"),
                policy_year,
            )
            rates.append(qx / 12)  # Convert annual to monthly
        return pd.Series(rates)

    def _get_lapse_series(
        self, policy: pd.Series, assumptions_provider, n_timesteps: int
    ) -> pd.Series:
        """Get lapse rates for policy over projection period."""
        rates = []
        for t in range(n_timesteps):
            policy_year = t // 12 + 1
            age = policy["age"] + t // 12

            # Determine age band
            if age < 30:
                age_band = "20-30"
            elif age < 40:
                age_band = "30-40"
            elif age < 50:
                age_band = "40-50"
            else:
                age_band = "50-60"

            # Determine sum assured band
            sa = policy.get("sum_assured", 0)
            if sa < 100000:
                sa_band = "0-100000"
            elif sa < 500000:
                sa_band = "100000-500000"
            elif sa < 1000000:
                sa_band = "500000-1000000"
            else:
                sa_band = "1000000+"

            lapse_rate = assumptions_provider.get_lapse(
                policy["product"], policy_year, age=age_band, sum_assured_band=sa_band
            )
            rates.append(lapse_rate / 12)  # Convert annual to monthly
        return pd.Series(rates)

    def _get_expense_series(
        self, policy: pd.Series, assumptions_provider, n_timesteps: int
    ) -> pd.Series:
        """Get expense amounts for policy over projection period."""
        expenses = []
        for t in range(n_timesteps):
            policy_year = t // 12 + 1
            premium = policy.get("annual_premium", 0)

            # Determine premium band
            if premium < 10000:
                prem_band = "0-10000"
            elif premium < 50000:
                prem_band = "10000-50000"
            elif premium < 100000:
                prem_band = "50000-100000"
            else:
                prem_band = "100000+"

            # Get acquisition expense in year 1, maintenance thereafter
            if policy_year == 1:
                expense = assumptions_provider.get_expense(
                    policy["product"], policy_year, premium_band=prem_band
                )
            else:
                expense = assumptions_provider.get_expense(
                    policy["product"], policy_year, premium_band=prem_band
                )

            expenses.append(expense / 12)  # Convert annual to monthly
        return pd.Series(expenses)
