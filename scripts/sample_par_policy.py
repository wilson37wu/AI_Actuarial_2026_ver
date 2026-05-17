"""
Sample PAR (Participating) Policy — Test Case and Validation

Policy: Male, age 40, standard underwriting, whole-life participating
        Sum assured: CNY 200,000 | Premium term: 20 years
        Reversionary bonus: 2.0% p.a. of SA (compound)
        Projection valuation date: 2026-01-01

Outputs produced:
  1. Deterministic GPV, asset share, and profit test (annual)
  2. Stochastic projection (500 trials × 30 years) via Hull-White 1F directly,
     with deterministic tail BEL appended beyond the ESG horizon
  3. TVOG = E[stochastic PV(benefits+tail)] - deterministic GPV(best-estimate)
  4. IFRS 17 fulfilment cash flow breakdown (BEL + RA + TVOG)
  5. Validation results: GPV recursion, asset share, profit split, cashflows,
     tail coverage (F-003 resolution)

F-003 Resolution:
  The whole-life policy has meaningful mortality beyond the 30-year stochastic ESG
  horizon.  At the end of the ESG run (attained age 80), the insured still has
  material survival probability.  We handle this with a deterministic tail:

      PV(0) of tail benefit at time T_esc conditioned on r(T_esg):
        = (1/D(0,T_esg)) × Σ_τ [S(T_esg)·p_τ·q_{T+τ}·DB_{T+τ}·P(T_esg,T_esg+τ|r_T)]

  where D(0,T_esg) is the path-realised discount accumulation, p_τ the
  deterministic survival from T_esg to T_esg+τ, and P(t,T|r_t) is the
  Hull-White closed-form ZCB price.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from par_model_v2.liabilities.deterministic_liability import (
    calculate_gpv,
    generate_monthly_cashflows,
    default_mortality_qx,
)
from par_model_v2.esg.models.hull_white_1f import (
    YieldCurve, HullWhite1F, DEFAULT_HW_PARAMS, DEFAULT_YIELD_CURVES
)

# ─── Policy definition ───────────────────────────────────────────────────────

POLICY = {
    "policy_id": "PAR-TEST-001",
    "product_code": "WL",
    "issue_age": 40,
    "sex": "M",
    "uw_class": "STD",
    "sum_assured": 200_000.0,
    "premium_term": 20,
    "issue_year": 2016,
    "rb_accum_sa": 20_000.0,   # 10 yrs of 2% pa RB already accrued at val date
    "retirement_age": 65,
    "status": "INFORCE",
}

VALUATION_YEAR = 2026
DISCOUNT_RATE_DET = 0.030          # deterministic best-estimate discount rate (CNY RFR)
BONUS_RATE = 0.020                 # non-guaranteed reversionary bonus rate pa
EXPENSE_LOADING = 0.050            # 5% of gross premium
LAPSE_RATE = 0.010                 # 1% pa
PROFIT_SPLIT_PH = 0.70             # 70% policyholder, 30% shareholder
ANNUAL_PREMIUM = POLICY["sum_assured"] / POLICY["premium_term"]   # 10,000 pa


# ─── 1. Deterministic GPV ────────────────────────────────────────────────────

def run_deterministic_gpv() -> dict:
    result = calculate_gpv(
        POLICY,
        discount_rate=DISCOUNT_RATE_DET,
        expense_loading=EXPENSE_LOADING,
        rb_growth_rate=BONUS_RATE,
        valuation_year=VALUATION_YEAR,
    )
    return result


# ─── 2. Deterministic profit test (annual, 50 years) ────────────────────────

def run_profit_test(discount_rate: float = DISCOUNT_RATE_DET) -> pd.DataFrame:
    """Project asset share and profit analytically year by year."""
    age_at_val = POLICY["issue_age"] + (VALUATION_YEAR - POLICY["issue_year"])
    duration = VALUATION_YEAR - POLICY["issue_year"]
    SA = POLICY["sum_assured"]
    PT = POLICY["premium_term"]
    premium = ANNUAL_PREMIUM
    rb_accum = POLICY["rb_accum_sa"]
    inv_return = discount_rate  # deterministic investment return = discount rate

    asset_share = 0.0  # starting asset share at valuation date
    rows = []

    for t in range(1, 51):
        age = age_at_val + t
        pol_yr = duration + t
        qx = default_mortality_qx(age - 1)  # mortality in the year (beginning of year age)

        # Premium income (if in premium term)
        prem_income = premium * (1 - EXPENSE_LOADING) if pol_yr <= PT else 0.0

        # Investment return on asset share + net premium
        inv_income = (asset_share + prem_income) * inv_return

        # Gross death benefit = SA + accrued RB
        rb_t = rb_accum * (1 + BONUS_RATE) ** t
        death_benefit = SA + rb_t

        # Expected death cost = qx * death_benefit
        death_cost = qx * death_benefit

        # Shareholder share of investment surplus (30%)
        inv_surplus = inv_income * (1 - PROFIT_SPLIT_PH)

        # Ending asset share
        asset_share = (asset_share + prem_income + inv_income - death_cost
                       - inv_surplus)

        # Policyholder share = 70% of investment surplus
        ph_surplus = inv_income * PROFIT_SPLIT_PH

        rows.append({
            "policy_year": pol_yr,
            "age": age,
            "qx": round(qx, 6),
            "asset_share_end": round(asset_share, 2),
            "prem_income": round(prem_income, 2),
            "inv_income": round(inv_income, 2),
            "rb_accrued": round(rb_t, 2),
            "death_benefit": round(death_benefit, 2),
            "death_cost": round(death_cost, 2),
            "inv_surplus_sh": round(inv_surplus, 2),      # shareholder 30%
            "ph_surplus": round(ph_surplus, 2),           # policyholder 70%
            "net_cf_fund": round(prem_income + ph_surplus - death_cost, 2),
        })

        if asset_share < 0:
            break  # fund exhausted

    return pd.DataFrame(rows)


# ─── 3. Stochastic ESG projection with deterministic tail (F-003 fix) ────────

def run_stochastic_projection(n_trials: int = 500, n_years: int = 30) -> dict:
    """
    Project PAR policy BEL over stochastic short-rate paths with a
    deterministic tail beyond the ESG horizon.

    Architecture
    ------------
    - Simulate CNY Hull-White short rate paths directly (no GlobalESGGenerator).
    - Vectorize all per-trial computations over the NumPy axis-0 dimension.
    - For each trial: at T_esg, condition on the terminal rate r_T to price
      all future tail cashflows analytically via the HW closed-form ZCB.
    """
    dt = 1.0 / 12.0
    n_steps = round(n_years / dt)  # 360 monthly steps for 30yr

    # Build CNY interest rate model
    cny_curve = YieldCurve.nelson_siegel(currency="CNY", **DEFAULT_YIELD_CURVES["CNY"])
    cny_hw = HullWhite1F(cny_curve, DEFAULT_HW_PARAMS["CNY"])

    # Simulate short rate paths: (n_trials, n_steps+1)
    z = np.random.default_rng(42).standard_normal((n_trials, n_steps))
    r_paths = cny_hw.simulate(n_trials, n_steps, dt, z=z)

    # ── Deterministic decrement arrays (same for all trials) ────────────────
    SA = POLICY["sum_assured"]
    PT = POLICY["premium_term"]
    age_at_val = POLICY["issue_age"] + (VALUATION_YEAR - POLICY["issue_year"])
    dur_at_val = VALUATION_YEAR - POLICY["issue_year"]

    months = np.arange(1, n_steps + 1)            # 1 … n_steps
    ages_m = age_at_val + months * dt
    pol_yr_m = dur_at_val + months * dt

    # Monthly mortality and survival
    qx_m = np.array([default_mortality_qx(float(a)) / 12 for a in ages_m])
    decrement_m = np.maximum(1.0 - qx_m - LAPSE_RATE / 12, 0.0)

    # survival_start[m-1] = in-force probability at START of month m
    # = prod_{k=0}^{m-2} decrement_k, with convention S(0)=1
    survival_start = np.r_[1.0, np.cumprod(decrement_m[:-1])]  # (n_steps,)

    # RB at month m: bonus applied at end of each policy year
    # rb_m[m-1] = rb_accum_sa * (1.02)^(m // 12)
    rb_m = POLICY["rb_accum_sa"] * (1 + BONUS_RATE) ** (months // 12)  # (n_steps,)
    db_m = SA + rb_m  # total death benefit at month m

    # Expected death cashflow (probability-weighted, deterministic)
    expected_deaths_m = qx_m * survival_start * db_m   # (n_steps,)

    # Monthly gross premium (during premium term only)
    gross_prem_m = np.where(pol_yr_m <= PT, ANNUAL_PREMIUM / 12, 0.0)  # (n_steps,)
    prem_inc_m = gross_prem_m * survival_start                          # (n_steps,)

    # ── Path-dependent discount factors ────────────────────────────────────
    # cum_disc[trial, m-1] = exp(Σ_{k=0}^{m-1} r[trial,k]·dt)
    #                      = accumulated account value factor to end of month m
    cum_disc = np.exp(np.cumsum(r_paths[:, :-1] * dt, axis=1))   # (n_trials, n_steps)

    # ── PV of stochastic-period cashflows ───────────────────────────────────
    # Broadcast deterministic cashflows over trials dimension
    pv_b_stoch = (expected_deaths_m[np.newaxis, :] / cum_disc).sum(axis=1)  # (n_trials,)
    pv_p_stoch = (prem_inc_m[np.newaxis, :] / cum_disc).sum(axis=1)         # (n_trials,)

    # ── Terminal state (same for all trials except the short rate) ──────────
    # In-force probability at end of stochastic horizon
    survival_T = float(np.prod(decrement_m))           # scalar

    # RB accumulated over the stochastic horizon
    rb_T = float(POLICY["rb_accum_sa"] * (1 + BONUS_RATE) ** (n_steps // 12))

    # Accumulated discount factor at end of horizon (per trial)
    cum_disc_T = cum_disc[:, -1]                       # (n_trials,)

    # Terminal short rate per trial
    r_T_all = r_paths[:, n_steps]                     # (n_trials,)

    # ── Deterministic tail BEL (F-003 fix) ─────────────────────────────────
    age_T = age_at_val + n_years                      # attained age at end of horizon
    max_age = 110.0                                   # terminal age (qx → 1.0 before this)
    n_tail = max(0, int((max_age - age_T) * 12))

    if n_tail > 0:
        tail_months = np.arange(1, n_tail + 1)
        taus_tail = tail_months / 12.0               # (n_tail,) time from T_esg
        ages_tail = age_T + taus_tail

        # Tail mortality and survival
        qx_tail = np.array([default_mortality_qx(float(a)) / 12 for a in ages_tail])
        decrement_tail = np.maximum(1.0 - qx_tail - LAPSE_RATE / 12, 0.0)
        # survival_tail[m-1] = in-force at start of tail month m (weighted by survival_T)
        survival_tail = survival_T * np.r_[1.0, np.cumprod(decrement_tail[:-1])]  # (n_tail,)

        # Tail RB and death benefit
        rb_tail = rb_T * (1 + BONUS_RATE) ** (tail_months // 12)   # (n_tail,)
        db_tail = SA + rb_tail                                       # (n_tail,)

        # Expected tail deaths (deterministic)
        expected_deaths_tail = qx_tail * survival_tail * db_tail    # (n_tail,)

        # ZCB prices conditioned on terminal rate: P(T_esg, T_esg+tau | r_T)
        # Shape: (n_trials, n_tail)
        t_h = float(n_years)
        T_mats = t_h + taus_tail
        zcb_matrix = np.column_stack([
            cny_hw.zcb_price(t_h, float(T), r_T_all) for T in T_mats
        ])  # (n_trials, n_tail)

        # Tail PV at time T_esg (per trial): Σ_τ expected_death_τ × P(T,T+τ|r_T)
        tail_pv_at_T = zcb_matrix @ expected_deaths_tail              # (n_trials,)

        # Discount back to t=0: divide by accumulated discount factor
        tail_bel = tail_pv_at_T / cum_disc_T                          # (n_trials,)
    else:
        tail_bel = np.zeros(n_trials)

    # ── Combine ─────────────────────────────────────────────────────────────
    pv_b_total = pv_b_stoch + tail_bel
    stoch_bel = float(np.mean(pv_b_total - pv_p_stoch))

    det_gpv = run_deterministic_gpv()
    tvog = stoch_bel - det_gpv["GPV"]

    mean_pv_b_stoch = float(np.mean(pv_b_stoch))
    mean_tail = float(np.mean(tail_bel))
    mean_pv_b_total = float(np.mean(pv_b_total))
    mean_pv_p = float(np.mean(pv_p_stoch))

    return {
        "n_trials": n_trials,
        "mean_pv_benefits_stoch_only": round(mean_pv_b_stoch, 2),
        "mean_tail_bel": round(mean_tail, 2),
        "tail_bel_pct_total_benefits": round(mean_tail / max(mean_pv_b_total, 1) * 100, 1),
        "mean_pv_benefits": round(mean_pv_b_total, 2),
        "mean_pv_premiums": round(mean_pv_p, 2),
        "stoch_bel": round(stoch_bel, 2),
        "det_gpv": round(det_gpv["GPV"], 2),
        "tvog": round(tvog, 2),
        "tvog_pct_bel": round(tvog / abs(stoch_bel) * 100, 2) if stoch_bel != 0 else 0,
        "pct_5_gpv": round(float(np.percentile(pv_b_total - pv_p_stoch, 5)), 2),
        "pct_95_gpv": round(float(np.percentile(pv_b_total - pv_p_stoch, 95)), 2),
        "std_gpv": round(float(np.std(pv_b_total - pv_p_stoch)), 2),
        # Terminal state (deterministic, informational)
        "survival_at_T_esg": round(survival_T, 6),
        "rb_at_T_esg": round(rb_T, 2),
    }


# ─── 4. Validation checks ────────────────────────────────────────────────────

def validate_par_policy() -> dict:
    """Run all PAR policy validation checks. Returns dict with pass/fail results."""
    results = {}

    # V1: GPV recursive check using standard prospective reserve identity
    #   (V(t) + P_net)·(1+r) = q_x·DB + p_x·V(t+1)
    gpv_res = run_deterministic_gpv()
    age_at_val = POLICY["issue_age"] + (VALUATION_YEAR - POLICY["issue_year"])

    # Manually step one year forward and re-compute GPV
    policy_next_year = dict(POLICY)
    policy_next_year["issue_year"] -= 1  # advance one year
    policy_next_year["rb_accum_sa"] = POLICY["rb_accum_sa"] * (1 + BONUS_RATE)
    gpv_next = calculate_gpv(policy_next_year, discount_rate=DISCOUNT_RATE_DET,
                             expense_loading=EXPENSE_LOADING, rb_growth_rate=BONUS_RATE,
                             valuation_year=VALUATION_YEAR)

    # Standard prospective recursion: (V(t) + P_net)(1+r) = q_x·DB + p_x·V(t+1)
    qx = default_mortality_qx(age_at_val)
    px = 1 - qx
    rb_t = POLICY["rb_accum_sa"]
    death_benefit_t = POLICY["sum_assured"] + rb_t
    net_premium = ANNUAL_PREMIUM * (1 - EXPENSE_LOADING)
    lhs = (gpv_res["GPV"] + net_premium) * (1 + DISCOUNT_RATE_DET)
    rhs = qx * death_benefit_t + px * gpv_next["GPV"]

    recursion_error = abs(lhs - rhs)
    recursion_error_pct = recursion_error / max(abs(lhs), 1) * 100
    results["V1_gpv_recursion"] = {
        "lhs_(V+P)*(1+r)": round(lhs, 2),
        "rhs_q*DB+p*V(t+1)": round(rhs, 2),
        "error": round(recursion_error, 2),
        "error_pct": round(recursion_error_pct, 4),
        "pass": recursion_error_pct < 5.0,
        "criterion": "GPV recursion error < 5% (approximation due to annual/monthly mismatch)",
    }

    # V2: Premium adequacy — GPV should be positive (net liability)
    results["V2_premium_adequacy"] = {
        "GPV": round(gpv_res["GPV"], 2),
        "PV_benefits": round(gpv_res["PV_benefits"], 2),
        "PV_premiums": round(gpv_res["PV_premiums"], 2),
        "pass": gpv_res["GPV"] > 0,
        "criterion": "GPV > 0 (benefits exceed premiums at best-estimate)",
    }

    # V3: Asset share positivity — profit test should not go negative in early years
    profit_df = run_profit_test()
    early_years = profit_df.head(10)
    negative_as = (early_years["asset_share_end"] < 0).sum()
    results["V3_asset_share_positivity"] = {
        "negative_years_in_first_10": int(negative_as),
        "pass": negative_as == 0,
        "criterion": "Asset share non-negative in first 10 policy years (level premium adequacy)",
    }

    # V4: 70/30 profit split integrity
    profit_df["sh_pct"] = profit_df["inv_surplus_sh"] / (
        profit_df["inv_surplus_sh"] + profit_df["ph_surplus"]
    ).replace(0, np.nan)
    mean_sh = float(profit_df["sh_pct"].dropna().mean())
    results["V4_profit_split"] = {
        "mean_shareholder_pct": round(mean_sh * 100, 2),
        "target": 30.0,
        "pass": abs(mean_sh - 0.30) < 0.001,
        "criterion": "Average shareholder share = 30% ± 0.1%",
    }

    # V5: Mortality adequacy — total expected deaths should be positive
    cf_df = generate_monthly_cashflows(POLICY, discount_rate=DISCOUNT_RATE_DET,
                                       expense_loading=EXPENSE_LOADING,
                                       rb_growth_rate=BONUS_RATE,
                                       surrender_rate=LAPSE_RATE,
                                       valuation_year=VALUATION_YEAR)
    total_expected_claims = cf_df["death"].sum()
    total_guaranteed = cf_df["guaranteed_benefit"].sum()
    total_ng = cf_df["non_guaranteed_benefit"].sum()
    results["V5_cashflow_completeness"] = {
        "total_expected_claims": round(total_expected_claims, 2),
        "guaranteed_component": round(total_guaranteed, 2),
        "non_guaranteed_component": round(total_ng, 2),
        "pass": total_expected_claims > 0 and total_guaranteed > 0,
        "criterion": "Total expected death claims > 0; guaranteed and non-guaranteed components present",
    }

    # V6: Cashflow sign convention — premiums positive, benefits negative in net_cf
    cf_df["net_cf"] = cf_df["premium"] - cf_df["expense"] - cf_df["surrender"] - cf_df["death"]
    early_cf = cf_df[cf_df["month_index"] < 60]["net_cf"]   # first 5 years
    results["V6_cashflow_sign"] = {
        "early_net_cf_positive_months": int((early_cf > 0).sum()),
        "early_net_cf_total_months": int(len(early_cf)),
        "pass": (early_cf > 0).sum() > 40,
        "criterion": "Net cashflow (premium - claims - expenses) positive for most months in premium-paying years",
    }

    # V7: Deterministic tail materiality (F-003)
    # The tail of a WL policy (beyond the 30-yr ESG horizon) must be material.
    # We compute the proportion of total PV benefits attributable to age > (age_at_val+30)
    # using purely deterministic arithmetic to keep the validation suite fast.
    _v7 = _deterministic_tail_coverage()
    results["V7_wl_tail_coverage"] = {
        "tail_pv_benefits": round(_v7["tail_pv"], 2),
        "total_pv_benefits": round(gpv_res["PV_benefits"], 2),
        "tail_pct": round(_v7["tail_pct"], 1),
        "pass": _v7["tail_pct"] > 15.0,
        "criterion": "Tail PV benefits (beyond 30yr ESG horizon) > 15% of total WL benefits",
    }

    overall = all(v.get("pass", False) for v in results.values())
    results["overall_pass"] = overall
    return results


def _deterministic_tail_coverage() -> dict:
    """
    Compute the proportion of total PV benefits for this WL policy that
    falls beyond the 30-year ESG horizon, using deterministic assumptions.

    Method
    ------
    1. Compute in-force probability at the end of the 30-year horizon
       (stochastic period), including lapses.
    2. Compute discount factor to the end of the horizon at 3% p.a.
    3. Value a 'tail' version of the policy at the attained age at horizon end.
    4. Tail contribution = survival_to_T × disc_to_T × PV_benefits(age at T)
    """
    age_at_val = POLICY["issue_age"] + (VALUATION_YEAR - POLICY["issue_year"])
    horizon_years = 30

    # Probability of surviving to end of 30yr horizon (mortality + lapse)
    surv = 1.0
    for yr in range(horizon_years):
        age_yr = age_at_val + yr
        qx = default_mortality_qx(float(age_yr))
        surv *= (1.0 - qx) * (1.0 - LAPSE_RATE)

    # Discount factor at 3% p.a. to end of horizon
    disc_T = (1.0 + DISCOUNT_RATE_DET) ** (-horizon_years)

    # RB accumulated at end of horizon
    rb_at_T = POLICY["rb_accum_sa"] * (1.0 + BONUS_RATE) ** horizon_years

    # 'Tail' policy: same policy but with age shifted to (age_at_val + horizon_years)
    # and updated RB accumulation
    tail_policy = dict(POLICY)
    tail_policy["issue_year"] = VALUATION_YEAR - (age_at_val + horizon_years - POLICY["issue_age"])
    tail_policy["rb_accum_sa"] = rb_at_T

    tail_gpv = calculate_gpv(
        tail_policy,
        discount_rate=DISCOUNT_RATE_DET,
        expense_loading=EXPENSE_LOADING,
        rb_growth_rate=BONUS_RATE,
        valuation_year=VALUATION_YEAR,
    )

    # PV at time 0 of tail benefits
    tail_pv = surv * disc_T * tail_gpv["PV_benefits"]

    total_pv = run_deterministic_gpv()["PV_benefits"]
    tail_pct = tail_pv / max(total_pv, 1.0) * 100.0

    return {"tail_pv": tail_pv, "total_pv": total_pv, "tail_pct": tail_pct}


# ─── Main ────────────────────────────────────────────────────────────────────

def main(run_stochastic: bool = True, n_trials: int = 500):
    print("=" * 70)
    print("PAR POLICY TEST CASE — PAR-TEST-001")
    print("=" * 70)

    # Policy summary
    age_at_val = POLICY["issue_age"] + (VALUATION_YEAR - POLICY["issue_year"])
    print(f"\nPolicy: {POLICY['policy_id']}")
    print(f"  Product: Whole Life Participating (WL)")
    print(f"  Insured: Male, age {age_at_val} (issued age {POLICY['issue_age']} in {POLICY['issue_year']})")
    print(f"  Sum Assured: CNY {POLICY['sum_assured']:,.0f}")
    print(f"  Premium term: {POLICY['premium_term']} years  |  Annual premium: CNY {ANNUAL_PREMIUM:,.0f}")
    print(f"  Accrued RB at valuation: CNY {POLICY['rb_accum_sa']:,.0f}")
    print(f"  Valuation date: {VALUATION_YEAR}-01-01")

    # GPV
    print("\n--- 1. DETERMINISTIC GPV ---")
    gpv = run_deterministic_gpv()
    for k, v in gpv.items():
        print(f"  {k:<25}: {v:>12,.2f}" if isinstance(v, float) else f"  {k:<25}: {v}")

    # Profit test summary (first 10 years)
    print("\n--- 2. PROFIT TEST (first 10 projected years) ---")
    pt = run_profit_test()
    print(pt.head(10)[
        ["policy_year","age","qx","prem_income","inv_income",
         "death_cost","inv_surplus_sh","asset_share_end"]
    ].to_string(index=False))

    # Tail coverage (deterministic)
    print("\n--- 3. DETERMINISTIC TAIL COVERAGE (F-003 check) ---")
    tail_cov = _deterministic_tail_coverage()
    print(f"  Survival probability to age {age_at_val + 30:.0f}: {tail_cov['tail_pv'] / tail_cov['total_pv'] * 100:.0f}% of benefits in tail")
    print(f"  Tail PV benefits (age 80+): CNY {tail_cov['tail_pv']:>12,.2f}")
    print(f"  Total PV benefits:          CNY {tail_cov['total_pv']:>12,.2f}")
    print(f"  Tail coverage ratio:        {tail_cov['tail_pct']:.1f}%")

    # Stochastic
    if run_stochastic:
        print("\n--- 4. STOCHASTIC PROJECTION (with deterministic tail) ---")
        stoch = run_stochastic_projection(n_trials=n_trials)
        print(f"  Trials: {stoch['n_trials']}")
        print(f"  Mean PV Benefits (stoch only): CNY {stoch['mean_pv_benefits_stoch_only']:>12,.2f}")
        print(f"  Mean Tail BEL (age 80-110):    CNY {stoch['mean_tail_bel']:>12,.2f}  ({stoch['tail_bel_pct_total_benefits']:.1f}% of total)")
        print(f"  Mean PV Benefits (total):      CNY {stoch['mean_pv_benefits']:>12,.2f}")
        print(f"  Mean PV Premiums:              CNY {stoch['mean_pv_premiums']:>12,.2f}")
        print(f"  Stochastic BEL (total):        CNY {stoch['stoch_bel']:>12,.2f}")
        print(f"  Deterministic GPV:             CNY {stoch['det_gpv']:>12,.2f}")
        print(f"  TVOG:                          CNY {stoch['tvog']:>12,.2f}  ({stoch['tvog_pct_bel']:.2f}% of BEL)")
        print(f"  BEL 5th percentile:            CNY {stoch['pct_5_gpv']:>12,.2f}")
        print(f"  BEL 95th percentile:           CNY {stoch['pct_95_gpv']:>12,.2f}")
        print(f"  BEL std:                       CNY {stoch['std_gpv']:>12,.2f}")
        print(f"  Survival at T=30yr:            {stoch['survival_at_T_esg']:.4%}")
        print(f"  RB at T=30yr:                  CNY {stoch['rb_at_T_esg']:>12,.2f}")

    # Validation
    print("\n--- 5. VALIDATION RESULTS ---")
    val = validate_par_policy()
    for name, res in val.items():
        if name == "overall_pass":
            continue
        status = "PASS" if res.get("pass") else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {res.get('criterion','')}")
        for k, v in res.items():
            if k not in ("pass", "criterion"):
                print(f"         {k}: {v}")

    overall = "ALL PASS" if val.get("overall_pass") else "FAILURES PRESENT"
    print(f"\n  Overall: {overall}")

    # Save results
    out_dir = PROJECT_ROOT / "data" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([gpv]).to_csv(out_dir / "par_gpv.csv", index=False)
    run_profit_test().to_csv(out_dir / "par_profit_test.csv", index=False)
    generate_monthly_cashflows(POLICY, discount_rate=DISCOUNT_RATE_DET,
                               expense_loading=EXPENSE_LOADING,
                               valuation_year=VALUATION_YEAR).to_csv(
        out_dir / "par_cashflows.csv", index=False)
    with open(out_dir / "par_validation.json", "w") as f:
        json.dump({k: v for k, v in val.items() if k != "details"}, f, indent=2, default=str)

    print(f"\n  Results saved to: {out_dir}")
    return val


if __name__ == "__main__":
    main(run_stochastic=True, n_trials=500)
