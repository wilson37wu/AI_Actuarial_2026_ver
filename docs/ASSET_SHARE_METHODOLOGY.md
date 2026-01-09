# Asset Share Projection Methodology

## Overview

This document describes the asset share accounting methodology for projecting non-guaranteed dividends in participating (PAR) insurance policies using ESG scenarios.

## Accounting Conventions

### 1. Dividend Mode
**Chosen Convention**: `"accumulate"`

- Non-guaranteed dividends increase the Reversionary Bonus (RB) or terminal bonus
- Dividends are paid at maturity, death, or surrender
- Asset share is not reduced when dividend is declared
- Alternative `"pay"` mode immediately pays dividend and reduces asset share

### 2. Lifetime Profit Sharing Cap
**Rule**: Shareholder cumulative distributions ≤ 30% of total cumulative distributions

**Enforcement**:
```
cum_shareholder ≤ 0.30 × (cum_policyholder + cum_shareholder)

Equivalently:
cum_shareholder / cum_policyholder ≤ 0.30/0.70 = 0.4286
```

When the cap would be breached:
- Reduce current period shareholder share to stay at cap
- Reallocate excess to policyholder (increases policyholder dividend)

### 3. Excess Surplus Handling
**Chosen Convention**: Remains in asset share as buffer

- After profit sharing, any remaining surplus stays in the asset share
- Provides cushion for future adverse scenarios
- Alternative: pay excess as additional policyholder dividend

### 4. Shareholder Deficit Account (SDA)
**Purpose**: Track shareholder support when asset share would go negative

