"""Batch execution of external valuation engine over multiple scenarios.

This module defines :class:`ValuationBatchExecutor`, a small orchestration
utility that prepares per-scenario inputs, optionally calls an external
valuation engine, and collects present value (PV) results.

The external engine is assumed to be invokable via a command-line interface
which takes, at minimum, a reference to:

- a grouped model point file (e.g. from ``model_point_grouping``), and
- a scenario time series file for the given scenario.

The exact command-line is left configurable by the user by overriding or
wrapping this class as needed. Here we provide a generic skeleton using
``subprocess.run``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

import numpy as np
import pandas as pd

from par_model_v2.esg import ESGAdapter
from par_model_v2.valuation.valuation_timeseries_builder import (
    ValuationTimeseriesBuilder,
)


@dataclass
class ValuationBatchExecutor:
    """Execute external valuation runs for blocks of scenarios.

    Parameters
    ----------
    external_engine_path:
        Path to the external valuation engine executable or script.
    prepare_only:
        If True, only prepare scenario time series files (no external engine
        execution). This is useful for dry-runs or when another process will
        consume the prepared files.
    logger:
        Optional :class:`logging.Logger` for diagnostic messages. If omitted,
        a module-level logger is used.
    """

    external_engine_path: str
    prepare_only: bool = True
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def run_block(
        self,
        scenario_ids: Iterable[int],
        grouped_mp_path: str | Path,
        scenario_files_dir: str | Path,
        scenario_builder: ValuationTimeseriesBuilder,
        esg_adapter: ESGAdapter,
        bonus_data: Mapping[str, np.ndarray],
        pv_output_dir: str | Path,
    ) -> pd.DataFrame:
        """Run valuation for a block of scenarios.

        This method does the following for each scenario in ``scenario_ids``:

        1. Ensures that the scenario time series file exists by invoking
           ``scenario_builder.build_scenario_files`` if necessary.
        2. If ``prepare_only`` is ``False``, constructs and executes a
           command-line call to ``external_engine_path`` with appropriate
           arguments (grouped model point file, scenario file, and any
           configured output location).
        3. Reads the resulting PV file for that scenario (assumed to be a CSV
           with columns ``Scenario``, ``ModelPointID``, ``LiabilityPV``) into a
           :class:`pandas.DataFrame`.
        4. Concatenates PV results across all scenarios and returns a combined
           DataFrame.

        Parameters
        ----------
        scenario_ids:
            Iterable of scenario indices (1-based) to run.
        grouped_mp_path:
            Path to the grouped model points file (e.g. Parquet) used by the
            external valuation engine.
        scenario_files_dir:
            Directory where scenario time series CSV files are or will be
            stored.
        scenario_builder:
            Instance of :class:`ValuationTimeseriesBuilder` used to prepare
            scenario time series files.
        scenario_data:
            Instance of :class:`MoodyESGAdapter` providing ESG paths for all
            scenarios.
        bonus_data:
            Mapping from bonus series names to 2D arrays [scenario, time] as
            produced by the par fund engine.
        pv_output_dir:
            Directory where the external engine writes per-scenario PV files.

        Returns
        -------
        pandas.DataFrame
            Combined PV results with columns ``Scenario``, ``ModelPointID``,
            ``LiabilityPV``.
        """

        grouped_mp_path = Path(grouped_mp_path)
        scenario_files_dir = Path(scenario_files_dir)
        pv_output_dir = Path(pv_output_dir)

        scenario_files_dir.mkdir(parents=True, exist_ok=True)
        pv_output_dir.mkdir(parents=True, exist_ok=True)

        all_results: List[pd.DataFrame] = []

        for scen_id in scenario_ids:
            scen_id_int = int(scen_id)
            if scen_id_int < 1:
                raise ValueError("Scenario IDs must be >= 1")

            self.logger.info("Processing scenario %s", scen_id_int)

            # 1) Prepare scenario time series file (if not already present)
            scen_file = scenario_files_dir / f"scenario_{scen_id_int:04d}.csv"
            if not scen_file.exists():
                self.logger.debug("Building scenario file %s", scen_file)
                scenario_builder.build_scenario_files(
                    trial_id=scen_id_int,
                    scenario_data=scenario_data,
                    bonus_data=bonus_data,
                    output_dir=scenario_files_dir,
                )

            # 2) Optionally invoke external engine
            pv_file = pv_output_dir / f"pv_{scen_id_int:04d}.csv"

            if not self.prepare_only:
                cmd = [
                    self.external_engine_path,
                    "--grouped-mp",
                    str(grouped_mp_path),
                    "--scenario-file",
                    str(scen_file),
                    "--output-pv",
                    str(pv_file),
                    "--scenario-id",
                    str(scen_id_int),
                ]

                self.logger.info("Running external engine for scenario %s", scen_id_int)
                try:
                    subprocess.run(cmd, check=True)
                except subprocess.CalledProcessError as exc:
                    self.logger.error(
                        "External valuation engine failed for scenario %s: %s",
                        scen_id_int,
                        exc,
                    )
                    raise

            # 3) Read PV results (if the file exists or was produced externally)
            if not pv_file.exists():
                self.logger.warning(
                    "PV file %s does not exist; skipping scenario %s",
                    pv_file,
                    scen_id_int,
                )
                continue

            df_pv = pd.read_csv(pv_file)
            expected_cols = {"Scenario", "ModelPointID", "LiabilityPV"}
            if not expected_cols.issubset(df_pv.columns):
                raise ValueError(f"PV file {pv_file} is missing required columns {expected_cols}")

            all_results.append(df_pv[["Scenario", "ModelPointID", "LiabilityPV"]])

        if not all_results:
            # No PV data was collected
            return pd.DataFrame(columns=["Scenario", "ModelPointID", "LiabilityPV"])

        combined = pd.concat(all_results, axis=0, ignore_index=True)
        return combined

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def merge_scenario_results(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
        """Merge PV results from multiple blocks.

        Parameters
        ----------
        frames:
            Iterable of DataFrames, each with columns ``Scenario``,
            ``ModelPointID``, ``LiabilityPV``.

        Returns
        -------
        pandas.DataFrame
            Concatenated PV results, sorted by ``Scenario`` then
            ``ModelPointID``.
        """

        frames_list = [
            f[["Scenario", "ModelPointID", "LiabilityPV"]] for f in frames if not f.empty
        ]
        if not frames_list:
            return pd.DataFrame(columns=["Scenario", "ModelPointID", "LiabilityPV"])

        combined = pd.concat(frames_list, axis=0, ignore_index=True)
        combined.sort_values(["Scenario", "ModelPointID"], inplace=True)
        combined.reset_index(drop=True, inplace=True)
        return combined


__all__ = ["ValuationBatchExecutor"]
