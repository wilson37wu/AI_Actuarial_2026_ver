"""
Monthly Projection Engine - PAR Endowment Products
===================================================

Four components in one module:
  1. ParEndowmentProduct  - product definition (5 / 10 / 20 year terms)
  2. Liability cashflows  - monthly, by benefit type, guaranteed vs non-guaranteed
  3. Asset cashflows      - monthly, by asset class (Govt, Credit, Equity, Cash)
  4. Asset share          - monthly recursion with 70/30 profit sharing

Timestep convention
-------------------
  m=0 : valuation date
  m=1..T : end of month m (premium at BOM, benefits at EOM)
  T : final month (maturity)

SOA ASOP 56 alignment:
  - Monthly discount: v_m = (1+i)^(-1/12)
  - UDD for monthly mortality: q_x^(1/12) = 1 - (1-q_x)^(1/12)
  - Explicit guaranteed / non-guaranteed split throughout
  - Full cashflow audit trail in output DataFrames
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TERMS = (5, 10, 20)


# ---------------------------------------------------------------------------
# 1. PRODUCT DEFINITION
# ---------------------------------------------------------------------------

@dataclass
class ParEndowmentProduct:
    """PAR endowment policy.

    Pays on death within term  : sum_assured (guar) + accumulated_RB (non-guar)
    Pays on survival to term   : sum_assured (guar) + terminal_bonus (non-guar)
    Pays on surrender           : surrender_value_pct * asset_share
    """
    term_years: int
    issue_age: int
    gender: str
    sum_assured: float
    annual_premium: float
    rb_rate_annual: float = 0.030
    terminal_bonus_pct: float = 0.50
    surrender_value_pct: float = 0.90
    initial_rb_accum: float = 0.0

    def __post_init__(self):
        if self.term_years not in VALID_TERMS:
            raise ValueError(f"term_years must be one of {VALID_TERMS}, got {self.term_years}")

    @property
    def term_months(self) -> int:
        return self.term_years * 12

    @property
    def monthly_premium(self) -> float:
        return self.annual_premium / 12.0

    @property
    def rb_rate_monthly(self) -> float:
        return (1.0 + self.rb_rate_annual) ** (1.0 / 12.0) - 1.0


# ---------------------------------------------------------------------------
# 2. HELPERS
# ---------------------------------------------------------------------------

def monthly_discount_factor(annual_rate: float) -> float:
    """v_m = (1+i)^(-1/12) - standard actuarial monthly conversion."""
    return (1.0 + annual_rate) ** (-1.0 / 12.0)


def monthly_mortality_qx(annual_qx: float) -> float:
    """UDD monthly mortality: 1 - (1 - q_x)^(1/12)."""
    return 1.0 - (1.0 - min(annual_qx, 0.9999)) ** (1.0 / 12.0)


def _base_annual_qx(age: int, gender: str = "M") -> float:
    """Approximation calibrated to China Life Experience Study shape.
    Male:   q_x = 0.00040 * exp(0.080*(x-25))
    Female: q_x = 0.00028 * exp(0.078*(x-25))
    Replace with table lookup when real data is available.
    """
    age = max(0, min(int(age), 100))
    if gender.upper() == "F":
        raw = 0.00028 * np.exp(0.078 * (age - 25))
    else:
        raw = 0.00040 * np.exp(0.080 * (age - 25))
    return min(max(raw, 0.0001), 1.0)


def _base_annual_lapse(policy_year: int) -> float:
    """Base lapse rates consistent with data/assumptions/lapse.csv."""
    if policy_year <= 1:   return 0.12
    elif policy_year == 2: return 0.09
    elif policy_year == 3: return 0.07
    elif policy_year <= 5: return 0.05
    elif policy_year <= 10:return 0.03
    else:                  return 0.015


# ---------------------------------------------------------------------------
# 3. LIABILITY CASHFLOW PROJECTION
# ---------------------------------------------------------------------------

@dataclass
class LiabilityProjectionResult:
    cashflows: pd.DataFrame
    pv_premiums: float
    pv_guaranteed_benefits: float
    pv_non_guaranteed_benefits: float
    pv_surrender_benefits: float
    pv_expenses: float
    pv_net_liability: float
    term_years: int
    term_months: int


def project_liability_cashflows(
    product: ParEndowmentProduct,
    discount_rate_annual: float = 0.035,
    acquisition_expense_pct: float = 0.08,
    renewal_expense_pct: float = 0.04,
    renewal_expense_fixed_monthly: float = 12.50,
    annual_lapse_fn: Optional[Callable] = None,
    annual_qx_fn: Optional[Callable] = None,
) -> LiabilityProjectionResult:
    """
    Monthly liability projection for a PAR endowment.

    Cashflow columns
    ----------------
    month, policy_year, in_force_prob, monthly_qx, monthly_lapse,
    premium, acq_expense, renewal_expense,
    death_benefit_guar, death_benefit_ng,
    maturity_benefit_guar, maturity_benefit_ng,
    surrender_benefit, net_cashflow,
    discount_factor, pv_net_cashflow
    """
    if annual_lapse_fn is None:
        annual_lapse_fn = _base_annual_lapse
    if annual_qx_fn is None:
        annual_qx_fn = _base_annual_qx

    T = product.term_months
    v_m = monthly_discount_factor(discount_rate_annual)

    # --- arrays (0-indexed; month m stored at position m-1) ---
    in_force    = np.zeros(T + 1); in_force[0] = 1.0

    monthly_qx_arr    = np.zeros(T)
    monthly_lapse_arr = np.zeros(T)
    premium_arr           = np.zeros(T)
    acq_expense_arr       = np.zeros(T)
    renewal_expense_arr   = np.zeros(T)
    death_guar_arr        = np.zeros(T)
    death_ng_arr          = np.zeros(T)
    maturity_guar_arr     = np.zeros(T)
    maturity_ng_arr       = np.zeros(T)
    surrender_arr         = np.zeros(T)
    net_cf_arr            = np.zeros(T)
    disc_arr              = np.zeros(T)
    pv_net_arr            = np.zeros(T)

    rb_accum        = product.initial_rb_accum
    asset_share_prx = 0.0   # proxy for surrender value (no ESG)
    r_proxy         = discount_rate_annual / 12.0

    for m in range(T):
        month_num  = m + 1
        policy_year = (m // 12) + 1
        age        = product.issue_age + m / 12.0

        qx_ann    = annual_qx_fn(int(age), product.gender)
        lapse_ann = annual_lapse_fn(policy_year)
        qx_m      = monthly_mortality_qx(qx_ann)
        lapse_m   = lapse_ann / 12.0
        monthly_qx_arr[m]    = qx_m
        monthly_lapse_arr[m] = lapse_m

        prob_bom = in_force[m]

        # --- premium (beginning of month) ---
        prem = product.monthly_premium * prob_bom
        premium_arr[m] = prem

        # --- expenses ---
        if m == 0:
            acq_exp = acquisition_expense_pct * product.annual_premium * prob_bom
            acq_expense_arr[m] = acq_exp
            ren_exp = 0.0
        else:
            acq_exp = 0.0
            ren_exp = (renewal_expense_pct * product.monthly_premium
                       + renewal_expense_fixed_monthly) * prob_bom
            renewal_expense_arr[m] = ren_exp

        # --- update proxy asset share (invest at discount rate) ---
        net_prem_m = prem - acq_exp - ren_exp
        asset_share_prx = (asset_share_prx + net_prem_m) * (1.0 + r_proxy)

        # --- accumulated RB (grows on in-force fund) ---
        rb_accum = (rb_accum * (1.0 + product.rb_rate_monthly)
                    + product.sum_assured * product.rb_rate_monthly * prob_bom)

        # --- benefits (end of month) ---
        is_maturity = (month_num == T)

        death_guar = product.sum_assured * qx_m * prob_bom
        death_ng   = rb_accum * qx_m * prob_bom
        death_guar_arr[m] = death_guar
        death_ng_arr[m]   = death_ng

        if is_maturity:
            surv_prob = prob_bom * (1.0 - qx_m) * (1.0 - lapse_m)
            maturity_guar_arr[m] = product.sum_assured * surv_prob
            maturity_ng_arr[m]   = product.terminal_bonus_pct * asset_share_prx * surv_prob
            sv = 0.0
        else:
            sv = product.surrender_value_pct * asset_share_prx * lapse_m * prob_bom
            surrender_arr[m] = sv

        total_benefit = (death_guar + death_ng
                         + maturity_guar_arr[m] + maturity_ng_arr[m] + sv)
        net_cf_arr[m] = prem - acq_expense_arr[m] - renewal_expense_arr[m] - total_benefit

        disc_arr[m]   = v_m ** month_num
        pv_net_arr[m] = net_cf_arr[m] * disc_arr[m]

        # --- update in-force ---
        if not is_maturity:
            in_force[m + 1] = max(prob_bom * (1.0 - qx_m) * (1.0 - lapse_m), 0.0)
        else:
            in_force[m + 1] = 0.0

    months = np.arange(1, T + 1)
    df = pd.DataFrame({
        "month":                 months,
        "policy_year":           (months - 1) // 12 + 1,
        "in_force_prob":         in_force[:T],
        "monthly_qx":            monthly_qx_arr,
        "monthly_lapse":         monthly_lapse_arr,
        "premium":               premium_arr,
        "acq_expense":           acq_expense_arr,
        "renewal_expense":       renewal_expense_arr,
        "death_benefit_guar":    death_guar_arr,
        "death_benefit_ng":      death_ng_arr,
        "maturity_benefit_guar": maturity_guar_arr,
        "maturity_benefit_ng":   maturity_ng_arr,
        "surrender_benefit":     surrender_arr,
        "net_cashflow":          net_cf_arr,
        "discount_factor":       disc_arr,
        "pv_net_cashflow":       pv_net_arr,
    })

    pv_prem   = (premium_arr * disc_arr).sum()
    pv_g_ben  = ((death_guar_arr + maturity_guar_arr) * disc_arr).sum()
    pv_ng_ben = ((death_ng_arr + maturity_ng_arr) * disc_arr).sum()
    pv_surr   = (surrender_arr * disc_arr).sum()
    pv_exp    = ((acq_expense_arr + renewal_expense_arr) * disc_arr).sum()
    pv_net    = pv_net_arr.sum()

    return LiabilityProjectionResult(
        cashflows=df,
        pv_premiums=pv_prem,
        pv_guaranteed_benefits=pv_g_ben,
        pv_non_guaranteed_benefits=pv_ng_ben,
        pv_surrender_benefits=pv_surr,
        pv_expenses=pv_exp,
        pv_net_liability=-pv_net,   # positive = insurer owes policyholders
        term_years=product.term_years,
        term_months=T,
    )


# ---------------------------------------------------------------------------
# 4. ASSET CASHFLOW PROJECTION BY ASSET CLASS
# ---------------------------------------------------------------------------

@dataclass
class AssetPosition:
    """Single fund holding."""
    asset_class: str          # 'Govt', 'Credit_A', 'Equity', 'Cash'
    market_value: float
    book_value: float
    duration_years: float = 0.0
    annual_yield: float = 0.04          # coupon/dividend/cash rate
    annual_capital_growth: float = 0.0  # equity capital appreciation
    average_maturity_years: float = 0.0
    credit_rating: str = ""


@dataclass
class AssetCashflowResult:
    """Monthly asset cashflow projection."""
    cashflows: pd.DataFrame
    pv_total_income: float
    by_class_summary: pd.DataFrame


def project_asset_cashflows(
    positions: List[AssetPosition],
    projection_months: int,
    discount_rate_annual: float = 0.035,
    reinvest_at_rate: Optional[float] = None,
) -> AssetCashflowResult:
    """
    Monthly asset cashflows by asset class.

    Govt / Credit bonds
      - Monthly coupon = annual_yield / 12 × MV
      - Linear principal amortisation over average_maturity_years
      - Repaid principal reinvested at reinvest_at_rate

    Equity
      - Monthly dividend = annual dividend yield / 12 × MV
      - Capital appreciation compounds monthly

    Cash
      - Monthly interest = annual_rate / 12 × balance

    Cashflow columns
    ----------------
    month, Govt_coupon, Govt_maturity, Govt_reinvestment,
    Credit_coupon, Credit_maturity, Credit_reinvestment,
    Equity_dividend, Equity_capital_gain, Cash_interest,
    total_income, total_cashflow, running_fund_mv,
    discount_factor, pv_cashflow
    """
    if reinvest_at_rate is None:
        reinvest_at_rate = discount_rate_annual

    T = projection_months
    v_m = monthly_discount_factor(discount_rate_annual)

    # Aggregate positions by broad class
    mv: Dict[str, float] = {"Govt": 0.0, "Credit": 0.0, "Equity": 0.0, "Cash": 0.0}
    yld: Dict[str, float]  = {}
    grw: Dict[str, float]  = {}
    mat: Dict[str, float]  = {}

    for pos in positions:
        cls = ("Govt" if pos.asset_class == "Govt"
               else "Equity" if pos.asset_class == "Equity"
               else "Cash"   if pos.asset_class == "Cash"
               else "Credit")
        mv[cls]  += pos.market_value
        yld[cls]  = pos.annual_yield
        grw[cls]  = pos.annual_capital_growth
        mat[cls]  = pos.average_maturity_years

    govt_coupon   = np.zeros(T); govt_mat    = np.zeros(T); govt_reinv  = np.zeros(T)
    cred_coupon   = np.zeros(T); cred_mat    = np.zeros(T); cred_reinv  = np.zeros(T)
    eq_div        = np.zeros(T); eq_capgain  = np.zeros(T)
    cash_int      = np.zeros(T)
    run_mv        = np.zeros(T)
    disc_arr      = np.zeros(T)

    for i in range(T):
        # Government
        if mv["Govt"] > 0:
            mat_yrs = mat.get("Govt", 8.5)
            cpn = mv["Govt"] * yld.get("Govt", 0.032) / 12.0
            principal = mv["Govt"] / max(mat_yrs * 12.0, 1.0)
            reinvest  = principal * (1.0 + reinvest_at_rate / 12.0)
            govt_coupon[i] = cpn; govt_mat[i] = principal; govt_reinv[i] = reinvest
            mv["Govt"] = mv["Govt"] - principal + reinvest

        # Credit
        if mv["Credit"] > 0:
            mat_yrs = mat.get("Credit", 6.2)
            cpn = mv["Credit"] * yld.get("Credit", 0.038) / 12.0
            principal = mv["Credit"] / max(mat_yrs * 12.0, 1.0)
            reinvest  = principal * (1.0 + reinvest_at_rate / 12.0)
            cred_coupon[i] = cpn; cred_mat[i] = principal; cred_reinv[i] = reinvest
            mv["Credit"] = mv["Credit"] - principal + reinvest

        # Equity
        if mv["Equity"] > 0:
            div_m  = mv["Equity"] * yld.get("Equity", 0.025) / 12.0
            capg_m = mv["Equity"] * grw.get("Equity", 0.06) / 12.0
            eq_div[i] = div_m; eq_capgain[i] = capg_m
            mv["Equity"] *= (1.0 + grw.get("Equity", 0.06) / 12.0)

        # Cash
        if mv["Cash"] > 0:
            int_m = mv["Cash"] * yld.get("Cash", 0.020) / 12.0
            cash_int[i] = int_m
            mv["Cash"] *= (1.0 + yld.get("Cash", 0.020) / 12.0)

        run_mv[i]   = sum(mv.values())
        disc_arr[i] = v_m ** (i + 1)

    months     = np.arange(1, T + 1)
    tot_income = govt_coupon + cred_coupon + eq_div + cash_int
    tot_cf     = tot_income + govt_mat + cred_mat + eq_capgain
    pv_income  = (tot_income * disc_arr).sum()

    df = pd.DataFrame({
        "month":               months,
        "Govt_coupon":         govt_coupon,
        "Govt_maturity":       govt_mat,
        "Govt_reinvestment":   govt_reinv,
        "Credit_coupon":       cred_coupon,
        "Credit_maturity":     cred_mat,
        "Credit_reinvestment": cred_reinv,
        "Equity_dividend":     eq_div,
        "Equity_capital_gain": eq_capgain,
        "Cash_interest":       cash_int,
        "total_income":        tot_income,
        "total_cashflow":      tot_cf,
        "running_fund_mv":     run_mv,
        "discount_factor":     disc_arr,
        "pv_cashflow":         tot_income * disc_arr,
    })

    summary = pd.DataFrame({
        "asset_class":      ["Govt", "Credit", "Equity", "Cash", "Total"],
        "total_coupon_div": [
            govt_coupon.sum(), cred_coupon.sum(), eq_div.sum(), cash_int.sum(),
            tot_income.sum(),
        ],
        "pv_income": [
            (govt_coupon * disc_arr).sum(), (cred_coupon * disc_arr).sum(),
            (eq_div * disc_arr).sum(),      (cash_int * disc_arr).sum(),
            pv_income,
        ],
    })

    return AssetCashflowResult(cashflows=df, pv_total_income=pv_income, by_class_summary=summary)


# ---------------------------------------------------------------------------
# 5. ASSET SHARE PROJECTION
# ---------------------------------------------------------------------------

@dataclass
class AssetShareResult:
    projection: pd.DataFrame
    final_asset_share: float
    total_shareholder_dist: float
    total_policyholder_dist: float
    asset_share_at_maturity: float


def project_asset_share(
    product: ParEndowmentProduct,
    liability_cf: LiabilityProjectionResult,
    asset_cf: AssetCashflowResult,
    policyholder_share: float = 0.70,
    shareholder_share: float = 0.30,
    min_surplus_to_distribute: float = 0.0,
) -> AssetShareResult:
    """
    Monthly asset share recursion.

    Recursion per month m:
      fund_mid(m) = [AS_bom(m) + prem(m) - acq_exp(m) - ren_exp(m)] * (1 + r_m)
      distributable = investment_return on fund_mid (30% to shareholder)
      AS_eom(m) = fund_mid(m) - death_guar(m) - death_ng(m)
                  - surrender(m) - shareholder_dist(m)

    Columns
    -------
    month, policy_year, asset_share_bom, premium, acq_expense,
    renewal_expense, investment_return, inv_return_rate,
    death_outgo_guar, death_outgo_ng, surrender_outgo,
    distributable_surplus, shareholder_dist, policyholder_dist,
    asset_share_eom
    """
    T = product.term_months
    lcf = liability_cf.cashflows
    acf = asset_cf.cashflows

    inv_rates = np.where(
        acf["running_fund_mv"].values > 0,
        acf["total_income"].values / acf["running_fund_mv"].values,
        0.0,
    )

    as_bom_arr   = np.zeros(T); as_eom_arr  = np.zeros(T)
    prem_arr     = np.zeros(T); acq_arr     = np.zeros(T); ren_arr     = np.zeros(T)
    inv_ret_arr  = np.zeros(T); inv_rate_arr= np.zeros(T)
    dg_arr       = np.zeros(T); dn_arr      = np.zeros(T); surr_arr    = np.zeros(T)
    dist_arr     = np.zeros(T); sh_arr      = np.zeros(T); ph_arr      = np.zeros(T)

    asset_share = 0.0
    total_sh = total_ph = 0.0

    for m in range(T):
        as_bom = asset_share
        as_bom_arr[m] = as_bom

        prem    = lcf["premium"].iloc[m]
        acq_exp = lcf["acq_expense"].iloc[m]
        ren_exp = lcf["renewal_expense"].iloc[m]
        d_guar  = lcf["death_benefit_guar"].iloc[m]
        d_ng    = lcf["death_benefit_ng"].iloc[m]
        surr    = lcf["surrender_benefit"].iloc[m]

        prem_arr[m] = prem; acq_arr[m] = acq_exp; ren_arr[m] = ren_exp
        dg_arr[m] = d_guar; dn_arr[m] = d_ng; surr_arr[m] = surr

        r_m = inv_rates[m] if m < len(inv_rates) else 0.0
        inv_rate_arr[m] = r_m

        fund_mid  = (as_bom + prem - acq_exp - ren_exp) * (1.0 + r_m)
        inv_ret   = (as_bom + prem - acq_exp - ren_exp) * r_m
        inv_ret_arr[m] = inv_ret

        if fund_mid > min_surplus_to_distribute and inv_ret > 0:
            distrib = inv_ret
            sh_dist = shareholder_share * distrib
            ph_dist = policyholder_share * distrib
        else:
            distrib = sh_dist = ph_dist = 0.0

        dist_arr[m] = distrib; sh_arr[m] = sh_dist; ph_arr[m] = ph_dist
        total_sh += sh_dist; total_ph += ph_dist

        as_eom = max(fund_mid - d_guar - d_ng - surr - sh_dist, 0.0)
        as_eom_arr[m] = as_eom
        asset_share   = as_eom

    proj = pd.DataFrame({
        "month":               np.arange(1, T + 1),
        "policy_year":         (np.arange(T) // 12) + 1,
        "asset_share_bom":     as_bom_arr,
        "premium":             prem_arr,
        "acq_expense":         acq_arr,
        "renewal_expense":     ren_arr,
        "investment_return":   inv_ret_arr,
        "inv_return_rate":     inv_rate_arr,
        "death_outgo_guar":    dg_arr,
        "death_outgo_ng":      dn_arr,
        "surrender_outgo":     surr_arr,
        "distributable_surplus": dist_arr,
        "shareholder_dist":    sh_arr,
        "policyholder_dist":   ph_arr,
        "asset_share_eom":     as_eom_arr,
    })

    return AssetShareResult(
        projection=proj,
        final_asset_share=float(asset_share),
        total_shareholder_dist=total_sh,
        total_policyholder_dist=total_ph,
        asset_share_at_maturity=float(as_eom_arr[T - 1]),
    )


# ---------------------------------------------------------------------------
# 6. COMBINED RUNNER
# ---------------------------------------------------------------------------

@dataclass
class FullProjectionResult:
    product: ParEndowmentProduct
    liability: LiabilityProjectionResult
    assets: AssetCashflowResult
    asset_share: AssetShareResult

    def summary(self) -> Dict:
        lib = self.liability; ash = self.asset_share
        return {
            "term_years":                  self.product.term_years,
            "sum_assured":                 self.product.sum_assured,
            "annual_premium":              self.product.annual_premium,
            "pv_premiums":                 lib.pv_premiums,
            "pv_guaranteed_benefits":      lib.pv_guaranteed_benefits,
            "pv_non_guaranteed_benefits":  lib.pv_non_guaranteed_benefits,
            "pv_expenses":                 lib.pv_expenses,
            "pv_net_liability":            lib.pv_net_liability,
            "asset_share_at_maturity":     ash.asset_share_at_maturity,
            "total_shareholder_dist":      ash.total_shareholder_dist,
            "total_policyholder_dist":     ash.total_policyholder_dist,
            "pv_asset_income":             self.assets.pv_total_income,
        }


def run_full_projection(
    product: ParEndowmentProduct,
    fund_positions: List[AssetPosition],
    discount_rate_annual: float = 0.035,
    acquisition_expense_pct: float = 0.08,
    renewal_expense_pct: float = 0.04,
    renewal_expense_fixed_monthly: float = 12.50,
    policyholder_share: float = 0.70,
    shareholder_share: float = 0.30,
) -> FullProjectionResult:
    """Run end-to-end monthly projection: liability -> asset -> asset share."""
    T = product.term_months
    lib = project_liability_cashflows(
        product,
        discount_rate_annual=discount_rate_annual,
        acquisition_expense_pct=acquisition_expense_pct,
        renewal_expense_pct=renewal_expense_pct,
        renewal_expense_fixed_monthly=renewal_expense_fixed_monthly,
    )
    assets = project_asset_cashflows(
        fund_positions, projection_months=T,
        discount_rate_annual=discount_rate_annual,
    )
    ash = project_asset_share(
        product, lib, assets,
        policyholder_share=policyholder_share,
        shareholder_share=shareholder_share,
    )
    return FullProjectionResult(product=product, liability=lib, assets=assets, asset_share=ash)


__all__ = [
    "VALID_TERMS", "ParEndowmentProduct",
    "LiabilityProjectionResult", "project_liability_cashflows",
    "AssetPosition", "AssetCashflowResult", "project_asset_cashflows",
    "AssetShareResult", "project_asset_share",
    "FullProjectionResult", "run_full_projection",
    "monthly_discount_factor", "monthly_mortality_qx",
]
