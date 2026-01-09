"""
Strategic Asset Allocation (SAA) Provider

Provides hierarchical lookup of SAA target weights with support for:
- Product-specific allocations
- Time-varying allocations (policy year, calendar year)
- Fund-level differentiation
- Interpolation for intermediate policy years
"""

from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from par_model_v2.assets.fund_portfolio import AssetClass


class SAAProvider:
    """
    Provides Strategic Asset Allocation target weights.

    Supports hierarchical lookup with fallbacks:
    1. Exact match: (product_code, policy_year, calendar_year, fund_id)
    2. Product + policy_year + fund_id (ignore calendar_year)
    3. ALL + policy_year + fund_id
    4. ALL + nearest policy_year + fund_id (interpolation)

    Parameters
    ----------
    saa_file_path : str or Path
        Path to strategic_asset_allocation.csv

    Examples
    --------
    >>> provider = SAAProvider('data/assumptions/strategic_asset_allocation.csv')
    >>> weights = provider.get_saa_weights(
    ...     product_code='PAR_TRAD',
    ...     policy_year=5,
    ...     calendar_year=2024,
    ...     fund_id='PAR'
    ... )
    >>> print(weights)
    {AssetClass.GOVT: 0.40, AssetClass.EQUITY: 0.30, ...}
    """

    def __init__(self, saa_file_path: str | Path):
        self.saa_file_path = Path(saa_file_path)
        self._saa_df: Optional[pd.DataFrame] = None

        # Load and validate
        self._load_saa_table()

    def _load_saa_table(self):
        """Load and validate SAA table."""
        if not self.saa_file_path.exists():
            raise FileNotFoundError(f"SAA table not found: {self.saa_file_path}")

        self._saa_df = pd.read_csv(self.saa_file_path)

        # Validate required columns
        required_cols = ["product_code", "policy_year", "fund_id", "asset_class", "target_weight"]
        missing = set(required_cols) - set(self._saa_df.columns)
        if missing:
            raise ValueError(f"SAA table missing columns: {missing}")

        # Validate weights are in [0, 1]
        if not self._saa_df["target_weight"].between(0, 1).all():
            raise ValueError("SAA target_weight values must be in [0, 1]")

        # Convert asset_class strings to AssetClass enums
        self._saa_df["asset_class_enum"] = self._saa_df["asset_class"].apply(AssetClass.from_string)

        # Fill optional columns with defaults
        if "calendar_year" not in self._saa_df.columns:
            self._saa_df["calendar_year"] = 0  # Use 0 as wildcard

        # Validate that weights sum to 1 within each group
        self._validate_weight_sums()

    def _validate_weight_sums(self):
        """Validate that weights sum to 1.0 within each group."""
        group_cols = ["product_code", "policy_year", "calendar_year", "fund_id"]

        # Group and sum weights
        weight_sums = self._saa_df.groupby(group_cols)["target_weight"].sum()

        # Check for groups that don't sum to 1.0
        invalid = weight_sums[~np.isclose(weight_sums, 1.0, atol=1e-4)]

        if len(invalid) > 0:
            print("WARNING: Some SAA weight groups do not sum to 1.0:")
            for idx, total in invalid.items():
                print(f"  {idx}: sum = {total:.6f}")
            raise ValueError(
                f"SAA weights must sum to 1.0 within each group. "
                f"Found {len(invalid)} invalid groups."
            )

    def get_saa_weights(
        self,
        product_code: str,
        policy_year: int,
        calendar_year: Optional[int] = None,
        fund_id: str = "PAR",
    ) -> Dict[AssetClass, float]:
        """
        Get SAA target weights for given policy characteristics.

        Parameters
        ----------
        product_code : str
            Product code (e.g., 'PAR_TRAD', 'PAR_UL')
        policy_year : int
            Policy duration year (1, 2, 3, ...)
        calendar_year : int, optional
            Calendar year (for time-varying SAA)
        fund_id : str
            Fund identifier (default 'PAR')

        Returns
        -------
        dict
            Target weights by AssetClass
            Keys are AssetClass enums, values are floats summing to 1.0

        Raises
        ------
        ValueError
            If no matching SAA found
        """
        # Try exact match first
        weights = self._lookup_exact(product_code, policy_year, calendar_year, fund_id)
        if weights is not None:
            return weights

        # Try without calendar_year
        weights = self._lookup_exact(product_code, policy_year, None, fund_id)
        if weights is not None:
            return weights

        # Try with "ALL" product
        weights = self._lookup_exact("ALL", policy_year, calendar_year, fund_id)
        if weights is not None:
            return weights

        # Try "ALL" without calendar_year
        weights = self._lookup_exact("ALL", policy_year, None, fund_id)
        if weights is not None:
            return weights

        # Try interpolation for "ALL" product
        weights = self._lookup_interpolated("ALL", policy_year, fund_id)
        if weights is not None:
            return weights

        # No match found
        raise ValueError(
            f"No SAA weights found for product_code={product_code}, "
            f"policy_year={policy_year}, calendar_year={calendar_year}, "
            f"fund_id={fund_id}"
        )

    def _lookup_exact(
        self,
        product_code: str,
        policy_year: int,
        calendar_year: Optional[int],
        fund_id: str,
    ) -> Optional[Dict[AssetClass, float]]:
        """Exact lookup in SAA table."""
        # Build filter
        mask = (
            (self._saa_df["product_code"] == product_code)
            & (self._saa_df["policy_year"] == policy_year)
            & (self._saa_df["fund_id"] == fund_id)
        )

        if calendar_year is not None:
            mask &= (
                (self._saa_df["calendar_year"] == calendar_year)
                | (self._saa_df["calendar_year"] == 0)  # Wildcard
            )

        matches = self._saa_df[mask]

        if len(matches) == 0:
            return None

        # Convert to dict
        weights = {}
        for _, row in matches.iterrows():
            ac = row["asset_class_enum"]
            weight = row["target_weight"]
            weights[ac] = weight

        # Fill missing asset classes with 0
        for ac in AssetClass:
            if ac not in weights:
                weights[ac] = 0.0

        return weights

    def _lookup_interpolated(
        self,
        product_code: str,
        policy_year: int,
        fund_id: str,
    ) -> Optional[Dict[AssetClass, float]]:
        """
        Interpolate SAA weights for intermediate policy years.

        Uses linear interpolation between nearest available policy years.
        """
        # Get all available policy years for this product/fund
        mask = (self._saa_df["product_code"] == product_code) & (self._saa_df["fund_id"] == fund_id)
        available_years = sorted(self._saa_df[mask]["policy_year"].unique())

        if len(available_years) == 0:
            return None

        # If exact match exists, use it
        if policy_year in available_years:
            return self._lookup_exact(product_code, policy_year, None, fund_id)

        # Find bounding years
        lower_years = [y for y in available_years if y < policy_year]
        upper_years = [y for y in available_years if y > policy_year]

        if len(lower_years) == 0:
            # Use first available year
            return self._lookup_exact(product_code, available_years[0], None, fund_id)

        if len(upper_years) == 0:
            # Use last available year
            return self._lookup_exact(product_code, available_years[-1], None, fund_id)

        # Interpolate between nearest years
        y_lower = max(lower_years)
        y_upper = min(upper_years)

        weights_lower = self._lookup_exact(product_code, y_lower, None, fund_id)
        weights_upper = self._lookup_exact(product_code, y_upper, None, fund_id)

        if weights_lower is None or weights_upper is None:
            return None

        # Linear interpolation
        alpha = (policy_year - y_lower) / (y_upper - y_lower)

        weights_interp = {}
        for ac in AssetClass:
            w_lower = weights_lower.get(ac, 0.0)
            w_upper = weights_upper.get(ac, 0.0)
            weights_interp[ac] = (1 - alpha) * w_lower + alpha * w_upper

        return weights_interp

    def get_available_products(self) -> list:
        """Get list of available product codes."""
        return sorted(self._saa_df["product_code"].unique())

    def get_available_funds(self) -> list:
        """Get list of available fund IDs."""
        return sorted(self._saa_df["fund_id"].unique())

    def get_policy_year_range(self, product_code: str = "ALL", fund_id: str = "PAR") -> tuple:
        """
        Get min and max policy years available for a product/fund.

        Returns
        -------
        tuple
            (min_year, max_year)
        """
        mask = (self._saa_df["product_code"] == product_code) & (self._saa_df["fund_id"] == fund_id)
        years = self._saa_df[mask]["policy_year"].unique()

        if len(years) == 0:
            return (0, 0)

        return (int(years.min()), int(years.max()))
