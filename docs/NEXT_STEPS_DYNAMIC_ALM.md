# Next Steps: Dynamic Linkage of Liability and Asset Projections

## Overview

This document outlines the implementation plan for integrating the liability-side cashflow projection with the asset-side portfolio management into a fully dynamic Asset-Liability Management (ALM) model. The current implementation has separate components that need to be linked through a consistent time-stepping framework with explicit funding and reinvestment logic.

---

## 1. Objective: Why a Dynamic ALM Model?

A dynamic ALM model creates a **closed-loop feedback system** between liabilities and assets, enabling:

- **Asset Share Evolution**: Track the true backing assets per policy/cohort as they evolve under stochastic scenarios, reflecting actual investment returns rather than assumed rates.

- **Dividend Capacity**: Calculate distributable surplus based on actual asset performance, not just liability assumptions. Non-guaranteed dividends depend on realized investment gains and losses.

- **Liquidity Management**: Ensure sufficient liquid assets to meet benefit payments and expenses. Model forced asset sales when cashflows turn negative.

- **Reinvestment Strategy**: Allocate positive net cashflows according to Strategic Asset Allocation (SAA) targets, maintaining target risk/return profiles over time.

- **TVOG Sensitivity**: Measure Time Value of Options and Guarantees by comparing stochastic asset returns against guaranteed liability floors. Capture asymmetric payoffs from minimum guarantees.

- **Regulatory Capital**: Support Solvency II, C-ROSS, or local capital calculations requiring stochastic asset-liability projections with realistic management actions.

- **ALM Strategy Testing**: Evaluate different SAA glide paths, rebalancing frequencies, and liquidity buffers under stress scenarios.

**Key Insight**: Without dynamic linkage, the model cannot capture how poor asset performance constrains dividend payments, or how large surrenders force asset liquidation at unfavorable prices.

---

## 2. Integration Architecture

### 2.1 Time Grid Alignment

**Requirement**: Single unified time grid across liability and asset projections.

- **Frequency**: Monthly timesteps (t = 0, 1, 2, ..., T_max)
- **Scenario Alignment**: Each ESG trial maps to a liability scenario trial
  - `Trial`: Scenario identifier (1 to N_trials)
  - `Timestep`: Monthly index (0 to N_timesteps)
- **Projection Horizon**: Typically 30-40 years (360-480 monthly steps) to cover policy maturities

### 2.2 Liability-Side Data Contract

**Required Outputs** (per policy or cohort, per trial, per timestep):

```python
liability_outputs = {
    'net_cashflow': float,           # Premiums - Benefits - Expenses - Dividends_Paid
    'premiums': float,               # Gross premiums received
    'guaranteed_benefits': float,    # Death + maturity + surrender (guaranteed portion)
    'expenses': float,               # Acquisition + maintenance + claim expenses
    'dividends_paid': float,         # Non-guaranteed dividends paid out (if mode='pay')
    'dividends_accumulated': float,  # Non-guaranteed dividends added to reserves (if mode='accumulate')
    'reserve_bel': float,            # Best Estimate Liability (discounted future CFs)
    'reserve_rb': float,             # Reversionary Bonus reserve
    'shareholder_deficit': float,    # Accumulated deficit if AS < 0
    'tax_liability': float,          # Corporate tax on profits (if modeled)
}
```

**Aggregation**: Sum across all policies in the fund to get fund-level net cashflow.

### 2.3 Asset-Side Data Contract

**Required Outputs** (per fund, per trial, per timestep):

```python
asset_outputs = {
    'market_value_total': float,                    # Total fund MV
    'market_value_by_asset': Dict[AssetClass, float],  # MV by Govt/Credit/Equity/Cash
    'asset_income': float,                          # Coupons + dividends received
    'realized_gains': float,                        # Gains/losses from sales
    'unrealized_gains': float,                      # Mark-to-market changes
    'portfolio_return': float,                      # Total return factor (1 + r)
    'transaction_costs': float,                     # Bid-offer + fees
    'trades': List[TradeRecord],                    # Buy/sell transactions
    'weight_drift': Dict[AssetClass, float],        # Actual weight - target weight
}
```

