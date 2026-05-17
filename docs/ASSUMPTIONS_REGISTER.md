# Assumptions Register — PAR Fund Stochastic ALM & TVOG Model
**Date:** 2026-05-17  
**Prepared by:** Claude Actuarial Agent (Automated Cycle 2)  
**Phase:** 1 — Model Review & Documentation  
**Task:** Document all model assumptions and parameters  
**Version:** 1.0  

---

## 1. Purpose & Scope

This register documents all actuarial assumptions used in the PAR Fund Stochastic ALM & TVOG model. It covers eight assumption tables, their data structure, current values, interpolation methodology, known limitations, and alignment status against SOA (ASOP 25, ASOP 56) and IA (TAS M, TAS R) requirements.

**Products in scope:** Whole Life (WL), Pension (PEN)  
**Currency:** CNY  
**Projection horizon:** Up to 130 years (full runoff)

---

## 2. Assumption Summary Table

| # | Table | File | Rows | Dimensions | Status | SOA/IA Gaps |
|---|-------|------|------|-----------|--------|-------------|
| A | Mortality (base) | `mortality_qx.csv` | 15,552 | 10-dim | ✅ Loaded | Basis undocumented |
| B | Mortality (enhanced) | `mortality_qx_enhanced.csv` | 78 | 6-dim | ✅ Loaded | Active table per metadata |
| C | Lapse (base) | `lapse.csv` | 9,504 | 10-dim | ✅ Loaded | Basis undocumented |
| D | Lapse (enhanced) | `lapse_enhanced.csv` | 112 | 5-dim | ✅ Loaded | Active table per metadata |
| E | Expenses | `expenses.csv` | 198 | 6-dim | ✅ Loaded | Inflation indexation absent |
| F | Expenses (enhanced) | `expenses_enhanced.csv` | 56 | 5-dim | ✅ Loaded | Active table per metadata |
| G | Bonus Rates | `bonus_rates.csv` | 24 | 4-dim | ✅ Loaded | Discretionary basis undocumented |
| H | Reversionary Bonus (RB) | `bonus_rb.csv` | 66 | 3-dim | ✅ Loaded | No upper/lower bound rationale |
| I | Discount Curve | `discount_curve.csv` | 131 | 2-dim | ✅ Loaded | No market calibration evidence |
| J | Investment Return | `investment_return.csv` | 131 | 2-dim | ✅ Loaded | Deterministic only; no spread assumptions |
| K | Strategic Asset Allocation | `strategic_asset_allocation.csv` | 28 | 6-dim | ✅ Loaded | Glide path basis undocumented |
| L | Initial Fund Assets | `initial_fund_assets.csv` | 4 | 7-dim | ✅ Loaded | Single valuation date; no history |

---

## 3. Detailed Assumption Documentation

---

### A/B. Mortality

**Active table:** `mortality_qx_enhanced.csv` (per `metadata.json`)  
**Base table:** `mortality_qx.csv` (legacy, more granular; retained for backward compatibility)

#### Base Table Structure (`mortality_qx.csv`)
| Dimension | Values | Notes |
|-----------|--------|-------|
| `table_id` | `MORT_BASE_V1_T1` | Single base table |
| `product_code` | WL, PEN | Whole Life and Pension |
| `issue_year` | 2020 | Single cohort; no issue year gradient |
| `gender` | M, F, ALL | Male, Female, Gender-blended |
| `uw_class` | STD, ALL | Standard underwriting class only |
| `policy_status` | INFORCE | No NTU or claim-in-progress splits |
| `sa_band` | SA_0_100K, SA_100K_500K, SA_500K_plus | Sum assured banding |
| `prem_band` | PREM_0_10K, PREM_10K_50K, PREM_50K_plus | Premium banding |
| `attained_age` | 20–100 | Full range; no rates above 100 |
| `qx_annual` | 0.000115 (age 20, M) – omega at 100 | Annual probability of death |

#### Enhanced Table Structure (`mortality_qx_enhanced.csv`)
| Dimension | Values |
|-----------|--------|
| `product` | WL |
| `gender` | M |
| `age` | 25–(inferred from row count) |
| `smoker_status` | N (non-smoker) — no smoker loading present |
| `policy_year` | 1–n |
| `qx` | Values around 0.0005 at age 25 |

#### Key Values
- **Male, age 40, standard class:** qx ≈ 0.000421–0.000570 (mean 0.000495)
- **Female differential:** Female qx materially lower (typical ~60–70% of male rates)
- **Omega:** Age 100 (model assumes certain death; no rates beyond 100)

#### Interpolation
- Method: `linear` (per metadata)
- Extrapolation: `constant` (rates capped at boundary values)

