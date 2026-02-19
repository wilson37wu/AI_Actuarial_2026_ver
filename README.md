# Stochastic ALM & TVOG Model for Participating Insurance

A Python-based framework for **Stochastic Asset-Liability Management (ALM)** and **Time Value of Guarantees (TVOG)** modeling for participating (par) insurance business. This research-grade implementation provides ESG-driven stochastic projection, dynamic asset-liability linkage, strategic asset allocation with trading rules, dividend smoothing, and TVOG calculation.

## 🎯 Overview

This project implements a comprehensive actuarial modeling framework for participating insurance products, integrating:
- **Economic Scenario Generation (ESG)** with multi-asset class projections
- **Dynamic Asset-Liability Management** with deterministic trading rules
- **Strategic Asset Allocation (SAA)** with rebalancing and transaction costs
- **Liability valuation** with guaranteed and non-guaranteed benefits
- **Par fund projection** with surplus distribution and dividend smoothing
- **TVOG calculation** for financial option and guarantee valuation

## ✨ Key Features

### 📊 ESG & Scenario Management
- Multi-asset class scenario generation (government bonds, credit, equity, cash)
- Zero-coupon bond pricing with term structure modeling
- Hybrid monthly/annual time grids for efficient long-horizon projection
- Support for 100+ stochastic trials with vectorized operations

### 💼 Dynamic Asset-Liability Management
- **Holdings-based portfolio tracking** (government bonds, credit bonds, equity, cash)
- **ESG-driven returns** applied to all asset classes
- **Deterministic buy/sell rules** with priority-based liquidation
- **Transaction cost modeling** (2-60 bps by asset type and rating)
- **Cash buffer management** with automatic funding
- **Rebalancing to SAA targets** (annual, each-step, or none)

### 📈 Strategic Asset Allocation
- Hierarchical SAA lookup by policy type, year, and fund type
- Time-varying glide paths (growth → balanced → conservative)
- Asset class weights: Government, Credit, Equity, Cash
- Interpolation for missing periods

### 🏦 Liability Modeling
- Deterministic liability cashflow projection
- Guaranteed benefits (death, maturity, surrender)
- Non-guaranteed benefits (reversionary bonus, terminal bonus)
- Premium and expense modeling
- Asset share calculation

### 📉 Par Fund & Dividend Smoothing
- Stochastic surplus calculation
- Smoothing mechanisms (exponential smoothing, target bonus)
- Profit sharing rules (70/30 policyholder/shareholder split)
- Bonus rate determination (reversionary and terminal)

### 🎲 TVOG Calculation
- Present value of guarantees under stochastic scenarios
- Option value quantification
- Risk-neutral vs. real-world measure comparison

## 🚀 Quick Start

### Prerequisites
- **Python 3.10+** (tested on 3.10, 3.11, 3.12)
- **pip** or **poetry** for dependency management

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/tvog-model.git
cd tvog-model
```

2. **Create virtual environment**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Set up environment (optional)**
```bash
cp .env.example .env
# Edit .env to customize data paths if needed
```

### Running Examples

#### 1. Generate ESG Scenarios
```bash
python scripts/generate_sample_esg.py
# Output: data/esg/sample_scenarios.parquet (100 trials × 361 timesteps)
```

#### 2. Generate Sample Assumptions
```bash
python scripts/generate_sample_assumptions.py
# Output: data/assumptions/*.csv (mortality, lapse, expenses, SAA, etc.)
```

#### 3. Run Dynamic ALM Projection
```bash
python scripts/example_dynamic_alm.py
# Output: output/dynamic_alm/*.csv (fund history, trades, reconciliation)
```

### Basic Usage

```python
from par_model_v2.valuation.dynamic_alm import DynamicALMEngine, ALMConfig, Holdings
import pandas as pd

# 1. Configure ALM engine
config = ALMConfig(
    rebalance_frequency='annual',
    target_cash_buffer=0.02,
    min_cash_buffer=0.01,
)
engine = DynamicALMEngine(config)

# 2. Set up initial Par fund assets
initial_assets = Holdings()
initial_assets.govt[10] = 5000.0      # 10Y government bonds
initial_assets.credit[('A', 5)] = 3000.0  # A-rated 5Y credit
initial_assets.equity = 4000.0
initial_assets.cash = 1000.0

# 3. Define SAA schedule
def saa_schedule(timestep):
    return {
        'Govt': 0.30,
        'Credit': 0.30,
        'Equity': 0.30,
        'Cash': 0.10,
    }

# 4. Load data
liability_cf_df = pd.read_csv('data/liability_cashflows.csv')
esg_df = pd.read_parquet('data/esg/sample_scenarios.parquet')

# 5. Project trial
result = engine.project_trial(
    trial=1,
    liability_cf_df=liability_cf_df,
    esg_df=esg_df,
    saa_schedule=saa_schedule,
    initial_assets=initial_assets,
)