**ESG Linkage**: Asset returns derived from ESG scenario data (ZCB prices, equity returns, cash rates).

### 2.4 Interface Contract

```python
class DynamicALMEngine:
    def project_trial(
        self,
        trial: int,
        policies: List[Policy],
        initial_fund_assets: Dict[AssetClass, float],
        saa_schedule: SAAProvider,
        esg_provider: ESGScenarioProvider,
    ) -> ALMProjectionResult:
        """
        Project a single trial with dynamic asset-liability linkage.

        Returns
        -------
        ALMProjectionResult containing:
        - liability_df: DataFrame with liability cashflows per timestep
        - asset_df: DataFrame with asset positions per timestep
        - fund_df: DataFrame with fund-level metrics per timestep
        """
```

---

## 3. Core Dynamic Recursion (Actuarial Mathematics)

### 3.1 Per-Timestep Recursion

For each **trial** and **timestep** t, the fund evolves as:

```
Step 1: Compute liability net cashflow
    NetCF_liab(t) = Premiums(t) - GuaranteedBenefits(t) - Expenses(t) - DividendsPaid(t)

Step 2: Apply investment returns to existing assets
    MV_before_CF(t) = MV_end(t-1)
    MV_after_return(t) = MV_before_CF(t) × (1 + portfolio_return(t))

    where portfolio_return(t) = Σ [w_i(t-1) × r_i(t)]
          w_i(t-1) = weight of asset class i at end of t-1
          r_i(t) = return of asset class i from ESG scenario

Step 3: Apply net cashflow
    MV_after_CF(t) = MV_after_return(t) + NetCF_liab(t)

Step 4: Execute trading/rebalancing
    If MV_after_CF(t) < 0:
        → Liquidate all assets, create Shareholder Deficit
        → MV_end(t) = 0, SDA(t) = -MV_after_CF(t)
    Else:
        → Rebalance toward SAA targets
        → Execute trades: Trades(t) = Target_MV(t) - MV_after_CF(t)
        → Apply transaction costs
        → MV_end(t) = MV_after_CF(t) + Σ Trades(t) - TransactionCosts(t)

Step 5: Update asset share
    AS(t) = MV_end(t) / N_policies_inforce(t)

Step 6: Calculate distributable surplus
    Surplus(t) = max(0, AS(t) - Reserve_Required(t) - Buffer(t))

Step 7: Determine non-guaranteed dividends
    If SDA(t) > 0:
        → Repay SDA first, no dividends
    Else:
        → Apply profit-sharing rule (e.g., 70/30 split)
        → Enforce lifetime shareholder cap
        → Dividends(t+1) = Policyholder_Share × Surplus(t)
```

### 3.2 Detailed Asset Return Calculation

Portfolio return at timestep t depends on asset class composition:

```
Government Bonds:
    r_govt(t) = [P_zcb(t+1) / P_zcb(t)] - 1 + accrued_coupon(t)
    where P_zcb from ESG scenario for relevant tenor

Corporate Bonds:
    r_credit(t) = [P_credit(t+1) / P_credit(t)] - 1 + accrued_coupon(t)
    with credit spread adjustment from ESG

Equity:
    r_equity(t) = TotalReturn(t) from ESG
                = price_return(t) + dividend_yield(t)

Cash:
    r_cash(t) = CashTotalReturn(t) from ESG
              ≈ 1 + short_rate(t)/12
```

**Blended Return**:
```
portfolio_return(t) = Σ_i [w_i(t-1) × r_i(t)]
```

### 3.3 Funding Logic

**Negative Net Cashflow** (benefits exceed premiums):
```
If NetCF_liab(t) < 0:
    1. Use cash buffer first (up to max_cash_buffer)
    2. Sell liquid assets in order:
       a) Excess cash beyond min_cash_buffer
       b) Short-duration government bonds
       c) Credit bonds (by liquidity rating)
       d) Long-duration government bonds
       e) Equity (last resort, highest transaction cost)
    3. If insufficient assets → create SDA
```

**Positive Net Cashflow** (premiums exceed benefits):
```
If NetCF_liab(t) > 0:
    1. Repay any outstanding SDA first
    2. Invest remainder according to SAA target weights
    3. Apply transaction costs to purchases
```

---

