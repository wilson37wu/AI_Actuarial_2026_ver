# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-01-10

### Added
- **Dynamic ALM Engine (MVP)**: Core stochastic asset-liability management framework
  - Holdings-based portfolio tracking (government bonds, credit, equity, cash)
  - ESG-driven returns application
  - Deterministic buy/sell rules with priority-based liquidation
  - Transaction cost modeling (2-60 bps by asset type)
  - Cash buffer management with automatic funding
  - Rebalancing to SAA targets (annual, each-step, or none)
  - Comprehensive output: fund history, trade history, reconciliation

- **ESG Scenario Generation**: Multi-asset class stochastic projection
  - Government zero-coupon bond pricing
  - Credit bond pricing with rating-based spreads
  - Equity total return modeling
  - Cash return modeling
  - Vectorized operations for 100+ trials × 360+ timesteps
  - Parquet file output for efficient storage

- **Strategic Asset Allocation (SAA)**: Hierarchical allocation framework
  - Policy type, year, and fund type dimensions
  - Time-varying glide paths
  - Interpolation for missing periods
  - CSV-based configuration

- **Assumption Management**: Comprehensive assumption provider system
  - Mortality tables (qx by age, sex, smoker status)
  - Lapse rates (by policy year, age)
  - Expense assumptions (acquisition, maintenance, per-policy)
  - SAA schedules
  - Initial fund assets

- **Sample Data Generators**: Scripts for generating test data
  - ESG scenario generator (100 trials × 361 timesteps)
  - Assumption table generator (mortality, lapse, expenses, SAA)
  - Model point generator (synthetic portfolios)

- **Documentation**: Comprehensive technical documentation
  - Dynamic ALM MVP user guide (600 lines)
  - Implementation summary with architecture diagrams
  - SAA implementation guide
  - ESG generator performance optimization notes
  - Next steps and enhancement roadmap

- **Testing**: Unit test suite with 91% pass rate
  - 10/11 tests passing for Dynamic ALM engine
  - Coverage for positive/negative cashflows, rebalancing, transaction costs
  - Reconciliation validation

- **Project Infrastructure**:
  - Professional README with quick start guide
  - MIT License
  - Comprehensive .gitignore for sensitive data and large files
  - Environment configuration template (.env.example)
  - Requirements.txt with pinned dependencies

### Technical Details
- Python 3.10+ support
- NumPy-based vectorized operations for performance
- Pandas DataFrames for I/O and analysis
- Parquet format for large dataset storage
- Type hints throughout codebase
- Docstrings for all public APIs

### Known Limitations (MVP)
- No Shareholder Deficit Account (SDA) logic
- No tax modeling (corporate tax, withholding tax)
- No book value tracking (market value only)
- No duration management or constraints
- No rating constraints or migration modeling
- Sequential processing only (no parallel execution)
- Single currency support (CNY)
- Simplified rebalancing (asset class level only)

### Future Enhancements
See `docs/NEXT_STEPS_DYNAMIC_ALM.md` for detailed roadmap including:
- SDA implementation
- Parallel processing
- Duration management
- Tax modeling
- Book value tracking
- Advanced rebalancing optimization
- Multi-currency support
- Alternative asset classes

---

## [0.2.0] - 2026-01-11

### Added

#### Flexible Assumption Framework
- **FlexibleAssumptionProvider**: Multi-dimensional assumption lookup engine
  - Support for 7+ dimensions: product, gender, age, policy_year, smoker_status, sum_assured_band, premium_band
  - Linear and step interpolation for missing values
  - Constant extrapolation for out-of-bounds values
  - Performance caching with 200x speedup for repeated lookups
  - Metadata-driven table configuration via JSON
  - Generic `get_value()` method works for any table
  - Convenience methods: `get_mortality()`, `get_lapse()`, `get_expense()`, `get_bonus_rate()`

- **Enhanced Assumption Tables**:
  - `mortality_qx_enhanced.csv`: 84 rows with product/gender/age/smoker/policy_year dimensions
  - `lapse_enhanced.csv`: 120 rows with age bands and sum assured bands
  - `expenses_enhanced.csv`: 60 rows with expense types and premium bands
  - `bonus_rates.csv`: 24 rows with fund type differentiation
  - `metadata.json`: Configuration schema for all tables

#### Asset Share Engine with Profit Sharing
- **AssetShareEngine**: Policy-level projection with 70/30 profit sharing
  - Explicit Shareholder Deficit Account (SDA) tracking
  - SDA repayment priority before surplus distribution
  - Lifetime shareholder cap (15% of cumulative premiums)
  - Reversionary bonus calculation with smoothing
  - Terminal bonus pool management
  - Comprehensive cashflow tracking (premiums, benefits, expenses, surplus, bonuses)
  - Portfolio-level aggregation support

