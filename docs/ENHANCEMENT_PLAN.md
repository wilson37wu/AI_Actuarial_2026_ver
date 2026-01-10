# ALM/TVOG Model Enhancement Plan

## Overview

This document outlines the comprehensive enhancement plan to extend the ALM/TVOG model with:
1. Dynamic asset-liability linkage
2. Flexible CSV-driven assumption framework
3. Asset share approach with 70/30 profit sharing
4. Enhanced distributed processing
5. User-friendly Streamlit interface

---

## Current Architecture Review

### Existing Modules

```
par_model_v2/
├── assets/
│   ├── asset_cashflows.py          # Asset CF generation
│   ├── asset_mix.py                # Asset allocation
│   ├── asset_share_projection.py  # Asset share logic
│   ├── fund_portfolio.py           # Portfolio management
│   └── par_fund_stochastic.py     # Stochastic par fund
├── assumptions/
│   ├── provider.py                 # Assumption loader (basic)
│   ├── saa_provider.py            # SAA lookup
│   └── banding.py                 # Banding utilities
├── esg/
│   ├── scenario_adapter.py        # ESG reader
│   └── esg_scenario_provider.py   # ESG provider
├── grid/
│   └── grid_manager.py            # Time grid utilities
├── liabilities/
│   └── deterministic_liability.py # Deterministic liability engine
├── model_points/
│   ├── mp_generator.py            # Model point generator
│   └── model_point_grouping.py    # MP grouping
└── valuation/
    ├── dynamic_alm.py             # Dynamic ALM MVP
    ├── valuation_batch_executor.py # Batch processing
    └── valuation_timeseries_builder.py # Time series builder
```

### Integration Points

1. **Liability → Asset**: Liability cashflows feed into ALM engine
2. **ESG → Both**: ESG scenarios drive both liability and asset projections
3. **Assumptions → Both**: Shared assumption framework
4. **ALM Engine**: Central integration point for dynamic projection

---

## Enhancement 1: Flexible Assumption Framework

### Current State
- Basic AssumptionProvider loads CSV files
- Limited dimensions (age, policy_year)
- Hard-coded table names

### Target State
- Multi-dimensional lookup: product, policy_year, gender, age, underwriting_class, sum_assured_band, premium_band
- Flexible table schema with metadata
- Interpolation and extrapolation support
- Caching for performance

### Implementation

#### New Module: `par_model_v2/assumptions/flexible_provider.py`

```python
class FlexibleAssumptionProvider:
    """
    Multi-dimensional assumption provider with flexible schema.

    Supports:
    - Product-specific assumptions
    - Multi-dimensional keys (gender, age, policy_year, etc.)
    - Sum assured and premium banding
    - Interpolation for missing values
    - Metadata-driven table loading
    """

    def __init__(self, assumption_dir: str, metadata_path: str):
        """Load assumptions based on metadata configuration."""

    def get_mortality(self, product, gender, age, smoker_status, policy_year):
        """Lookup mortality rate with interpolation."""

    def get_lapse(self, product, policy_year, age, sum_assured_band):
        """Lookup lapse rate with banding."""

    def get_expense(self, product, policy_year, premium_band):
        """Lookup expense loading."""

    def get_bonus_rate(self, product, policy_year, fund_type):
        """Lookup reversionary bonus rate."""
```

#### Assumption Metadata Schema: `data/assumptions/metadata.json`

```json
{
  "mortality_qx": {
    "file": "mortality_qx.csv",
    "dimensions": ["product", "gender", "age", "smoker_status", "policy_year"],
    "value_column": "qx",
    "interpolation": "linear",
    "extrapolation": "constant"
  },
  "lapse_rates": {
    "file": "lapse.csv",
    "dimensions": ["product", "policy_year", "age_band", "sum_assured_band"],
    "value_column": "lapse_rate",
    "interpolation": "step",
    "extrapolation": "constant"
  }
}
```

#### Enhanced CSV Tables

**mortality_qx.csv**
```csv
product,gender,age,smoker_status,policy_year,qx
WL,M,25,N,1,0.00050
WL,M,25,N,2,0.00052
WL,F,25,N,1,0.00040
Pension,M,30,N,1,0.00055
```