**Mechanism**:
- If asset share < 0 after paying guaranteed benefits, deficit = -asset_share
- SDA accumulates deficit; asset share set to 0
- Future profits first repay SDA before any profit sharing
- SDA repayment does NOT count toward lifetime 30% cap (it's recovery, not profit)

## Asset Share Update Equations

### Step-by-Step Accounting (per timestep)

```
1. Start with AS_t (asset share from previous period)

2. Add premium, deduct expenses:
   AS_t = AS_t + Premium_t - Expenses_t

3. Apply investment return:
   AS_t = AS_t × R_t
   where R_t = investment return factor from ESG scenario

4. Pay guaranteed benefits:
   AS_t = AS_t - GuaranteedBenefit_t

5. Handle negative asset share:
   if AS_t < 0:
       SDA_t = SDA_{t-1} + (-AS_t)
       AS_t = 0

6. Calculate distributable surplus:
   Surplus_t = max(0, AS_t - RequiredBuffer)
   where RequiredBuffer = buffer_ratio × SumAssured

7. Repay SDA (first priority):
   if SDA_t > 0:
       Repayment = min(Surplus_t, SDA_t)
       SDA_t = SDA_t - Repayment
       Surplus_t = Surplus_t - Repayment

8. Distribute remaining surplus (70/30):
   PH_share_raw = Surplus_t × 0.70
   SH_share_raw = Surplus_t × 0.30

9. Enforce lifetime cap:
   CumPH_after = CumPH + PH_share_raw
   CumSH_after = CumSH + SH_share_raw

   MaxSH_allowed = 0.30 × (CumPH_after + CumSH_after)

   if CumSH_after > MaxSH_allowed:
       SH_share_actual = max(0, MaxSH_allowed - CumSH)
       Excess = SH_share_raw - SH_share_actual
       PH_share_actual = PH_share_raw + Excess
   else:
       PH_share_actual = PH_share_raw
       SH_share_actual = SH_share_raw

10. Update cumulative trackers:
    CumPH = CumPH + PH_share_actual
    CumSH = CumSH + SH_share_actual

11. Credit dividend (mode-dependent):
    if dividend_mode == 'accumulate':
        AccumulatedRB = AccumulatedRB + PH_share_actual
        # AS unchanged
    else:  # 'pay'
        AS_t = AS_t - PH_share_actual

12. Shareholder distribution:
    AS_t = AS_t - SH_share_actual
```

## Investment Return Calculation

### Asset Mix
Configurable weights across asset classes:
- Cash (w_cash)
- Government bonds (w_govt)
- Corporate bonds (w_credit)
- Equity (w_equity)

Weights must sum to 1.0.

### Blended Return
```
R_t = w_cash × R_cash,t
    + w_govt × R_govt_bond,t
    + w_credit × R_credit_bond,t
    + w_equity × R_equity,t
```

### Bond Total Return (Roll-Down Strategy)
For a bond with tenor n at time t:
```
Price_t = ZCB(t, n)
Price_{t+1} = ZCB(t+1, n-1)

TotalReturn = Price_{t+1} / Price_t
```

**Edge Cases**:
- Tenor 1: Use cash return as proxy
- Missing tenor n-1: Interpolate or use adjacent tenor
- Tenor > 60 (max in ESG): Use tenor 60 as proxy

## ESG Data Loading

### Column Naming Convention
```
Trial, Timestep (keys)

Government ZCB:
ESG.Economies.CNY.NominalZCBP(Govt, {tenor}, 3)
where tenor ∈ [1, 60]

Corporate ZCB:
ESG.Economies.CNY.NominalZCBP({rating}, {tenor}, 3)
where rating ∈ {AAA, AA, A, BBB, BB, B, CCC}
      tenor ∈ [1, 60]

Equity:
ESG.Assets.EquityAssets.{ticker}.TotalReturn
ESG.Assets.EquityAssets.{ticker}.DividendYield.Value

Cash:
ESG.Economies.CNY.NominalYieldCurves.NominalYieldCurve.CashTotalReturn
```

### Memory Efficiency
- Load only required columns (not all ~5000+ columns)
- Use Parquet format for columnar access
- Filter by max_tenor, ratings, equity_tickers

## Test Cases

### Test 1: Constant Positive Returns
**Setup**:
- Constant 5% return every period
- No guaranteed benefits
- Run until shareholder hits 30% cap

**Expected**:
- Shareholder share exactly 30% of total distributions
- Policyholder receives 70% + any excess from cap enforcement

### Test 2: Early Negative Returns with Recovery
**Setup**:
- Periods 1-5: -10% returns (creates SDA)
- Periods 6-20: +8% returns (repays SDA, then distributes)

**Expected**:
- SDA > 0 during periods 1-5
- SDA gradually repaid in periods 6-10
- Normal profit sharing resumes after SDA = 0
- No dividends while SDA > 0

### Test 3: Persistent Negative Returns
**Setup**:
- All periods: -5% returns
- Guaranteed benefits paid each period

**Expected**:
- SDA accumulates continuously
- Zero non-guaranteed dividends
- Shareholder bears all losses
- Asset share remains at 0

## Integration with Existing Valuation Engine

### Deterministic Mode (Special Case)
- Use 1 trial with fixed return assumptions
- ESGScenarioProvider returns constant values
- Asset share projection runs as single scenario

### Stochastic Mode
- Loop over trials (ESG scenarios)
- For each trial, project asset share using scenario-specific returns
- Aggregate results across trials (mean, percentiles)

### Output Structure
```python
{
    'trial': trial_id,
    'timestep': t,
    'asset_share': AS_t,
    'shareholder_deficit': SDA_t,
    'guaranteed_benefit': GB_t,
    'ng_dividend': Dividend_t,
    'investment_return': R_t,
    'cum_policyholder': CumPH_t,
    'cum_shareholder': CumSH_t,
}
```

## Governance Metrics

### Shareholder Deficit Frequency
```
Frequency = (# of (trial, timestep) pairs where SDA > 0) / Total observations
```

### Lifetime Shareholder Ratio
```
Ratio = CumSH_final / (CumPH_final + CumSH_final)
```

Should be ≤ 0.30 for all trials if cap is enforced.

### Dividend Coverage
```
Coverage = Total_NG_Dividends / Total_Distributable_Surplus
```

Measures what fraction of surplus is paid as policyholder dividends.

## References

- Asset share accounting: Traditional actuarial practice
- Profit sharing: Regulatory requirements for PAR products
- Deficit accounting: Solvency II guidance on shareholder support
