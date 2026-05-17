# Model Audit Report — PAR Fund Stochastic ALM & TVOG Model
**Date:** 2026-05-17  
**Auditor:** Claude Actuarial Agent (Automated Cycle 1)  
**Phase:** 1 — Model Review & Documentation  
**Task:** Audit current model code and architecture  

---

## 1. Executive Summary

The repository implements a Python-based stochastic Asset-Liability Management (ALM) and Time Value of Guarantees (TVOG) framework for participating (par) insurance products denominated in CNY. The codebase is substantially built, with 8 functional modules, 4 test suites, and supporting data assumptions. However, 8 of 67 unit tests fail, the external ESG input file is missing (causing all 20 liability checkpoints to fail), and several material gaps exist relative to SOA and IA standards.

**Overall readiness:** Pre-production. Suitable for research use; requires material remediation before regulatory use.

---

## 2. Repository Structure

```
par_model_v2/
├── esg/              # ESG scenario adapter (Moody's CNY format)
├── assets/           # Asset cashflows, par fund projection, portfolio
├── liabilities/      # Deterministic liability cashflow projection
├── model_points/     # Synthetic MP generation and grouping
├── assumptions/      # Multi-dimensional assumption provider
├── valuation/        # Dynamic ALM, asset share engine, distributed executor
├── grid/             # Hybrid monthly/annual time grid
└── utils/            # Resource monitoring, memory profiling

data/
├── assumptions/      # 7 CSV assumption tables + metadata.json
├── inforce/          # (empty — no inforce data present)
└── liability_results/ # 20 .failed checkpoints (no ESG input file)

tests/               # 4 test files, 67 tests, 59 pass / 8 fail
docs/                # 14 implementation documentation files
scripts/             # 7 run/example scripts
```

**Version inconsistency (CRITICAL):** `__version__.py` reports `0.1.0`; `__init__.py` reports `2.0.0`. These must be reconciled before any release.

---

## 3. Module-by-Module Assessment

### 3.1 ESG Module (`par_model_v2/esg/`)
- **Design:** Thin adapter over Moody's ESG CSV outputs in long-table format (Trial × Timestep)
- **Assets supported:** Government ZCBs (up to 60Y tenor), Credit ZCBs (7 ratings: AAA–CCC), Equity total return + dividend yield, Cash total return
- **Column convention:** `ESG.Economies.CNY.NominalZCBP(Rating, Tenor, 3)` — CNY-specific, hardcoded
- **Gap:** No internal ESG generator — relies entirely on external Moody's file. No ESG file is included in the repository, rendering all scenario-dependent modules non-runnable without external data.
- **Gap (SOA):** Stochastic process underlying ESG paths (GBM? Vasicek? multi-factor?) is not documented. SOA ASOP 56 requires explicit documentation of stochastic model assumptions.

### 3.2 Asset Module (`par_model_v2/assets/`)
- **Design:** Holdings-based portfolio with government bonds, credit bonds, equity, and cash
- **Dynamic ALM:** Buy/sell rules with priority-based liquidation; rebalancing to SAA targets; transaction cost modeling (2–60 bps by asset class and rating)
- **Par Fund:** Stochastic surplus calculation; 70/30 policyholder/shareholder profit sharing; bonus smoothing (exponential or target-bonus)
- **Test failure (`test_rebalancing_to_saa`):** When initial allocation is 100% cash and SAA target is 10% cash, the engine fails to rebalance — cash weight remains at 1.0. This indicates the rebalancing logic does not trigger purchase transactions when cash is above target.
- **Gap:** No duration/convexity management. No interest rate sensitivity (DV01) metrics.

### 3.3 Liability Module (`par_model_v2/liabilities/`)
- **Design:** Deterministic liability cashflow projection (premiums, benefits, expenses)
- **Gap (MATERIAL):** Liabilities are deterministic only. TVOG requires stochastic liability projection to compute the present value of embedded options and guarantees under different scenarios. The project name indicates TVOG as a primary output, but stochastic liability projection is not implemented.

### 3.4 Valuation Module (`par_model_v2/valuation/`)
- **Asset Share Engine:** Policy-level projection with 70/30 profit sharing, shareholder deficit account (SDA), reversionary and terminal bonus calculation
- **Distributed Executor:** Multiprocessing-based chunk executor with checkpoint/restart support
- **Test failures (3 distributed, 4 scalability):** `Can't pickle local object` error — the distributed executor cannot serialize locally-defined functions passed as `process_func`. This is a fundamental Python multiprocessing constraint; the functions must be defined at module top level.
- **Gap:** TVOG is described in README but no dedicated TVOG calculation module exists. Asset share engine computes surplus but does not produce a TVOG output (PV of guarantees under stochastic scenarios).

### 3.5 Assumptions Module (`par_model_v2/assumptions/`)
- **Design:** Multi-dimensional table-driven approach with hierarchical lookup and fallback; supports mortality, lapse, expenses, bonus rates, discount curve, SAA, initial fund assets
- **Assessment:** Well-structured. Metadata-driven dimension specification. Enhanced tables (e.g., `mortality_qx_enhanced.csv`) present alongside base tables.
- **Gap:** No sensitivity/shock assumption tables for stress testing. No documentation of assumption basis (e.g., which mortality table — China Life Experience Study? CMI?).

