"""
Assumption Provider

Loads and provides access to table-driven actuarial assumptions
with hierarchical lookup and fallback logic.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


class AssumptionProvider:
    """
    Provides table-driven actuarial assumptions with hierarchical lookup.

    Loads CSV tables from a directory and provides methods to retrieve
    assumptions for specific policies and projection points.
    """

    def __init__(self, assumption_dir: Optional[Path] = None):
        """
        Initialize the assumption provider.

        Parameters
        ----------
        assumption_dir : Path, optional
            Directory containing assumption CSV files.
            If None, uses default data/assumptions relative to project root.
        """
        if assumption_dir is None:
            # Default to data/assumptions (Path already imported at module level)
            project_root = Path(__file__).resolve().parent.parent.parent
            assumption_dir = project_root / "data" / "assumptions"

        self.assumption_dir = Path(assumption_dir)

        # Storage for loaded tables
        self._mortality_df: Optional[pd.DataFrame] = None
        self._lapse_df: Optional[pd.DataFrame] = None
        self._expenses_df: Optional[pd.DataFrame] = None
        self._bonus_df: Optional[pd.DataFrame] = None
        self._discount_df: Optional[pd.DataFrame] = None
        self._investment_df: Optional[pd.DataFrame] = None

        # Caches for fast lookup
        self._mortality_cache: Dict[str, float] = {}
        self._lapse_cache: Dict[str, float] = {}
        self._discount_factors: Optional[np.ndarray] = None
        self._investment_returns: Optional[np.ndarray] = None

        # Load all tables
        self._load_all_tables()

    def _load_all_tables(self):
        """Load all assumption tables from CSV files."""

        # Mortality
        mort_path = self.assumption_dir / "mortality_qx.csv"
        if mort_path.exists():
            self._mortality_df = pd.read_csv(mort_path)
            self._validate_mortality_table()
        else:
            raise FileNotFoundError(f"Mortality table not found: {mort_path}")

        # Lapse
        lapse_path = self.assumption_dir / "lapse.csv"
        if lapse_path.exists():
            self._lapse_df = pd.read_csv(lapse_path)
            self._validate_lapse_table()
        else:
            raise FileNotFoundError(f"Lapse table not found: {lapse_path}")

        # Expenses
        exp_path = self.assumption_dir / "expenses.csv"
        if exp_path.exists():
            self._expenses_df = pd.read_csv(exp_path)
            self._validate_expenses_table()
        else:
            raise FileNotFoundError(f"Expenses table not found: {exp_path}")

        # Bonus/RB
        bonus_path = self.assumption_dir / "bonus_rb.csv"
        if bonus_path.exists():
            self._bonus_df = pd.read_csv(bonus_path)
            self._validate_bonus_table()
        else:
            raise FileNotFoundError(f"Bonus table not found: {bonus_path}")

        # Discount curve
        disc_path = self.assumption_dir / "discount_curve.csv"
        if disc_path.exists():
            self._discount_df = pd.read_csv(disc_path)
            self._validate_discount_curve()
            self._precompute_discount_factors()
        else:
            raise FileNotFoundError(f"Discount curve not found: {disc_path}")

        # Investment return
        inv_path = self.assumption_dir / "investment_return.csv"
        if inv_path.exists():
            self._investment_df = pd.read_csv(inv_path)
            self._validate_investment_curve()
            self._precompute_investment_returns()
        else:
            raise FileNotFoundError(f"Investment return curve not found: {inv_path}")

    def _validate_mortality_table(self):
        """Validate mortality table schema."""
        required_cols = [
            "table_id",
            "product_code",
            "issue_year",
            "gender",
            "uw_class",
            "policy_status",
            "sa_band",
            "prem_band",
            "attained_age",
            "qx_annual",
        ]
        missing = set(required_cols) - set(self._mortality_df.columns)
        if missing:
            raise ValueError(f"Mortality table missing columns: {missing}")

        # Validate qx in [0, 1]
        if not self._mortality_df["qx_annual"].between(0, 1).all():
            raise ValueError("Mortality qx values must be in [0, 1]")

    def _validate_lapse_table(self):
        """Validate lapse table schema."""
        required_cols = [
            "table_id",
            "product_code",
            "issue_year",
            "gender",
            "uw_class",
            "policy_status",
            "sa_band",
            "prem_band",
            "policy_year",
            "lapse_annual",
        ]
        missing = set(required_cols) - set(self._lapse_df.columns)
        if missing:
            raise ValueError(f"Lapse table missing columns: {missing}")

        # Validate lapse in [0, 1]
        if not self._lapse_df["lapse_annual"].between(0, 1).all():
            raise ValueError("Lapse rates must be in [0, 1]")

    def _validate_expenses_table(self):
        """Validate expenses table schema."""
        required_cols = [
            "table_id",
            "product_code",
            "policy_status",
            "policy_year",
            "expense_fixed_monthly",
            "expense_pct_premium",
        ]
        missing = set(required_cols) - set(self._expenses_df.columns)
        if missing:
            raise ValueError(f"Expenses table missing columns: {missing}")

    def _validate_bonus_table(self):
        """Validate bonus table schema."""
        required_cols = ["table_id", "product_code", "policy_year", "rb_growth_annual"]
        missing = set(required_cols) - set(self._bonus_df.columns)
        if missing:
            raise ValueError(f"Bonus table missing columns: {missing}")

    def _validate_discount_curve(self):
        """Validate discount curve schema."""
        required_cols = ["curve_id", "tenor_years", "zero_rate_annual"]
        missing = set(required_cols) - set(self._discount_df.columns)
        if missing:
            raise ValueError(f"Discount curve missing columns: {missing}")

        # Check rates are reasonable
        if not self._discount_df["zero_rate_annual"].between(-0.05, 0.20).all():
            raise ValueError("Discount rates outside reasonable bounds [-5%, 20%]")

    def _validate_investment_curve(self):
        """Validate investment return curve schema."""
        required_cols = ["curve_id", "tenor_years", "return_rate_annual"]
        missing = set(required_cols) - set(self._investment_df.columns)
        if missing:
            raise ValueError(f"Investment return curve missing columns: {missing}")

    def _precompute_discount_factors(self):
        """Precompute monthly discount factors from annual zero rates."""
        # Take first curve_id
        curve_id = self._discount_df["curve_id"].iloc[0]
        curve_data = self._discount_df[self._discount_df["curve_id"] == curve_id].copy()
        curve_data = curve_data.sort_values("tenor_years")

        # Convert annual rates to monthly discount factors
        # DF(t) = exp(-r * t) where t is in years
        max_years = int(curve_data["tenor_years"].max())
        max_months = max_years * 12 + 12  # Extra buffer

        self._discount_factors = np.zeros(max_months)

        for month_idx in range(max_months):
            year_frac = month_idx / 12.0

            # Interpolate rate
            if year_frac <= curve_data["tenor_years"].min():
                rate = curve_data["zero_rate_annual"].iloc[0]
            elif year_frac >= curve_data["tenor_years"].max():
                rate = curve_data["zero_rate_annual"].iloc[-1]
            else:
                rate = np.interp(
                    year_frac,
                    curve_data["tenor_years"].values,
                    curve_data["zero_rate_annual"].values,
                )

            # Discount factor
            self._discount_factors[month_idx] = np.exp(-rate * year_frac)

    def _precompute_investment_returns(self):
        """Precompute monthly investment returns from annual rates."""
        curve_id = self._investment_df["curve_id"].iloc[0]
        curve_data = self._investment_df[self._investment_df["curve_id"] == curve_id].copy()
        curve_data = curve_data.sort_values("tenor_years")

        max_years = int(curve_data["tenor_years"].max())
        max_months = max_years * 12 + 12

        self._investment_returns = np.zeros(max_months)

        for month_idx in range(max_months):
            year_frac = month_idx / 12.0

            if year_frac <= curve_data["tenor_years"].min():
                rate = curve_data["return_rate_annual"].iloc[0]
            elif year_frac >= curve_data["tenor_years"].max():
                rate = curve_data["return_rate_annual"].iloc[-1]
            else:
                rate = np.interp(
                    year_frac,
                    curve_data["tenor_years"].values,
                    curve_data["return_rate_annual"].values,
                )

            # Convert to monthly rate (simple approximation)
            self._investment_returns[month_idx] = rate / 12.0

    def get_mortality_qx(self, policy: Dict[str, Any], attained_age: int) -> Tuple[float, str]:
        """
        Get mortality rate for a policy at a given attained age.

        Parameters
        ----------
        policy : dict
            Policy attributes
        attained_age : int
            Attained age

        Returns
        -------
        tuple
            (qx_annual, table_id_used)
        """
        # Build cache key
        cache_key = (
            policy.get("product_code", "WL"),
            policy.get("issue_year", 2020),
            policy.get("gender", "U"),
            policy.get("uw_class", "STD"),
            policy.get("policy_status", "INFORCE"),
            policy.get("sa_band", "ALL"),
            policy.get("prem_band", "ALL"),
            attained_age,
        )

        if cache_key in self._mortality_cache:
            return self._mortality_cache[cache_key], "CACHED"

        # Hierarchical lookup with fallbacks
        product = policy.get("product_code", "WL")
        issue_year = policy.get("issue_year", 2020)
        gender = policy.get("gender", "U")
        uw_class = policy.get("uw_class", "STD")
        status = policy.get("policy_status", "INFORCE")
        sa_band = policy.get("sa_band", "ALL")
        prem_band = policy.get("prem_band", "ALL")

        # Fallback hierarchy
        lookup_attempts = [
            (product, issue_year, gender, uw_class, status, sa_band, prem_band),
            (product, issue_year, gender, uw_class, status, sa_band, "ALL"),
            (product, issue_year, gender, uw_class, status, "ALL", "ALL"),
            (product, "ALL", gender, uw_class, status, "ALL", "ALL"),
            (product, "ALL", "ALL", uw_class, status, "ALL", "ALL"),
            (product, "ALL", "ALL", "ALL", status, "ALL", "ALL"),
            (product, "ALL", "ALL", "ALL", "ALL", "ALL", "ALL"),
        ]

        for attempt in lookup_attempts:
            prod, iy, gen, uw, stat, sa, pr = attempt

            mask = (
                (self._mortality_df["product_code"] == prod)
                & (self._mortality_df["issue_year"] == iy)
                & (self._mortality_df["gender"] == gen)
                & (self._mortality_df["uw_class"] == uw)
                & (self._mortality_df["policy_status"] == stat)
                & (self._mortality_df["sa_band"] == sa)
                & (self._mortality_df["prem_band"] == pr)
                & (self._mortality_df["attained_age"] == attained_age)
            )

            matches = self._mortality_df[mask]
            if len(matches) > 0:
                qx = matches["qx_annual"].iloc[0]
                table_id = matches["table_id"].iloc[0]
                self._mortality_cache[cache_key] = qx
                return qx, table_id

        # Ultimate fallback: use a default
        return 0.001, "DEFAULT"

    def get_lapse_rate(self, policy: Dict[str, Any], policy_year: int) -> Tuple[float, str]:
        """
        Get lapse rate for a policy at a given policy year.

        Parameters
        ----------
        policy : dict
            Policy attributes
        policy_year : int
            Policy year (1-based)

        Returns
        -------
        tuple
            (lapse_annual, table_id_used)
        """
        cache_key = (
            policy.get("product_code", "WL"),
            policy.get("issue_year", 2020),
            policy.get("gender", "U"),
            policy.get("uw_class", "STD"),
            policy.get("policy_status", "INFORCE"),
            policy.get("sa_band", "ALL"),
            policy.get("prem_band", "ALL"),
            policy_year,
        )

        if cache_key in self._lapse_cache:
            return self._lapse_cache[cache_key], "CACHED"

        product = policy.get("product_code", "WL")
        issue_year = policy.get("issue_year", 2020)
        gender = policy.get("gender", "U")
        uw_class = policy.get("uw_class", "STD")
        status = policy.get("policy_status", "INFORCE")
        sa_band = policy.get("sa_band", "ALL")
        prem_band = policy.get("prem_band", "ALL")

        # Find closest policy_year in table (may not have all years)
        available_years = self._lapse_df["policy_year"].unique()
        closest_year = min(available_years, key=lambda x: abs(x - policy_year))

        lookup_attempts = [
            (product, issue_year, gender, uw_class, status, sa_band, prem_band),
            (product, issue_year, gender, uw_class, status, sa_band, "ALL"),
            (product, issue_year, gender, uw_class, status, "ALL", "ALL"),
            (product, "ALL", gender, uw_class, status, "ALL", "ALL"),
            (product, "ALL", "ALL", uw_class, status, "ALL", "ALL"),
            (product, "ALL", "ALL", "ALL", status, "ALL", "ALL"),
            (product, "ALL", "ALL", "ALL", "ALL", "ALL", "ALL"),
        ]

        for attempt in lookup_attempts:
            prod, iy, gen, uw, stat, sa, pr = attempt

            mask = (
                (self._lapse_df["product_code"] == prod)
                & (self._lapse_df["issue_year"] == iy)
                & (self._lapse_df["gender"] == gen)
                & (self._lapse_df["uw_class"] == uw)
                & (self._lapse_df["policy_status"] == stat)
                & (self._lapse_df["sa_band"] == sa)
                & (self._lapse_df["prem_band"] == pr)
                & (self._lapse_df["policy_year"] == closest_year)
            )

            matches = self._lapse_df[mask]
            if len(matches) > 0:
                lapse = matches["lapse_annual"].iloc[0]
                table_id = matches["table_id"].iloc[0]
                self._lapse_cache[cache_key] = lapse
                return lapse, table_id

        return 0.01, "DEFAULT"

    def get_expenses(self, policy: Dict[str, Any], policy_year: int) -> Tuple[float, float, str]:
        """
        Get expense assumptions for a policy.

        Parameters
        ----------
        policy : dict
            Policy attributes
        policy_year : int
            Policy year (1-based)

        Returns
        -------
        tuple
            (expense_fixed_monthly, expense_pct_premium, table_id_used)
        """
        product = policy.get("product_code", "WL")
        status = policy.get("policy_status", "INFORCE")

        # Find closest policy_year
        available_years = self._expenses_df["policy_year"].unique()
        closest_year = min(available_years, key=lambda x: abs(x - policy_year))

        # Lookup attempts
        lookup_attempts = [
            (product, status),
            (product, "ALL"),
        ]

        for prod, stat in lookup_attempts:
            mask = (
                (self._expenses_df["product_code"] == prod)
                & (self._expenses_df["policy_status"] == stat)
                & (self._expenses_df["policy_year"] == closest_year)
            )

            matches = self._expenses_df[mask]
            if len(matches) > 0:
                fixed = matches["expense_fixed_monthly"].iloc[0]
                pct = matches["expense_pct_premium"].iloc[0]
                table_id = matches["table_id"].iloc[0]
                return fixed, pct, table_id

        return 50.0, 0.02, "DEFAULT"

    def get_rb_growth(self, policy: Dict[str, Any], policy_year: int) -> Tuple[float, str]:
        """
        Get reversionary bonus growth rate.

        Parameters
        ----------
        policy : dict
            Policy attributes
        policy_year : int
            Policy year (1-based)

        Returns
        -------
        tuple
            (rb_growth_annual, table_id_used)
        """
        product = policy.get("product_code", "WL")

        # Find closest policy_year
        available_years = self._bonus_df["policy_year"].unique()
        closest_year = min(available_years, key=lambda x: abs(x - policy_year))

        mask = (self._bonus_df["product_code"] == product) & (
            self._bonus_df["policy_year"] == closest_year
        )

        matches = self._bonus_df[mask]
        if len(matches) > 0:
            rb_growth = matches["rb_growth_annual"].iloc[0]
            table_id = matches["table_id"].iloc[0]
            return rb_growth, table_id

        return 0.02, "DEFAULT"

    def get_discount_factor(self, month_index: int) -> float:
        """
        Get discount factor for a given month.

        Parameters
        ----------
        month_index : int
            Month index (0 = valuation date)

        Returns
        -------
        float
            Discount factor
        """
        if month_index < 0:
            return 1.0

        if month_index >= len(self._discount_factors):
            # Extrapolate using last rate
            return self._discount_factors[-1]

        return self._discount_factors[month_index]

    def get_discount_rate(self, month_index: int) -> float:
        """
        Get annualized discount rate for a given month.

        Parameters
        ----------
        month_index : int
            Month index

        Returns
        -------
        float
            Annualized discount rate
        """
        year_frac = month_index / 12.0
        df = self.get_discount_factor(month_index)

        if df <= 0 or year_frac <= 0:
            return 0.03  # Default

        # r = -ln(DF) / t
        rate = -np.log(df) / year_frac
        return rate

    def get_investment_return(self, month_index: int) -> float:
        """
        Get monthly investment return rate.

        Parameters
        ----------
        month_index : int
            Month index

        Returns
        -------
        float
            Monthly return rate
        """
        if month_index < 0:
            return 0.0

        if month_index >= len(self._investment_returns):
            return self._investment_returns[-1]

        return self._investment_returns[month_index]

    def get_summary_info(self) -> Dict[str, Any]:
        """
        Get summary information about loaded assumptions.

        Returns
        -------
        dict
            Summary statistics and metadata
        """
        return {
            "assumption_dir": str(self.assumption_dir),
            "mortality_tables": self._mortality_df["table_id"].nunique()
            if self._mortality_df is not None
            else 0,
            "mortality_rows": len(self._mortality_df) if self._mortality_df is not None else 0,
            "lapse_tables": self._lapse_df["table_id"].nunique()
            if self._lapse_df is not None
            else 0,
            "lapse_rows": len(self._lapse_df) if self._lapse_df is not None else 0,
            "expense_tables": self._expenses_df["table_id"].nunique()
            if self._expenses_df is not None
            else 0,
            "bonus_tables": self._bonus_df["table_id"].nunique()
            if self._bonus_df is not None
            else 0,
            "discount_curves": self._discount_df["curve_id"].nunique()
            if self._discount_df is not None
            else 0,
            "investment_curves": self._investment_df["curve_id"].nunique()
            if self._investment_df is not None
            else 0,
            "discount_rate_1y": float(self.get_discount_rate(12)),
            "discount_rate_10y": float(self.get_discount_rate(120)),
        }