# 6. Get results
fund_df, trade_df, recon_df = result.to_dataframes()
print(f"Final MV: {fund_df.iloc[-1]['MV_total']:,.0f}")
```

## 📁 Project Structure

```
TVOG_model/
├── par_model_v2/                    # Core package
│   ├── __init__.py
│   ├── assets/                      # Asset modeling & portfolio management
│   │   ├── fund_portfolio.py        # FundPortfolio class with trading logic
│   │   ├── asset_classes.py         # Asset class definitions
│   │   └── __init__.py
│   ├── assumptions/                 # Assumption providers
│   │   ├── provider.py              # AssumptionProvider (mortality, lapse, etc.)
│   │   ├── saa_provider.py          # SAAProvider for strategic allocation
│   │   └── __init__.py
│   ├── esg/                         # Economic scenario generation
│   │   ├── scenario_adapter.py      # ESGAdapter for reading scenarios
│   │   └── __init__.py
│   ├── grid/                        # Time grid utilities
│   │   ├── time_grid.py             # TimeGrid (monthly/annual hybrid)
│   │   └── __init__.py
│   ├── liabilities/                 # Liability modeling
│   │   ├── liability_engine.py      # Deterministic liability projection
│   │   └── __init__.py
│   ├── model_points/                # Model point generation
│   │   ├── generator.py             # Synthetic portfolio generation
│   │   └── __init__.py
│   └── valuation/                   # Valuation & ALM engines
│       ├── dynamic_alm.py           # DynamicALMEngine (MVP)
│       └── __init__.py
│
├── scripts/                         # Executable scripts
│   ├── generate_sample_esg.py       # Generate ESG scenarios
│   ├── generate_sample_assumptions.py  # Generate assumption tables
│   ├── example_dynamic_alm.py       # Dynamic ALM example
│   └── run_mp_generator.py          # Model point generator CLI
│
├── tests/                           # Unit tests
│   ├── test_dynamic_alm.py          # Dynamic ALM tests (10/11 passing)
│   └── __init__.py
│
├── docs/                            # Documentation
│   ├── DYNAMIC_ALM_MVP.md           # Dynamic ALM user guide
│   ├── DYNAMIC_ALM_IMPLEMENTATION_SUMMARY.md
│   ├── SAA_IMPLEMENTATION_SUMMARY.md
│   ├── ESG_GENERATOR_REFACTORING.md
│   └── NEXT_STEPS_DYNAMIC_ALM.md
│
├── data/                            # Data directories (gitignored)
│   ├── assumptions/                 # Assumption CSV files
│   ├── esg/                         # ESG scenario files (*.parquet)
│   ├── inforce/                     # Model point files
│   └── liability_results/           # Liability projection outputs
│
├── output/                          # Runtime outputs (gitignored)
│   └── dynamic_alm/                 # ALM projection results
│
├── .env.example                     # Environment template
├── .gitignore                       # Git ignore rules
├── README.md                        # This file
├── requirements.txt                 # Python dependencies
└── LICENSE                          # MIT License
```

## 🏗️ Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     ESG Scenario Generation                      │
│  (Government ZCB, Credit ZCB, Equity Returns, Cash Returns)     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Liability Cashflow Projection                   │
│     (Premiums, Benefits, Expenses → Net Cashflow)               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Dynamic ALM Engine                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  For each timestep t:                                     │  │
│  │  1. Apply ESG returns to holdings                         │  │
│  │  2. Apply liability net cashflow to cash                  │  │
│  │  3. Execute funding rule (sell if cash < minimum)         │  │
│  │  4. Rebalance to SAA targets (if enabled)                 │  │
│  │  5. Record fund state & trades                            │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Output & Analysis                             │
│  • Fund History (MV by asset class, returns, TC)                │
│  • Trade History (buy/sell, amounts, reasons)                   │
│  • Reconciliation (MV rollforward checks)                       │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

1. **ESGAdapter**: Reads and validates ESG scenario files
2. **DynamicALMEngine**: Core projection engine with recursion logic
3. **Holdings**: Asset holdings representation (govt, credit, equity, cash)
4. **SAAProvider**: Strategic asset allocation lookup with interpolation
5. **FundPortfolio**: Portfolio management with trading and rebalancing

## 📦 Dependencies

### Core
- **numpy** (≥1.24.0): Numerical computing and array operations
- **pandas** (≥2.0.0): Data manipulation and analysis
- **pyarrow** (≥12.0.0): Parquet file I/O for large datasets

### Utilities
- **python-dotenv** (≥1.0.0): Environment variable management
- **pytest** (≥7.0.0): Unit testing framework

### Optional
- **matplotlib**: Visualization (for analysis scripts)
- **jupyter**: Interactive notebooks (for exploration)

## 🧪 Testing

Run unit tests:
```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_dynamic_alm.py -v

# With coverage
pytest tests/ --cov=par_model_v2 --cov-report=html
```

Current test coverage: **10/11 tests passing (91%)**

## 📚 Documentation

Comprehensive documentation available in `docs/`:
- **DYNAMIC_ALM_MVP.md**: User guide for Dynamic ALM Engine
- **SAA_IMPLEMENTATION_SUMMARY.md**: Strategic asset allocation design
- **ESG_GENERATOR_REFACTORING.md**: ESG performance optimization
- **NEXT_STEPS_DYNAMIC_ALM.md**: Future enhancement roadmap

## ⚠️ Disclaimer

**This is a research-grade implementation for educational and actuarial research purposes only.**

- Not production-validated or audited
- No warranty or guarantee of accuracy
- Not intended for regulatory reporting or pricing actual insurance products
- Users should validate results independently
- Consult qualified actuaries for production use

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## 📧 Contact

For questions or collaboration inquiries, please open an issue on GitHub.

## 🙏 Acknowledgments

This project implements actuarial modeling concepts commonly used in the insurance industry for participating product valuation and asset-liability management.

## 🧾 Assumption Governance

- External assumption sources and change history are tracked in `assumption.md`.
- For the stochastic participating model, the HK mortality source reference is maintained there, while runtime still expects a user-provided CSV (`attained_age`, `male`, `female`).