**lapse.csv**
```csv
product,policy_year,age_band,sum_assured_band,lapse_rate
WL,1,20-30,0-100000,0.15
WL,1,20-30,100000-500000,0.12
WL,2,20-30,0-100000,0.10
Pension,1,30-40,0-100000,0.08
```

---

## Enhancement 2: Asset Share Approach with Profit Sharing

### Current State
- DynamicALMEngine handles fund-level projection
- No policy-level asset share tracking
- No profit sharing mechanism

### Target State
- Policy-level asset share projection
- 70/30 profit sharing (policyholder/shareholder)
- Lifetime shareholder cap tracking
- Shareholder deficit account (SDA) for negative returns
- Repayment priority before surplus sharing

### Implementation

#### New Module: `par_model_v2/valuation/asset_share_engine.py`

```python
@dataclass
class AssetShareConfig:
    """Configuration for asset share projection."""
    policyholder_share: float = 0.70
    shareholder_share: float = 0.30
    lifetime_shareholder_cap: float = 0.15  # 15% of premiums
    sda_repayment_priority: bool = True
    smoothing_method: str = "exponential"  # or "target_bonus"
    smoothing_alpha: float = 0.3

@dataclass
class PolicyAssetShare:
    """Asset share state for a single policy."""
    policy_id: str
    asset_share: float
    cumulative_premiums: float
    cumulative_shareholder_profit: float
    shareholder_deficit: float
    guaranteed_benefit: float
    reversionary_bonus: float
    terminal_bonus: float

class AssetShareEngine:
    """
    Policy-level asset share projection with profit sharing.

    Recursion per timestep:
    1. Apply investment return to asset share
    2. Add premium, subtract expenses
    3. Calculate surplus = asset_share - guaranteed_benefit
    4. If surplus > 0:
       - Repay shareholder deficit first
       - Split remaining 70/30 (policyholder/shareholder)
       - Check lifetime shareholder cap
    5. If surplus < 0:
       - Add to shareholder deficit
    6. Update reversionary bonus for survivors
    7. Pay death/surrender benefits
    """

    def project_policy(
        self,
        policy: Policy,
        esg_trial: pd.DataFrame,
        assumptions: FlexibleAssumptionProvider,
        config: AssetShareConfig
    ) -> PolicyAssetShareResult:
        """Project single policy asset share."""
```

#### Integration with Dynamic ALM

Enhance `DynamicALMEngine` to:
1. Accept policy-level data
2. Aggregate policy asset shares to fund level
3. Apply fund-level trading and rebalancing
4. Distribute returns back to policy level

---

## Enhancement 3: Enhanced Distributed Processing

### Current State
- `run_liability_distributed.py` with fixed chunk size
- Basic error handling
- No dynamic resource management

### Target State
- Dynamic chunk sizing based on available RAM/CPU
- 90% resource cap to prevent system overload
- Resume failed chunks from checkpoint
- Graceful degradation on worker failures
- Progress monitoring and logging

### Implementation

#### Enhanced Module: `par_model_v2/valuation/distributed_executor.py`

```python
class DistributedConfig:
    """Configuration for distributed processing."""
    max_ram_usage_pct: float = 0.90
    max_cpu_usage_pct: float = 0.90
    chunk_size_auto: bool = True
    checkpoint_frequency: int = 100  # policies
    retry_failed_chunks: int = 3
    graceful_degradation: bool = True

class ResourceMonitor:
    """Monitor system resources and adjust chunk size."""

    def get_available_ram(self) -> float:
        """Get available RAM in GB."""

    def get_cpu_usage(self) -> float:
        """Get current CPU usage percentage."""

    def calculate_optimal_chunk_size(
        self,
        total_policies: int,
        avg_policy_memory: float
    ) -> int:
        """Calculate optimal chunk size based on resources."""

class DistributedExecutor:
    """
    Enhanced distributed executor with fault tolerance.

    Features:
    - Dynamic chunk sizing
    - Checkpoint/resume
    - Worker health monitoring
    - Automatic retry on failure
    - Progress reporting
    """

    def execute_distributed(
        self,
        policies: pd.DataFrame,
        config: DistributedConfig,
        checkpoint_dir: str
    ) -> pd.DataFrame:
        """Execute distributed valuation with fault tolerance."""
```