## 4. Buy/Sell Decision Policy (Deterministic Rules)

### 4.1 Rebalancing Policy

**Frequency Options**:
- `each_step`: Rebalance every timestep (most responsive, highest costs)
- `annual`: Rebalance once per year (lower costs, higher drift)
- `threshold`: Rebalance only if weight drift exceeds tolerance (e.g., ±5%)

**Target Weights**: From SAA table, interpolated by policy year and calendar year.

### 4.2 Sell Order (Liquidity Cascade)

When raising cash to meet negative net cashflow:

```
1. Cash: Sell excess cash above min_cash_buffer (e.g., 2% of fund)
   - Transaction cost: 0 bps (no market impact)

2. Government Bonds (short tenor first):
   - Sell 1Y-3Y govt bonds (most liquid)
   - Then 3Y-7Y govt bonds
   - Then 7Y+ govt bonds (least liquid)
   - Transaction cost: 2-5 bps

3. Credit Bonds (by rating, short tenor first):
   - Sell AAA/AA first (most liquid)
   - Then A/BBB
   - Then BB/B (least liquid, higher cost)
   - Transaction cost: 5-15 bps depending on rating

4. Equity:
   - Last resort due to volatility and cost
   - Transaction cost: 10-20 bps

Constraints:
- Cannot sell more than current holdings (no shorting)
- Minimum trade size: [PLACEHOLDER: e.g., 10,000 currency units]
- If insufficient assets → liquidate all, create SDA
```

### 4.3 Buy Order (SAA-Driven)

When investing positive net cashflow:

```
1. Repay SDA: First priority if SDA > 0

2. Allocate to asset classes per SAA target weights:
   Target_MV_i = (Total_MV + NetCF) × SAA_weight_i
   Buy_Amount_i = Target_MV_i - Current_MV_i

3. Within each asset class:
   - Government: Buy target tenor (e.g., 10Y) from SAA
   - Credit: Buy target rating/tenor mix from SAA
   - Equity: Buy index tracker
   - Cash: Deposit in money market

4. Apply transaction costs:
   - Govt: 2-5 bps
   - Credit: 5-15 bps
   - Equity: 10-20 bps
   - Cash: 0 bps

5. Round to minimum trade sizes
```

### 4.4 Transaction Cost Model

```python
transaction_cost_bps = {
    'Cash': 0,
    'Govt_1Y-5Y': 2,
    'Govt_5Y-10Y': 3,
    'Govt_10Y+': 5,
    'Credit_AAA': 5,
    'Credit_AA': 7,
    'Credit_A': 10,
    'Credit_BBB': 15,
    'Credit_BB': 25,
    'Credit_B': 40,
    'Equity': 15,
}

# Apply to both buys and sells
cost = abs(trade_amount) × (transaction_cost_bps / 10000)
net_proceeds = trade_amount - cost
```

### 4.5 Constraints

- **No Shorting**: `MV_i(t) ≥ 0` for all asset classes
- **Rating Limits**: [PLACEHOLDER: e.g., max 10% in BB or below]
- **Duration Limits**: [PLACEHOLDER: e.g., average duration 5-12 years]
- **Concentration Limits**: [PLACEHOLDER: e.g., max 40% in any single asset class]
- **Liquidity Buffer**: Maintain min_cash_buffer (e.g., 2-5% of fund)

---

## 5. Starting Asset Backing (Par Fund Initial Assets)

### 5.1 Optional Initial Portfolio

The model must support two initialization modes:

**Mode A: Explicit Starting Assets** (preferred for existing funds)
```python
initial_fund_assets = {
    AssetClass.GOVT: {
        'market_value': 900_000,
        'book_value': 880_000,
        'tenor_distribution': {1: 0.1, 5: 0.3, 10: 0.4, 20: 0.2},  # weights by tenor
        'average_duration': 8.5,
    },
    AssetClass.CREDIT_A: {
        'market_value': 575_000,
        'book_value': 570_000,
        'tenor_distribution': {3: 0.2, 5: 0.5, 7: 0.3},
        'average_duration': 6.2,
    },
    AssetClass.EQUITY: {
        'market_value': 700_000,
        'book_value': 700_000,
    },
    AssetClass.CASH: {
        'market_value': 125_000,
        'book_value': 125_000,
    },
}
```

