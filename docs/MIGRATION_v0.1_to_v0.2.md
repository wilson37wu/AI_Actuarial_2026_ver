# Migration Guide: v0.1.0 → v0.2.0

**Date:** 2026-01-11
**Target Audience:** Developers upgrading from v0.1.0 to v0.2.0

---

## 🎯 Overview

Version 0.2.0 introduces significant enhancements while maintaining **full backward compatibility** with v0.1.0. All existing code will continue to work without modification.

**New capabilities:**
- Multi-dimensional assumption management
- Policy-level asset share projection with profit sharing
- Enhanced distributed processing with fault tolerance
- Comprehensive testing and benchmarking tools

---

## 📦 Installation

### Step 1: Update Dependencies

```bash
pip install -r requirements.txt
```

**New dependencies added:**
- `streamlit>=1.28.0` (for future UI)
- `plotly>=5.17.0` (visualization)
- `altair>=5.1.0` (visualization)
- `pytest>=7.4.0` (testing)
- `pytest-cov>=4.1.0` (coverage)

### Step 2: Verify Installation

```bash
python -c "from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider; print('✓ v0.2.0 installed')"
```

---

## 🔄 Migration Paths

### Path 1: Continue Using v0.1.0 Features (No Changes Required)

If you're satisfied with v0.1.0 functionality, **no code changes are needed**. All existing modules continue to work:

```python
# v0.1.0 code - still works in v0.2.0
from par_model_v2.assumptions.provider import AssumptionProvider
from par_model_v2.valuation.dynamic_alm import DynamicALMEngine

provider = AssumptionProvider('data/assumptions')
engine = DynamicALMEngine(config)
# ... existing code continues to work
```

### Path 2: Adopt New Features Incrementally

Gradually adopt v0.2.0 features as needed:

#### 2A: Upgrade to Flexible Assumptions

**Before (v0.1.0):**
```python
from par_model_v2.assumptions.provider import AssumptionProvider

provider = AssumptionProvider('data/assumptions')
qx = provider.get_mortality_qx(age=35, gender='M', policy_year=1)
```

**After (v0.2.0):**
```python
from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider

provider = FlexibleAssumptionProvider('data/assumptions')
qx = provider.get_mortality(
    product='WL',
    gender='M',
    age=35,
    smoker_status='N',
    policy_year=1
)
```

**Benefits:**
- Multi-dimensional lookup (product, smoker status, etc.)
- Interpolation for missing values
- 200x faster with caching

#### 2B: Add Asset Share Projection

**New in v0.2.0:**
```python
from par_model_v2.valuation.asset_share_engine import (
    AssetShareEngine,
    AssetShareConfig
)

# Configure profit sharing
config = AssetShareConfig(
    policyholder_share=0.70,
    shareholder_share=0.30,
    lifetime_shareholder_cap=0.15
)

engine = AssetShareEngine(config)

# Project policy
result = engine.project_policy(
    policy=policy_data,
    investment_returns=returns_series,
    mortality_rates=qx_series,
    lapse_rates=lapse_series,
    expenses=expense_series,
    n_timesteps=360
)

# Access results
print(f"Shareholder profit: {result.summary_metrics['total_shareholder_profit']}")
print(f"SDA balance: {result.summary_metrics['final_shareholder_deficit']}")
```

#### 2C: Enable Distributed Processing

**New in v0.2.0:**
```python
from par_model_v2.valuation.distributed_executor import (
    DistributedExecutor,
    DistributedConfig
)

# Configure executor
config = DistributedConfig(
    chunk_size_auto=True,
    checkpoint_dir='checkpoints',
    max_workers=4
)

executor = DistributedExecutor(config)

# Execute with fault tolerance
result = executor.execute(
    data=policies_df,
    process_func=process_policy_chunk,
    avg_item_memory_mb=10.0,
    resume=True  # Resume from checkpoint if exists
)
```

---

## 📊 Assumption Table Migration

### Old Format (v0.1.0)

```csv
# mortality_qx.csv
age,gender,qx
25,M,0.0005
30,M,0.0006
```

### New Format (v0.2.0)

```csv
# mortality_qx_enhanced.csv
product,gender,age,smoker_status,policy_year,qx
WL,M,25,N,1,0.0005
WL,M,30,N,1,0.0006
WL,M,25,Y,1,0.0008
Pension,M,25,N,1,0.0004
```

### Migration Steps

1. **Add metadata.json:**

