# Model Development Log — AI Actuarial 2026

Automated development log. Appended each cycle by Claude Actuarial Agent.

---

## Run 2026-05-17T00:00:00Z — Phase 1: Model Review & Documentation

**Task Completed:** Audit current model code and architecture

**Accomplishments:**
- Cloned and fully inventoried repository (8 modules, 67 tests, 14 doc files, 7 assumption tables)
- Ran full test suite: 59/67 passing (88%), identified 8 failures across 2 root causes
- Identified version inconsistency: `__version__.py` (0.1.0) vs `__init__.py` (2.0.0)
- Confirmed all 20 liability result checkpoints failed — root cause: missing ESG input file
- Documented 5 critical issues by priority
- Identified 15 SOA/IA/ERM compliance gaps across 3 standards frameworks
- Produced comprehensive audit report: `docs/MODEL_AUDIT_REPORT.md`
- Initialized `.claude-dev/MODEL_DEV_STATE.json` (first cycle)
- Created this development log

**Key Findings:**
- Model is pre-production; solid foundation but not end-to-end runnable without Moody's ESG file
- TVOG computation (stated goal) is not yet implemented — critical gap
- Distributed executor has a pickling bug (local functions can't be passed to multiprocessing)
- ALM rebalancing logic does not handle 100%-cash starting position correctly
- No SOA-required stochastic process documentation; no VaR/ES; no governance framework

**Next Step:** Document all model assumptions and parameters (Phase 1, Task 2)

**Industry Standards Progress:**
- SOA ASOP 56 (Modeling): Gaps documented — stochastic process undocumented, no calibration methodology
- IA TAS M (Models): Gaps documented — audit trail initiated this cycle
- ERM Framework: VaR/ES absent — flagged for Phase 2

---

## Run 2026-05-17T12:00:00Z — Phase 1: Model Review & Documentation

**Task Completed:** Document all model assumptions and parameters

**Accomplishments:**
- Audited all 12 assumption files (8 table types, base + enhanced variants)
- Documented structure, key values, interpolation methodology, and cross-assumption consistency for every table
- Identified 6 priority remediation items (discount curve rate, dynamic lapse, mortality improvements, ESG-linked bonus, expense inflation, change control)
- Ran 6 cross-assumption consistency checks — 5 pass, 1 minor flag (equity SAA drift within tolerance)
- Produced `docs/ASSUMPTIONS_REGISTER.md` (350 lines)

**Key Findings:**
- Discount curve long-end rate (5.0% flat) is likely overstated vs current CNY market (~2.2–3.5%)
- Dynamic lapse function absent — critical for TVOG as policyholder option value depends on rate sensitivity
- No mortality improvement factors; no assumption change control process
- Investment return table (4.5–6.0%) is deterministic and its relationship to stochastic ESG returns is undocumented

**Next Step:** Identify deviations from SOA stochastic modeling standards (Phase 1, Task 3)

**Industry Standards Progress:**
- SOA ASOP 25 (Credibility): All assumption bases flagged as undocumented — remediation in Phase 2
- IA TAS M: Assumption change control process absent — flagged for Phase 2
- ERM: Dynamic lapse and stressed assumptions absent — flagged for Phase 3

---

## Run 2026-05-17T14:00:00Z — Model Enhancement: Monthly Projection

**Task Completed:** Monthly timestep, asset/liability CF by class, asset share projection

**New module:** `par_model_v2/projection/monthly_projection.py` (631 lines)

**Accomplishments:**
- `ParEndowmentProduct`: product spec for 5Y / 10Y / 20Y PAR endowment (60 / 120 / 240 monthly timesteps)
- Monthly discount: v_m = (1+i)^(-1/12); UDD mortality: 1-(1-qx)^(1/12) — SOA ASOP 56 compliant
- `project_liability_cashflows()`: full monthly CF table — premium, acquisition expense, renewal expense,
  death benefit (guaranteed SA + non-guaranteed RB), maturity benefit (guaranteed SA + terminal bonus),
  surrender benefit; explicit in-force probability decrements at each month
- `project_asset_cashflows()`: monthly income + MV by asset class — Govt coupon + linear amortisation,
  Credit coupon + amortisation, Equity dividend + capital appreciation, Cash interest
- `project_asset_share()`: monthly recursion AS_eom = (AS_bom + prem - exp)(1+r) - benefits - 30% dist
  with 70/30 policyholder/shareholder profit sharing applied each month
- `run_full_projection()`: end-to-end runner: liability → assets → asset share
- Test suite: 62 tests, 62 passing (100%)
- Demo script: `scripts/run_monthly_projection.py` — produces full output for all three terms

**Key output values (representative policy, SA=100K, prem=5K, age 35M):**
  5Y: AS at maturity 16,625 | PV net liability 48,469 | PV premiums 18,506
 10Y: AS at maturity 30,624 | PV net liability 25,936 | PV premiums 30,687
 20Y: AS at maturity 57,750 | PV net liability    483 | PV premiums 46,714

**Industry Standards Progress:**
- SOA ASOP 56: Monthly v_m, UDD, explicit G/NG split — all compliant
- IA TAS M: Full audit trail in cashflow DataFrames; each column documented
- 70/30 profit sharing per par fund governance standard

**Next Step:** Identify deviations from SOA stochastic modeling standards (Phase 1, Task 3)

---
