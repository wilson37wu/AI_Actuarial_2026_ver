# Dynamic ALM Engine - MVP Documentation

## Overview

The Dynamic ALM (Asset-Liability Management) Engine integrates liability cashflows with asset portfolio projection under ESG scenarios. This MVP implementation provides the core functionality for linking liability-side and asset-side projections in a dynamic, time-stepping framework.

---

## Sign Convention

**Liability Net Cashflow (`NetCF_liab`)**:
- **Positive** (`NetCF_liab > 0`): Premiums exceed benefits → Cash **inflow** to fund
- **Negative** (`NetCF_liab < 0`): Benefits exceed premiums → Cash **outflow** from fund

**Asset Holdings**:
- All holdings must be **non-negative** (no shorting allowed)
- Market value (MV) is always `>= 0`

**Transaction Costs**:
- Always **positive** (reduce proceeds on sell, increase cost on buy)
- Expressed in **basis points (bps)** and converted to currency amounts

---

## Core Recursion Steps

For each **trial** and **timestep** `t`, the fund evolves through the following steps:

### Step A: Apply ESG Returns to Holdings

Apply investment returns from ESG scenarios to each asset bucket:

```
For each holding bucket:
    - Government bonds (tenor k):
        return_factor = P_zcb(t+1) / P_zcb(t)
        MV_new = MV_old × return_factor

    - Credit bonds (rating r, tenor k):
        return_factor = P_credit(t+1) / P_credit(t)
        MV_new = MV_old × return_factor

    - Equity:
        return_factor = TotalReturn(t+1)  # From ESG
        MV_new = MV_old × return_factor

    - Cash:
        return_factor = CashTotalReturn(t+1)  # From ESG
        MV_new = MV_old × return_factor
```

**Note**: At timestep 0, equity `TotalReturn = 1.0` (no return). Subsequent timesteps have `TotalReturn > 0` as a multiplicative factor.

### Step B: Apply Liability Net Cashflow

Add (or subtract) the liability net cashflow to the cash bucket:

```
Cash(t) = Cash(t) + NetCF_liab(t)
```

- If `NetCF_liab(t) > 0`: Cash increases (premiums received)
- If `NetCF_liab(t) < 0`: Cash decreases (benefits paid out)

### Step C: Funding Rule (Cash Management)

If cash falls below the minimum buffer, sell assets to restore cash to target level:

```
total_mv = sum(all holdings)
target_cash = total_mv × target_cash_buffer  # e.g., 2%
min_cash = total_mv × min_cash_buffer  # e.g., 1%

If Cash(t) < min_cash:
    cash_needed = target_cash - Cash(t)
    Sell assets to raise cash_needed (see Sell Order below)
    Apply transaction costs
    Cash(t) += net_proceeds_from_sales
```

### Step D: Rebalancing to SAA

If rebalancing is enabled at this timestep, adjust holdings to match SAA target weights:

```
Get SAA target weights: {Govt: w_g, Credit: w_c, Equity: w_e, Cash: w_cash}

For each asset class:
    target_mv = total_mv × target_weight
    current_mv = sum(holdings in asset class)
    trade_amount = target_mv - current_mv

    If trade_amount < 0:
        Sell assets (proportionally within asset class)
    If trade_amount > 0:
        Buy assets (using default tenors/ratings)

    Apply transaction costs to all trades
```

### Step E: Update Holdings and Record Metrics

```
Holdings(t) = Holdings after all steps above
MV_end(t) = total market value at end of timestep
```

Record fund history, trade history, and reconciliation metrics.

---

## Sell Order Priority

When selling assets to raise cash (Step C or Step D), follow this **deterministic priority**:

1. **Government Bonds** (short tenor → long tenor)
   - Sell 1Y first, then 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y
   - Transaction cost: 2-5 bps depending on tenor

2. **Credit Bonds** (high rating → low rating, short tenor → long tenor within rating)
   - Sell AAA first, then AA, A, BBB, BB, B, CCC
   - Within each rating, sell short tenors first
   - Transaction cost: 5-60 bps depending on rating

3. **Equity** (last resort)
   - Sell only if govt and credit insufficient
   - Transaction cost: 15 bps

4. **Cash** (never sold for funding, only for rebalancing)