```json
{
  "mortality_qx": {
    "file": "mortality_qx_enhanced.csv",
    "dimensions": ["product", "gender", "age", "smoker_status", "policy_year"],
    "value_column": "qx",
    "interpolation": "linear",
    "extrapolation": "constant"
  }
}
```

2. **Expand existing tables:**

```python
# Script to expand v0.1.0 tables to v0.2.0 format
import pandas as pd

# Load old table
old_df = pd.read_csv('data/assumptions/mortality_qx.csv')

# Expand with new dimensions
new_rows = []
for _, row in old_df.iterrows():
    for product in ['WL', 'Pension']:
        for smoker in ['Y', 'N']:
            for py in [1, 2, 3, 5, 10]:
                new_rows.append({
                    'product': product,
                    'gender': row['gender'],
                    'age': row['age'],
                    'smoker_status': smoker,
                    'policy_year': py,
                    'qx': row['qx'] * (1.5 if smoker == 'Y' else 1.0)
                })

new_df = pd.DataFrame(new_rows)
new_df.to_csv('data/assumptions/mortality_qx_enhanced.csv', index=False)
```

3. **Update code to use new provider:**

```python
# Old
provider = AssumptionProvider('data/assumptions')

# New
provider = FlexibleAssumptionProvider('data/assumptions')
```

---

## 🔧 Code Examples

### Example 1: Complete Workflow with v0.2.0 Features

```python
from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider
from par_model_v2.valuation.asset_share_engine import AssetShareEngine, AssetShareConfig
from par_model_v2.valuation.distributed_executor import DistributedExecutor, DistributedConfig
import pandas as pd

# 1. Load assumptions
provider = FlexibleAssumptionProvider('data/assumptions')

# 2. Configure engines
asset_config = AssetShareConfig(
    policyholder_share=0.70,
    shareholder_share=0.30,
    lifetime_shareholder_cap=0.15
)
asset_engine = AssetShareEngine(asset_config)

dist_config = DistributedConfig(
    chunk_size_auto=True,
    checkpoint_dir='checkpoints'
)
dist_executor = DistributedExecutor(dist_config)

# 3. Define processing function
def process_policy_chunk(chunk_df):
    results = []
    for _, policy in chunk_df.iterrows():
        # Get assumptions
        mortality_rates = pd.Series([
            provider.get_mortality(
                policy['product'],
                policy['gender'],
                policy['age'] + t//12,
                policy['smoker_status'],
                t//12 + 1
            ) / 12
            for t in range(360)
        ])

        # Project policy
        result = asset_engine.project_policy(
            policy=policy,
            investment_returns=returns[policy['trial']],
            mortality_rates=mortality_rates,
            lapse_rates=get_lapse_rates(policy),
            expenses=get_expenses(policy),
            n_timesteps=360
        )
        results.append(result)
    return results

# 4. Execute distributed
result = dist_executor.execute(
    data=policies_df,
    process_func=process_policy_chunk,
    avg_item_memory_mb=5.0,
    resume=True
)

print(f"Completed: {result.chunks_completed}")
print(f"Duration: {result.total_duration:.2f}s")
```

### Example 2: Resource Monitoring

```python
from par_model_v2.utils.resource_monitor import ResourceMonitor
from par_model_v2.utils.memory_profiler import MemoryTracker

# Monitor resources
monitor = ResourceMonitor(max_ram_pct=0.90)

# Check before processing
snapshot = monitor.get_snapshot()
print(f"RAM: {snapshot.ram_percent:.1f}%, CPU: {snapshot.cpu_percent:.1f}%")

# Calculate optimal chunk size
chunk_size = monitor.calculate_optimal_chunk_size(
    total_items=100000,
    avg_item_memory_mb=10
)
print(f"Optimal chunk size: {chunk_size:,}")

# Profile memory usage
with MemoryTracker("Processing") as tracker:
    process_large_dataset()

print(f"Peak memory: {tracker.peak_mb:.2f} MB")
```

### Example 3: Benchmarking

```bash
# Run performance benchmarks
python scripts/benchmark_performance.py \
    --assumptions-dir data/assumptions \
    --output benchmark_results.txt

# Run quick benchmarks (fewer iterations)
python scripts/benchmark_performance.py --quick
```

---

## 🧪 Testing

### Run All Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test suites
pytest tests/test_flexible_assumptions.py -v
pytest tests/test_distributed_processing.py -v
pytest tests/test_integration_e2e.py -v