---

## Enhancement 4: Memory Optimization

### Current Issues
- DataFrame fragmentation in ESG generator
- Repeated `df.loc` assignments
- Inefficient column-wise operations

### Solutions Implemented
- ✅ ESG generator refactored to use `pd.concat`
- ✅ Vectorized operations for ZCB pricing
- ✅ Pre-allocated NumPy arrays

### Additional Optimizations

1. **Chunked Processing**: Process policies in chunks to limit memory
2. **Lazy Loading**: Load ESG scenarios on-demand per trial
3. **Result Streaming**: Write results incrementally to disk
4. **Memory Profiling**: Add decorators to track memory usage

```python
from functools import wraps
import tracemalloc

def profile_memory(func):
    """Decorator to profile memory usage."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        tracemalloc.start()
        result = func(*args, **kwargs)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"{func.__name__}: Peak memory: {peak / 1024**2:.2f} MB")
        return result
    return wrapper
```

---

## Enhancement 5: Streamlit User Interface

### UI Structure

```
ui/
├── __init__.py
├── app.py                    # Main Streamlit app
├── components/
│   ├── __init__.py
│   ├── sidebar.py           # Input sidebar
│   ├── file_upload.py       # File upload widgets
│   ├── parameter_inputs.py  # Parameter forms
│   └── results_display.py   # Results visualization
├── pages/
│   ├── 1_Model_Points.py    # MP generation page
│   ├── 2_Deterministic.py   # Deterministic valuation
│   ├── 3_Stochastic_ALM.py  # Stochastic ALM
│   └── 4_Results.py         # Results viewer
└── utils/
    ├── __init__.py
    ├── session_state.py     # Session state management
    └── plotting.py          # Plotting utilities
```

### Main App Features

#### Sidebar Inputs
- File uploads (assumptions, ESG, initial portfolio)
- Product mix selection
- Projection parameters (horizon, scenarios)
- SAA schedule selection
- Rebalancing options

#### Pages

**1. Model Point Generation**
- Configure portfolio (n_policies, product mix)
- Generate synthetic model points
- Preview and download

**2. Deterministic Valuation**
- Run deterministic projection
- View cashflow waterfall
- Export results

**3. Stochastic ALM**
- Configure ALM parameters
- Run stochastic projection
- Monitor progress
- View trial-level results

**4. Results Viewer**
- Interactive tables (filterable, sortable)
- Charts:
  - Asset share evolution
  - Cashflow waterfall
  - Surplus distribution
  - TVOG distribution
  - Shareholder deficit over time
- Summary metrics (BEL, TVOG, dividends)
- Download results (CSV/Parquet)

### Implementation

#### Main App: `ui/app.py`

```python
import streamlit as st
from components.sidebar import render_sidebar
from components.results_display import display_results

st.set_page_config(
    page_title="ALM/TVOG Model",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

def main():
    st.title("🏦 Stochastic ALM & TVOG Model")
    st.markdown("**Participating Insurance Valuation Framework**")

    # Render sidebar
    config = render_sidebar()

    # Main content
    tab1, tab2, tab3, tab4 = st.tabs([
        "📋 Model Points",
        "📈 Deterministic",
        "🎲 Stochastic ALM",
        "📊 Results"
    ])

    with tab1:
        render_model_points_page(config)

    with tab2:
        render_deterministic_page(config)

    with tab3:
        render_stochastic_page(config)

    with tab4:
        render_results_page(config)

if __name__ == "__main__":
    main()
```

---

## Enhancement 6: Detailed Per-Trial Output

### Output Structure

