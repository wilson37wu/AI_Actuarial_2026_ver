"""
ESG Scenario Provider for PAR Fund Asset Share Projection

Provides memory-efficient access to ESG scenario data with support for:
- Government and corporate bond ZCB prices by tenor and rating
- Equity total returns and dividend yields
- Cash returns
- Chunked/columnar loading for large scenario files

Column naming convention (examples):
- Trial, Timestep (keys)
- ESG.Economies.CNY.NominalZCBP(Govt, 1, 3) ... ESG.Economies.CNY.NominalZCBP(Govt, 60, 3)
- ESG.Economies.CNY.NominalZCBP(AAA, 1, 3) ... ESG.Economies.CNY.NominalZCBP(CCC, 60, 3)
- ESG.Assets.EquityAssets.E_CNY.TotalReturn
- ESG.Assets.EquityAssets.E_CNY.DividendYield.Value
- ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


class ESGScenarioProvider:
    """
    Memory-efficient provider for ESG scenario data.

    Loads only required columns from wide ESG CSV/Parquet files.
    Supports government/credit ZCB prices, equity returns, and cash returns.

    Parameters
    ----------
    file_path : str or Path
        Path to ESG scenario file (CSV or Parquet)
    max_tenor : int, default=60
        Maximum tenor to load (limits columns)
    ratings : list of str, optional
        Credit ratings to load (e.g., ['AAA', 'AA', 'A'])
        If None, loads all available ratings
    equity_tickers : list of str, optional
        Equity tickers to load (e.g., ['E_CNY', 'P_CNY'])
        If None, loads E_CNY only
    use_parquet : bool, default=True
        If True and file is CSV, convert to Parquet for faster access

    Examples
    --------
    >>> provider = ESGScenarioProvider('esg_scenarios.csv', max_tenor=30)
    >>> zcb_price = provider.get_govt_zcb(trial=1, timestep=12, tenor=10)
    >>> equity_ret = provider.get_equity_total_return(trial=1, timestep=12)
    """

    def __init__(
        self,
        file_path: str | Path,
        max_tenor: int = 60,
        ratings: Optional[List[str]] = None,
        equity_tickers: Optional[List[str]] = None,
        use_parquet: bool = True,
    ):
        self.file_path = Path(file_path)
        self.max_tenor = max_tenor
        self.ratings = ratings or ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]
        self.equity_tickers = equity_tickers or ["E_CNY"]
        self.use_parquet = use_parquet

        # Data storage
        self._data: Optional[pd.DataFrame] = None
        self._trial_index: Optional[pd.MultiIndex] = None

        # Column name patterns
        self._govt_zcb_pattern = re.compile(
            r"ESG\.Economies\.CNY\.NominalZCBP\(Govt,\s*(\d+),\s*3\)"
        )
        self._credit_zcb_pattern = re.compile(
            r"ESG\.Economies\.CNY\.NominalZCBP\(([A-Z]+),\s*(\d+),\s*3\)"
        )

        # Column mappings (built during load)
        self._govt_zcb_cols: Dict[int, str] = {}  # tenor -> column name
        self._credit_zcb_cols: Dict[Tuple[str, int], str] = {}  # (rating, tenor) -> column
        self._equity_return_cols: Dict[str, str] = {}  # ticker -> column
        self._equity_div_yield_cols: Dict[str, str] = {}  # ticker -> column
        self._cash_return_col: Optional[str] = None

        # Load data
        self._load_data()

    def _load_data(self):
        """Load ESG scenario file with column filtering."""
        print(f"Loading ESG scenarios from: {self.file_path}")

        # Determine file format
        if self.file_path.suffix.lower() == ".parquet":
            self._load_from_parquet()
        else:
            self._load_from_csv()

        # Build column mappings
        self._build_column_mappings()

        # Create multi-index for fast lookup
        if "Trial" in self._data.columns and "Timestep" in self._data.columns:
            self._data.set_index(["Trial", "Timestep"], inplace=True)
            self._data.sort_index(inplace=True)
        else:
            raise ValueError("ESG file must contain 'Trial' and 'Timestep' columns")

        print(f"  Loaded {len(self._data)} rows, {len(self._data.columns)} columns")
        print(
            f"  Trials: {self._data.index.get_level_values(0).min()} to {self._data.index.get_level_values(0).max()}"
        )
        print(
            f"  Timesteps: {self._data.index.get_level_values(1).min()} to {self._data.index.get_level_values(1).max()}"
        )

    def _load_from_parquet(self):
        """Load from Parquet with column pruning."""
        # Read schema first to identify columns
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(self.file_path)
        all_columns = parquet_file.schema.names

        # Select columns to load
        columns_to_load = self._select_columns(all_columns)

        # Load only selected columns
        self._data = pd.read_parquet(self.file_path, columns=columns_to_load)

    def _load_from_csv(self):
        """Load from CSV, optionally converting to Parquet."""
        # Read header to identify columns
        header = pd.read_csv(self.file_path, nrows=0)
        all_columns = header.columns.tolist()

        # Select columns to load
        columns_to_load = self._select_columns(all_columns)

        # Load selected columns
        self._data = pd.read_csv(self.file_path, usecols=columns_to_load)

        # Optionally convert to Parquet for future use
        if self.use_parquet:
            parquet_path = self.file_path.with_suffix(".parquet")
            if not parquet_path.exists():
                print(f"  Converting to Parquet: {parquet_path}")
                self._data.to_parquet(parquet_path, index=False)

    def _select_columns(self, all_columns: List[str]) -> List[str]:
        """Select columns to load based on configuration."""
        selected = ["Trial", "Timestep"]

        for col in all_columns:
            # Government ZCB
            match = self._govt_zcb_pattern.match(col)
            if match:
                tenor = int(match.group(1))
                if tenor <= self.max_tenor:
                    selected.append(col)
                continue

            # Credit ZCB
            match = self._credit_zcb_pattern.match(col)
            if match:
                rating = match.group(1)
                tenor = int(match.group(2))
                if rating in self.ratings and tenor <= self.max_tenor:
                    selected.append(col)
                continue

            # Equity total return
            for ticker in self.equity_tickers:
                if f"ESG.Assets.EquityAssets.{ticker}.TotalReturn" in col:
                    selected.append(col)
                    break

            # Equity dividend yield
            for ticker in self.equity_tickers:
                if f"ESG.Assets.EquityAssets.{ticker}.DividendYield" in col:
                    selected.append(col)
                    break

            # Cash return
            if "CashTotalReturn" in col:
                selected.append(col)

        print(f"  Selected {len(selected)} columns from {len(all_columns)} total")
        return selected

    def _build_column_mappings(self):
        """Build mappings from (rating, tenor) to column names."""
        for col in self._data.columns:
            # Government ZCB
            match = self._govt_zcb_pattern.match(col)
            if match:
                tenor = int(match.group(1))
                self._govt_zcb_cols[tenor] = col
                continue

            # Credit ZCB
            match = self._credit_zcb_pattern.match(col)
            if match:
                rating = match.group(1)
                tenor = int(match.group(2))
                self._credit_zcb_cols[(rating, tenor)] = col
                continue

            # Equity total return
            for ticker in self.equity_tickers:
                if f"ESG.Assets.EquityAssets.{ticker}.TotalReturn" in col:
                    self._equity_return_cols[ticker] = col
                    break

            # Equity dividend yield
            for ticker in self.equity_tickers:
                if f"ESG.Assets.EquityAssets.{ticker}.DividendYield" in col:
                    self._equity_div_yield_cols[ticker] = col
                    break

            # Cash return
            if "CashTotalReturn" in col:
                self._cash_return_col = col

        print(f"  Mapped {len(self._govt_zcb_cols)} government ZCB tenors")
        print(f"  Mapped {len(self._credit_zcb_cols)} credit ZCB (rating, tenor) pairs")
        print(f"  Mapped {len(self._equity_return_cols)} equity return series")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def get_govt_zcb(self, trial: int, timestep: int, tenor: int) -> float:
        """
        Get government ZCB price for given trial, timestep, and tenor.

        Parameters
        ----------
        trial : int
            Scenario trial number
        timestep : int
            Time step (monthly or annual depending on file)
        tenor : int
            Bond tenor in years

        Returns
        -------
        float
            ZCB price (e.g., 0.95 for 95% of par)
        """
        if tenor not in self._govt_zcb_cols:
            # Extrapolate using last available tenor
            max_available = max(self._govt_zcb_cols.keys())
            if tenor > max_available:
                tenor = max_available
            else:
                raise ValueError(f"Tenor {tenor} not available in ESG data")

        col = self._govt_zcb_cols[tenor]
        return self._data.loc[(trial, timestep), col]

    def get_credit_zcb(self, trial: int, timestep: int, rating: str, tenor: int) -> float:
        """
        Get corporate bond ZCB price for given trial, timestep, rating, and tenor.

        Parameters
        ----------
        trial : int
            Scenario trial number
        timestep : int
            Time step
        rating : str
            Credit rating (e.g., 'AAA', 'BBB')
        tenor : int
            Bond tenor in years

        Returns
        -------
        float
            ZCB price
        """
        key = (rating, tenor)
        if key not in self._credit_zcb_cols:
            # Try fallback: use government ZCB if rating not available
            if tenor in self._govt_zcb_cols:
                return self.get_govt_zcb(trial, timestep, tenor)
            raise ValueError(f"Credit ZCB for {rating}, tenor {tenor} not available")

        col = self._credit_zcb_cols[key]
        return self._data.loc[(trial, timestep), col]

    def get_cash_return(self, trial: int, timestep: int) -> float:
        """
        Get cash total return for given trial and timestep.

        Returns
        -------
        float
            Gross return factor (e.g., 1.002 for 0.2% return)
        """
        if self._cash_return_col is None:
            raise ValueError("Cash return column not found in ESG data")

        return self._data.loc[(trial, timestep), self._cash_return_col]

    def get_equity_total_return(self, trial: int, timestep: int, ticker: str = "E_CNY") -> float:
        """
        Get equity total return for given trial and timestep.

        Parameters
        ----------
        ticker : str, default='E_CNY'
            Equity ticker

        Returns
        -------
        float
            Gross return factor (e.g., 1.05 for 5% return)
        """
        if ticker not in self._equity_return_cols:
            raise ValueError(f"Equity ticker {ticker} not available")

        col = self._equity_return_cols[ticker]
        return self._data.loc[(trial, timestep), col]

    def get_equity_dividend_yield(self, trial: int, timestep: int, ticker: str = "E_CNY") -> float:
        """
        Get equity dividend yield for given trial and timestep.

        Parameters
        ----------
        ticker : str, default='E_CNY'
            Equity ticker

        Returns
        -------
        float
            Annualized dividend yield (e.g., 0.03 for 3%)
        """
        if ticker not in self._equity_div_yield_cols:
            # Return 0 if not available
            return 0.0

        col = self._equity_div_yield_cols[ticker]
        return self._data.loc[(trial, timestep), col]

    def get_bond_total_return(
        self,
        trial: int,
        timestep: int,
        timestep_next: int,
        rating: str,
        tenor: int,
    ) -> float:
        """
        Calculate bond total return using roll-down strategy.

        For a bond with tenor n at timestep t:
        - Buy at price P(t, n)
        - Sell at timestep t+1 at price P(t+1, n-1)
        - Total return = P(t+1, n-1) / P(t, n)

        Parameters
        ----------
        trial : int
            Scenario trial
        timestep : int
            Current timestep
        timestep_next : int
            Next timestep (typically timestep + 1)
        rating : str
            'Govt' for government bonds, or credit rating
        tenor : int
            Current tenor in years

        Returns
        -------
        float
            Gross return factor
        """
        # Get current price
        if rating == "Govt":
            price_t = self.get_govt_zcb(trial, timestep, tenor)
        else:
            price_t = self.get_credit_zcb(trial, timestep, rating, tenor)

        # Get next period price (tenor rolled down by 1)
        tenor_next = max(1, tenor - 1)  # Don't go below 1

        try:
            if rating == "Govt":
                price_t1 = self.get_govt_zcb(trial, timestep_next, tenor_next)
            else:
                price_t1 = self.get_credit_zcb(trial, timestep_next, rating, tenor_next)

            return price_t1 / price_t if price_t > 0 else 1.0

        except (KeyError, ValueError):
            # Fallback: use cash return if next period not available
            return self.get_cash_return(trial, timestep)

    @property
    def n_trials(self) -> int:
        """Number of trials in the ESG file."""
        return self._data.index.get_level_values(0).max()

    @property
    def n_timesteps(self) -> int:
        """Number of timesteps in the ESG file."""
        return self._data.index.get_level_values(1).max()

    @property
    def available_tenors(self) -> List[int]:
        """List of available government bond tenors."""
        return sorted(self._govt_zcb_cols.keys())

    @property
    def available_ratings(self) -> List[str]:
        """List of available credit ratings."""
        return sorted(set(r for r, t in self._credit_zcb_cols.keys()))