#### **Documented Gaps**
1. **Assumption basis undocumented** — No reference to China Life Experience Study (CLES), China Life Tables (CLT 2010–2013), or equivalent published mortality study. SOA ASOP 25 requires the actuary to disclose the source and credibility of experience data.
2. **No mortality improvement factors** — Static table; no allowance for future longevity improvements (e.g., no CMI-style projection or Society of Actuaries improvement scales).
3. **Single underwriting class** — Only `STD` present. No preferred/substandard splits despite being technically available as a dimension.
4. **No COVID or pandemic shock** — No stressed mortality table for scenario testing.
5. **Smoker loading absent in enhanced table** — `smoker_status=N` only; base table has this dimension but no smoker rates populated.

---

### C/D. Lapse (Voluntary Discontinuance)

**Active table:** `lapse_enhanced.csv` (per metadata)  
**Base table:** `lapse.csv` (full multi-dimensional, 9,504 rows; used for production runs)

#### Base Table Structure (`lapse.csv`)
| Dimension | Values |
|-----------|--------|
| `product_code` | WL, PEN |
| `issue_year` | 2020 |
| `gender` | M, F, ALL |
| `uw_class` | STD, ALL |
| `policy_status` | INFORCE |
| `sa_band` | SA_0_100K, SA_100K_500K, SA_500K_plus |
| `prem_band` | PREM_0_10K, PREM_10K_50K, PREM_50K_plus |
| `policy_year` | 1–130 |
| `lapse_annual` | Annual lapse rate |

#### Key Values
| Policy Year | Mean Rate | Range |
|-------------|-----------|-------|
| 1 | 10.6% | 5.25% – 15.0% |
| 2 | ~10% | varies by band |
| 4 | ~6% | varies |
| 10 | 2.8% | 1.4% – 4.0% |
| 20+ | <2% | grading down |

#### Pattern
First-year lapse is highest (up to 15%), grading down materially over the first 5 years — consistent with typical par product experience where early lapsers incur surrender charges. Rates grade to under 2% by policy year 10 and continue declining to near-zero by year 20+, which is standard for long-term participating business.

#### Interpolation
- Method: `step` (no interpolation between banded values)
- Extrapolation: `constant`

#### **Documented Gaps**
1. **Basis undocumented** — No reference to company experience study, CBIRC industry data, or actuarial judgment rationale.
2. **No dynamic lapse adjustment** — Lapse rates are static; no market-adjusted lapse function tied to credited rate vs. market rate spread (a material omission for TVOG, where the option to lapse is a key policyholder behaviour assumption).
3. **No mass lapse scenario** — No stressed lapse assumption for scenario testing (e.g., 200% of base lapse in year 1).
4. **Single cohort (2020)** — No differentiation by issue year cohort effects.

---

### E/F. Expenses

**Active table:** `expenses_enhanced.csv` (per metadata)  
**Base table:** `expenses.csv` (198 rows, policy years 1–10 only)

#### Base Table Structure (`expenses.csv`)
| Dimension | Values |
|-----------|--------|
| `product_code` | WL, PEN |
| `policy_status` | INFORCE |
| `policy_year` | 1–10 (base); 1–n (enhanced) |
| `expense_fixed_monthly` | CNY/month per policy |
| `expense_pct_premium` | % of annual premium |

#### Key Values
| Table | Product | Year 1 Fixed (monthly) | Year 1 % Premium | Year 2+ Fixed | Year 2+ % Premium |
|-------|---------|----------------------|-----------------|---------------|------------------|
| T1 | WL | CNY 41.67 | 10.0% | CNY 12.50 | 5.0% |
| T2 | WL | CNY 20.83 | 0.0% | (varies) | — |
| T3 | WL | CNY 41.67 | 10.0% | (varies) | — |
| T4 | PEN | CNY 33.33 | 8.0% | (varies) | — |
| T5 | PEN | CNY 16.67 | 0.0% | — | — |

**Interpretation:** Year 1 expenses are approximately 3× maintenance expenses, consistent with standard actuarial treatment of acquisition costs being front-loaded in year 1.

#### Enhanced Table (`expenses_enhanced.csv`)
Provides acquisition vs. renewal cost split by premium band (0–10K, 10K–50K), enabling more granular expense analysis.

#### **Documented Gaps**
1. **No inflation indexation** — Expense table is in nominal CNY with no inflation escalation. Projections beyond 10 years using flat nominal expenses will understate real expense burdens.
2. **Base table covers only policy years 1–10** — Maintenance expenses for years 11+ require extrapolation; the `constant` extrapolation method is applied (year 10 expense level repeated indefinitely).
3. **No unit cost vs. overhead split** — Cannot separately shock direct vs. allocated overhead for sensitivity analysis.
4. **No claim handling expenses** — Expenses on death, surrender, and maturity events not captured.

