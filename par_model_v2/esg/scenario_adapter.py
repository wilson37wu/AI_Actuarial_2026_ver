"""Scenario adapter for Moody's ESG CSV outputs.

This module provides a thin adapter that reads Moody ESG scenario CSV files and
exposes per-trial arrays ready for the asset and par-fund layers.

It assumes a long-table layout with at least the following columns:

- "Trial": integer scenario index (1..N, up to 1000)
- "Timestep": integer time index (e.g. months)
- Risk-free / government ZCB prices, e.g. columns like
  "ESG.Economies.CNY.NominalZCBP(AAA, 1, 3)"
- Credit ZCB prices, e.g. "ESG.Economies.CNY.NominalZCBP(A, 1, 3)", etc.
- Equity total return: "ESG.Assets.EquityAssets.E_CNY.TotalReturn"
- Equity dividend yield: "ESG.Assets.EquityAssets.E_CNY.DividendYield" (or
  any other agreed column name; see class attributes to customize).

No plotting or additional I/O is implemented here; the adapter only reads the
CSV and returns NumPy arrays / dicts suitable for downstream layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from par_model_v2.grid.grid_manager import TimeGrid

_ZCB_PATTERN = re.compile(
    r"^ESG\.Economies\.CNY\.NominalZCBP\((?P<rating>[^,]+),\s*(?P<tenor>[^,]+),"
)


@dataclass
class ColumnConfig:
    """Configuration of key Moody ESG column names/patterns.

    If your Moody ESG extract uses slightly different column names, adjust this
    configuration or subclass ``ESGAdapter`` and override ``column_config``.
    """

    equity_total_return: str = "ESG.Assets.EquityAssets.E_CNY.TotalReturn"
    equity_dividend_yield: str = "ESG.Assets.EquityAssets.E_CNY.DividendYield"


class ESGAdapter:
    """Adapter for Moody ESG scenario CSV data.

    Parameters
    ----------
    csv_path:
        Path to the Moody ESG CSV file.
    time_grid:
        Optional ``TimeGrid`` instance used when aggregating monthly series
        to annual ones. If ``None``, annual aggregation will not be performed
        and monthly data will be returned even when ``annual=True``.
    max_trials:
        Maximum number of trials (scenarios) to consider. Trials with index
        greater than ``max_trials`` are ignored.
    column_config:
        Optional ``ColumnConfig`` to override default column names.
    """

    def __init__(
        self,
        csv_path: str,
        time_grid: Optional[TimeGrid] = None,
        max_trials: int = 1000,
        column_config: Optional[ColumnConfig] = None,
    ) -> None:
        self.csv_path = csv_path
        self.time_grid = time_grid
        self.max_trials = int(max_trials)
        self.column_config = column_config or ColumnConfig()

        self._df = self._load_csv()
        self._zcb_columns_info = self._parse_zcb_columns()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_csv(self) -> pd.DataFrame:
        df = pd.read_csv(self.csv_path)
        if "Trial" not in df.columns or "Timestep" not in df.columns:
            raise ValueError("CSV must contain 'Trial' and 'Timestep' columns")

        # Restrict to max_trials
        df = df[df["Trial"] <= self.max_trials].copy()

        # Ensure consistent ordering
        df.sort_values(["Trial", "Timestep"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def _parse_zcb_columns(self) -> Dict[str, Dict[float, str]]:
        """Identify ZCB columns and organise them by rating and tenor.

        Returns
        -------
        Mapping from rating -> {tenor -> column_name}.
        """

        rating_to_tenor_cols: Dict[str, Dict[float, str]] = {}
        for col in self._df.columns:
            m = _ZCB_PATTERN.match(col)
            if not m:
                continue
            rating = m.group("rating").strip()
            tenor_raw = m.group("tenor").strip()
            try:
                tenor = float(tenor_raw)
            except ValueError:
                # If tenor isn't numeric, we still store but keep string index
                # by mapping it to NaN-keyed dict; this can be extended later.
                continue

            rating_dict = rating_to_tenor_cols.setdefault(rating, {})
            rating_dict[tenor] = col

        return rating_to_tenor_cols

    def _trial_slice(self, trial: int) -> pd.DataFrame:
        trial = int(trial)
        if trial < 1:
            raise ValueError("trial index must be >= 1")
        if trial > self.max_trials:
            raise ValueError(f"trial index {trial} exceeds max_trials={self.max_trials}")

        df_t = self._df[self._df["Trial"] == trial].copy()
        if df_t.empty:
            raise ValueError(f"No data found for trial={trial}")
        df_t.sort_values("Timestep", inplace=True)
        df_t.reset_index(drop=True, inplace=True)
        return df_t

    def _aggregate_deflators(self, values: np.ndarray, annual: bool) -> np.ndarray:
        """Aggregate deflators to the hybrid grid if requested.

        ``values`` is assumed to be a monthly series where index m corresponds
        to month m. When ``annual`` is True and a ``TimeGrid`` is provided, the
        hybrid grid deflators are obtained via ``TimeGrid.aggregate_deflators``.
        """

        if not annual or self.time_grid is None:
            return values
        return self.time_grid.aggregate_deflators(values)

    def _map_series_to_hybrid(self, values: np.ndarray, annual: bool, mode: str) -> np.ndarray:
        """Map a 1D monthly series to the hybrid grid using ``TimeGrid``.

        If ``annual`` is False or no ``TimeGrid`` is provided, the original
        series is returned unchanged.
        """

        if not annual or self.time_grid is None:
            return values
        return self.time_grid.map_monthly_series(values, mode=mode)

    def _map_matrix_to_hybrid(self, mat: np.ndarray, annual: bool, mode: str) -> np.ndarray:
        """Map a 2D monthly matrix [time, tenor] to the hybrid grid.

        The mapping is applied column-wise using ``TimeGrid.map_monthly_series``.
        """

        if not annual or self.time_grid is None:
            return mat

        if mat.ndim != 2:
            raise ValueError("Expected a 2D matrix for hybrid grid mapping")

        cols = [
            self.time_grid.map_monthly_series(mat[:, j], mode=mode) for j in range(mat.shape[1])
        ]
        return np.stack(cols, axis=1)

    # ------------------------------------------------------------------
    # Public API: per-trial extractors
    # ------------------------------------------------------------------

    def get_deflator(self, trial: int, annual: bool = False) -> np.ndarray:
        """Return risk-free deflator series for a given trial.

        Currently this uses the AAA ZCB with the shortest available tenor as a
        proxy for the risk-free deflator. This can be refined as needed.

        Returns
        -------
        np.ndarray
            1D array of shape [time]. If ``annual=True`` and a ``GridManager``
            is provided, this series is down-sampled to an annual grid.
        """

        df_t = self._trial_slice(trial)
        rating_dict = self._zcb_columns_info.get("AAA")
        if not rating_dict:
            raise ValueError("No AAA ZCB columns found for risk-free deflator")

        # Use the smallest tenor as proxy for short rate / deflator
        tenor = sorted(rating_dict.keys())[0]
        col = rating_dict[tenor]

        values = df_t[col].to_numpy(dtype=float)
        return self._aggregate_deflators(values, annual)

    def get_gov_zero_curve(self, trial: int, annual: bool = False) -> np.ndarray:
        """Return government zero curve matrix for a given trial.

        The matrix shape is [time, tenor], where tenors are sorted ascending and
        correspond to AAA ZCB columns in the Moody ESG extract.
        """

        df_t = self._trial_slice(trial)
        rating_dict = self._zcb_columns_info.get("AAA")
        if not rating_dict:
            raise ValueError("No AAA ZCB columns found for government zero curve")

        tenors = sorted(rating_dict.keys())
        cols = [rating_dict[t] for t in tenors]
        mat = df_t[cols].to_numpy(dtype=float)  # shape [time, tenor]
        # For ZCB price curves we typically want the value at each grid point,
        # not sums or compounded returns.
        return self._map_matrix_to_hybrid(mat, annual, mode="value")

    def get_credit_curve(self, trial: int, annual: bool = False) -> Dict[str, np.ndarray]:
        """Return credit curves for all ratings (except AAA) for a given trial.

        Returns
        -------
        dict
            Mapping rating -> matrix [time, tenor]. If ``annual=True`` and a
            ``GridManager`` is provided, each matrix is down-sampled to an
            annual grid.
        """

        df_t = self._trial_slice(trial)
        out: Dict[str, np.ndarray] = {}

        for rating, tenor_dict in self._zcb_columns_info.items():
            if rating == "AAA":
                continue  # considered government curve
            tenors = sorted(tenor_dict.keys())
            cols = [tenor_dict[t] for t in tenors]
            mat = df_t[cols].to_numpy(dtype=float)
            out[rating] = self._map_matrix_to_hybrid(mat, annual, mode="value")

        return out

    def get_equity_total_return(self, trial: int, annual: bool = False) -> np.ndarray:
        """Return equity total return series for a given trial."""

        df_t = self._trial_slice(trial)
        col = self.column_config.equity_total_return
        if col not in df_t.columns:
            raise ValueError(f"Equity total return column '{col}' not found in ESG CSV")

        values = df_t[col].to_numpy(dtype=float)
        timesteps = df_t["Timestep"].to_numpy(dtype=int)
        return self._maybe_aggregate_to_annual(timesteps, values, annual)

    def get_equity_dividend_yield(self, trial: int, annual: bool = False) -> np.ndarray:
        """Return equity dividend yield series for a given trial."""

        df_t = self._trial_slice(trial)
        col = self.column_config.equity_dividend_yield
        if col not in df_t.columns:
            raise ValueError(f"Equity dividend yield column '{col}' not found in ESG CSV")

        values = df_t[col].to_numpy(dtype=float)
        timesteps = df_t["Timestep"].to_numpy(dtype=int)
        return self._maybe_aggregate_to_annual(timesteps, values, annual)


__all__ = ["ColumnConfig", "ESGAdapter"]