**Mode B: SAA-Based Initialization** (for new funds or greenfield)
```python
# If no initial assets provided:
initial_asset_share_per_policy = [PLACEHOLDER: e.g., 100% of first premium]
total_initial_mv = initial_asset_share_per_policy × N_policies

# Allocate according to SAA at t=0
saa_weights_t0 = saa_provider.get_saa_weights(
    product_code=product_code,
    policy_year=1,
    fund_id='PAR'
)

initial_fund_assets = {
    asset_class: total_initial_mv × weight
    for asset_class, weight in saa_weights_t0.items()
}
```

### 5.2 Mapping to ESG Instruments

**Challenge**: Initial holdings are in aggregate buckets (e.g., "Government Bonds"), but ESG provides prices for specific tenors.

**Solution**: Distribute holdings across ESG tenors using tenor_distribution weights:

```python
# Example: Govt bonds with 40% in 10Y tenor
govt_mv_total = initial_fund_assets[AssetClass.GOVT]['market_value']
tenor_weights = initial_fund_assets[AssetClass.GOVT]['tenor_distribution']

for tenor, weight in tenor_weights.items():
    mv_at_tenor = govt_mv_total × weight
    # Track this as a holding in ESG tenor bucket
    holdings[(AssetClass.GOVT, tenor)] = mv_at_tenor
```

**For Credit Bonds**: Map to rating/tenor pairs from ESG:
```python
# Example: Credit_A with 50% in 5Y tenor
credit_mv_total = initial_fund_assets[AssetClass.CREDIT_A]['market_value']
tenor_weights = initial_fund_assets[AssetClass.CREDIT_A]['tenor_distribution']

for tenor, weight in tenor_weights.items():
    mv_at_tenor = credit_mv_total × weight
    holdings[(AssetClass.CREDIT_A, tenor)] = mv_at_tenor
```

**For Equity and Cash**: Single bucket, no tenor distribution needed.

### 5.3 Book Value vs. Market Value

- **Market Value (MV)**: Used for all return calculations and trading decisions
- **Book Value (BV)**: Optional, for accounting reconciliation
- **Unrealized Gains**: `UG(t) = MV(t) - BV(t)`

If BV not provided, initialize `BV(0) = MV(0)`.

---

## 6. Validation and Controls

### 6.1 Reconciliation Checks

**Asset Share Roll-Forward**:
```
AS(t) = AS(t-1) + Premiums(t) - Benefits(t) - Expenses(t)
        + Investment_Income(t) + Realized_Gains(t) + Unrealized_Gains(t)
        - Transaction_Costs(t) - Dividends_Paid(t)

Check: |AS(t)_calculated - AS(t)_from_recursion| < tolerance (e.g., 0.01)
```

**Cashflow Identity**:
```
Sources = Premiums(t) + Asset_Sales(t) + Asset_Income(t)
Uses = Benefits(t) + Expenses(t) + Dividends_Paid(t) + Asset_Purchases(t) + Transaction_Costs(t)

Check: |Sources - Uses - ΔCash(t)| < tolerance
```

**Weight Drift vs. SAA**:
```
For each asset class i:
    weight_drift_i(t) = actual_weight_i(t) - target_weight_i(t)

Check: max(|weight_drift_i(t)|) < drift_tolerance (e.g., 5%)
       unless rebalancing is disabled or threshold not met
```

**Rebalancing Frequency**:
```
If rebalance_frequency = 'annual':
    Check: Trades only occur at t = 12, 24, 36, ... (annual timesteps)

If rebalance_frequency = 'threshold':
    Check: Trades only occur when max(|weight_drift|) > threshold
```

### 6.2 Scenario-by-Scenario Sanity Checks

**No Negative MV** (unless SDA allowed):
```
For each trial, timestep:
    If SDA_allowed:
        Check: MV(t) ≥ 0 OR SDA(t) > 0
    Else:
        Check: MV(t) ≥ 0 (strict)
```

