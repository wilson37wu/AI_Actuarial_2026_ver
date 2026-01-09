# Strategic Asset Allocation (SAA) and Trading Integration

## Overview

This document describes the integration of Strategic Asset Allocation (SAA), portfolio trading/rebalancing, and initial fund backing into the PAR liability valuation engine.

## Modelling Approach

**Chosen: Policy-Level Portfolio (Option 1)**

Each policy maintains its own mini-portfolio driven by SAA targets. This approach:
- ✅ Simpler implementation
- ✅ Consistent with existing asset share per-policy logic
- ✅ Easier to parallelize across policies
- ✅ Fund-level view obtained by aggregating across policies
- ✅ No complex allocation of fund-level surplus to policies

## Architecture

### New Components

1. **`FundPortfolio`** class (`par_model_v2/assets/fund_portfolio.py`)
   - Manages asset buckets by `AssetClass`
   - Applies ESG-driven returns
   - Executes trades based on net cashflows
   - Rebalances toward SAA targets
   - Tracks shareholder deficit

2. **`SAAProvider`** class (`par_model_v2/assumptions/saa_provider.py`)
   - Loads strategic_asset_allocation.csv
   - Hierarchical lookup with fallbacks
   - Interpolation for intermediate policy years
   - Validates weights sum to 1.0

3. **`TradingPolicy`** class (`par_model_v2/assets/fund_portfolio.py`)
   - Configures rebalancing frequency
   - Defines sell order (Cash → Govt → Credit → Equity)
   - Transaction cost modeling
   - Constraints (no shorting)

### Asset Classes

```python
class AssetClass(Enum):
    GOVT = "Govt"
    CREDIT_AAA = "Credit_AAA"
    CREDIT_AA = "Credit_AA"
    CREDIT_A = "Credit_A"
    CREDIT_BBB = "Credit_BBB"
    CREDIT_BB = "Credit_BB"
    CREDIT_B = "Credit_B"
    EQUITY = "Equity"
    CASH = "Cash"
```

## CSV Table Schemas

### 1. strategic_asset_allocation.csv

```csv
product_code,policy_year,calendar_year,fund_id,asset_class,target_weight
ALL,1,0,PAR,Govt,0.35
ALL,1,0,PAR,Credit_A,0.25
ALL,1,0,PAR,Equity,0.35
ALL,1,0,PAR,Cash,0.05
ALL,5,0,PAR,Govt,0.40
ALL,5,0,PAR,Credit_A,0.25
ALL,5,0,PAR,Equity,0.30
ALL,5,0,PAR,Cash,0.05
```

**Columns**:
- `product_code`: Product identifier or "ALL" for default
- `policy_year`: Policy duration year (1, 5, 10, etc.)
- `calendar_year`: Calendar year (0 = wildcard for all years)
- `fund_id`: Fund identifier (default "PAR")
- `asset_class`: Govt, Credit_AAA, Credit_A, Equity, Cash, etc.
- `target_weight`: Target allocation (must sum to 1.0 within group)

**Lookup Logic**:
1. Exact match: (product_code, policy_year, calendar_year, fund_id)
2. Product + policy_year (ignore calendar_year)
3. "ALL" + policy_year
4. "ALL" + interpolated policy_year

### 2. initial_fund_assets.csv

```csv
fund_id,valuation_date,asset_class,market_value,book_value,duration,average_rating
PAR,2024-01-01,Govt,900000,880000,8.5,
PAR,2024-01-01,Credit_A,575000,570000,6.2,A
PAR,2024-01-01,Equity,700000,700000,0.0,
PAR,2024-01-01,Cash,125000,125000,0.0,
```

**Columns**:
- `fund_id`: Fund identifier
- `valuation_date`: Starting date (YYYY-MM-DD)
- `asset_class`: Asset class identifier
- `market_value`: Market value at start
- `book_value`: Book value (optional)
- `duration`: Duration in years (optional, for bonds)
- `average_rating`: Credit rating (optional, for credit assets)

## Trading Mechanics

### Per-Timestep Sequence

```
1. Apply ESG returns to each asset bucket:
   MV_after_return[asset] = MV_t[asset] × R_t[asset]

2. Compute net cashflow:
   NetCF_t = Premiums_t - GuaranteedBenefits_t - Expenses_t - DividendsPaid_t

3. Compute target total MV:
   TargetTotal = sum(MV_after_return) + NetCF_t

4. If TargetTotal < 0:
   - Liquidate all assets
   - Create shareholder deficit = -TargetTotal
   - Set all MV to 0
   - STOP

5. Compute target MV by asset:
   TargetMV[asset] = TargetTotal × SAA_weight[asset]

6. Compute required trades:
   Trade[asset] = TargetMV[asset] - MV_after_return[asset]

7. Execute sells first (in sell_order):
   - Sell up to available MV (no shorting)
   - Apply transaction cost

8. Execute buys:
   - Buy to reach target
   - Apply transaction cost (reduces MV)

9. Snapshot state for audit trail
```

