"""Time grid manager for par_model_v2 scenarios.

Provides utilities to map a fine timestep grid (e.g. monthly) to a coarser
annual grid. This module is intentionally lightweight so it can be replaced
or extended by the main model configuration if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


@dataclass
class AnnualGridConfig:
    """Configuration describing how to derive annual steps from timesteps.

    Attributes
    ----------
    steps_per_year:
        Number of timesteps per year (e.g. 12 for monthly data).
    use_end_of_year:
        If True, annual index k corresponds to timestep (k + 1) * steps_per_year.
        Otherwise, timestep indices are taken as given in ``explicit_indices``.
    explicit_indices:
        Optional explicit mapping from annual step index to underlying ``Timestep``
        values in the ESG file. If provided, this takes precedence over the
        ``steps_per_year`` rule.
    """

    steps_per_year: int = 12
    use_end_of_year: bool = True
    explicit_indices: Sequence[int] | None = None


class GridManager:
    """Utility to aggregate high-frequency ESG series to an annual grid."""

    def __init__(self, config: AnnualGridConfig | None = None) -> None:
        self.config = config or AnnualGridConfig()

    def annual_indices(self, timesteps: Iterable[int]) -> np.ndarray:
        """Return the timestep indices to be used as annual grid.

        Parameters
        ----------
        timesteps:
            Iterable of integer timestep values from the ESG file (for a
            single trial), typically sorted.
        """

        ts = np.asarray(list(timesteps), dtype=int)
        if ts.size == 0:
            return ts

        if self.config.explicit_indices is not None:
            candidates = np.intersect1d(ts, np.asarray(self.config.explicit_indices, dtype=int))
            return candidates

        if self.config.use_end_of_year:
            max_t = ts.max()
            step = int(self.config.steps_per_year)
            years = max_t // step
            targets = np.arange(step, (years + 1) * step + 1, step, dtype=int)
            return np.intersect1d(ts, targets)

        # Fallback: use unique timesteps directly
        return np.unique(ts)

    def aggregate_series_to_annual(
        self, timesteps: Iterable[int], values: np.ndarray
    ) -> np.ndarray:
        """Down-sample a time series on the fine grid to an annual grid.

        Currently this is implemented as simple picking of the value at the
        selected annual timestep (no interpolation or compounding). Asset and
        par-fund layers can perform more sophisticated transformations if
        needed.
        """

        ts = np.asarray(list(timesteps), dtype=int)
        if values.shape[0] != ts.size:
            raise ValueError("values and timesteps must have the same length along axis 0")

        annual_ts = self.annual_indices(ts)
        if annual_ts.size == 0:
            return values[:0]

        # Map each annual timestep to its index in the original series
        index_map = {t: i for i, t in enumerate(ts)}
        idx = [index_map[t] for t in annual_ts if t in index_map]
        return values[np.asarray(idx, dtype=int)]


__all__ = ["AnnualGridConfig", "GridManager"]