---

### G. Reversionary Bonus Rates (Declared)

**File:** `bonus_rates.csv` (24 rows)

#### Structure
| Dimension | Values |
|-----------|--------|
| `product` | WL, Pension |
| `policy_year` | 1, 2, 3, 5, 10, 15, 20 |
| `fund_type` | PAR, NPAR |
| `bonus_rate` | Annual reversionary bonus rate |

#### Key Values
| Product | Year 1 | Year 3 | Year 10+ |
|---------|--------|--------|---------|
| WL PAR | 2.5% | 3.0% | 3.5% |
| Pension PAR | 3.0% | 3.5% | 4.0% |
| NPAR (all) | 0.0% | 0.0% | 0.0% |

Bonus rates grade up from year 1 to year 10, then remain flat — consistent with discretionary bonus smoothing where the fund builds reserves before stabilising declared rates.

#### **Documented Gaps**
1. **Discretionary basis undocumented** — These are declared (not earned) bonus rates. No documentation of the smoothing philosophy, bonus reserve target, or surplus distribution policy.
2. **No dynamic bonus linkage** — Rates are independent of ESG scenario. In a stochastic model, declared bonus should ideally be a function of investment return surplus per scenario — this linkage does not currently exist.
3. **No terminal bonus schedule** — Terminal bonus (payable on claim or surrender) has a calculation mechanism in the asset share engine (`terminal_bonus_factor = 0.5`) but no scenario-specific table.

---

### H. Reversionary Bonus Growth (RB)

**File:** `bonus_rb.csv` (66 rows)

#### Structure
| Dimension | Values |
|-----------|--------|
| `table_id` | `BONUS_BASE_V1_T1` |
| `product_code` | WL |
| `policy_year` | 1–130 |
| `rb_growth_annual` | Annual RB growth rate |

#### Key Values
- Year 1: 3.0% (PAR), 0.0% (NPAR)
- Year 20: 2.0% (PAR), 0.0% (NPAR)
- Pattern: Grading down from 3.0% to 2.0% over the projection — consistent with reducing investment expectations over time

---

### I. Discount Curve

**File:** `discount_curve.csv` (131 rows, tenors 0–130Y)

#### Structure and Values
| Tenor | Zero Rate |
|-------|----------|
| 0Y | 2.5% |
| 1Y | 2.8% |
| 2Y | 3.2% |
| 3Y | 3.4% |
| 5Y+ | ~5.0% (flat beyond ~5 years) |
| 130Y | 5.0% |

The curve is upward-sloping from 2.5% (short end) to 5.0% (long end, flat), with a sharp jump in the first 3 years. This shape is consistent with a stylised CNY risk-free curve, but the long-end flat rate of 5.0% is high relative to observed CNY government bond yields in 2024–2026 (~2.2–3.5% for 10–30Y tenors).

#### **Documented Gaps**
1. **No market calibration** — Curve is not calibrated to observable market rates. For IFRS 17 or CBIRC liability valuation, the discount curve must reference liquid market rates (e.g., China Government Bond yield curve).
2. **Long-end rate appears overstated** — 5.0% flat beyond 5 years may overstate the risk-free rate relative to current market levels, understating liability values.
3. **No credit spread adjustment** — No illiquidity premium or credit spread overlay documented.
4. **Static curve** — Single deterministic curve; no scenario-specific discount curves linked to ESG paths.

---

### J. Investment Return Assumption

**File:** `investment_return.csv` (131 rows, tenors 0–130Y)

#### Values
| Tenor Band | Return Rate |
|-----------|------------|
| 0–5 years | 4.5% p.a. |
| 6–10 years | 5.0% p.a. |
| 11Y+ | Grades up to ~6.0% |

#### **Documented Gaps**
1. **Deterministic assumption used alongside stochastic ESG** — The investment return table is a deterministic assumption. In the stochastic model, ESG-driven returns should supersede this table for scenario-dependent projections. The relationship between this table and the ESG adapter is not documented.
2. **No asset-class breakdown** — Single blended return; no separate returns by government, credit, equity, or cash (these are captured in the ESG adapter for stochastic runs but not reflected here).
3. **Return rate likely overstated** — 4.5–6.0% returns in current CNY low-yield environment require justification.

---

### K. Strategic Asset Allocation (SAA)

**File:** `strategic_asset_allocation.csv` (28 rows)

#### SAA Glide Path (PAR Fund)
| Policy Year | Govt | Credit_A | Equity | Cash |
|-------------|------|---------|--------|------|
| 1 | 35% | 25% | 35% | 5% |
| 10 | 40% | 25% | 30% | 5% |
| 15 | 50% | 25% | 20% | 5% |
| 25 | 55% | 25% | 15% | 5% |
| 30 | 55% | 25% | 15% | 5% |