### Sell Order

Default: `[Cash, Govt, Credit, Equity]`

When raising cash to meet outflows:
1. Sell Cash first (most liquid)
2. Then Government bonds
3. Then Credit bonds
4. Finally Equity (least liquid, highest transaction cost)

### Transaction Costs

- Configurable via `transaction_cost_bps` (default: 5 bps = 0.05%)
- Applied to both buys and sells
- Reduces net proceeds from sells
- Reduces net investment from buys

### Deficit Handling

When assets insufficient to meet outflows:
1. Liquidate all assets to 0
2. Create/increase Shareholder Deficit Account (SDA)
3. Future positive cashflows first repay SDA
4. Only after SDA = 0 can profit sharing resume

## Integration with Asset Share Projection

### Modified Asset Share Sequence

```python
# Original asset share logic
AS_t = AS_{t-1}
AS_t += Premium_t - Expenses_t
AS_t *= (1 + investment_return_t)  # <-- NOW DRIVEN BY PORTFOLIO
AS_t -= GuaranteedBenefit_t

# NEW: Portfolio-driven return
portfolio.apply_returns(returns_by_asset, timestep=t)
portfolio_return = portfolio.total_market_value / portfolio_mv_before - 1.0

# Use portfolio return instead of fixed return
AS_t = AS_{t-1}
AS_t += Premium_t - Expenses_t
AS_t *= (1 + portfolio_return)
AS_t -= GuaranteedBenefit_t

# NEW: Apply net cashflow to portfolio
net_cf = Premium_t - GuaranteedBenefit_t - Expenses_t - Dividend_t
deficit, trades = portfolio.apply_net_cashflow(
    net_cf=net_cf,
    saa_weights=saa_weights,
    timestep=t
)

# Handle deficit
if deficit > 0:
    shareholder_deficit += deficit
    AS_t = 0
```

## Usage Example

### Basic Setup

```python
from par_model_v2.assets.fund_portfolio import (
    FundPortfolio, TradingPolicy, AssetClass, load_initial_assets
)
from par_model_v2.assumptions.saa_provider import SAAProvider
from par_model_v2.esg.esg_scenario_provider import ESGScenarioProvider

# Load ESG scenarios
esg = ESGScenarioProvider('data/esg/scenarios.parquet')

# Load SAA
saa = SAAProvider('data/assumptions/strategic_asset_allocation.csv')

# Load initial assets (optional)
initial_assets = load_initial_assets(
    'data/assumptions/initial_fund_assets.csv',
    fund_id='PAR'
)

# Create portfolio
trading_policy = TradingPolicy(
    rebalance_frequency='each_step',
    transaction_cost_bps=5.0
)

portfolio = FundPortfolio(
    initial_assets=initial_assets,
    trading_policy=trading_policy
)
```

### Per-Timestep Projection

```python
for t in range(1, n_timesteps + 1):
    # Get SAA weights for this policy year
    policy_year = t // 12 + 1  # Convert timestep to policy year
    saa_weights = saa.get_saa_weights(
        product_code='PAR_TRAD',
        policy_year=policy_year,
        fund_id='PAR'
    )

    # Get ESG returns for this trial/timestep
    returns_by_asset = {
        AssetClass.GOVT: esg.get_bond_total_return(
            trial=trial, timestep=t, timestep_next=t+1,
            rating='Govt', tenor=10
        ),
        AssetClass.CREDIT_A: esg.get_bond_total_return(
            trial=trial, timestep=t, timestep_next=t+1,
            rating='A', tenor=7
        ),
        AssetClass.EQUITY: esg.get_equity_total_return(
            trial=trial, timestep=t
        ),
        AssetClass.CASH: esg.get_cash_return(trial=trial, timestep=t),
    }

    # Apply returns
    portfolio.apply_returns(returns_by_asset, timestep=t)

    # Compute net cashflow
    net_cf = premiums[t] - guaranteed_benefits[t] - expenses[t] - dividends[t]

    # Apply cashflow and rebalance
    deficit, trades = portfolio.apply_net_cashflow(
        net_cashflow=net_cf,
        saa_weights=saa_weights,
        timestep=t
    )

    # Snapshot for audit
    snapshot = portfolio.snapshot(
        timestep=t,
        saa_weights=saa_weights,
        net_cashflow=net_cf
    )
```

### Output Analysis