**Rationale**: Sell most liquid assets first to minimize market impact and transaction costs.

---

## Rebalancing Logic

### Rebalancing Frequency

Controlled by `config.rebalance_frequency`:

- **`'each_step'`**: Rebalance every timestep (most responsive, highest costs)
- **`'annual'`**: Rebalance every 12 timesteps (once per year)
- **`'none'`**: No rebalancing (holdings drift freely)
- **`'threshold'`** (not implemented in MVP): Rebalance only if weight drift exceeds tolerance

### Rebalancing Mechanism

**Asset Class Level** (MVP simplification):
- Rebalance at aggregate level: Govt, Credit, Equity, Cash
- Within each class, use default tenors/ratings for purchases:
  - Govt: Buy 10Y tenor
  - Credit: Buy A-rated 5Y tenor
  - Equity: Buy index tracker
  - Cash: No action needed

**Proportional Sales**:
- When selling from an asset class, sell proportionally from all buckets
- Example: If selling 30% of Govt, sell 30% from each tenor bucket

**Transaction Costs**:
- Applied to both buys and sells
- Buys: `total_cost = amount + tc`
- Sells: `net_proceeds = amount - tc`

---

## Transaction Costs

Transaction costs are specified in **basis points (bps)** and vary by asset type:

| Asset Type | Tenor/Rating | TC (bps) |
|------------|--------------|----------|
| Government | 1Y-5Y        | 2        |
| Government | 5Y-10Y       | 3        |
| Government | 10Y+         | 5        |
| Credit AAA | Any          | 5        |
| Credit AA  | Any          | 7        |
| Credit A   | Any          | 10       |
| Credit BBB | Any          | 15       |
| Credit BB  | Any          | 25       |
| Credit B   | Any          | 40       |
| Credit CCC | Any          | 60       |
| Equity     | N/A          | 15       |
| Cash       | N/A          | 0        |

**Calculation**:
```
tc_amount = trade_amount × (tc_bps / 10000)
```

---

## Input Specifications

### 1. Liability Cashflow DataFrame

**Required columns**:
- `Trial` (int): Scenario trial number
- `Timestep` (int): Monthly timestep (0, 1, 2, ...)
- `NetCF_liab` (float): Net cashflow from liabilities

**Optional columns**:
- `DividendsPaid` (float): Non-guaranteed dividends paid
- `RequiredReserve` (float): Regulatory reserve requirement
- `Inforce` (int): Number of policies in force

**Example**:
```python
liability_cf_df = pd.DataFrame([
    {'Trial': 1, 'Timestep': 0, 'NetCF_liab': 1000.0},
    {'Trial': 1, 'Timestep': 1, 'NetCF_liab': 950.0},
    # ...
])
```

### 2. ESG Scenario DataFrame

**Required columns**:
- `Trial` (int): Scenario trial number
- `Timestep` (int): Monthly timestep
- ESG columns for each asset bucket referenced in holdings:
  - `ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)`: Government ZCB prices
  - `ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)`: Credit ZCB prices
  - `ESG.Assets.EquityAssets.E_CNY.TotalReturn`: Equity total return factor
  - `ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn`: Cash return factor

**Example**:
```python
esg_df = pd.DataFrame([
    {
        'Trial': 1,
        'Timestep': 0,
        'ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)': 0.920,
        'ESG.Assets.EquityAssets.E_CNY.TotalReturn': 1.000,
        'ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn': 1.002,
    },
    # ...
])
```

### 3. SAA Schedule

A callable or object with `get_weights(timestep)` method that returns a dictionary:

```python
def saa_schedule(timestep: int) -> dict:
    """Return SAA target weights for a given timestep."""
    return {
        'Govt': 0.30,
        'Credit': 0.30,
        'Equity': 0.30,
        'Cash': 0.10,
    }
```

**Requirements**:
- Weights must sum to 1.0
- All asset classes must be present: `Govt`, `Credit`, `Equity`, `Cash`

### 4. Initial Assets (Optional)

A `Holdings` object specifying starting asset positions:

```python
from par_model_v2.valuation.dynamic_alm import Holdings

initial_assets = Holdings()
initial_assets.govt[10] = 3000.0  # 3000 in 10Y govt bonds
initial_assets.credit[('A', 5)] = 2000.0  # 2000 in A-rated 5Y credit
initial_assets.equity = 4000.0
initial_assets.cash = 1000.0
```