```python
@dataclass
class TrialResult:
    """Comprehensive trial-level results."""
    trial_id: int

    # Policy-level GPV
    policy_gpv: pd.DataFrame  # [policy_id, timestep, gpv, pv_premiums, pv_benefits, ...]

    # Monthly cashflows
    cashflows: pd.DataFrame  # [policy_id, timestep, premium, death_benefit, surrender, ...]

    # Asset evolution
    asset_history: pd.DataFrame  # [timestep, govt, credit, equity, cash, total_mv]

    # Profit sharing
    surplus_history: pd.DataFrame  # [timestep, surplus, ph_share, sh_share, sda]

    # Summary metrics
    metrics: Dict[str, float]  # {BEL, TVOG, total_dividends, final_sda, ...}
```

### Metrics to Calculate

1. **Best Estimate Liability (BEL)**: Mean PV of liabilities across trials
2. **TVOG**: Difference between stochastic and deterministic PV
3. **Dividends**: Total policyholder dividends (reversionary + terminal)
4. **Shareholder Deficit**: Final SDA balance
5. **Fund Solvency**: Probability of fund remaining solvent
6. **Coverage Ratio**: Asset/Liability ratio over time

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1)
- [ ] Implement FlexibleAssumptionProvider
- [ ] Create enhanced assumption CSV templates
- [ ] Add metadata.json configuration
- [ ] Unit tests for assumption lookups

### Phase 2: Asset Share Engine (Week 2)
- [ ] Implement AssetShareEngine
- [ ] Add 70/30 profit sharing logic
- [ ] Implement SDA tracking
- [ ] Integrate with DynamicALMEngine
- [ ] Unit tests for profit sharing

### Phase 3: Enhanced Processing (Week 3)
- [ ] Implement ResourceMonitor
- [ ] Enhance DistributedExecutor
- [ ] Add checkpoint/resume logic
- [ ] Memory profiling decorators
- [ ] Integration tests

### Phase 4: UI Development (Week 4)
- [ ] Build Streamlit app structure
- [ ] Implement file upload components
- [ ] Create parameter input forms
- [ ] Build results visualization
- [ ] Add download functionality

### Phase 5: Integration & Testing (Week 5)
- [ ] End-to-end integration testing
- [ ] Performance optimization
- [ ] Documentation updates
- [ ] User acceptance testing

### Phase 6: Deployment (Week 6)
- [ ] Git commits with clear messages
- [ ] Update README with new features
- [ ] Create migration guide
- [ ] Deploy to production

---

## Directory Structure (Enhanced)

```
TVOG_model/
├── par_model_v2/
│   ├── assumptions/
│   │   ├── flexible_provider.py      # NEW: Multi-dimensional provider
│   │   ├── metadata_loader.py        # NEW: Metadata handler
│   │   └── interpolation.py          # NEW: Interpolation utilities
│   ├── valuation/
│   │   ├── asset_share_engine.py     # NEW: Asset share with profit sharing
│   │   ├── distributed_executor.py   # ENHANCED: Fault-tolerant executor
│   │   └── resource_monitor.py       # NEW: Resource monitoring
│   └── utils/
│       ├── memory_profiler.py        # NEW: Memory profiling
│       └── performance.py            # NEW: Performance utilities
├── ui/                                # NEW: Streamlit interface
│   ├── app.py
│   ├── components/
│   ├── pages/
│   └── utils/
├── data/
│   └── assumptions/
│       ├── metadata.json             # NEW: Assumption metadata
│       ├── mortality_qx_enhanced.csv # ENHANCED: Multi-dimensional
│       ├── lapse_enhanced.csv        # ENHANCED: With banding
│       └── bonus_rates.csv           # NEW: Reversionary bonus rates
├── tests/
│   ├── test_flexible_assumptions.py  # NEW
│   ├── test_asset_share_engine.py    # NEW
│   ├── test_distributed_executor.py  # NEW
│   └── test_ui_components.py         # NEW
└── docs/
    ├── ENHANCEMENT_PLAN.md           # This document
    ├── ASSET_SHARE_GUIDE.md          # NEW: Asset share methodology
    ├── UI_USER_GUIDE.md              # NEW: UI usage guide
    └── ASSUMPTION_SCHEMA.md          # NEW: Assumption table specs
```

---

