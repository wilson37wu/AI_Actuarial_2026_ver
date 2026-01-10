"""
Flexible multi-dimensional assumption provider.

This module provides a flexible framework for loading and querying actuarial assumptions
with support for multiple dimensions (product, gender, age, policy year, etc.) and
automatic interpolation/extrapolation.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TableMetadata:
    """Metadata for an assumption table."""

    name: str
    file: str
    dimensions: List[str]
    value_column: str
    interpolation: str = "linear"
    extrapolation: str = "constant"
    description: Optional[str] = None


class FlexibleAssumptionProvider:
    """
    Multi-dimensional assumption provider with flexible schema.

    Features:
    - Product-specific assumptions
    - Multi-dimensional keys (gender, age, policy_year, etc.)
    - Sum assured and premium banding
    - Interpolation for missing values
    - Metadata-driven table loading
    - Caching for performance

    Example:
        >>> provider = FlexibleAssumptionProvider("data/assumptions", "metadata.json")
        >>> qx = provider.get_mortality("WL", "M", 35, "N", 5)
        >>> lapse = provider.get_lapse("Pension", 3, "30-40", "100000-500000")
    """

    def __init__(self, assumption_dir: str, metadata_path: Optional[str] = None):
        """
        Initialize the flexible assumption provider.

        Args:
            assumption_dir: Directory containing assumption CSV files
            metadata_path: Path to metadata.json (relative to assumption_dir or absolute)
        """
        self.assumption_dir = Path(assumption_dir)

        if metadata_path is None:
            metadata_path = self.assumption_dir / "metadata.json"
        else:
            metadata_path = Path(metadata_path)
            if not metadata_path.is_absolute():
                metadata_path = self.assumption_dir / metadata_path

        self.metadata_path = metadata_path
        self.tables: Dict[str, pd.DataFrame] = {}
        self.metadata: Dict[str, TableMetadata] = {}
        self.cache: Dict[Tuple, Any] = {}

        self._load_metadata()
        self._load_all_tables()

    def _load_metadata(self):
        """Load table metadata from JSON file."""
        if not self.metadata_path.exists():
            logger.warning(f"Metadata file not found: {self.metadata_path}")
            logger.warning("Using default metadata for backward compatibility")
            self._create_default_metadata()
            return

        with open(self.metadata_path, "r") as f:
            metadata_dict = json.load(f)

        for table_name, meta in metadata_dict.items():
            self.metadata[table_name] = TableMetadata(
                name=table_name,
                file=meta["file"],
                dimensions=meta["dimensions"],
                value_column=meta["value_column"],
                interpolation=meta.get("interpolation", "linear"),
                extrapolation=meta.get("extrapolation", "constant"),
                description=meta.get("description"),
            )

        logger.info(f"Loaded metadata for {len(self.metadata)} tables")

    def _create_default_metadata(self):
        """Create default metadata for backward compatibility."""
        self.metadata = {
            "mortality_qx": TableMetadata(
                name="mortality_qx",
                file="mortality_qx.csv",
                dimensions=["age", "gender", "policy_year"],
                value_column="qx",
                interpolation="linear",
                extrapolation="constant",
            ),
            "lapse": TableMetadata(
                name="lapse",
                file="lapse.csv",
                dimensions=["policy_year", "age"],
                value_column="lapse_rate",
                interpolation="linear",
                extrapolation="constant",
            ),
            "expenses": TableMetadata(
                name="expenses",
                file="expenses.csv",
                dimensions=["product_type"],
                value_column="expense_rate",
                interpolation="step",
                extrapolation="constant",
            ),
        }

    def _load_all_tables(self):
        """Load all assumption tables specified in metadata."""
        for table_name, meta in self.metadata.items():
            file_path = self.assumption_dir / meta.file

            if not file_path.exists():
                logger.warning(f"Table file not found: {file_path}")
                continue

            try:
                df = pd.read_csv(file_path)
                self._validate_table(df, meta)
                self.tables[table_name] = df
                logger.info(f"Loaded table '{table_name}': {len(df)} rows")
            except Exception as e:
                logger.error(f"Error loading table '{table_name}': {e}")
                raise

    def _validate_table(self, df: pd.DataFrame, meta: TableMetadata):
        """Validate that table has required columns."""
        required_cols = meta.dimensions + [meta.value_column]
        missing_cols = set(required_cols) - set(df.columns)

        if missing_cols:
            raise ValueError(f"Table '{meta.name}' missing required columns: {missing_cols}")

    def get_value(self, table_name: str, use_cache: bool = True, **dimensions) -> float:
        """
        Generic lookup method for any table.

        Args:
            table_name: Name of the assumption table
            use_cache: Whether to use cached values
            **dimensions: Dimension values as keyword arguments

        Returns:
            Assumption value (float)

        Example:
            >>> provider.get_value('mortality_qx', product='WL', gender='M', age=35)
        """
        # Check cache
        if use_cache:
            cache_key = (table_name, tuple(sorted(dimensions.items())))
            if cache_key in self.cache:
                return self.cache[cache_key]

        # Get table and metadata
        if table_name not in self.tables:
            raise ValueError(f"Table '{table_name}' not loaded")

        df = self.tables[table_name]
        meta = self.metadata[table_name]

        # Build query
        value = self._lookup_with_interpolation(df, meta, dimensions)

        # Cache result
        if use_cache:
            self.cache[cache_key] = value

        return value

    def _lookup_with_interpolation(
        self, df: pd.DataFrame, meta: TableMetadata, dimensions: Dict[str, Any]
    ) -> float:
        """
        Lookup value with interpolation support.

        Strategy:
        1. Try exact match first
        2. If not found, check if any numeric dimensions need interpolation
        3. Apply interpolation method from metadata
        4. If still not found, apply extrapolation
        """
        # Try exact match first
        mask = pd.Series(True, index=df.index)
        for dim in meta.dimensions:
            if dim in dimensions:
                mask &= df[dim] == dimensions[dim]

        matches = df[mask]
        if len(matches) > 0:
            return float(matches[meta.value_column].iloc[0])

        # Check for numeric dimensions that need interpolation
        numeric_dims = self._identify_numeric_dimensions(df, meta.dimensions)

        if not numeric_dims:
            # No numeric dimensions, try partial match
            return self._partial_match(df, meta, dimensions)

        # Apply interpolation for numeric dimensions
        return self._interpolate(df, meta, dimensions, numeric_dims)

    def _identify_numeric_dimensions(self, df: pd.DataFrame, dimensions: List[str]) -> List[str]:
        """Identify which dimensions are numeric and can be interpolated."""
        numeric_dims = []
        for dim in dimensions:
            if dim in df.columns and pd.api.types.is_numeric_dtype(df[dim]):
                numeric_dims.append(dim)
        return numeric_dims

    def _partial_match(
        self, df: pd.DataFrame, meta: TableMetadata, dimensions: Dict[str, Any]
    ) -> float:
        """Find best partial match when exact match not found."""
        # Match as many dimensions as possible
        mask = pd.Series(True, index=df.index)
        matched_dims = 0

        for dim in meta.dimensions:
            if dim in dimensions and dim in df.columns:
                dim_mask = df[dim] == dimensions[dim]
                if dim_mask.any():
                    mask &= dim_mask
                    matched_dims += 1

        matches = df[mask]
        if len(matches) > 0:
            return float(matches[meta.value_column].iloc[0])

        # If no match, use first row (with warning)
        logger.warning(
            f"No match found for {dimensions} in table '{meta.name}', using first row as fallback"
        )
        return float(df[meta.value_column].iloc[0])

    def _interpolate(
        self,
        df: pd.DataFrame,
        meta: TableMetadata,
        dimensions: Dict[str, Any],
        numeric_dims: List[str],
    ) -> float:
        """
        Interpolate value for numeric dimensions.

        Supports:
        - Linear interpolation
        - Step interpolation (nearest neighbor)
        """
        # Filter by non-numeric dimensions first
        mask = pd.Series(True, index=df.index)
        for dim in meta.dimensions:
            if dim not in numeric_dims and dim in dimensions:
                mask &= df[dim] == dimensions[dim]

        subset = df[mask].copy()

        if len(subset) == 0:
            logger.warning(f"No data for non-numeric dimensions in '{meta.name}', using full table")
            subset = df.copy()

        # For simplicity, interpolate on first numeric dimension
        if len(numeric_dims) > 0:
            primary_dim = numeric_dims[0]
            target_value = dimensions.get(primary_dim)

            if target_value is None:
                return float(subset[meta.value_column].iloc[0])

            # Sort by primary dimension
            subset = subset.sort_values(primary_dim)

            if meta.interpolation == "linear":
                return self._linear_interpolate(
                    subset, primary_dim, meta.value_column, target_value
                )
            elif meta.interpolation == "step":
                return self._step_interpolate(subset, primary_dim, meta.value_column, target_value)

        return float(subset[meta.value_column].iloc[0])

    def _linear_interpolate(
        self, df: pd.DataFrame, x_col: str, y_col: str, x_target: float
    ) -> float:
        """Linear interpolation between two points."""
        x_vals = df[x_col].values
        y_vals = df[y_col].values

        # Check bounds
        if x_target <= x_vals[0]:
            return float(y_vals[0])
        if x_target >= x_vals[-1]:
            return float(y_vals[-1])

        # Find surrounding points
        idx = np.searchsorted(x_vals, x_target)
        x0, x1 = x_vals[idx - 1], x_vals[idx]
        y0, y1 = y_vals[idx - 1], y_vals[idx]

        # Linear interpolation
        t = (x_target - x0) / (x1 - x0)
        return float(y0 + t * (y1 - y0))

    def _step_interpolate(self, df: pd.DataFrame, x_col: str, y_col: str, x_target: float) -> float:
        """Step interpolation (nearest neighbor)."""
        x_vals = df[x_col].values
        y_vals = df[y_col].values

        # Find nearest point
        idx = np.argmin(np.abs(x_vals - x_target))
        return float(y_vals[idx])

    # Convenience methods for common assumptions

    def get_mortality(
        self, product: str, gender: str, age: int, smoker_status: str = "N", policy_year: int = 1
    ) -> float:
        """
        Get mortality rate (qx).

        Args:
            product: Product code (e.g., 'WL', 'Pension')
            gender: 'M' or 'F'
            age: Attained age
            smoker_status: 'Y' or 'N'
            policy_year: Policy year

        Returns:
            Mortality rate (qx)
        """
        return self.get_value(
            "mortality_qx",
            product=product,
            gender=gender,
            age=age,
            smoker_status=smoker_status,
            policy_year=policy_year,
        )

    def get_lapse(
        self,
        product: str,
        policy_year: int,
        age: Optional[Union[int, str]] = None,
        sum_assured_band: Optional[str] = None,
    ) -> float:
        """
        Get lapse rate.

        Args:
            product: Product code
            policy_year: Policy year
            age: Age or age band (e.g., 30 or "30-40")
            sum_assured_band: Sum assured band (e.g., "100000-500000")

        Returns:
            Lapse rate
        """
        kwargs = {"product": product, "policy_year": policy_year}

        if age is not None:
            if isinstance(age, int):
                kwargs["age"] = age
            else:
                kwargs["age_band"] = age

        if sum_assured_band is not None:
            kwargs["sum_assured_band"] = sum_assured_band

        return self.get_value("lapse", **kwargs)

    def get_expense(
        self, product: str, policy_year: int = 1, premium_band: Optional[str] = None
    ) -> float:
        """
        Get expense loading.

        Args:
            product: Product code
            policy_year: Policy year
            premium_band: Premium band (e.g., "0-10000")

        Returns:
            Expense rate or amount
        """
        kwargs = {"product": product, "policy_year": policy_year}

        if premium_band is not None:
            kwargs["premium_band"] = premium_band

        return self.get_value("expenses", **kwargs)

    def get_bonus_rate(self, product: str, policy_year: int, fund_type: str = "PAR") -> float:
        """
        Get reversionary bonus rate.

        Args:
            product: Product code
            policy_year: Policy year
            fund_type: Fund type (e.g., 'PAR', 'NPAR')

        Returns:
            Bonus rate
        """
        return self.get_value(
            "bonus_rates", product=product, policy_year=policy_year, fund_type=fund_type
        )

    def clear_cache(self):
        """Clear the lookup cache."""
        self.cache.clear()
        logger.info("Assumption cache cleared")

    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """
        Get information about a loaded table.

        Returns:
            Dictionary with table metadata and statistics
        """
        if table_name not in self.tables:
            raise ValueError(f"Table '{table_name}' not loaded")

        df = self.tables[table_name]
        meta = self.metadata[table_name]

        return {
            "name": meta.name,
            "file": meta.file,
            "dimensions": meta.dimensions,
            "value_column": meta.value_column,
            "interpolation": meta.interpolation,
            "extrapolation": meta.extrapolation,
            "description": meta.description,
            "rows": len(df),
            "columns": list(df.columns),
            "value_range": (df[meta.value_column].min(), df[meta.value_column].max()),
        }

    def list_tables(self) -> List[str]:
        """Get list of loaded table names."""
        return list(self.tables.keys())