**Holdings Structure**:
- `govt`: `Dict[int, float]` - {tenor: market_value}
- `credit`: `Dict[Tuple[str, int], float]` - {(rating, tenor): market_value}
- `equity`: `float` - market value
- `cash`: `float` - market value

If `initial_assets=None`, the projection starts with zero assets.

---

## Output Specifications

The engine returns an `ALMProjectionResult` object with three components:

### 1. Fund History DataFrame

**Columns**:
- `Trial` (int): Trial number
- `Timestep` (int): Timestep
- `MV_total` (float): Total market value at end of timestep
- `MV_govt` (float): Market value in government bonds
- `MV_credit` (float): Market value in credit bonds
- `MV_equity` (float): Market value in equity
- `MV_cash` (float): Market value in cash
- `NetCF_liab` (float): Liability net cashflow applied
- `PortfolioReturnEffect` (float): Change in MV due to investment returns
- `TransactionCosts` (float): Total transaction costs incurred
- `WeightDriftMax` (float): Maximum weight drift from SAA targets

### 2. Trade History DataFrame

**Columns**:
- `Trial` (int): Trial number
- `Timestep` (int): Timestep
- `Action` (str): 'BUY' or 'SELL'
- `AssetType` (str): 'Govt', 'Credit', 'Equity', 'Cash'
- `Bucket` (str): Specific bucket (e.g., 'Govt_10Y', 'Credit_A_5Y')
- `AmountGross` (float): Gross trade amount
- `TC_bps` (float): Transaction cost in basis points
- `TC_Amount` (float): Transaction cost in currency
- `AmountNet` (float): Net proceeds (sell) or net cost (buy)
- `Reason` (str): 'FUNDING', 'REBALANCE', 'INITIAL'

### 3. Reconciliation DataFrame

**Columns**:
- `Trial` (int): Trial number
- `Timestep` (int): Timestep
- `MV_RollforwardError` (float): Absolute error in MV rollforward
- `Status` (str): 'OK' if error < 1.0, else 'CHECK'

**Reconciliation Check**:
```
Expected_MV(t) = MV(t-1) + PortfolioReturnEffect(t) + NetCF_liab(t) - TransactionCosts(t)
Error = |Actual_MV(t) - Expected_MV(t)|
```

---

## Usage Examples

### Example 1: Single Trial Projection

```python
from par_model_v2.valuation.dynamic_alm import DynamicALMEngine, ALMConfig, Holdings
import pandas as pd

# Configure engine
config = ALMConfig(
    rebalance_frequency='annual',
    target_cash_buffer=0.02,
)
engine = DynamicALMEngine(config)

# Load data
liability_cf_df = pd.read_csv('liability_cashflows.csv')
esg_df = pd.read_parquet('esg_scenarios.parquet')

# Define SAA schedule
def saa_schedule(timestep):
    return {'Govt': 0.30, 'Credit': 0.30, 'Equity': 0.30, 'Cash': 0.10}

# Set initial assets
initial_assets = Holdings()
initial_assets.govt[10] = 5000.0
initial_assets.equity = 3000.0
initial_assets.cash = 2000.0

# Project trial 1
result = engine.project_trial(
    trial=1,
    liability_cf_df=liability_cf_df,
    esg_df=esg_df,
    saa_schedule=saa_schedule,
    initial_assets=initial_assets,
)

# Get results
fund_df, trade_df, recon_df = result.to_dataframes()

# Analyze
print(f"Final MV: {fund_df.iloc[-1]['MV_total']:,.0f}")
print(f"Total trades: {len(trade_df)}")
print(f"Reconciliation status: {recon_df['Status'].value_counts()}")
```

### Example 2: Multiple Trials

```python
# Project all trials in data
result = engine.project_portfolio(
    liability_cf_df=liability_cf_df,
    esg_df=esg_df,
    saa_schedule=saa_schedule,
    initial_assets=initial_assets,
    n_trials=100,
    parallel=False,  # MVP does not support parallel
)

fund_df, trade_df, recon_df = result.to_dataframes()

# Analyze across trials
import matplotlib.pyplot as plt

for trial in range(1, 11):  # Plot first 10 trials
    trial_data = fund_df[fund_df['Trial'] == trial]
    plt.plot(trial_data['Timestep'], trial_data['MV_total'], alpha=0.5)

plt.xlabel('Timestep')
plt.ylabel('Total MV')
plt.title('Fund MV Evolution (10 Trials)')
plt.show()
```

