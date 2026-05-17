"""
Consolidated Validation Suite — runs all model validation tests and
writes a machine-readable JSON report to data/validation/validation_report.json.

Tests covered:
  A. ESG Quality Tests (hull_white, equity, correlation, martingale)
  B. PAR Policy Tests  (GPV recursion, asset share, profit split, cashflows)
  C. GMAB Tests        (MC vs BS, fund martingale, Greeks, convergence)

Usage:
    python scripts/run_validation_suite.py [--n_trials N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def run_section(label: str, fn, *args, **kwargs):
    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"{'='*65}")
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        print(f"  Completed in {elapsed:.1f}s")
        return result, elapsed, None
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR: {e}")
        return None, elapsed, str(e)


def main(n_trials: int = 500):
    timestamp = datetime.utcnow().isoformat()
    report = {
        "report_id": f"VAL-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        "generated_at": timestamp,
        "n_trials": n_trials,
        "sections": {},
        "summary": {},
    }

    # ── A. ESG quality tests ──────────────────────────────────────────────
    from par_model_v2.esg.global_esg import GlobalESGConfig, GlobalESGGenerator
    from par_model_v2.esg.models.hull_white_1f import (
        YieldCurve, HullWhite1F, HullWhite1FParams, build_default_hw_models
    )
    from par_model_v2.esg.models.correlation import CorrelationMatrix

    def run_esg_tests():
        results = {}

        # A1: HW ZCB at t=0 matches initial curve
        # Use alpha(0) as r(0) — the no-arbitrage initial condition (x0=0 ⟹ r0=alpha(0))
        for ccy in ["USD", "CNY"]:
            from par_model_v2.esg.models.hull_white_1f import DEFAULT_YIELD_CURVES, DEFAULT_HW_PARAMS
            curve = YieldCurve.nelson_siegel(currency=ccy, **DEFAULT_YIELD_CURVES[ccy])
            hw = HullWhite1F(curve, DEFAULT_HW_PARAMS[ccy])
            r0 = float(hw.alpha(np.array([0.0]))[0])   # r(0) = alpha(0) by no-arb
            r_vec = np.full(100, r0)
            errs = []
            for T in [1, 5, 10, 20]:
                mc = float(np.mean(hw.zcb_price(0.0, float(T), r_vec)))
                expected = float(curve.discount_factor(float(T)))
                errs.append(abs(mc - expected) / expected * 100)
            results[f"A1_zcb_t0_{ccy}"] = {
                "max_rel_err_pct": round(max(errs), 4),
                "pass": max(errs) < 0.5,
                "criterion": "ZCB price at t=0 matches initial curve within 0.5% for all tenors",
            }

        # A2: Martingale test (5yr horizon)
        curve = YieldCurve.nelson_siegel(currency="USD", **DEFAULT_YIELD_CURVES["USD"])
        hw = HullWhite1F(curve, DEFAULT_HW_PARAMS["USD"])
        rng = np.random.default_rng(42)
        dt = 1/12
        n_steps_mt = 60
        z = rng.standard_normal((2000, n_steps_mt))
        r = hw.simulate(2000, n_steps_mt, dt, z=z)
        cum_r = np.cumsum(r[:, :-1] * dt, axis=1)
        D5 = np.exp(-cum_r[:, -1])
        P5_15 = hw.zcb_price(5.0, 15.0, r[:, n_steps_mt])
        empirical = float(np.mean(P5_15 * D5))
        theoretical = float(curve.discount_factor(15.0))
        err_pct = abs(empirical - theoretical) / theoretical * 100
        results["A2_bond_martingale_USD"] = {
            "empirical": round(empirical, 6),
            "theoretical": round(theoretical, 6),
            "error_pct": round(err_pct, 3),
            "pass": err_pct < 2.0,
            "criterion": "E[P(5,15)·D(0,5)] within 2% of P(0,15) using 2000 trials",
        }

        # A3: ZCB price bounds
        cfg = GlobalESGConfig(n_trials=200, n_years=10, currencies=["CNY"],
                              equity_tickers=["E_CNY"], seed=42)
        gen = GlobalESGGenerator(cfg)
        esg_df = gen.run()
        zcb_col = "ESG.Economies.CNY.NominalZCBP(Govt, 10, 3)"
        vals = esg_df[zcb_col].values
        results["A3_zcb_bounds"] = {
            "min": round(float(vals.min()), 6),
            "max": round(float(vals.max()), 6),
            "pass": float(vals.min()) > 0 and float(vals.max()) <= 1.001,
            "criterion": "All ZCB prices in (0, 1.001]",
        }

        # A4: Equity total return t=0 = 1.0
        tr_col = "ESG.Assets.EquityAssets.E_CNY.TotalReturn"
        t0_vals = esg_df[esg_df["Timestep"] == 0][tr_col].values
        results["A4_equity_tr_t0"] = {
            "max_deviation_from_1": round(float(np.abs(t0_vals - 1.0).max()), 8),
            "pass": float(np.abs(t0_vals - 1.0).max()) < 1e-4,
            "criterion": "Equity total return = 1.0 at t=0 for all trials",
        }

        # A5: Correlation matrix PSD
        cm = CorrelationMatrix()
        val_cm = cm.validate()
        results["A5_correlation_psd"] = {
            "positive_definite": val_cm["positive_definite"],
            "min_eigenvalue": round(val_cm["min_eigenvalue"], 6),
            "pass": val_cm["positive_definite"],
            "criterion": "Default correlation matrix is positive definite",
        }

        # A6: Equity risk-neutral drift
        qr = gen.quality_report()
        eq_drift = qr["tests"].get("E_CNY_equity_rn_drift", {})
        results["A6_equity_rn_drift"] = {
            **{k: v for k, v in eq_drift.items() if k != "pass"},
            "pass": eq_drift.get("pass", False),
            "criterion": "Mean equity log-return matches risk-neutral expectation within 0.1%",
        }

        return results

    esg_res, esg_t, esg_err = run_section("A. ESG QUALITY TESTS", run_esg_tests)
    report["sections"]["A_esg"] = {
        "results": esg_res or {"error": esg_err},
        "elapsed_s": round(esg_t, 2),
    }
    _print_section(esg_res, "A")

    # ── B. PAR policy tests ───────────────────────────────────────────────
    from scripts.sample_par_policy import validate_par_policy

    par_res, par_t, par_err = run_section(
        "B. PAR POLICY VALIDATION", validate_par_policy
    )
    report["sections"]["B_par"] = {
        "results": par_res or {"error": par_err},
        "elapsed_s": round(par_t, 2),
    }
    _print_section(par_res, "B")

    # ── C. GMAB tests ─────────────────────────────────────────────────────
    from scripts.sample_gmab_policy import validate_gmab

    gmab_res, gmab_t, gmab_err = run_section(
        f"C. GMAB VALIDATION ({n_trials} trials)", validate_gmab, n_trials=n_trials
    )
    report["sections"]["C_gmab"] = {
        "results": gmab_res or {"error": gmab_err},
        "elapsed_s": round(gmab_t, 2),
    }
    _print_section(gmab_res, "C")

    # ── Summary ───────────────────────────────────────────────────────────
    all_sections = [esg_res, par_res, gmab_res]
    total_tests = 0
    total_pass = 0
    total_fail = 0
    failures = []

    for sec_label, sec_res in zip(["A_esg", "B_par", "C_gmab"], all_sections):
        if sec_res is None:
            total_fail += 1
            failures.append(f"{sec_label}: SECTION ERROR")
            continue
        for tname, tval in sec_res.items():
            if tname == "overall_pass":
                continue
            if not isinstance(tval, dict):
                continue
            total_tests += 1
            if tval.get("pass"):
                total_pass += 1
            else:
                total_fail += 1
                failures.append(f"{sec_label}/{tname}")

    report["summary"] = {
        "total_tests": total_tests,
        "passed": total_pass,
        "failed": total_fail,
        "pass_rate_pct": round(total_pass / max(total_tests, 1) * 100, 1),
        "overall_pass": total_fail == 0,
        "failures": failures,
    }

    print("\n" + "=" * 65)
    print("  VALIDATION SUMMARY")
    print("=" * 65)
    print(f"  Total tests:  {total_tests}")
    print(f"  Passed:       {total_pass}")
    print(f"  Failed:       {total_fail}")
    print(f"  Pass rate:    {report['summary']['pass_rate_pct']:.1f}%")
    print(f"  Overall:      {'ALL PASS' if report['summary']['overall_pass'] else 'FAILURES PRESENT'}")
    if failures:
        for f in failures:
            print(f"    FAIL: {f}")

    # Save
    out_dir = PROJECT_ROOT / "data" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved to: {report_path}")
    return report


def _print_section(results, label):
    if results is None:
        print("  [ERROR] Section failed to run")
        return
    for name, res in results.items():
        if name == "overall_pass":
            continue
        if not isinstance(res, dict):
            continue
        status = "PASS" if res.get("pass") else "FAIL"
        print(f"  [{status}] {label}.{name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_trials", type=int, default=500)
    args = parser.parse_args()
    main(n_trials=args.n_trials)