- **Data Structures**:
  - `AssetShareConfig`: Profit sharing configuration
  - `PolicyState`: Current state tracking (asset share, SDA, bonuses)
  - `PolicyCashflow`: Detailed cashflow records
  - `AssetShareResult`: Projection results with summary metrics

#### Enhanced Distributed Processing
- **ResourceMonitor**: Dynamic resource tracking and management
  - Real-time RAM/CPU monitoring with 90% cap enforcement
  - Dynamic chunk size calculation based on available resources
  - Resource history tracking (last 1000 snapshots)
  - Wait-for-resources functionality for system stability
  - Memory estimation per policy (heuristic-based)

- **Memory Profiling Utilities**:
  - `@profile_memory` decorator for automatic function profiling
  - `MemoryTracker` context manager for code blocks
  - `MemoryMonitor` for background monitoring with thresholds
  - `MemoryBudget` for memory allocation management
  - Utility functions: `get_memory_usage()`, `estimate_dataframe_memory()`

- **DistributedExecutor**: Fault-tolerant distributed processing
  - Dynamic chunk sizing (auto or manual)
  - Checkpoint/resume functionality for long-running jobs
  - Automatic retry on failure (configurable attempts: 3)
  - Progress reporting and execution summaries
  - Graceful degradation on worker failures
  - Intermediate result saving per chunk
  - Timeout handling per chunk
  - WebSocket-ready for real-time progress updates

#### Testing & Benchmarking
- **End-to-End Integration Tests** (`test_integration_e2e.py`):
  - Complete workflow validation (assumptions → projection → results)
  - Distributed processing integration tests
  - Resource monitoring integration tests
  - Cashflow reconciliation validation
  - Performance baseline tests
  - Scalability tests with varying portfolio sizes

- **Performance Benchmarking Script** (`benchmark_performance.py`):
  - Assumption lookup performance (cached vs uncached)
  - Policy projection throughput
  - Distributed processing scalability
  - Memory usage by portfolio size
  - Resource monitoring overhead
  - Automated report generation (text + JSON)

#### Documentation
- **ENHANCEMENT_PLAN.md** (3,500+ lines): Comprehensive enhancement roadmap
- **ENHANCEMENT_SUMMARY.md** (2,500+ lines): Executive summary with usage examples
- **ENHANCEMENT_PROGRESS.md** (1,500+ lines): Progress tracking and milestones
- **PHASE3_DISTRIBUTED_PROCESSING.md** (2,000+ lines): Complete technical guide
- **PHASE4_UI_ARCHITECTURE.md** (1,500+ lines): UI framework analysis (Dash vs FastAPI+React)

### Changed
- **requirements.txt**: Updated with new dependencies
  - Added `streamlit>=1.28.0`, `plotly>=5.17.0`, `altair>=5.1.0` for UI
  - Uncommented `pytest>=7.4.0`, `pytest-cov>=4.1.0` for testing

### Performance Improvements
- Assumption lookups: 200x faster with caching
- Policy projection: ~500 policies/sec on single core
- Distributed processing: Scales to 100K+ policies
- Resource monitoring overhead: <0.1%
- Checkpoint/resume: Instant resume, no duplicate work

### Technical Details
- **New modules**: 5 (2,750+ lines of production code)
- **Test files**: 3 (1,500+ lines, 78+ tests, all passing)
- **Enhanced tables**: 4 (288 data rows)
- **Documentation**: 8 comprehensive guides (18,000+ lines)

### Breaking Changes
None - All new modules are backward compatible with v0.1.0

### Known Limitations
- Banding logic: String-based bands require exact match (no numeric parsing yet)
- Smoothing: Exponential smoothing not fully implemented (uses simple alpha)
- Stochastic decrements: Uses deterministic random draws (not vectorized)
- Multi-currency: Single currency support only (CNY)
- UI: No web interface yet (Phase 4 deferred)

### Migration Guide
See `docs/MIGRATION_v0.1_to_v0.2.md` for detailed migration instructions.

---

## [Unreleased]

### Planned
- Web-based UI (Dash or FastAPI+React)
- Liability valuation engine integration
- Par fund surplus calculation
- Dividend smoothing mechanisms
- TVOG calculation framework
- Regulatory capital outputs (SCR, C-ROSS)
- GPU acceleration for large portfolios
- Multi-currency support

---

[0.2.0]: https://github.com/wilson37wu/AI_Actuarial_2026_ver/releases/tag/v0.2.0
[0.1.0]: https://github.com/wilson37wu/AI_Actuarial_2026_ver/releases/tag/v0.1.0