### Example 3: Zero Initial Assets

```python
# Start with no assets, build from cashflows
result = engine.project_trial(
    trial=1,
    liability_cf_df=liability_cf_df,
    esg_df=esg_df,
    saa_schedule=saa_schedule,
    initial_assets=None,  # Start from zero
)

fund_df, _, _ = result.to_dataframes()
print(fund_df[['Timestep', 'MV_total', 'NetCF_liab']].head(10))
```

---

## Limitations of MVP

This MVP implementation has the following limitations (to be addressed in future versions):

### 1. No Tax Modeling
- Corporate tax on investment profits not modeled
- Withholding tax on dividends not modeled
- Tax loss carryforwards not tracked

### 2. No Detailed Book Value Tracking
- Only market value (MV) is tracked
- Book value (BV) is not maintained
- Realized vs. unrealized gains not separated for accounting

### 3. No Duration Constraints
- Portfolio duration not calculated or constrained
- No duration matching to liability duration
- No convexity management

### 4. No Rating Constraints
- No limits on credit rating exposure (e.g., max 10% in BB or below)
- No rating migration modeling
- No default risk modeling

### 5. No Concentration Limits
- No limits on single issuer exposure
- No sector concentration limits
- No geographic concentration limits

### 6. Simplified Rebalancing
- Rebalancing only at asset class level
- Within-class rebalancing uses fixed default tenors/ratings
- No optimization of rebalancing trades

### 7. No Shareholder Deficit Account (SDA)
- If assets become negative, model does not handle gracefully
- No SDA creation or repayment logic
- No shareholder capital injection

### 8. No Parallel Processing
- `parallel=True` not implemented
- All trials run sequentially
- Performance limited for large trial counts

### 9. Single Currency
- Only CNY (Chinese Yuan) supported
- No multi-currency portfolios
- No FX risk modeling

### 10. Fixed ESG Schema
- ESG column names are hardcoded
- Limited flexibility for different ESG providers
- No support for alternative asset classes (real estate, alternatives)

---

## Validation and Testing

The MVP includes comprehensive unit tests in `tests/test_dynamic_alm.py`:

1. **`test_positive_cashflow_growth`**: Verify assets grow with positive cashflows
2. **`test_negative_cashflow_liquidation`**: Verify assets are sold with negative cashflows
3. **`test_rebalancing_to_saa`**: Verify rebalancing converges to SAA targets
4. **`test_zero_initial_assets`**: Verify starting from zero works correctly
5. **`test_transaction_costs`**: Verify TC are applied correctly
6. **`test_sell_order_priority`**: Verify deterministic sell order is followed
7. **`test_reconciliation_checks`**: Verify MV rollforward reconciles

**Run tests**:
```bash
pytest tests/test_dynamic_alm.py -v
```

---

## Next Steps for Production

To move from MVP to production-ready implementation:

1. **Add SDA Logic**: Handle negative asset scenarios with shareholder deficit account
2. **Implement Parallel Processing**: Use multiprocessing for large trial counts
3. **Add Duration Management**: Calculate and constrain portfolio duration
4. **Add Rating Constraints**: Implement credit rating limits and migration
5. **Add Tax Modeling**: Corporate tax, withholding tax, loss carryforwards
6. **Enhance Rebalancing**: Optimize trades within asset classes, minimize costs
7. **Add Book Value Tracking**: Maintain BV, calculate realized/unrealized gains
8. **Add Validation Reports**: Comprehensive validation output with diagnostics
9. **Performance Optimization**: Vectorize operations, use NumPy arrays
10. **Documentation**: User guide, methodology document, API reference

---

## References

- **NEXT_STEPS_DYNAMIC_ALM.md**: Detailed implementation roadmap
- **SAA_IMPLEMENTATION_SUMMARY.md**: SAA and trading integration overview
- **par_model_v2/valuation/dynamic_alm.py**: Source code
- **tests/test_dynamic_alm.py**: Unit tests