```python
# Get portfolio history
df_portfolio = portfolio.get_history_dataframe()
print(df_portfolio[['timestep', 'total_mv', 'weight_Equity', 'shareholder_deficit']])

# Get trade history
df_trades = portfolio.get_trade_dataframe()
print(df_trades[['timestep', 'asset_class', 'trade_amount', 'transaction_cost']])

# Analyze rebalancing activity
total_trade_cost = df_trades['transaction_cost'].sum()
n_rebalances = df_trades.groupby('timestep').size().count()
print(f"Total transaction costs: ${total_trade_cost:,.2f}")
print(f"Number of rebalancing events: {n_rebalances}")
```

## Configuration Options

### CLI Arguments (to be added to scripts)

```bash
python scripts/run_liability_distributed.py \
    --saa_table data/assumptions/strategic_asset_allocation.csv \
    --initial_fund_assets data/assumptions/initial_fund_assets.csv \
    --rebalance_frequency each_step \
    --transaction_cost_bps 5.0
```

### TradingPolicy Configuration

```python
# Conservative: rebalance annually, low costs
policy = TradingPolicy(
    rebalance_frequency='annual',
    transaction_cost_bps=3.0,
    rebalance_threshold=0.05  # Only rebalance if >5% drift
)

# Aggressive: rebalance each step, higher costs
policy = TradingPolicy(
    rebalance_frequency='each_step',
    transaction_cost_bps=10.0,
    rebalance_threshold=0.0  # Always rebalance
)

# No rebalancing: buy-and-hold
policy = TradingPolicy(
    rebalance_frequency='none',
    transaction_cost_bps=5.0
)
```

## Test Scenarios

### Test 1: Positive Cashflows → Portfolio Growth

**Setup**:
- Constant positive premiums
- No benefits
- SAA: 50% Equity, 50% Bonds

**Expected**:
- Portfolio grows each period
- Rebalancing maintains 50/50 split
- Transaction costs reduce growth slightly

### Test 2: Negative Cashflows → Asset Sales

**Setup**:
- Large benefit payments
- SAA: 40% Govt, 30% Credit, 30% Equity

**Expected**:
- Assets sold in order: Cash → Govt → Credit → Equity
- Portfolio shrinks but maintains SAA ratios
- Transaction costs on sales

### Test 3: Severe Outflows → Deficit Creation

**Setup**:
- Benefits exceed assets
- SAA: any

**Expected**:
- All assets liquidated to 0
- Shareholder deficit created
- Future cashflows repay deficit first

### Test 4: SAA Changes Over Time

**Setup**:
- Policy year 1-5: 40% Equity
- Policy year 6-10: 20% Equity
- Positive cashflows

**Expected**:
- Gradual shift from equity to bonds
- Rebalancing trades execute the shift
- Portfolio follows SAA glide path

## Performance Considerations

### Memory

- Each policy portfolio: ~1 KB state
- 10,000 policies × 1000 trials: ~10 GB
- Use chunked processing for large portfolios

### Computation

- Per-policy per-timestep: ~0.1 ms
- 10,000 policies × 360 timesteps: ~6 minutes (single-threaded)
- Parallelizable across policies

### Optimization Tips

1. **Reduce rebalancing frequency**: Use 'annual' instead of 'each_step'
2. **Increase rebalance_threshold**: Only rebalance when drift > 5%
3. **Simplify SAA**: Use fewer asset classes
4. **Batch ESG lookups**: Cache returns for all policies in a trial

## Governance Outputs

### Portfolio Drift Analysis

```python
# Measure drift from SAA targets
df_portfolio['drift_equity'] = abs(
    df_portfolio['weight_Equity'] - df_portfolio['target_Equity']
)

avg_drift = df_portfolio['drift_equity'].mean()
max_drift = df_portfolio['drift_equity'].max()
```

### Transaction Cost Impact

```python
# Total cost as % of assets
total_costs = df_trades['transaction_cost'].sum()
avg_assets = df_portfolio['total_mv'].mean()
cost_ratio = total_costs / avg_assets

print(f"Transaction costs: {cost_ratio:.2%} of average assets")
```

### Deficit Frequency

```python
# Fraction of scenarios with deficit
deficit_scenarios = (df_portfolio['shareholder_deficit'] > 0).any()
deficit_freq = deficit_scenarios.sum() / n_trials

print(f"Deficit occurred in {deficit_freq:.1%} of scenarios")
```

## Next Steps

1. **Generate sample tables**:
   ```bash
   python scripts/generate_sample_assumptions.py
   ```

2. **Test FundPortfolio standalone**:
   ```python
   from par_model_v2.assets.fund_portfolio import FundPortfolio
   # ... test basic operations
   ```

3. **Integrate with AssetShareProjector**:
   - Modify `AssetShareProjector.step()` to use portfolio returns
   - Pass net cashflows to portfolio
   - Handle deficit scenarios

4. **Add CLI arguments** to valuation scripts

5. **Run validation tests** with known scenarios

6. **Production deployment** with full ESG scenarios