### 3.6 Model Points (`par_model_v2/model_points/`)
- **Design:** Synthetic model point generator and grouping logic
- **Gap:** No real inforce data (`data/inforce/` directory is empty). Validation against a real portfolio is not possible without this.

---

## 4. Test Suite Results

| Test File | Tests | Passed | Failed | Key Issues |
|-----------|-------|--------|--------|------------|
| test_dynamic_alm.py | 11 | 10 | 1 | Rebalancing logic (cash→SAA) |
| test_flexible_assumptions.py | 21 | 21 | 0 | — |
| test_integration_e2e.py | 28 | 24 | 4 | Distributed pickling failure |
| test_distributed_processing.py | 7 | 4 | 3 | Distributed pickling failure |
| **TOTAL** | **67** | **59** | **8** | |

**Pass rate: 88%** — above threshold for development work; not acceptable for production use.

---

## 5. Gaps vs Industry Standards

### 5.1 SOA Standards (ASOP 56 — Modeling; ASOP 25 — Credibility)
| Requirement | Status | Notes |
|-------------|--------|-------|
| Stochastic process documentation | ❌ Missing | ESG process type/parameters not documented in code |
| Parameter calibration methodology | ❌ Missing | No calibration scripts or documentation |
| Model validation framework | ⚠️ Partial | Unit tests exist; no independent validation |
| Sensitivity analysis | ❌ Missing | No sensitivity runner; no parameter shock framework |
| Model governance & audit trail | ❌ Missing | No model change log; no approval workflow |
| User documentation | ⚠️ Partial | Scripts exist; no end-user guide |

### 5.2 IA Requirements (IFoA TAS M / TAS R)
| Requirement | Status | Notes |
|-------------|--------|-------|
| Assumptions documentation | ⚠️ Partial | metadata.json covers structure; basis not documented |
| Model limitations disclosure | ❌ Missing | No model risk card or known limitations document |
| Audit trail | ❌ Missing | No MODEL_DEV_LOG.md; remediated this cycle |
| Peer review evidence | ❌ Missing | No review records in repository |
| Run-off/liability uncertainty | ❌ Missing | Stochastic liability projection absent |

### 5.3 ERM / Risk Framework
| Requirement | Status | Notes |
|-------------|--------|-------|
| VaR metrics | ❌ Missing | No tail risk computation |
| Expected Shortfall (ES) | ❌ Missing | No CVaR/ES implementation |
| Stress testing framework | ❌ Missing | No scenario shock runner |
| Model risk disclosure | ❌ Missing | No model risk card |
| Backtesting framework | ❌ Missing | No historical calibration pipeline |

---

## 6. Critical Issues Requiring Remediation (Priority Order)

1. **No ESG input file** — All scenario-dependent computation is blocked. Repository requires either a bundled sample ESG generator or clear documentation on how to obtain/generate the Moody's input file.

2. **Distributed executor pickling bug** — Local function passed to multiprocessing cannot be pickled. Fix: move `process_func` to module-level or use `functools.partial` with a module-level function.

3. **ALM rebalancing bug** — When portfolio is 100% cash, buy transactions are not triggered. Fix: ensure rebalancing logic handles the case where all assets must be purchased from cash.

4. **Version inconsistency** — Reconcile `__version__.py` (0.1.0) vs `__init__.py` (2.0.0).

5. **TVOG module absent** — Core deliverable of the model is not implemented. Requires stochastic liability projection + PV of guarantee options under each ESG trial.

6. **No tail risk metrics** — VaR and ES at 95%/99.5% confidence levels required for ERM and Solvency II/CBIRC-equivalent reporting.

---

## 7. Recommended Development Sequence

The remaining Phase 1 tasks address documentation and assumption gaps. Phases 2–4 address the structural gaps above:

- **Phase 2:** Add SOA stochastic process documentation; implement VaR/ES; fix distributed executor; add governance framework
- **Phase 3:** Fix rebalancing bug; build validation suite; add stress testing
- **Phase 4:** Implement simple ESG generator (GBM-based) to replace Moody's dependency; implement TVOG computation; calibrate to reference data
- **Phase 5:** Model risk card; deployment checklist; final review

---

## 8. Conclusion

The model has a solid architectural foundation for a stochastic ALM framework. The ESG adapter, dynamic ALM engine, assumption provider, and asset share engine are well-structured and largely tested. The primary gaps are: (a) missing ESG input data making the model non-runnable end-to-end, (b) absent TVOG computation despite being the model's stated purpose, (c) no tail risk metrics, and (d) no SOA/IA-required stochastic process documentation or governance framework. These are material gaps for production use and will be addressed systematically in Phases 2–4.

---

*Generated by automated audit — Cycle 1, Phase 1*  
*Next task: Document all model assumptions and parameters*
