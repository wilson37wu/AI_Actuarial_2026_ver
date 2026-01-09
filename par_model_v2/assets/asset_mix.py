"""
Asset Mix Configuration and Investment Return Calculation

Defines asset allocation for PAR fund asset share and computes
blended investment returns from ESG scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class AssetMixConfig:
    """
    Asset allocation configuration for PAR fund asset share.

    Weights must sum to 1.0. Each weight represents the proportion
    of the asset share invested in that asset class.

    Parameters
    ----------
    w_cash : float, default=0.05
        Weight in cash (short-term instruments)
    w_govt_bonds : float, default=0.40
        Weight in government bonds
    w_credit_bonds : float, default=0.25
        Weight in corporate/credit bonds
    w_equity : float, default=0.30
        Weight in equities
    govt_bond_tenor : int, default=10
        Representative tenor for government bond holdings (years)
    credit_bond_tenor : int, default=7
        Representative tenor for credit bond holdings (years)
    credit_rating : str, default='A'
        Representative credit rating for corporate bonds
    equity_ticker : str, default='E_CNY'
        Equity ticker to use for returns

    Examples
    --------
    >>> config = AssetMixConfig(w_equity=0.20, w_govt_bonds=0.50)
    >>> config.validate()
    """

    w_cash: float = 0.05
    w_govt_bonds: float = 0.40
    w_credit_bonds: float = 0.25
    w_equity: float = 0.30

    # Asset class parameters
    govt_bond_tenor: int = 10
    credit_bond_tenor: int = 7
    credit_rating: str = "A"
    equity_ticker: str = "E_CNY"

    def validate(self):
        """Validate that weights sum to 1.0."""
        total = self.w_cash + self.w_govt_bonds + self.w_credit_bonds + self.w_equity
        if not np.isclose(total, 1.0, atol=1e-6):
            raise ValueError(
                f"Asset weights must sum to 1.0, got {total:.6f}\n"
                f"  w_cash={self.w_cash}, w_govt_bonds={self.w_govt_bonds}, "
                f"w_credit_bonds={self.w_credit_bonds}, w_equity={self.w_equity}"
            )

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary for serialization."""
        return {
            "w_cash": self.w_cash,
            "w_govt_bonds": self.w_govt_bonds,
            "w_credit_bonds": self.w_credit_bonds,
            "w_equity": self.w_equity,
            "govt_bond_tenor": self.govt_bond_tenor,
            "credit_bond_tenor": self.credit_bond_tenor,
            "credit_rating": self.credit_rating,
            "equity_ticker": self.equity_ticker,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> AssetMixConfig:
        """Create from dictionary."""
        return cls(**data)

    @classmethod
    def conservative(cls) -> AssetMixConfig:
        """Conservative allocation (low equity, high bonds)."""
        return cls(
            w_cash=0.10,
            w_govt_bonds=0.50,
            w_credit_bonds=0.30,
            w_equity=0.10,
        )

    @classmethod
    def balanced(cls) -> AssetMixConfig:
        """Balanced allocation (default)."""
        return cls()  # Uses defaults

    @classmethod
    def growth(cls) -> AssetMixConfig:
        """Growth allocation (higher equity)."""
        return cls(
            w_cash=0.05,
            w_govt_bonds=0.25,
            w_credit_bonds=0.20,
            w_equity=0.50,
        )


class InvestmentReturnCalculator:
    """
    Calculate blended investment returns for asset share projection.

    Computes weighted average return across cash, bonds, and equity
    using ESG scenario data.

    Parameters
    ----------
    asset_mix : AssetMixConfig
        Asset allocation configuration
    esg_provider : ESGScenarioProvider
        ESG scenario data provider

    Examples
    --------
    >>> from par_model_v2.esg.esg_scenario_provider import ESGScenarioProvider
    >>> provider = ESGScenarioProvider('esg.csv')
    >>> config = AssetMixConfig.balanced()
    >>> calc = InvestmentReturnCalculator(config, provider)
    >>> return_factor = calc.get_return(trial=1, timestep=12, timestep_next=13)
    """

    def __init__(self, asset_mix: AssetMixConfig, esg_provider):
        self.asset_mix = asset_mix
        self.esg_provider = esg_provider

        # Validate configuration
        self.asset_mix.validate()

    def get_return(
        self,
        trial: int,
        timestep: int,
        timestep_next: Optional[int] = None,
    ) -> float:
        """
        Calculate blended investment return for one time step.

        Parameters
        ----------
        trial : int
            ESG scenario trial number
        timestep : int
            Current timestep
        timestep_next : int, optional
            Next timestep (default: timestep + 1)
            Used for bond roll-down calculation

        Returns
        -------
        float
            Gross return factor (e.g., 1.05 for 5% return)

        Notes
        -----
        Return is calculated as:
        R = w_cash * R_cash
          + w_govt * R_govt_bond
          + w_credit * R_credit_bond
          + w_equity * R_equity

        Bond returns use roll-down: buy at tenor n, sell at tenor n-1
        """
        if timestep_next is None:
            timestep_next = timestep + 1

        # Cash return
        r_cash = self.esg_provider.get_cash_return(trial, timestep)

        # Government bond return (roll-down)
        r_govt = self.esg_provider.get_bond_total_return(
            trial=trial,
            timestep=timestep,
            timestep_next=timestep_next,
            rating="Govt",
            tenor=self.asset_mix.govt_bond_tenor,
        )

        # Credit bond return (roll-down)
        r_credit = self.esg_provider.get_bond_total_return(
            trial=trial,
            timestep=timestep,
            timestep_next=timestep_next,
            rating=self.asset_mix.credit_rating,
            tenor=self.asset_mix.credit_bond_tenor,
        )

        # Equity return
        r_equity = self.esg_provider.get_equity_total_return(
            trial=trial,
            timestep=timestep,
            ticker=self.asset_mix.equity_ticker,
        )

        # Weighted average
        blended_return = (
            self.asset_mix.w_cash * r_cash
            + self.asset_mix.w_govt_bonds * r_govt
            + self.asset_mix.w_credit_bonds * r_credit
            + self.asset_mix.w_equity * r_equity
        )

        return blended_return

    def get_return_components(
        self,
        trial: int,
        timestep: int,
        timestep_next: Optional[int] = None,
    ) -> Dict[str, float]:
        """
        Get detailed breakdown of investment returns by asset class.

        Returns
        -------
        dict
            Dictionary with keys: 'cash', 'govt_bonds', 'credit_bonds',
            'equity', 'blended'
        """
        if timestep_next is None:
            timestep_next = timestep + 1

        r_cash = self.esg_provider.get_cash_return(trial, timestep)

        r_govt = self.esg_provider.get_bond_total_return(
            trial=trial,
            timestep=timestep,
            timestep_next=timestep_next,
            rating="Govt",
            tenor=self.asset_mix.govt_bond_tenor,
        )

        r_credit = self.esg_provider.get_bond_total_return(
            trial=trial,
            timestep=timestep,
            timestep_next=timestep_next,
            rating=self.asset_mix.credit_rating,
            tenor=self.asset_mix.credit_bond_tenor,
        )

        r_equity = self.esg_provider.get_equity_total_return(
            trial=trial,
            timestep=timestep,
            ticker=self.asset_mix.equity_ticker,
        )

        blended = (
            self.asset_mix.w_cash * r_cash
            + self.asset_mix.w_govt_bonds * r_govt
            + self.asset_mix.w_credit_bonds * r_credit
            + self.asset_mix.w_equity * r_equity
        )

        return {
            "cash": r_cash,
            "govt_bonds": r_govt,
            "credit_bonds": r_credit,
            "equity": r_equity,
            "blended": blended,
        }