**Monotonic Behavior in Simple Tests**:
```
Test 1: Constant positive premiums, no benefits
    → Expect: MV(t) increases monotonically

Test 2: Constant negative net cashflow (benefits > premiums)
    → Expect: MV(t) decreases, eventually → 0 or SDA created

Test 3: Zero net cashflow, positive returns
    → Expect: MV(t) grows at portfolio_return rate
```

**Dividend Capacity**:
```
Check: Dividends_Paid(t) ≤ Distributable_Surplus(t)
Check: Cumulative_Shareholder_Share ≤ 0.30 × Cumulative_Total_Surplus (lifetime cap)
```

### 6.3 Back-Testing and Cross-Checks

**Compare with Deterministic Run**:
```
Run single trial with deterministic returns (e.g., fixed 5% p.a.)
Compare with existing deterministic valuation engine
Check: NPV of cashflows within 1-2% tolerance
```

**Consultant Tool Reconciliation**:
```
If available, compare key metrics with Prophet/ALS/MoSes outputs:
- Best Estimate Liability (BEL)
- Asset share at key durations (5Y, 10Y, 20Y)
- Dividend payout ratios
- TVOG estimates
```

**Stress Testing**:
```
Run extreme scenarios:
- Equity crash (-50% in year 1)
- Interest rate spike (+300 bps)
- Mass lapse event (20% surrender in year 1)

Check: Model handles gracefully (no crashes, SDA created if needed)
```

---

## 7. Deliverables and Implementation Plan

### 7.1 Concrete Deliverables

**Module 1: Dynamic ALM Engine**
```
File: par_model_v2/valuation/dynamic_alm.py

Classes:
- DynamicALMEngine: Main projection engine
- ALMState: State container for trial/timestep
- ALMProjectionResult: Output container

Key Methods:
- project_trial(trial, policies, initial_assets, saa, esg)
- project_portfolio(policies, initial_assets, saa, esg, n_trials)
- validate_projection(result) → ValidationReport
```

**Module 2: Standardized Input Schemas**
```
File: par_model_v2/schemas/alm_inputs.py

Schemas:
- LiabilityCashflowSchema: Validate liability outputs
- InitialFundAssetsSchema: Validate starting assets
- SAAScheduleSchema: Validate SAA table
- TradingPolicySchema: Validate trading rules
```

**Module 3: Output Dataset**
```
File: par_model_v2/outputs/alm_outputs.py

DataFrames:
- fund_history_df: Per trial/timestep fund metrics
  Columns: Trial, Timestep, MV_total, MV_Govt, MV_Credit, MV_Equity, MV_Cash,
           portfolio_return, net_cashflow, realized_gains, transaction_costs,
           weight_drift_max, SDA, dividends_paid

- trade_history_df: Per trial/timestep trade records
  Columns: Trial, Timestep, AssetClass, TradeAmount, TransactionCost, Reason

- reconciliation_df: Per trial/timestep reconciliation checks
  Columns: Trial, Timestep, AS_rollforward_error, cashflow_identity_error,
           weight_drift_max, validation_status
```

**Module 4: Validation Suite**
```
File: tests/test_dynamic_alm.py

Test Cases:
- test_positive_cashflow_growth()
- test_negative_cashflow_liquidation()
- test_sda_creation_and_repayment()
- test_rebalancing_frequency()
- test_transaction_costs()
- test_saa_glide_path()
- test_initial_assets_mapping()
- test_reconciliation_checks()
```

**Documentation**
```
Files:
- docs/DYNAMIC_ALM_METHODOLOGY.md: Mathematical specification
- docs/DYNAMIC_ALM_USER_GUIDE.md: Usage examples and configuration
- docs/DYNAMIC_ALM_VALIDATION.md: Validation approach and test results
```

### 7.2 Implementation Milestones

**Milestone 1: MVP (Minimum Viable Product)** [Target: 2-3 weeks]
```
Scope:
- Single trial projection with fixed SAA
- Simple rebalancing (each_step frequency)
- Basic transaction costs (flat bps)
- No initial assets (start from zero)
- Validation: reconciliation checks only

Deliverables:
- DynamicALMEngine class (core recursion)
- Integration with existing ESGScenarioProvider
- Basic output DataFrames
- Unit tests for simple scenarios

Success Criteria:
- Runs 1 trial × 360 timesteps without errors
- Passes reconciliation checks
- Produces sensible MV evolution
```

