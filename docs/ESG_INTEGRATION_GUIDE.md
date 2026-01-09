# ESG Integration Guide - Non-Guaranteed Dividend Projection

## Quick Start

### 1. Prepare ESG Scenario File

Your ESG file should be CSV or Parquet with columns:
```
Trial, Timestep, ESG.Economies.CNY.NominalZCBP(Govt, 1, 3), ...,
ESG.Assets.EquityAssets.E_CNY.TotalReturn, ...
```

**Recommended**: Convert large CSV files to Parquet for faster loading:
```python
import pandas as pd
df = pd.read_csv('esg_scenarios.csv')
df.to_parquet('esg_scenarios.parquet', index=False)
```

### 2. Basic Usage Example

```python
from par_model_v2.esg.esg_scenario_provider import ESGScenarioProvider
from par_model_v2.assets.asset_mix import AssetMixConfig, InvestmentReturnCalculator
from par_model_v2.assets.asset_share_projection import (
    AssetShareProjector,
    AssetShareState,
    DividendRules
)

# Load ESG scenarios
esg_provider = ESGScenarioProvider(
    file_path='data/esg/scenarios.parquet',
    max_tenor=30,  # Only load tenors up to 30 years
    ratings=['AAA', 'A', 'BBB'],  # Only load these ratings
)

# Configure asset mix
asset_mix = AssetMixConfig.balanced()  # 40% govt, 25% credit, 30% equity, 5% cash

# Create investment return calculator
return_calc = InvestmentReturnCalculator(asset_mix, esg_provider)

# Configure dividend rules
div_rules = DividendRules(
    policyholder_share=0.70,
    shareholder_share=0.30,
    enforce_lifetime_cap=True,
    dividend_mode='accumulate',
)

# Create projector
projector = AssetShareProjector(div_rules)

# Project for one trial
trial = 1
n_years = 20
investment_returns = np.array([
    return_calc.get_return(trial=trial, timestep=t)
    for t in range(1, n_years + 1)
])

# Example policy cashflows (you'd get these from deterministic engine)
premiums = np.array([1000] * 10 + [0] * 10)  # 10-pay
expenses = np.array([50] * 20)
guaranteed_benefits = np.array([0] * 19 + [100000])  # Maturity at year 20

# Project asset share
initial_state = AssetShareState(asset_share=0.0)
states, df = projector.project_policy(
    initial_state=initial_state,
    premiums=premiums,
    expenses=expenses,
    guaranteed_benefits=guaranteed_benefits,
    investment_returns=investment_returns,
    sum_assured=100000,
)

# Analyze results
print(df[['timestep', 'asset_share', 'period_ng_dividend', 'shareholder_deficit']])
```

### 3. Multi-Trial Projection

```python
import pandas as pd

results = []

for trial in range(1, 101):  # First 100 trials
    # Get investment returns for this trial
    investment_returns = np.array([
        return_calc.get_return(trial=trial, timestep=t)
        for t in range(1, n_years + 1)
    ])

    # Project
    states, df = projector.project_policy(
        initial_state=initial_state,
        premiums=premiums,
        expenses=expenses,
        guaranteed_benefits=guaranteed_benefits,
        investment_returns=investment_returns,
        sum_assured=100000,
    )

    # Store final state
    final_state = states[-1]
    results.append({
        'trial': trial,
        'total_ng_dividends': final_state.cum_policyholder_dividends,
        'total_sh_distributions': final_state.cum_shareholder_distributions,
        'final_asset_share': final_state.asset_share,
        'max_deficit': max(s.shareholder_deficit for s in states),
        'sh_ratio': final_state.cum_shareholder_distributions /
                   (final_state.cum_policyholder_dividends + final_state.cum_shareholder_distributions)
                   if (final_state.cum_policyholder_dividends + final_state.cum_shareholder_distributions) > 0 else 0,
    })

# Analyze across trials
df_results = pd.DataFrame(results)
print("\nSummary Statistics:")
print(df_results.describe())

print(f"\nSharehol ratio > 30%: {(df_results['sh_ratio'] > 0.30).sum()} trials")
print(f"Trials with deficit: {(df_results['max_deficit'] > 0).sum()}")
```

## Configuration Files

### Asset Mix Configuration (CSV)

Create `data/assumptions/asset_mix.csv`:
```csv
parameter,value
w_cash,0.05
w_govt_bonds,0.40
w_credit_bonds,0.25
w_equity,0.30
govt_bond_tenor,10
credit_bond_tenor,7
credit_rating,A
equity_ticker,E_CNY
```

Load with:
```python
import pandas as pd
config_df = pd.read_csv('data/assumptions/asset_mix.csv', index_col='parameter')
config_dict = config_df['value'].to_dict()
asset_mix = AssetMixConfig(**config_dict)
```

### Dividend Rules Configuration (CSV)

Create `data/assumptions/dividend_rules.csv`:
```csv
parameter,value
policyholder_share,0.70
shareholder_share,0.30
enforce_lifetime_cap,True
dividend_mode,accumulate
required_reserve_buffer,0.00
enable_deficit_account,True
```

