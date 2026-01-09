"""Time grid manager for par_model_v2.narios.

This module defines a hybrid time grid with an initial monthly part followed by
annual points. It also provides utilities to map ESG monthly series (e.g.
returns, cash flows, deflators) onto this hybrid grid.

Grid definition
---------------
- Monthly steps: months 0 .. monthly_years * 12 (inclusive)
- Annual steps: years (monthly_years + 1) .. horizon_years (inclusive)

For convenience, annual steps are also available as their month-equivalent
indices (year * 12) when mapping monthly series.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class TimeGrid:
    """Hybrid monthly / annual time grid.

    Parameters
    ----------
    monthly_years:
        Number of years at monthly resolution from t=0. Monthly steps will be
        0, 1, ..., monthly_years * 12.
    horizon_years:
        Total projection horizon in years. Annual steps will be
        monthly_years + 1, ..., horizon_years.
    """

    monthly_years: int
    horizon_years: int

    # ------------------------------------------------------------------
    # Basic grid construction
    # ------------------------------------------------------------------

    def monthly_steps(self) -> List[int]:
        """Return a list of monthly timesteps from 0 to monthly_years * 12."""

        last_month = int(self.monthly_years) * 12
        return list(range(0, last_month + 1))

    def annual_steps(self) -> List[int]:
        """Return a list of annual step indices in years.

        The list runs from (monthly_years + 1) to horizon_years inclusive.
        """

        start_year = int(self.monthly_years) + 1
        end_year = int(self.horizon_years)
        if end_year < start_year:
            return []
        return list(range(start_year, end_year + 1))

    def annual_steps_as_months(self) -> np.ndarray:
        """Return annual step positions expressed in months (year * 12)."""

        years = self.annual_steps()
        return np.asarray([y * 12 for y in years], dtype=int)

    def hybrid_month_indices(self) -> np.ndarray:
        """Return month indices for the hybrid grid.

        Combines:
        - monthly months: 0 .. monthly_years * 12
        - annual endpoints: year * 12 for years > monthly_years
        """

        monthly = np.asarray(self.monthly_steps(), dtype=int)
        annual_months = self.annual_steps_as_months()
        if annual_months.size == 0:
            return monthly
        return np.concatenate([monthly, annual_months])

    # ------------------------------------------------------------------
    # Mapping / aggregation
    # ------------------------------------------------------------------

    def map_monthly_series(
        self,
        series_monthly: np.ndarray,
        mode: str = "value",
    ) -> np.ndarray:
        """Map a monthly series to the hybrid grid.

        Parameters
        ----------
        series_monthly:
            1D numpy array of length at least horizon_years * 12 + 1, where
            index m corresponds to month m since t=0.
        mode:
            - "value": take the raw series value at each hybrid month index
              (including the annual part via year*12 positions).
            - "sum": for the annual part, sum monthly values within each year
              bucket (from previous annual boundary to current one), while
              leaving the monthly part as-is.
            - "compound": for the annual part, compound monthly returns within
              each year bucket assuming ``series_monthly`` contains returns r_m
              (so the annual return is prod(1 + r_m) - 1).

        Returns
        -------
        np.ndarray
            1D array on the hybrid grid.
        """

        series = np.asarray(series_monthly, dtype=float)
        needed_len = int(self.horizon_years) * 12 + 1
        if series.shape[0] < needed_len:
            raise ValueError(
                f"series_monthly length {series.shape[0]} is shorter than "
                f"required horizon {needed_len} months"
            )

        hybrid_idx = self.hybrid_month_indices()
        monthly_end = int(self.monthly_years) * 12

        # Monthly part: direct indexing
        monthly_values = series[0 : monthly_end + 1]

        if mode == "value":
            # Annual part: pick value at year-end (year * 12)
            annual_months = hybrid_idx[monthly_end + 1 :]
            annual_values = series[annual_months]
        else:
            # For sum/compound, work year by year
            annual_values_list: List[float] = []
            prev_month = monthly_end
            for year in self.annual_steps():
                end_month = year * 12
                if end_month > series.shape[0] - 1:
                    raise ValueError(f"Series does not extend to month {end_month} for year {year}")
                # Interval (prev_month, end_month], i.e. months prev_month+1..end_month
                if end_month > prev_month:
                    window = series[prev_month + 1 : end_month + 1]
                else:
                    window = series[prev_month : prev_month + 1]

                if mode == "sum":
                    val = float(window.sum())
                elif mode == "compound":
                    val = float(np.prod(1.0 + window) - 1.0)
                else:
                    raise ValueError(f"Unsupported mode '{mode}'")

                annual_values_list.append(val)
                prev_month = end_month

            annual_values = np.asarray(annual_values_list, dtype=float)

        return np.concatenate([monthly_values, annual_values])

    # ------------------------------------------------------------------
    # Specific helper for deflators
    # ------------------------------------------------------------------

    def aggregate_deflators(self, deflators_monthly: np.ndarray) -> np.ndarray:
        """Return deflators on the hybrid grid.

        Assumes ``deflators_monthly[m]`` is the discount factor/deflator from
        time 0 to month m. The deflator at an annual step y is simply taken as
        the deflator at month y * 12.
        """

        df = np.asarray(deflators_monthly, dtype=float)
        needed_len = int(self.horizon_years) * 12 + 1
        if df.shape[0] < needed_len:
            raise ValueError(
                f"deflators_monthly length {df.shape[0]} is shorter than required "
                f"horizon {needed_len} months"
            )

        hybrid_idx = self.hybrid_month_indices()
        return df[hybrid_idx]


__all__ = ["TimeGrid"]