**Milestone 2: Full Features** [Target: 4-6 weeks]
```
Scope:
- Multi-trial projection (100+ trials)
- Time-varying SAA (glide path)
- Multiple rebalancing frequencies
- Detailed transaction cost model
- Initial assets support (Mode A and B)
- SDA creation and repayment
- Dividend calculation with lifetime cap

Deliverables:
- Complete DynamicALMEngine with all features
- Standardized input schemas
- Full output datasets (fund, trade, reconciliation)
- Comprehensive test suite
- User guide documentation

Success Criteria:
- Runs 100 trials × 360 timesteps in < 10 minutes
- Passes all validation checks
- Matches deterministic run within 2% tolerance
```

**Milestone 3: Enhancements** [Target: 6-8 weeks]
```
Scope:
- Parallel processing (multi-core)
- Advanced trading rules (threshold rebalancing, cash ladders)
- Tax modeling (corporate tax on profits)
- Regulatory capital outputs (SCR, C-ROSS)
- TVOG calculation (stochastic vs. deterministic)
- Sensitivity analysis (SAA, rebalancing frequency, transaction costs)
- Back-testing against consultant tools

Deliverables:
- Optimized engine with parallelization
- Extended output metrics (TVOG, capital)
- Sensitivity analysis scripts
- Validation report vs. consultant tools
- Methodology documentation

Success Criteria:
- Runs 1000 trials × 360 timesteps in < 30 minutes
- TVOG estimates within 5% of consultant tools
- Passes all regulatory validation checks
```

### 7.3 Dependencies and Prerequisites

**Technical Dependencies**:
- ✅ ESGScenarioProvider (completed)
- ✅ AssetShareProjector (completed)
- ✅ FundPortfolio (completed)
- ✅ SAAProvider (completed)
- ⏳ Integration layer (to be built)

**Data Dependencies**:
- ✅ ESG scenario files (sample generator available)
- ✅ SAA table (sample generator available)
- ✅ Initial fund assets table (sample generator available)
- ⏳ Liability cashflow outputs (need to expose from deterministic engine)

**Validation Dependencies**:
- ⏳ Deterministic valuation baseline (for comparison)
- ⏳ Consultant tool outputs (if available, for back-testing)
- ⏳ Regulatory capital calculation framework (for SCR/C-ROSS)

### 7.4 Risk Mitigation

**Risk 1: Performance (large trial counts)**
```
Mitigation:
- Implement chunked processing (e.g., 100 trials per chunk)
- Use multiprocessing.Pool for parallel trials
- Profile and optimize hot paths (NumPy vectorization)
- Consider Numba JIT compilation for core loops
```

**Risk 2: Numerical Stability**
```
Mitigation:
- Use relative tolerances for reconciliation checks
- Floor asset values at small positive (e.g., 1e-6)
- Validate intermediate calculations at each timestep
- Add debug logging for extreme scenarios
```

**Risk 3: Data Quality**
```
Mitigation:
- Implement input validation schemas (Pydantic)
- Add sanity checks on ESG data (no negative prices, returns in reasonable range)
- Validate SAA weights sum to 1.0
- Check initial assets map to ESG instruments
```

**Risk 4: Complexity Creep**
```
Mitigation:
- Stick to MVP scope first, defer enhancements
- Use feature flags for optional components
- Maintain clear separation of concerns (liability/asset/integration)
- Document assumptions and limitations clearly
```

---

## Summary

This implementation plan provides a **concrete roadmap** for integrating liability and asset projections into a dynamic ALM model. The key steps are:

1. **Build the integration layer** that links liability net cashflows with asset portfolio evolution
2. **Implement the core recursion** with explicit funding and reinvestment logic
3. **Add deterministic trading rules** for buy/sell decisions and rebalancing
4. **Support initial asset backing** with flexible initialization modes
5. **Validate rigorously** with reconciliation checks and back-testing
6. **Deliver in milestones** starting with MVP, then full features, then enhancements

The resulting dynamic ALM engine will enable **realistic stochastic projections** of asset share evolution, dividend capacity, and TVOG under ESG scenarios, supporting regulatory capital calculations and ALM strategy optimization.