## Integration with Existing Deterministic Engine

### Option 1: Extend `value_portfolio` Function

Add optional ESG parameters to existing function:

```python
def value_portfolio(
    df_portfolio: pd.DataFrame,
    discount_rate: float = 0.03,
    # ... existing parameters ...
    esg_provider: Optional[ESGScenarioProvider] = None,
    asset_mix: Optional[AssetMixConfig] = None,
    dividend_rules: Optional[DividendRules] = None,
    trial_ids: Optional[List[int]] = None,
    **kwargs
) -> Union[Tuple, Dict]:
    """
    Value portfolio with optional ESG-driven non-guaranteed dividends.

    If esg_provider is None: runs deterministic mode (existing behavior)
    If esg_provider is provided: runs stochastic mode with asset share projection
    """

    if esg_provider is None:
        # Existing deterministic logic
        return _value_portfolio_deterministic(df_portfolio, discount_rate, ...)
    else:
        # New stochastic logic
        return _value_portfolio_stochastic(
            df_portfolio, esg_provider, asset_mix, dividend_rules, trial_ids, ...
        )
```

### Option 2: Separate Stochastic Function

Create new function `value_portfolio_stochastic`:

```python
from par_model_v2.liabilities.stochastic_liability import value_portfolio_stochastic

results = value_portfolio_stochastic(
    df_portfolio=df,
    esg_provider=esg_provider,
    asset_mix=asset_mix,
    dividend_rules=dividend_rules,
    trial_ids=range(1, 1001),  # All 1000 trials
    output_dir='data/stochastic_results',
)
```

## ESG Column Mapping Reference

### Government Bonds
```python
# Pattern: ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)
# Example columns:
'ESG.Economies.CNY.NominalZCBP(Govt, 1, 3)'
'ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)'
'ESG.Economies.CNY.NominalZCBP(Govt, 30, 3)'
```

### Corporate Bonds
```python
# Pattern: ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)
# Example columns:
'ESG.Economies.CNY.NominalZCBP(AAA, 5, 3)'
'ESG.Economies.CNY.NominalZCBP(A, 10, 3)'
'ESG.Economies.CNY.NominalZCBP(BBB, 7, 3)'
```

### Equity
```python
# Total return:
'ESG.Assets.EquityAssets.E_CNY.TotalReturn'
'ESG.Assets.EquityAssets.P_CNY.TotalReturn'

# Dividend yield:
'ESG.Assets.EquityAssets.E_CNY.DividendYield.Value'
```

### Cash
```python
'ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn'
```

## Performance Optimization

### Memory Management
```python
# Load only required columns
esg_provider = ESGScenarioProvider(
    file_path='esg.parquet',
    max_tenor=30,  # Don't load tenor 31-60
    ratings=['AAA', 'A'],  # Only 2 ratings instead of 7
    equity_tickers=['E_CNY'],  # Only 1 ticker
)

# This reduces memory from ~5GB to ~500MB for typical ESG file
```

### Parallel Processing
```python
from concurrent.futures import ProcessPoolExecutor

def project_trial(trial_id):
    # Project one trial (pickle-safe function)
    ...
    return results

with ProcessPoolExecutor(max_workers=8) as executor:
    futures = [executor.submit(project_trial, t) for t in range(1, 1001)]
    results = [f.result() for f in futures]
```

### Chunked Trial Processing
```python
# Process trials in chunks to manage memory
chunk_size = 100
n_trials = 1000

for chunk_start in range(1, n_trials + 1, chunk_size):
    chunk_end = min(chunk_start + chunk_size, n_trials + 1)
    trial_ids = range(chunk_start, chunk_end)

    # Process this chunk
    chunk_results = process_trials(trial_ids)

    # Save intermediate results
    save_chunk(chunk_results, chunk_id=chunk_start // chunk_size)
```

## Troubleshooting

### Issue: "Tenor not available in ESG data"
**Solution**: Increase `max_tenor` parameter or use extrapolation

### Issue: "Memory error loading ESG file"
**Solutions**:
1. Convert CSV to Parquet
2. Reduce `max_tenor`
3. Load fewer `ratings`
4. Process trials in smaller chunks

### Issue: "Shareholder ratio > 30%"
**Check**:
1. Is `enforce_lifetime_cap=True`?
2. Is cap enforcement logic working correctly?
3. Are there numerical precision issues?

### Issue: "Negative asset share persists"
**Check**:
1. Is `enable_deficit_account=True`?
2. Are investment returns extremely negative?
3. Are guaranteed benefits too high relative to premiums?

## Next Steps

1. **Generate sample ESG file** for testing (see `scripts/generate_sample_esg.py`)
2. **Run unit tests** (see `tests/test_asset_share.py`)
3. **Integrate with existing valuation scripts**
4. **Validate results** against spreadsheet models
5. **Run production scenarios** with full 1000 trials
