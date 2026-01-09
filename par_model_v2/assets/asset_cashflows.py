"""Deterministic asset cash-flow generation for par-fund modelling.

This module provides a set of position classes and utilities to generate
**deterministic** cash-flow schedules for a range of asset classes:

- Government bonds
- Corporate bonds (with rating/tenor support via metadata)
- Public equities
- Private equities
- Pooled funds

The cash flows are intended to be re-valued later under stochastic ESG
scenarios by the external valuation layer. This module performs **no
discounting**; it only schedules nominal cash flows.

Only standard Python, NumPy, and pandas are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Type

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _to_timestamp(d: Any) -> pd.Timestamp:
    """Convert a variety of date-like inputs to ``pd.Timestamp``."""

    if isinstance(d, pd.Timestamp):
        return d
    if isinstance(d, (datetime, date)):
        return pd.Timestamp(d)
    # Allow string parsing via pandas
    return pd.Timestamp(str(d))


def _monthly_date_range(as_of_date: Any, horizon_months: int, step: str = "M") -> pd.DatetimeIndex:
    """Return a date range from ``as_of_date`` over a horizon in months.

    Parameters
    ----------
    as_of_date:
        Valuation date or start date (string/date/Timestamp).
    horizon_months:
        Number of months to project forward.
    step:
        pandas offset alias (e.g. 'M' for month-end, 'MS' for month-start).

    Returns
    -------
    pandas.DatetimeIndex
        Dates from as_of_date (exclusive) up to ``as_of_date + horizon_months``.
    """

    start = _to_timestamp(as_of_date)
    end = start + pd.DateOffset(months=int(horizon_months))
    # Exclude as_of_date itself; start one step after
    return pd.date_range(start + pd.tseries.frequencies.to_offset(step), end, freq=step)


# ---------------------------------------------------------------------------
# Position class registry for extensibility
# ---------------------------------------------------------------------------

_POSITION_REGISTRY: Dict[str, Type["BasePosition"]] = {}


def register_position_type(name: str, class_reference: Type["BasePosition"]) -> None:
    """Register a new position type for use with the portfolio aggregator.

    Parameters
    ----------
    name:
        String key identifying the position type (e.g. 'GOVT_BOND').
    class_reference:
        Class implementing the position interface (must expose
        ``generate_cashflows`` and an ``asset_class`` attribute).
    """

    _POSITION_REGISTRY[name] = class_reference


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


@dataclass
class BasePosition:
    """Base class for asset positions.

    Subclasses must implement :meth:`generate_cashflows` and expose
    ``asset_id`` and ``asset_class`` attributes.
    """

    asset_id: str

    # Subclasses should override; this is metadata, not part of __init__
    asset_class: ClassVar[str] = "GENERIC_ASSET"

    def generate_cashflows(
        self,
        as_of_date: Any,
        horizon_months: int,
        step: str = "M",
    ) -> pd.DataFrame:
        """Generate a deterministic cash-flow schedule for this position.

        Parameters
        ----------
        as_of_date:
            Valuation date or start date.
        horizon_months:
            Projection horizon in months.
        step:
            pandas offset alias for the cash-flow time step (e.g. 'M').

        Returns
        -------
        pandas.DataFrame
            DataFrame with columns ``[asset_id, date, cf_type, amount]``.
        """

        raise NotImplementedError


# ---------------------------------------------------------------------------
# Bond positions
# ---------------------------------------------------------------------------


@dataclass
class BondPosition(BasePosition):
    """Fixed-income bond position (government or corporate).

    Parameters
    ----------
    asset_id:
        Unique identifier for the asset.
    face_amount:
        Nominal principal amount.
    coupon_rate:
        Annual coupon rate (e.g. 0.03 for 3% p.a.).
    coupon_freq:
        Number of coupon payments per year (e.g. 1, 2, 4, 12).
    day_count:
        Day-count convention label (currently informational only).
    maturity_date:
        Maturity date of the bond.
    issue_date:
        Issue date of the bond.
    rating:
        Credit rating (for corporate bonds), e.g. 'AAA', 'AA', 'A'.
    bond_type:
        'GOVT' for government bonds, 'CORP' for corporate bonds.
    amortizing:
        If True, principal is amortised evenly across coupon dates; otherwise,
        a bullet repayment at maturity is assumed.
    call_schedule:
        Optional list of call dates (at par) for callable bonds. If provided,
        cash flows are truncated at the earliest call date after ``as_of_date``.
    """

    face_amount: float
    coupon_rate: float
    coupon_freq: int
    day_count: str
    maturity_date: Any
    issue_date: Any
    rating: Optional[str] = None
    bond_type: str = "GOVT"  # 'GOVT' or 'CORP'
    amortizing: bool = False
    call_schedule: Optional[List[Any]] = None

    asset_class: str = field(init=False)

    def __post_init__(self) -> None:
        self.asset_class = "GOVT_BOND" if self.bond_type.upper() == "GOVT" else "CORP_BOND"

    def _coupon_dates(self, as_of: pd.Timestamp, horizon_months: int) -> pd.DatetimeIndex:
        """Compute coupon payment dates within the projection horizon."""

        start = max(_to_timestamp(self.issue_date), as_of)
        end = as_of + pd.DateOffset(months=int(horizon_months))

        if self.coupon_freq <= 0:
            return pd.DatetimeIndex([])

        months_per_coupon = 12 // int(self.coupon_freq)
        all_coupons = pd.date_range(start, self.maturity_date, freq=f"{months_per_coupon}M")
        coupons = all_coupons[(all_coupons > as_of) & (all_coupons <= end)]

        # Apply simple deterministic call schedule: truncate at first call date
        if self.call_schedule:
            call_dates = sorted(_to_timestamp(d) for d in self.call_schedule)
            for cd in call_dates:
                if cd > as_of:
                    coupons = coupons[coupons <= cd]
                    break

        return coupons

    def generate_cashflows(
        self,
        as_of_date: Any,
        horizon_months: int,
        step: str = "M",
    ) -> pd.DataFrame:
        """Generate deterministic bond cash flows.

        Returns coupon payments and principal repayments (bullet or amortising)
        between ``as_of_date`` and ``as_of_date + horizon_months``.
        """

        as_of = _to_timestamp(as_of_date)
        coupons = self._coupon_dates(as_of, horizon_months)

        if coupons.empty:
            return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount"])

        n = len(coupons)
        flows: List[Dict[str, Any]] = []

        coupon_amount = self.face_amount * self.coupon_rate / float(self.coupon_freq)

        if self.amortizing:
            principal_per_payment = self.face_amount / n
            outstanding = self.face_amount
            for i, dt in enumerate(coupons):
                cf_coupon = coupon_amount  # coupon on full face; can be refined
                cf_principal = principal_per_payment
                outstanding -= cf_principal
                flows.append(
                    {
                        "asset_id": self.asset_id,
                        "date": dt,
                        "cf_type": "COUPON",
                        "amount": cf_coupon,
                    }
                )
                flows.append(
                    {
                        "asset_id": self.asset_id,
                        "date": dt,
                        "cf_type": "PRINCIPAL",
                        "amount": cf_principal,
                    }
                )
        else:
            # Bullet: coupons until maturity, principal at final date
            for dt in coupons:
                flows.append(
                    {
                        "asset_id": self.asset_id,
                        "date": dt,
                        "cf_type": "COUPON",
                        "amount": coupon_amount,
                    }
                )
            # Principal at last coupon date (assumed maturity)
            flows.append(
                {
                    "asset_id": self.asset_id,
                    "date": coupons[-1],
                    "cf_type": "PRINCIPAL",
                    "amount": self.face_amount,
                }
            )

        df = pd.DataFrame(flows)
        return df


# ---------------------------------------------------------------------------
# Public equity positions
# ---------------------------------------------------------------------------


@dataclass
class EquityPosition(BasePosition):
    """Public equity position.

    Parameters
    ----------
    asset_id:
        Unique identifier for the equity position.
    shares:
        Number of shares held.
    current_price:
        Current price per share.
    dividend_yield:
        Annual dividend yield (e.g. 0.03 for 3% p.a.).
    market_index_key:
        Optional identifier linking this equity to a market index.
    """

    shares: float
    current_price: float
    dividend_yield: float
    market_index_key: Optional[str] = None

    # Class-level asset class label
    asset_class: ClassVar[str] = "EQUITY"

    def generate_cashflows(
        self,
        as_of_date: Any,
        horizon_months: int,
        step: str = "M",
    ) -> pd.DataFrame:
        """Generate deterministic dividend cash flows for the equity.

        Assumes dividends accrue evenly over the horizon based on the
        ``dividend_yield`` applied to current market value.
        """

        dates = _monthly_date_range(as_of_date, horizon_months, step=step)
        if dates.empty:
            return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount"])

        annual_div = self.shares * self.current_price * self.dividend_yield
        # Convert to per-step amount assuming each step is ~1/12 year for 'M'
        n_steps_per_year = 12 if step.upper().startswith("M") else 1
        div_per_step = annual_div / n_steps_per_year

        df = pd.DataFrame(
            {
                "asset_id": self.asset_id,
                "date": dates,
                "cf_type": "DIVIDEND",
                "amount": np.full(len(dates), div_per_step, dtype=float),
            }
        )
        return df


# ---------------------------------------------------------------------------
# Private equity positions
# ---------------------------------------------------------------------------


@dataclass
class PrivateEquityPosition(BasePosition):
    """Private equity or illiquid investment position.

    Parameters
    ----------
    asset_id:
        Unique identifier for the position.
    invested_amount:
        Initial invested amount (outflow at or before valuation date).
    valuation_date:
        Valuation date.
    expected_exit_date:
        Expected exit date when proceeds are realised.
    target_return_rate:
        Annual target return rate (e.g. 0.12 for 12% IRR).
    illiquidity_lag_months:
        Optional lag in months between valuation date and recognition of exit
        proceeds.
    mgmt_fee_rate:
        Optional annual management fee rate applied to invested amount,
        charged periodically over the horizon.
    """

    invested_amount: float
    valuation_date: Any
    expected_exit_date: Any
    target_return_rate: float
    illiquidity_lag_months: int = 0
    mgmt_fee_rate: float = 0.0

    asset_class: ClassVar[str] = "PRIVATE_EQUITY"

    def generate_cashflows(
        self,
        as_of_date: Any,
        horizon_months: int,
        step: str = "M",
    ) -> pd.DataFrame:
        """Generate deterministic private equity cash flows.

        Schedules:
        - A single exit cash flow at ``expected_exit_date`` (adjusted by
          illiquidity lag), with amount based on discounted target return.
        - Optional periodic management fees (negative cash flows).
        """

        as_of = _to_timestamp(as_of_date)
        val_date = _to_timestamp(self.valuation_date)
        exit_date = _to_timestamp(self.expected_exit_date) + pd.DateOffset(
            months=int(self.illiquidity_lag_months)
        )

        end = as_of + pd.DateOffset(months=int(horizon_months))
        flows: List[Dict[str, Any]] = []

        # Exit cash flow if within horizon
        if exit_date > as_of and exit_date <= end:
            # Time from valuation date to exit in years
            years_to_exit = max(0.0, (exit_date - val_date).days / 365.25)
            exit_amount = self.invested_amount * (1.0 + self.target_return_rate) ** years_to_exit
            flows.append(
                {
                    "asset_id": self.asset_id,
                    "date": exit_date,
                    "cf_type": "EXIT_PROCEEDS",
                    "amount": exit_amount,
                }
            )

        # Management fees as periodic outflows
        if self.mgmt_fee_rate > 0.0:
            dates = _monthly_date_range(as_of_date, horizon_months, step=step)
            if not dates.empty:
                annual_fee = self.invested_amount * self.mgmt_fee_rate
                n_steps_per_year = 12 if step.upper().startswith("M") else 1
                fee_per_step = -annual_fee / n_steps_per_year
                for dt in dates:
                    if dt <= end:
                        flows.append(
                            {
                                "asset_id": self.asset_id,
                                "date": dt,
                                "cf_type": "MGMT_FEE",
                                "amount": fee_per_step,
                            }
                        )

        if not flows:
            return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount"])

        df = pd.DataFrame(flows)
        return df


# ---------------------------------------------------------------------------
# Fund positions
# ---------------------------------------------------------------------------


@dataclass
class FundPosition(BasePosition):
    """Pooled fund position (e.g. mutual fund, unit trust).

    Parameters
    ----------
    asset_id:
        Unique identifier for the fund position.
    nav:
        Current net asset value of the holding.
    allocation_policy_id:
        Identifier linking this fund to an allocation policy definition.
    distribution_freq:
        Number of distributions per year (e.g. 4 for quarterly, 12 for monthly).
    distribution_yield:
        Annual distribution yield (e.g. 0.04 for 4% p.a.).
    """

    nav: float
    allocation_policy_id: str
    distribution_freq: int
    distribution_yield: float

    asset_class: ClassVar[str] = "FUND"

    def generate_cashflows(
        self,
        as_of_date: Any,
        horizon_months: int,
        step: str = "M",
    ) -> pd.DataFrame:
        """Generate deterministic distribution cash flows for the fund.

        Distributions are scheduled based on ``distribution_freq`` and assumed
        to be proportional to the current NAV and ``distribution_yield``.
        """

        as_of = _to_timestamp(as_of_date)
        end = as_of + pd.DateOffset(months=int(horizon_months))

        if self.distribution_freq <= 0 or self.distribution_yield <= 0.0:
            return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount"])

        months_per_dist = 12 // int(self.distribution_freq)
        first_dist = as_of + pd.DateOffset(months=months_per_dist)
        dist_dates = pd.date_range(first_dist, end, freq=f"{months_per_dist}M")

        if dist_dates.empty:
            return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount"])

        annual_dist = self.nav * self.distribution_yield
        dist_per_payment = annual_dist / float(self.distribution_freq)

        df = pd.DataFrame(
            {
                "asset_id": self.asset_id,
                "date": dist_dates,
                "cf_type": "DISTRIBUTION",
                "amount": np.full(len(dist_dates), dist_per_payment, dtype=float),
            }
        )
        return df


# Register built-in position types
register_position_type("GOVT_BOND", BondPosition)
register_position_type("CORP_BOND", BondPosition)
register_position_type("EQUITY", EquityPosition)
register_position_type("PRIVATE_EQUITY", PrivateEquityPosition)
register_position_type("FUND", FundPosition)


# ---------------------------------------------------------------------------
# Portfolio cash-flow aggregation
# ---------------------------------------------------------------------------


def aggregate_portfolio_cashflows(
    portfolio_positions: Iterable[BasePosition],
    as_of_date: Any,
    horizon_months: int,
    step: str = "M",
) -> pd.DataFrame:
    """Aggregate deterministic cash flows across a portfolio of positions.

    Parameters
    ----------
    portfolio_positions:
        Iterable of position objects (e.g. :class:`BondPosition`,
        :class:`EquityPosition`, etc.) each implementing
        :meth:`BasePosition.generate_cashflows`.
    as_of_date:
        Valuation date / start date for projections.
    horizon_months:
        Projection horizon in months.
    step:
        Time step for scheduling cash flows (pandas offset alias).

    Returns
    -------
    pandas.DataFrame
        Cash-flow table with columns ``[asset_id, date, cf_type, amount,
        asset_class]``, sorted by date then asset_id.
    """

    frames: List[pd.DataFrame] = []

    for pos in portfolio_positions:
        cf = pos.generate_cashflows(as_of_date=as_of_date, horizon_months=horizon_months, step=step)
        if cf.empty:
            continue
        cf = cf.copy()
        cf["asset_class"] = getattr(pos, "asset_class", "UNKNOWN")
        frames.append(cf)

    if not frames:
        return pd.DataFrame(columns=["asset_id", "date", "cf_type", "amount", "asset_class"])

    all_cf = pd.concat(frames, axis=0, ignore_index=True)
    all_cf.sort_values(["date", "asset_id"], inplace=True)
    all_cf.reset_index(drop=True, inplace=True)
    return all_cf


# ---------------------------------------------------------------------------
# Allocation policy configuration stub
# ---------------------------------------------------------------------------


def load_allocation_policy(policy_id: str) -> Dict[str, Any]:
    """Load allocation policy metadata for a given fund.

    This is a configuration stub intended for integration with a broader
    portfolio construction framework. In a full implementation, this function
    would retrieve the target asset mix and other parameters for the fund
    identified by ``policy_id``.

    Parameters
    ----------
    policy_id:
        Identifier of the allocation policy.

    Returns
    -------
    dict
        Placeholder dictionary containing the ``policy_id``. Users should
        extend this to return a richer configuration as needed.
    """

    return {"policy_id": policy_id}


__all__ = [
    "BasePosition",
    "BondPosition",
    "EquityPosition",
    "PrivateEquityPosition",
    "FundPosition",
    "register_position_type",
    "aggregate_portfolio_cashflows",
    "load_allocation_policy",
]
