"""Build valuation-ready time series files from ESG and par fund outputs.

This module defines :class:`ValuationTimeseriesBuilder`, which is responsible
for assembling per-scenario time series suitable for consumption by the
valuation engine.

It takes as inputs:
- Scenario data from :class:`par_model_v2.esg.esg_adapter: ESGAdapter`
  (risk-free curves, credit curves, equity returns/dividends) on the hybrid
  grid defined by :class:`TimeGrid`.
- Par fund bonus / crediting series as produced by
  :class:`moody_par_model_v2.assets.par_fund_stochastic.ParFundStochastic`.

For each scenario (trial), it can build a CSV file whose columns are mapped via
``field_map`` from internal series names to the external naming convention
required by the valuation engine.

The builder supports a "prepare-only" mode: in this implementation, that means
it only writes scenario files and does not attempt to invoke any external
valuation process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping

import numpy as np
import pandas as pd

from par_model_v2.esg import ESGAdapter


@dataclass
class ValuationTimeseriesBuilder:
    """Build per-scenario valuation time series files.

    Parameters
    ----------
    field_map:
        Mapping from internal series names to external column names required by
        the valuation engine. If not provided, internal names are used as
        column names.
    prepare_only:
        If True, the builder only prepares (writes) scenario time series files
        and **does not** attempt to trigger any external valuation engine.
    """

    field_map: Dict[str, str] = field(default_factory=dict)
    prepare_only: bool = True

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def build_scenario_files(
        self,
        trial_id: int,
        esg_adapter: ESGAdapter,
        bonus_data: Mapping[str, np.ndarray],
        output_dir: str | Path,
    ) -> Path:
        """Build a valuation time series file for a given scenario.

        This method extracts, for the specified ``trial_id``:

        - Risk-free government curves by tenor (AAA ZCB-derived spot rates).
        - Credit curves by rating/tenor and corresponding credit spreads
          (credit spot rate minus government spot rate).
        - Equity total return and dividend yield series.
        - Par fund bonus/crediting series from ``bonus_data``.

        All series are assumed to be on the hybrid time grid produced by the
        :class:`TimeGrid` associated with ``scenario_data``. The adapter is
        responsible for aggregating monthly ESG series onto this grid.

        The resulting series are combined into a single tabular structure and
        written to a CSV file in ``output_dir``. Column names are determined by
        ``field_map``: for each internal series key ``k``, the column name is
        ``field_map.get(k, k)``.

        Parameters
        ----------
        trial_id:
            Scenario index (1-based) to extract.
        scenario_data:
            Instance of :class:`MoodyESGAdapter` providing ESG paths.
        bonus_data:
            Mapping from bonus series names (e.g. "bonus_wl_rb",
            "bonus_wl_cashdiv", "crediting_rate_pension") to 2D arrays with
            shape [scenario, time].
        output_dir:
            Directory where the scenario CSV file will be written.

        Returns
        -------
        pathlib.Path
            Path to the written CSV file.
        """

        trial = int(trial_id)
        if trial < 1:
            raise ValueError("trial_id must be >= 1")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # 1) Extract risk-free / credit curves and equity series
        # ------------------------------------------------------------------

        # Deflator (risk-free discount factor); hybrid grid already handled
        defl = scenario_data.get_deflator(trial=trial, annual=True)

        # Government ZCB prices [time, tenor] and credit ZCBs by rating
        gov_prices = scenario_data.get_gov_zero_curve(trial=trial, annual=True)
        credit_prices = scenario_data.get_credit_curve(trial=trial, annual=True)

        # Equity paths
        eq_tr = scenario_data.get_equity_total_return(trial=trial, annual=True)
        eq_div = scenario_data.get_equity_dividend_yield(trial=trial, annual=True)

        # ------------------------------------------------------------------
        # 2) Derive spot rates and spreads by rating/tenor
        # ------------------------------------------------------------------

        # Access tenor structure from the adapter's internal ZCB metadata.
        zcb_info = getattr(scenario_data, "_zcb_columns_info", None)
        if not zcb_info or "AAA" not in zcb_info:
            raise ValueError("Scenario data does not expose AAA ZCB tenor information")

        tenors = sorted(zcb_info["AAA"].keys())
        n_time, n_tenor = gov_prices.shape
        if n_tenor != len(tenors):
            raise ValueError("Mismatch between government curve columns and tenor metadata")

        tenors_arr = np.asarray(tenors, dtype=float)

        # Compute government spot rates from ZCB prices
        gov_spot = np.empty_like(gov_prices, dtype=float)
        for j, T in enumerate(tenors_arr):
            col_prices = gov_prices[:, j]
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                gov_spot[:, j] = np.where(
                    col_prices > 0.0,
                    np.power(col_prices, -1.0 / T) - 1.0,
                    np.nan,
                )

        # Compute credit spot rates and spreads (per rating/tenor)
        spreads_by_rating: Dict[str, np.ndarray] = {}
        for rating, mat_prices in credit_prices.items():
            if mat_prices.shape != gov_prices.shape:
                raise ValueError(
                    f"Credit curve for rating {rating} has shape {mat_prices.shape}, "
                    f"expected {gov_prices.shape}"
                )
            credit_spot = np.empty_like(mat_prices, dtype=float)
            for j, T in enumerate(tenors_arr):
                col_prices = mat_prices[:, j]
                with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                    credit_spot[:, j] = np.where(
                        col_prices > 0.0,
                        np.power(col_prices, -1.0 / T) - 1.0,
                        np.nan,
                    )
            spreads_by_rating[rating] = credit_spot - gov_spot

        # ------------------------------------------------------------------
        # 3) Assemble internal series dictionary
        # ------------------------------------------------------------------

        series: Dict[str, np.ndarray] = {}

        # Core deflator and risk-free spots
        series["deflator"] = defl

        for j, T in enumerate(tenors_arr):
            key = f"rf_spot_AAA_{T:g}Y"
            series[key] = gov_spot[:, j]

        # Credit spreads per rating/tenor
        for rating, spread_mat in spreads_by_rating.items():
            for j, T in enumerate(tenors_arr):
                key = f"spread_{rating}_{T:g}Y"
                series[key] = spread_mat[:, j]

        # Equity series
        series["equity_total_return"] = eq_tr
        series["equity_dividend_yield"] = eq_div

        # Bonus / crediting series from par fund engine
        scen_idx = trial - 1  # bonus_data arrays are [scenario, time]
        for key in ("bonus_wl_cashdiv", "bonus_wl_rb", "crediting_rate_pension"):
            if key in bonus_data:
                arr = np.asarray(bonus_data[key])
                if arr.ndim != 2 or scen_idx >= arr.shape[0]:
                    raise ValueError(
                        f"bonus_data['{key}'] must be a 2D array with sufficient scenarios"
                    )
                series[key] = arr[scen_idx, :]

        # Sanity check: all series must have same length
        lengths = {k: v.shape[0] for k, v in series.items()}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"Inconsistent time dimensions across series: {lengths}")

        # ------------------------------------------------------------------
        # 4) Build DataFrame with external column names
        # ------------------------------------------------------------------

        n_time = next(iter(series.values())).shape[0]
        data: Dict[str, np.ndarray] = {}

        for internal_name, arr in series.items():
            col_name = self.field_map.get(internal_name, internal_name)
            data[col_name] = arr

        df = pd.DataFrame(data, index=np.arange(n_time))

        # ------------------------------------------------------------------
        # 5) Write scenario file (prepare-only behavior)
        # ------------------------------------------------------------------

        out_path = output_dir / f"scenario_{trial:04d}.csv"
        df.to_csv(out_path, index=False)

        # In this implementation, prepare_only simply means we do not trigger
        # any external valuation run here. The return value is the path to the
        # prepared time series file.
        return out_path


__all__ = ["ValuationTimeseriesBuilder"]
