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

## [Unreleased]

### Planned
- Liability valuation engine integration
- Par fund surplus calculation
- Dividend smoothing mechanisms
- TVOG calculation framework
- Regulatory capital outputs (SCR, C-ROSS)
- Performance optimization (Numba JIT compilation)
- Web-based visualization dashboard

---

[0.1.0]: https://github.com/yourusername/tvog-model/releases/tag/v0.1.0