**Pattern:** Classic liability-driven glide path — equity allocation reduces from 35% to 15% as the fund matures, replaced by government bonds (35% → 55%). Credit and cash remain broadly stable. This is consistent with regulatory practice for CNY par funds under CBIRC supervision.

#### **Documented Gaps**
1. **Single product (ALL)** — No differentiation by product type. WL and Pension typically have different liability durations and therefore different optimal SAAs.
2. **Glide path basis undocumented** — No ALM optimisation study or regulatory rationale cited.
3. **Credit quality fixed at 'A'** — No differentiation between AAA/AA/A in target weights despite transaction cost model distinguishing all rating classes.
4. **No stressed SAA** — No alternative SAA for downside scenarios.

---

### L. Initial Fund Assets

**File:** `initial_fund_assets.csv` (4 rows)  
**Valuation date:** 2024-01-01

#### Composition
| Asset Class | Market Value (CNY) | Book Value (CNY) | Weight | Duration |
|-------------|-------------------|-----------------|--------|---------|
| Govt | 900,000 | 880,000 | 39.1% | 8.5Y |
| Credit_A | 575,000 | 570,000 | 25.0% | 6.2Y |
| Equity | 700,000 | 700,000 | 30.4% | 0.0 |
| Cash | 125,000 | 125,000 | 5.4% | 0.0 |
| **Total** | **2,300,000** | **2,275,000** | **100%** | ~5.3Y |

**Unrealised gain:** CNY 25,000 (1.1% of market value) — modest and consistent with a rising-rate environment compressing bond prices.

**Fund leverage:** None (MV ≈ BV). No derivatives or off-balance-sheet items.

**SOA Alignment:** Asset mix at valuation date is close to SAA year-1 targets (Govt 39% vs 35% target; Equity 30% vs 35% target) — slight under-allocation to equity, over-allocation to government bonds.

#### **Documented Gaps**
1. **Single snapshot** — No historical series; no trend information.
2. **Total fund size of CNY 2.3M is very small** — Likely a representative or test portfolio, not a real inforce book. Real-scale validation not possible without actual inforce data (`data/inforce/` is empty).
3. **No individual bond details** — Govt and credit holdings are aggregated; no CUSIP/ISIN or maturity profile for cashflow matching analysis.

---

## 4. Cross-Assumption Consistency Checks

| Check | Result | Notes |
|-------|--------|-------|
| Lapse + mortality ≤ 1 at all durations | ✅ Pass | Year 1: ~10.6% lapse + <0.1% mortality; safe margin |
| Discount rate < investment return (positive spread) | ✅ Pass | Short-end: 4.5% investment vs 2.5% discount = +200bps spread |
| Initial fund SAA vs target SAA drift | ⚠️ Minor | Equity 30% actual vs 35% target; within 5pp tolerance |
| Bonus rate < investment return (viable par fund) | ✅ Pass | 2.5–3.5% bonus vs 4.5–6% investment return |
| Terminal bonus factor < 1 | ✅ Pass | 0.5 (50%) hardcoded in AssetShareConfig |
| Profit split sums to 100% | ✅ Pass | 70% policyholder + 30% shareholder = 100% |

---

## 5. Assumption Change Control

**Current status:** No formal change control process is in place. All assumptions reside as raw CSV files in `data/assumptions/` with no version history, approval workflow, or sign-off record.

**Required per IA TAS M / SOA ASOP 56:**
- Assumption changes must be documented with the rationale and effective date
- Material changes require actuarial sign-off
- Experience monitoring should trigger assumption reviews at defined intervals

**Recommended actions (Phase 2):**
1. Add `assumption_version` and `effective_date` fields to all assumption tables
2. Create an `ASSUMPTION_CHANGE_LOG.md` documenting any future changes
3. Add assumption-level metadata to `metadata.json` (basis, last review date, reviewer)

---

## 6. Priority Remediation Items

| Priority | Issue | Impact | Phase |
|----------|-------|--------|-------|
| 1 | Discount curve long-end rate (5.0%) likely overstated | Understates liability PV | Phase 2 |
| 2 | Dynamic lapse function absent | Material TVOG mispricing | Phase 4 |
| 3 | No mortality improvement factors | Longevity risk understated | Phase 2 |
| 4 | Deterministic bonus rates not linked to ESG | TVOG not scenario-dependent | Phase 4 |
| 5 | Expense inflation not modelled | Long-horizon projections biased | Phase 3 |
| 6 | No assumption change control process | Governance/audit failure | Phase 2 |

---

*Generated by automated cycle — Cycle 2, Phase 1*  
*Next task: Identify deviations from SOA stochastic modeling standards*