## Git Commit Strategy

### Commit 1: Flexible Assumption Framework
```bash
git checkout -b feature/flexible-assumptions
# Implement FlexibleAssumptionProvider, metadata loader, tests
git add par_model_v2/assumptions/ data/assumptions/metadata.json tests/test_flexible_assumptions.py
git commit -m "feat: add flexible multi-dimensional assumption framework

- Implement FlexibleAssumptionProvider with multi-dimensional lookup
- Add metadata.json for table configuration
- Support interpolation and banding
- Add comprehensive unit tests
- Update assumption CSV templates with enhanced schema"
```

### Commit 2: Asset Share Engine
```bash
git checkout -b feature/asset-share-engine
# Implement AssetShareEngine with profit sharing
git add par_model_v2/valuation/asset_share_engine.py tests/test_asset_share_engine.py docs/ASSET_SHARE_GUIDE.md
git commit -m "feat: implement asset share engine with 70/30 profit sharing

- Add AssetShareEngine for policy-level projection
- Implement 70/30 profit sharing rule
- Add shareholder deficit account (SDA) tracking
- Support lifetime shareholder cap
- Add detailed per-trial output
- Comprehensive documentation and tests"
```

### Commit 3: Enhanced Distributed Processing
```bash
git checkout -b feature/enhanced-distributed
# Enhance distributed executor
git add par_model_v2/valuation/distributed_executor.py par_model_v2/valuation/resource_monitor.py tests/test_distributed_executor.py
git commit -m "feat: enhance distributed processing with fault tolerance

- Add dynamic chunk sizing based on RAM/CPU
- Implement checkpoint/resume for failed chunks
- Add ResourceMonitor for system resource tracking
- Support graceful degradation on worker failures
- Add progress monitoring and logging
- 90% resource cap to prevent overload"
```

### Commit 4: Streamlit UI
```bash
git checkout -b feature/streamlit-ui
# Build Streamlit interface
git add ui/ requirements.txt docs/UI_USER_GUIDE.md
git commit -m "feat: add Streamlit user interface

- Build multi-page Streamlit app
- Add file upload for assumptions and portfolios
- Implement parameter input forms
- Create interactive results visualization
- Add download functionality for outputs
- Include user guide documentation"
```

### Commit 5: Integration & Documentation
```bash
git checkout -b feature/integration
# Final integration and docs
git add docs/ README.md tests/
git commit -m "docs: update documentation for enhanced features

- Update README with new features
- Add comprehensive user guides
- Expand API documentation
- Add migration guide from v0.1.0
- Update CHANGELOG for v0.2.0"
```

---

## Testing Strategy

### Unit Tests
- Assumption lookup with various dimensions
- Interpolation and extrapolation
- Asset share recursion
- Profit sharing calculations
- SDA tracking
- Resource monitoring
- Checkpoint/resume logic

### Integration Tests
- End-to-end ALM projection
- Distributed processing with multiple workers
- UI component interactions
- File upload and validation

### Performance Tests
- Memory usage profiling
- Execution time benchmarks
- Scalability tests (1K, 10K, 100K policies)

### User Acceptance Tests
- UI usability testing
- Results validation against Excel models
- Documentation completeness

---

## Success Criteria

1. ✅ Flexible assumption framework supports all required dimensions
2. ✅ Asset share engine correctly implements 70/30 profit sharing
3. ✅ SDA tracking works correctly for negative returns
4. ✅ Distributed processing scales to 100K+ policies
5. ✅ Memory usage stays under 90% cap
6. ✅ UI is intuitive and responsive
7. ✅ All tests pass with >90% coverage
8. ✅ Documentation is comprehensive and clear
9. ✅ Performance meets benchmarks (<1 min for 10K policies)
10. ✅ Results reconcile with existing Excel models

---

## Next Steps

1. Review and approve this enhancement plan
2. Set up development branch structure
3. Begin Phase 1 implementation
4. Schedule weekly progress reviews
5. Prepare test data and validation cases

---

**Document Version:** 1.0
**Date:** 2026-01-10
**Status:** Draft - Awaiting Approval