# Run with coverage
pytest tests/ --cov=par_model_v2 --cov-report=html
```

### Expected Results

- **78+ tests** should pass
- **Coverage:** >85% for new modules
- **Performance:** Benchmarks should meet baseline targets

---

## ⚠️ Breaking Changes

**None** - v0.2.0 is fully backward compatible with v0.1.0.

All existing code continues to work without modification.

---

## 🐛 Known Issues & Workarounds

### Issue 1: Banding Logic

**Problem:** String-based bands (e.g., "20-30", "100000-500000") require exact match.

**Workaround:**
```python
# Determine age band manually
age = 35
if age < 30:
    age_band = "20-30"
elif age < 40:
    age_band = "30-40"
else:
    age_band = "40-50"

lapse = provider.get_lapse(product='WL', policy_year=1, age=age_band)
```

**Future:** v0.3.0 will support numeric band parsing and interpolation.

### Issue 2: Lint Warnings in Test Files

**Problem:** "Module level import not at top of file" warnings in test files.

**Explanation:** This is due to `sys.path` modification for test discovery - a standard and acceptable pattern.

**Action:** Safe to ignore these warnings.

---

## 📈 Performance Expectations

### Baseline Performance (v0.2.0)

| Metric | Target | Notes |
|--------|--------|-------|
| Assumption lookup (cached) | <0.01ms | 200x faster than uncached |
| Policy projection | >50/sec | Single core |
| Distributed processing | >100/sec | Multi-core |
| Resource monitoring overhead | <0.1% | Negligible |
| Checkpoint save | <100ms | Per 100 chunks |

### Scalability

| Portfolio Size | Expected Duration | Memory Usage |
|----------------|-------------------|--------------|
| 1,000 policies | ~2 min | ~5 GB |
| 10,000 policies | ~20 min | ~50 GB |
| 100,000 policies | ~3 hrs | ~80 GB |

---

## 🆘 Troubleshooting

### Problem: Import Error

```python
ImportError: cannot import name 'FlexibleAssumptionProvider'
```

**Solution:**
```bash
# Verify installation
pip install -r requirements.txt

# Check Python path
python -c "import sys; print(sys.path)"

# Reinstall package
pip install -e .
```

### Problem: Metadata Not Found

```python
FileNotFoundError: metadata.json not found
```

**Solution:**
```bash
# Create metadata.json in assumptions directory
# See example in docs/ENHANCEMENT_SUMMARY.md

# Or use default metadata
python -c "from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider; FlexibleAssumptionProvider.create_default_metadata('data/assumptions')"
```

### Problem: Out of Memory

```python
MemoryError: Unable to allocate array
```

**Solution:**
```python
# Enable auto chunk sizing
config = DistributedConfig(
    chunk_size_auto=True,
    max_ram_usage_pct=0.80  # Reduce from 0.90
)

# Or set manual chunk size
config = DistributedConfig(
    chunk_size_auto=False,
    chunk_size_manual=100  # Reduce size
)
```

---

## 📚 Additional Resources

- **ENHANCEMENT_SUMMARY.md**: Complete feature overview with examples
- **PHASE3_DISTRIBUTED_PROCESSING.md**: Distributed processing guide
- **PHASE4_UI_ARCHITECTURE.md**: Future UI options
- **API Documentation**: Auto-generated from docstrings

---

## 🎓 Best Practices

### 1. Start Small

Test new features with small portfolios (100-1000 policies) before scaling up.

### 2. Enable Checkpointing

Always enable checkpointing for long-running jobs:

```python
config = DistributedConfig(
    checkpoint_dir='checkpoints',
    checkpoint_frequency=100
)
```

### 3. Monitor Resources

Use ResourceMonitor to prevent OOM errors:

```python
monitor = ResourceMonitor()
if not monitor.is_within_limits()[0]:
    monitor.wait_for_resources(max_wait_seconds=300)
```

### 4. Profile Memory

Profile memory usage to optimize chunk sizes:

```python
from par_model_v2.utils.memory_profiler import MemoryTracker

with MemoryTracker("Test run") as tracker:
    process_sample(policies.head(100))

print(f"Memory per policy: {tracker.peak_mb / 100:.4f} MB")
```

---

## 🚀 Next Steps

After migrating to v0.2.0:

1. **Run benchmarks** to establish baseline performance
2. **Test with sample data** to validate integration
3. **Enable checkpointing** for production runs
4. **Monitor resources** during processing
5. **Review documentation** for advanced features

---

## 📞 Support

For questions or issues:
- Review documentation in `docs/`
- Check test examples in `tests/`
- Open GitHub issue with details
- Contact project maintainers

---

**Version:** 0.2.0
**Last Updated:** 2026-01-11
**Status:** Production Ready
