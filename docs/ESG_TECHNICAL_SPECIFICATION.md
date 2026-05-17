# Economic Scenario Generator — Technical Specification

**Version:** 1.0  
**Date:** May 2026  
**Status:** Draft for Actuarial Review  
**Branch:** `claude/improve-stochastic-esg-model-x7nkA`

---

## Table of Contents

1. [Purpose and Regulatory Context](#1-purpose-and-regulatory-context)
2. [Scope and Coverage](#2-scope-and-coverage)
3. [Governance and Standards Alignment](#3-governance-and-standards-alignment)
4. [Model Architecture Overview](#4-model-architecture-overview)
5. [Initial Yield Curve — Term Structure Inputs](#5-initial-yield-curve--term-structure-inputs)
6. [Hull-White 1-Factor Interest Rate Model](#6-hull-white-1-factor-interest-rate-model)
7. [Equity Model — Risk-Neutral GBM](#7-equity-model--risk-neutral-gbm)
8. [Credit Spread Model — CIR Spread Process](#8-credit-spread-model--cir-spread-process)
9. [Multi-Factor Correlation Structure](#9-multi-factor-correlation-structure)
10. [Variance Reduction](#10-variance-reduction)
11. [Calibration Methodology](#11-calibration-methodology)
12. [Actuarial Quality Standards and Validation Tests](#12-actuarial-quality-standards-and-validation-tests)
13. [Output Format and Downstream Integration](#13-output-format-and-downstream-integration)
14. [Known Limitations and Planned Enhancements](#14-known-limitations-and-planned-enhancements)
15. [Glossary](#15-glossary)
16. [References](#16-references)

---

## 1. Purpose and Regulatory Context

### 1.1 Purpose

This document specifies the design, mathematics, calibration methodology, and validation standards for the **Global Economic Scenario Generator (ESG)** used in the AI Actuarial 2026 stochastic projection framework.

The ESG generates **risk-neutral stochastic economic scenarios** suitable for:

- **IFRS 17** — Insurance Contracts: stochastic projection of future cash flows for the Contractual Service Margin (CSM) and Risk Adjustment (RA); time value of financial options and guarantees (TVOG)
- **Solvency II** — SCR standard formula and internal model market risk modules; LTAS and best estimate liability (BEL) valuation
- **MCEV / EV** — Market-Consistent Embedded Value calculations per CFO Forum principles
- **Actuarial appraisal** — Fair value and appraisal value calculations for with-profits and unit-linked funds
- **Dynamic ALM** — Asset–liability matching analysis, duration gap monitoring, and strategic asset allocation optimisation

### 1.2 Regulatory Framework

The ESG is designed to comply with or directly support the following international standards and guidelines:

| Framework | Document | Relevant Sections |
|---|---|---|
| **IFRS 17** | IASB IFRS 17 Insurance Contracts (2017, amended 2020) | §36(b), §B74–B85 (discount rates), §B89–B92 (stochastic scenarios) |
| **Solvency II** | Directive 2009/138/EC; Delegated Regulation (EU) 2015/35 | Art. 77, 83, 86 (technical provisions); Art. 105, 112 (SCR market risk) |
| **EIOPA RFR** | EIOPA Technical Documentation — Risk-Free Rate (2024) | Smith-Wilson extrapolation; UFR adjustment |
| **IAA ESG Note** | IAA Note on ESG Requirements (2013) | Sections 3–6: model requirements, calibration, tests |
| **CFO Forum MCEV** | Market Consistent Embedded Value Principles (2016) | Principles 14–16: economic assumptions, calibration |
| **AAA P&P** | American Academy of Actuaries, Practice Note — Economic Scenarios (2018) | Sections 4–6 |
| **ASOP No. 25** | Actuarial Standard of Practice — Credibility Procedures (applies to calibration) | Sections 3.2–3.5 |

### 1.3 Measure Convention

All scenarios produced by this ESG are generated under the **risk-neutral (Q-measure)** probability measure, consistent with market-consistent valuation. Real-world (P-measure) scenarios are not produced in this version.

Under Q, discounted asset prices are martingales with respect to the risk-free numeraire:

$$\mathbb{E}^Q\!\left[\frac{S(T)}{B(T)}\,\middle|\,\mathcal{F}_t\right] = \frac{S(t)}{B(t)}$$

where $B(T) = \exp\!\left(\int_0^T r(s)\,ds\right)$ is the money-market account.

---

## 2. Scope and Coverage

### 2.1 Currencies and Markets

| Currency | Government Bond Model | Equity Index | Proxy Market |
|---|---|---|---|
| USD | Hull-White 1F | E_USD (S&P 500) | US Treasury curve |
| EUR | Hull-White 1F | E_EUR (Euro Stoxx 50) | EUR swap curve |
| GBP | Hull-White 1F | E_GBP (FTSE 100) | UK gilt curve |
| JPY | Hull-White 1F | E_JPY (Nikkei 225) | JPY swap curve |
| CNY | Hull-White 1F | E_CNY (CSI 300) | CGB curve |

### 2.2 Asset Classes Generated

| Asset Class | Output Column Pattern | Model |
|---|---|---|
| Government bonds | `ESG.Economies.{CCY}.NominalZCBP(Govt, {T}, 3)` | Hull-White 1F ZCB |
| Corporate bonds | `ESG.Economies.{CCY}.NominalZCBP({Rating}, {T}, 3)` | HW1F + CIR spread |
| Equity total return | `ESG.Assets.EquityAssets.{ticker}.TotalReturn` | Risk-neutral GBM |
| Equity dividend yield | `ESG.Assets.EquityAssets.{ticker}.DividendYield.Value` | OU mean-reverting |
| Cash/short rate | `ESG.Economies.{CCY}.NominalYieldCurves...CashTotalReturn` | HW1F short rate |
| Short rate (info) | `ESG.Economies.{CCY}.ShortRate` | HW1F |

### 2.3 Credit Ratings Covered

AAA, AA, A, BBB (investment-grade); BB, B, CCC (high-yield / speculative)

### 2.4 Projection Parameters

| Parameter | Minimum | Production Recommended | Notes |
|---|---|---|---|
| Number of trials | 100 | 1,000 – 5,000 | IAA recommends ≥ 1,000 for TVOG |
| Time step (dt) | 1/12 year | 1/12 year (monthly) | Quarterly dt may introduce bias |
| Horizon | 10 years | 30 – 50 years | Match longest policy duration |
| Bond tenors | 1, 5, 10 | 1, 2, 3, 5, 7, 10, 15, 20, 25, 30 | Match liability cashflow tenors |

---

## 3. Governance and Standards Alignment

### 3.1 Independence of Scenarios

Scenarios are drawn independently across trials using a seeded pseudo-random number generator (`numpy.random.default_rng`). Seed management:

- A fixed `seed` produces a fully reproducible run (required for model validation and audit)
- Production runs should use `seed=None` to avoid seed bias across reporting periods, with the seed logged for reproducibility

### 3.2 Model Change Control

Any change to parameters, model structure, or yield curve inputs constitutes a **model change** and must be:

1. Documented in the model change log
2. Validated against the quality tests in Section 12
3. Approved by the Appointed Actuary or equivalent

### 3.3 IAA ESG Quality Criteria Summary

The IAA Note on ESG Requirements (2013) identifies the following mandatory quality criteria. Compliance status is shown below:

| IAA Criterion | Requirement | This ESG |
|---|---|---|
| **No-arbitrage** | ZCB prices must be consistent with the initial yield curve | ✅ Enforced by α(t) calibration |
| **Martingale property** | E[P(t,T)·D(0,t)] = P(0,T) for all t, T | ✅ Tested — see §12 |
| **Positive bond prices** | P(t,T) ∈ (0,1] for all t < T | ✅ Guaranteed by closed-form formula |
| **Calibration to market** | Parameters fit to observable market instruments | ✅ Swaption calibration (§11) |
| **Sufficient trials** | Statistical accuracy adequate for use case | ⚠️ User must confirm |
| **Documented assumptions** | All model choices documented | ✅ This document |
| **Sensitivity testing** | Results tested under parameter perturbations | 🔲 Planned (§14) |

---

## 4. Model Architecture Overview

```
GlobalESGGenerator
│
├── CorrelationMatrix                  (§9)
│   └── 10-factor Cholesky structure
│       [r_USD, r_EUR, r_GBP, r_JPY, r_CNY,
│        E_USD, E_EUR, E_GBP, E_JPY, E_CNY]
│
├── Per currency:
│   ├── YieldCurve                     (§5)
│   │   └── Cubic-spline log-DF; Nelson-Siegel constructor
│   │
│   ├── HullWhite1F                    (§6)
│   │   ├── Exact OU discretisation
│   │   ├── Closed-form ZCB pricing
│   │   └── α(t) initial curve fit
│   │
│   └── CreditSpreadModel              (§8)
│       └── CIR spread per rating, correlated
│
└── Per equity index:
    └── EquityGBM                      (§7)
        ├── Risk-neutral GBM (r(t) as drift)
        └── OU dividend yield process
```

All stochastic drivers are drawn as correlated standard normals in a single vectorised call, then routed to the appropriate sub-model. This ensures mathematical consistency of the correlation structure throughout.

---

## 5. Initial Yield Curve — Term Structure Inputs

### 5.1 Role of the Yield Curve

The initial risk-free term structure $\{P(0,T),\, T \geq 0\}$ is a **required input** to the Hull-White model. It:

1. Anchors the no-arbitrage drift θ(t) of the short-rate process
2. Determines the model ZCB prices at $t = 0$ exactly (by construction)
3. Must be consistent with the regulatory risk-free rate used for IFRS 17 / Solvency II discount rates

### 5.2 Yield Curve Object

The `YieldCurve` class (`par_model_v2/esg/models/hull_white_1f.py`) accepts:

- An array of tenors $\{T_i\}$ and continuously-compounded zero rates $\{R_i\}$
- Internally fits a **cubic spline to log-discount factors** $\ln P(0,T) = -R(T)\cdot T$

The spline on $\ln P$ (rather than on $R$ or $P$ directly) ensures:
- $P(0,0) = 1$ is enforced by extrapolation
- Smooth positive forward rates $f(0,t) = -\partial_t \ln P(0,t)$
- No oscillatory artefacts from spline interpolation of $R$

### 5.3 Instantaneous Forward Rate

The instantaneous forward rate is computed as the first derivative of the spline:

$$f(0,t) = -\frac{\partial}{\partial t} \ln P(0,t)$$

This is used directly in the Hull-White drift calibration (Section 6.4). Accuracy of $f(0,t)$ is critical — errors here propagate into the ZCB pricing formula and can fail the martingale test.

### 5.4 Nelson-Siegel Parameterisation

When market data is unavailable, the yield curve may be initialised using the Nelson-Siegel model:

$$R(T) = \beta_0 + \beta_1 \cdot \frac{1 - e^{-\lambda T}}{\lambda T} + \beta_2 \cdot \left[\frac{1 - e^{-\lambda T}}{\lambda T} - e^{-\lambda T}\right]$$

| Parameter | Interpretation |
|---|---|
| $\beta_0$ | Long-run rate (level) |
| $\beta_1$ | Slope (short - long rate) |
| $\beta_2$ | Hump / curvature |
| $\lambda$ | Decay rate (position of hump) |

**Default parameters (proxy May 2026):**

| Currency | β₀ | β₁ | β₂ | λ |
|---|---|---|---|---|
| USD | 0.045 | -0.008 | 0.015 | 0.50 |
| EUR | 0.030 | -0.005 | 0.010 | 0.50 |
| GBP | 0.045 | -0.006 | 0.012 | 0.50 |
| JPY | 0.010 | +0.002 | 0.005 | 0.40 |
| CNY | 0.025 | +0.003 | 0.008 | 0.40 |

**These are illustrative proxies.** Production runs must use observed market rates or the EIOPA/regulatory risk-free rate (e.g. Smith-Wilson extrapolated curve for Solvency II).

### 5.5 EIOPA / Regulatory Risk-Free Rate (Solvency II)

For Solvency II applications the risk-free rate must follow EIOPA's published term structure, which:

- Uses swap rates (with credit risk adjustment) for the liquid part of the curve
- Applies Smith-Wilson extrapolation beyond the Last Liquid Point (LLP) toward the Ultimate Forward Rate (UFR)
- May include a Volatility Adjustment (VA) or Matching Adjustment (MA)

The `YieldCurve` object can be directly initialised from the EIOPA monthly release by passing the EIOPA zero rates as the `zero_rates` argument.

---

## 6. Hull-White 1-Factor Interest Rate Model

### 6.1 Model Specification

Under the risk-neutral measure $\mathbb{Q}$, the instantaneous short rate $r(t)$ satisfies:

$$dr(t) = [\theta(t) - a\,r(t)]\,dt + \sigma\,dW^r(t)$$

where:

| Symbol | Description |
|---|---|
| $a > 0$ | Mean-reversion speed |
| $\sigma > 0$ | Short-rate volatility |
| $\theta(t)$ | Time-dependent drift, calibrated to initial yield curve |
| $W^r(t)$ | Standard $\mathbb{Q}$-Brownian motion |

This is the **Hull-White extended Vasicek model** (Hull & White, 1990). It is analytically tractable, affine, and the only one-factor Gaussian model that fits the initial term structure exactly.

### 6.2 Decomposition into Mean-Zero OU Process

The short rate is decomposed as:

$$r(t) = x(t) + \alpha(t)$$

where $x(t)$ satisfies the **zero-mean Ornstein-Uhlenbeck (OU) process**:

$$dx(t) = -a\,x(t)\,dt + \sigma\,dW^r(t), \quad x(0) = r(0) - \alpha(0)$$

and $\alpha(t)$ is a deterministic drift calibrated to the initial curve (Section 6.4).

### 6.3 Exact Discretisation

The OU process has an **analytically exact discrete-time solution** (no approximation error):

$$x(t + \Delta) = x(t)\cdot e^{-a\Delta} + \sigma\sqrt{\frac{1 - e^{-2a\Delta}}{2a}}\cdot Z, \quad Z \sim \mathcal{N}(0,1)$$

The short rate path is then:

$$r(t + \Delta) = x(t + \Delta) + \alpha(t + \Delta)$$

**This is not an Euler-Maruyama approximation.** The exact discretisation eliminates step-size bias and means the time-step can be set coarser without introducing drift error, subject to the requirement that $\alpha(t)$ is evaluated on a sufficiently fine grid.

The step-conditional distribution is:

$$r(t+\Delta)\,|\,r(t) \sim \mathcal{N}\!\left(\alpha(t+\Delta) + [r(t)-\alpha(t)]e^{-a\Delta},\; \frac{\sigma^2}{2a}(1 - e^{-2a\Delta})\right)$$

### 6.4 No-Arbitrage Drift Calibration

The deterministic function $\alpha(t)$ ensures the model prices the initial yield curve exactly at $t=0$:

$$\alpha(t) = f(0,t) + \frac{\sigma^2}{2a^2}\left(1 - e^{-at}\right)^2$$

where $f(0,t) = -\partial_t \ln P(0,t)$ is the instantaneous forward rate implied by the initial curve.

**Proof:** With this choice, $\mathbb{E}^Q[r(t)] = \alpha(t)$ and the model ZCB price equals the market price at $t=0$ for all maturities. This is verified in the quality tests (Section 12).

### 6.5 Closed-Form Zero-Coupon Bond Price

Given $r(t)$ at time $t$, the price of a zero-coupon bond maturing at $T > t$ is:

$$P(t,T) = \frac{P(0,T)}{P(0,t)} \cdot \exp\!\left[B(t,T)\cdot\alpha(t) - B(t,T)\cdot r(t) - V(t,T)\right]$$

where:

$$B(t,T) = \frac{1 - e^{-a(T-t)}}{a}$$

$$V(t,T) = \frac{\sigma^2}{4a} \cdot B(t,T)^2 \cdot \left(1 - e^{-2at}\right)$$

The factor $V(t,T)$ is a **convexity correction** arising from Jensen's inequality. Omitting it would overstate ZCB prices and fail the martingale test.

### 6.6 Zero-Coupon Yield and Forward Rate

From the ZCB price:

$$R(t,T) = -\frac{\ln P(t,T)}{T - t} \quad \text{(zero rate)}$$

$$f(t,T) = -\frac{\partial}{\partial T}\ln P(t,T) = \alpha(T) + e^{-a(T-t)}[r(t) - \alpha(t)] - \frac{\sigma^2}{2a}B(t,T)^2 e^{-a(T-t)}$$

### 6.7 Default Parameters and Calibration Targets

Default parameters (see calibration, Section 11):

| Currency | $a$ | $\sigma$ | Floor | Rationale |
|---|---|---|---|---|
| USD | 0.10 | 0.012 | −1% | Moderate reversion; swaption market |
| EUR | 0.08 | 0.010 | −1% | Slower reversion; EIOPA calibration |
| GBP | 0.10 | 0.012 | −1% | Consistent with BoE swaption data |
| JPY | 0.05 | 0.006 | −1% | Low-vol; near-zero rate environment |
| CNY | 0.10 | 0.008 | 0% | Regulatory floor; PBoC guidance |

**The $a$ parameter controls the shape of the vol term structure.** Higher $a$ flattens long-tenor volatility. For Solvency II calibration, $a$ is typically estimated from the slope of the normal swaption vol surface across expiries.

### 6.8 Rate Floor

A nominal floor is applied after each simulation step:

$$r(t) \leftarrow \max\!\left(r(t),\; r_{\min}\right)$$

For CNY, $r_{\min} = 0$ (reflecting PBOC policy constraint). For developed-market currencies, $r_{\min} = -1\%$ to allow for modestly negative rates consistent with recent European and Japanese experience.

**Note:** The floor introduces a small bias relative to the true Gaussian model. For negative-rate calibration (EUR/JPY), consider using a shifted lognormal or free-boundary model in future versions.

---

## 7. Equity Model — Risk-Neutral GBM

### 7.1 Model Specification

Under $\mathbb{Q}$, the equity total-return index $S(t)$ follows geometric Brownian motion with stochastic drift equal to the domestic short rate:

$$\frac{dS(t)}{S(t)} = r(t)\,dt + \sigma_E\,dW^E(t)$$

where $W^E$ and $W^r$ satisfy:

$$dW^E \cdot dW^r = \rho_{Er}\,dt$$

This formulation ensures **drift consistency with the risk-neutral measure**: the expected return on any asset equals the risk-free rate $r(t)$, so $S(t)/B(t)$ is a $\mathbb{Q}$-martingale.

### 7.2 Discrete Update

The exact GBM update over interval $[t, t+\Delta]$ given $r(t)$ is:

$$S(t+\Delta) = S(t) \cdot \exp\!\left[\left(r(t) - \tfrac{1}{2}\sigma_E^2\right)\Delta + \sigma_E\sqrt{\Delta}\cdot Z_E\right]$$

$$Z_E = \rho_{Er} Z^r + \sqrt{1 - \rho_{Er}^2}\,Z^\perp, \quad Z^r, Z^\perp \sim \mathcal{N}(0,1)\text{ independent}$$

In implementation, $Z_E$ is drawn from the Cholesky-decomposed joint normal (Section 9), not directly from the above formula, ensuring global consistency.

**The total return factor at each step** (output column `TotalReturn`) is:

$$\text{TR}(t) = \frac{S(t)}{S(t-\Delta)} = \exp\!\left[\left(r(t-\Delta) - \tfrac{1}{2}\sigma_E^2\right)\Delta + \sigma_E\sqrt{\Delta}\cdot Z_E\right]$$

At $t=0$: $\text{TR}(0) = 1.0$ (no return applied to the starting period).

### 7.3 Equity-Rate Correlation

The correlation $\rho_{Er}$ between equity total returns and government short-rate innovations is:

| Index | $\rho_{Er}$ | Basis |
|---|---|---|
| E_USD (S&P 500) | −0.20 | Historical 2000–2024; risk-on/risk-off |
| E_EUR (Euro Stoxx) | −0.18 | ECB policy sensitivity |
| E_GBP (FTSE 100) | −0.18 | BoE policy sensitivity |
| E_JPY (Nikkei) | −0.15 | BOJ near-zero rate environment |
| E_CNY (CSI 300) | −0.10 | Lower financial integration |

The negative correlation reflects the typical "flight to quality" dynamic: rising rates generally occur in risk-on regimes when equities also rise (positive stock-bond correlation in some periods), but in policy-tightening episodes equities fall. The values above reflect a long-run average and **must be recalibrated** to the specific valuation date.

### 7.4 Dividend Yield Process

The dividend yield $\delta(t)$ follows a mean-reverting OU process independent of equity price and rate shocks:

$$d\delta(t) = \kappa_\delta\!\left(\bar{\delta} - \delta(t)\right)dt + \sigma_\delta\,dW^\delta(t), \quad W^\delta \perp W^E, W^r$$

Discrete update (exact OU):

$$\delta(t+\Delta) = \delta(t)\,e^{-\kappa_\delta\Delta} + \bar{\delta}(1 - e^{-\kappa_\delta\Delta}) + \sigma_\delta\sqrt{\frac{1-e^{-2\kappa_\delta\Delta}}{2\kappa_\delta}}\cdot Z^\delta$$

Floor applied: $\delta(t) \geq 0$.

| Index | $\bar{\delta}$ (mean yield) | $\kappa_\delta$ | $\sigma_\delta$ |
|---|---|---|---|
| E_USD | 1.8% | 1.0 | 0.8% |
| E_EUR | 3.0% | 1.0 | 0.8% |
| E_GBP | 3.5% | 1.0 | 0.8% |
| E_JPY | 2.0% | 1.0 | 0.8% |
| E_CNY | 2.5% | 1.0 | 0.8% |

### 7.5 Limitations of GBM for Equity

The GBM assumption implies:
- Constant volatility (no smile / skew)
- Log-normally distributed equity returns
- No jumps or regime shifts

For production TVOG calculations involving equity-linked guarantees (variable annuities, unit-linked minimum guarantees), a **stochastic volatility model** (e.g. Heston 1993) or **regime-switching model** is recommended. See Section 14.

---

## 8. Credit Spread Model — CIR Spread Process

### 8.1 Motivation

Corporate bond holdings are a material component of insurance asset portfolios. Credit spreads must be modelled stochastically to capture:

- Spread widening risk in stress scenarios (relevant for SCR)
- Interaction between credit spreads and government rates (flight-to-quality)
- Realistic corporate bond return volatility

### 8.2 Spread Model Specification

For each credit rating $i \in \{\text{AAA, AA, A, BBB, BB, B, CCC}\}$, the credit spread $s_i(t)$ (per annum, continuously compounded) follows a **CIR mean-reverting square-root process**:

$$ds_i(t) = \kappa_i\!\left(\theta_i - s_i(t)\right)dt + \sigma_i\sqrt{s_i(t)}\,dW^{s_i}(t)$$

The square-root diffusion coefficient keeps spreads non-negative when the **Feller condition** is satisfied:

$$2\kappa_i\theta_i > \sigma_i^2$$

### 8.3 Credit ZCB Price

Under the reduced-form (intensity-based) credit model (Lando 1998), the price of a credit-risky ZCB with maturity $T$ and rating $i$ at time $t$ is:

$$P^{cr}_i(t,T) = P^{govt}(t,T) \cdot \exp\!\left[-s_i(t) \cdot B^s(\kappa_i, t, T)\right]$$

where $P^{govt}(t,T)$ is the Hull-White government ZCB price and:

$$B^s(\kappa, t, T) = \frac{1 - e^{-\kappa(T-t)}}{\kappa}$$

This is consistent with a flat (constant) hazard rate equal to the current spread $s_i(t)$, mean-reverting between time steps. The $B^s$ function is the duration of the spread exposure.

### 8.4 Andersen QE Discretisation for CIR

The Andersen (2007) **Quadratic Exponential (QE)** scheme is used to discretise the CIR process. This preserves non-negativity and is significantly more accurate than Euler-Maruyama for CIR processes:

**Step 1:** Compute conditional mean and variance:

$$\mathbb{E}[s(t+\Delta)|s(t)] = s(t)e^{-\kappa\Delta} + \theta(1-e^{-\kappa\Delta})$$

$$\text{Var}[s(t+\Delta)|s(t)] = s(t)\frac{\sigma^2 e^{-\kappa\Delta}}{\kappa}(1-e^{-\kappa\Delta}) + \frac{\theta\sigma^2}{2\kappa}(1-e^{-\kappa\Delta})^2$$

**Step 2:** Compute $\psi = \text{Var}/\text{Mean}^2$. Apply:
- If $\psi \leq 1.5$: Gaussian approximation (moment-matched)
- If $\psi > 1.5$: Exponential approximation (preserves right tail)

### 8.5 Spread-Rate Correlation

Credit spread innovations are correlated with:
1. The **common credit factor** $W^c$ (shared across all ratings)
2. The **domestic government rate** Brownian $W^r$

$$dW^{s_i} = \rho^{cr}_{i} \cdot dW^r + \sqrt{1-(\rho^{cr}_{i})^2}\left[\lambda \cdot dW^c + \sqrt{1-\lambda^2}\cdot dW^{i}_\perp\right]$$

where $\lambda = 0.70$ is the common credit factor loading and $\rho^{cr}_i$ is the per-rating rate correlation (negative — spreads tighten when rates rise in risk-on environments).

### 8.6 Default Spread Parameters

| Rating | $\kappa$ | $\bar{s}$ | $\sigma$ | Feller | $\rho_{sr}$ |
|---|---|---|---|---|---|
| AAA | 0.60 | 10 bps | 8 bps | ✅ | −0.10 |
| AA | 0.55 | 25 bps | 15 bps | ✅ | −0.12 |
| A | 0.50 | 60 bps | 30 bps | ✅ | −0.15 |
| BBB | 0.45 | 120 bps | 60 bps | ✅ | −0.18 |
| BB | 0.40 | 250 bps | 120 bps | ✅ | −0.20 |
| B | 0.35 | 450 bps | 200 bps | ✅ | −0.20 |
| CCC | 0.30 | 900 bps | 350 bps | ✅ | −0.15 |

All Feller conditions are satisfied with the default parameters. Users must verify Feller when overriding parameters.

---

## 9. Multi-Factor Correlation Structure

### 9.1 Factor List

The ESG models 10 correlated stochastic factors:

| Index | Factor | Description |
|---|---|---|
| 0 | r_USD | USD short rate (HW1F Brownian) |
| 1 | r_EUR | EUR short rate |
| 2 | r_GBP | GBP short rate |
| 3 | r_JPY | JPY short rate |
| 4 | r_CNY | CNY short rate |
| 5 | E_USD | S&P 500 equity Brownian |
| 6 | E_EUR | Euro Stoxx 50 equity Brownian |
| 7 | E_GBP | FTSE 100 equity Brownian |
| 8 | E_JPY | Nikkei 225 equity Brownian |
| 9 | E_CNY | CSI 300 equity Brownian |

Credit spread factors are modelled conditionally (Section 8.5) and do not appear in the top-level Cholesky matrix.

### 9.2 Correlation Matrix

The default correlation matrix $\mathbf{C}$ is:

|  | r_USD | r_EUR | r_GBP | r_JPY | r_CNY | E_USD | E_EUR | E_GBP | E_JPY | E_CNY |
|---|---|---|---|---|---|---|---|---|---|---|
| **r_USD** | 1.00 | 0.65 | 0.60 | 0.25 | 0.20 | −0.15 | −0.12 | −0.12 | −0.08 | −0.05 |
| **r_EUR** | 0.65 | 1.00 | 0.75 | 0.30 | 0.15 | −0.12 | −0.15 | −0.13 | −0.08 | −0.05 |
| **r_GBP** | 0.60 | 0.75 | 1.00 | 0.28 | 0.18 | −0.12 | −0.14 | −0.16 | −0.08 | −0.04 |
| **r_JPY** | 0.25 | 0.30 | 0.28 | 1.00 | 0.15 | −0.08 | −0.08 | −0.08 | −0.12 | −0.05 |
| **r_CNY** | 0.20 | 0.15 | 0.18 | 0.15 | 1.00 | −0.05 | −0.04 | −0.04 | −0.06 | −0.10 |
| **E_USD** | −0.15 | −0.12 | −0.12 | −0.08 | −0.05 | 1.00 | 0.85 | 0.80 | 0.55 | 0.40 |
| **E_EUR** | −0.12 | −0.15 | −0.14 | −0.08 | −0.04 | 0.85 | 1.00 | 0.82 | 0.55 | 0.40 |
| **E_GBP** | −0.12 | −0.13 | −0.16 | −0.08 | −0.04 | 0.80 | 0.82 | 1.00 | 0.52 | 0.38 |
| **E_JPY** | −0.08 | −0.08 | −0.08 | −0.12 | −0.06 | 0.55 | 0.55 | 0.52 | 1.00 | 0.45 |
| **E_CNY** | −0.05 | −0.05 | −0.04 | −0.05 | −0.10 | 0.40 | 0.40 | 0.38 | 0.45 | 1.00 |

**Source:** Approximate empirical estimates from daily returns data 2010–2024, DM rates from G10 swap markets, equity indices from Bloomberg. Subject to revision upon calibration.

### 9.3 Simulation via Cholesky Decomposition

Correlated standard normals are generated as:

$$\mathbf{Z}_{corr} = \mathbf{Z}_{indep} \cdot \mathbf{L}^\top$$

where $\mathbf{L}$ is the lower Cholesky factor of $\mathbf{C}$ ($\mathbf{L}\mathbf{L}^\top = \mathbf{C}$) and $\mathbf{Z}_{indep} \in \mathbb{R}^{N_{trials} \times N_{steps} \times 10}$ has i.i.d. $\mathcal{N}(0,1)$ entries.

### 9.4 Positive Definiteness

The `CorrelationMatrix` class automatically applies a **nearest positive-definite projection** (Higham 1988) if the user-supplied matrix is not PD:

$$\mathbf{C}_{PD} = \mathbf{V}\,\mathrm{diag}(\max(\lambda_i, \epsilon))\,\mathbf{V}^\top, \quad \epsilon = 10^{-8}$$

This ensures Cholesky decomposition always succeeds. Users are warned if projection was necessary.

---

## 10. Variance Reduction

### 10.1 Antithetic Variates

For a trial drawn with Brownian increments $\mathbf{Z}$, the antithetic trial uses $-\mathbf{Z}$. This is implemented by drawing $N/2$ independent trials and mirroring them to form $N$ total paths.

**Effect:** The antithetic pair is negatively correlated with the base trial, reducing Monte Carlo variance for monotone payoffs. For bond prices (convex in rates), the antithetic estimator for $\hat{V}$:

$$\hat{V}_{antithetic} = \frac{1}{2}\!\left[V(\mathbf{Z}) + V(-\mathbf{Z})\right]$$

achieves lower variance than the crude estimator $V(\mathbf{Z})$ alone, often equivalent to doubling the trial count for near-monotone payoffs.

**Configuration:** Set `n_trials` to an even number. The generator automatically uses $N/2$ base trials and $N/2$ antithetic trials when `use_antithetic=True` (planned — see Section 14).

### 10.2 Planned: Quasi-Random (Sobol) Sequences

Sobol low-discrepancy sequences achieve $O((\log N)^d / N)$ convergence vs. $O(1/\sqrt{N})$ for pseudo-random Monte Carlo. For typical ESG dimensionalities ($d \leq 100$ effective dimensions) this provides substantial improvement.

Implementation is planned for Phase 2 (Section 14).

---

## 11. Calibration Methodology

### 11.1 Interest Rate Calibration — Swaption Market

The Hull-White parameters $(a, \sigma)$ are calibrated to fit **ATM normal (Bachelier) swaption implied volatilities**.

#### Hull-White ATM Normal Swaption Vol Formula

The model price of an ATM receiver swaption with expiry $T_{exp}$ and swap tenor $[T_{exp}, T_{mat}]$ is (Brigo & Mercurio 2006, §3.3):

$$\sigma_N^{model}(T_{exp}, T_{mat}) = \frac{P(0, T_{exp})}{A(0;\, T_{exp}, T_{mat})} \cdot \sqrt{v_p(T_{exp})}$$

where:

$$A(0;\, T_{exp}, T_{mat}) = \sum_{i=1}^{n} \tau_i \cdot P(0, T_{exp} + i/f)$$

is the forward annuity, and:

$$v_p(T) = \frac{\sigma^2}{a^2}\!\left[T - \frac{2\,B(0,T)}{a} + \frac{1 - e^{-2aT}}{2a}\right]$$

#### Calibration Objective

Minimise weighted squared error across $K$ swaption quotes:

$$\min_{a,\,\sigma}\;\sum_{k=1}^{K} w_k\!\left[\sigma_N^{model}(T_k, T_k^{mat}) - \sigma_N^{market}(T_k, T_k^{mat})\right]^2$$

subject to $a > 0$, $\sigma > 0$.

Implemented in `HullWhiteCalibrator.calibrate()` using `scipy.optimize.minimize` with L-BFGS-B.

#### Recommended Swaption Grid

| Expiry | Tenors |
|---|---|
| 1Y | 1Y, 5Y, 10Y |
| 2Y | 5Y, 10Y |
| 5Y | 5Y, 10Y, 20Y |
| 10Y | 10Y, 20Y, 30Y |

Weighting: longer-tenor swaptions receive higher weight for insurance ALM applications (matching liability duration).

#### Calibration Quality Criterion

Per IAA guidance: RMSE across the swaption grid should be $< 5$ bps in normal vol terms. If RMSE $> 10$ bps, review the yield curve input and initial parameter guesses.

### 11.2 Equity Volatility Calibration

Equity $\sigma_E$ is calibrated to **1-year ATM implied volatility** from options markets:

$$\sigma_E \approx \sigma^{implied}_{1Y\text{-ATM}}$$

For longer horizons, use the at-the-money term structure of implied vols. The single constant $\sigma_E$ is the simplest choice — a Heston or SABR model would better capture the vol smile.

**Alternative (when options market data is unavailable):** Use the 5-year historical realised volatility of monthly log returns:

$$\hat{\sigma}_E = \sqrt{12} \cdot \text{std}(\ln S_t - \ln S_{t-1})\text{ (monthly)}$$

### 11.3 Credit Spread Calibration

Calibrate long-run mean $\theta_i$ for each rating $i$ to the **current observed credit spread** at an intermediate tenor (e.g. 5-year):

$$\theta_i \approx s_i^{market}(0,\,5Y)$$

Mean reversion speed $\kappa_i$ and volatility $\sigma_i$ are calibrated to:
- Historical spread time-series (standard deviation of spread changes → σ)
- Half-life of spread deviations from trend (→ κ)

In the absence of time-series data, use the default parameters in Section 8.6 as a starting point.

### 11.4 Correlation Calibration

Calibrate the correlation matrix $\mathbf{C}$ from **historical daily returns** using a minimum 3-year lookback:

$$\hat{\rho}_{ij} = \frac{\text{Cov}(\Delta X_i, \Delta X_j)}{\sqrt{\text{Var}(\Delta X_i) \cdot \text{Var}(\Delta X_j)}}$$

Apply a **shrinkage estimator** (Ledoit-Wolf) to reduce estimation error for high-dimensional matrices. Ensure the resulting matrix is positive definite before Cholesky decomposition.

**Stability test:** Re-calibrate on rolling windows (e.g. 1Y, 3Y) and verify correlations are stable. Large changes indicate structural breaks (e.g. financial crisis) that may warrant regime-conditional correlation.

---

## 12. Actuarial Quality Standards and Validation Tests

The following tests are implemented in `GlobalESGGenerator.quality_report()` and in `tests/test_esg_models.py`. All must pass before scenarios are used in production valuations.

### 12.1 Test 1 — Bond Martingale (No-Arbitrage Check)

**IAA Reference:** IAA ESG Note §4.1; CFO Forum MCEV Principle 14.

**Statement:** For any $t, T$ with $t < T$:

$$\mathbb{E}^Q\!\left[P(t,T) \cdot D(0,t)\right] = P(0,T)$$

where $D(0,t) = \exp\!\left(-\int_0^t r(s)\,ds\right)$ is the stochastic discount factor.

**Implementation:** At test horizons $t \in \{5, 10\}$ years and tenor $\tau \in \{5, 10\}$ years:

1. Simulate $N$ trial paths of $r(t)$
2. Compute $\hat{D}(0,t)$ by summing $r_s \cdot \Delta t$ along each path
3. Compute $P(t, t+\tau)$ from the HW closed-form formula
4. Compute $\hat{\mu} = \frac{1}{N}\sum_{i=1}^{N} P_i(t, t+\tau) \cdot \hat{D}_i(0,t)$
5. Compare to $P(0, t+\tau)$ from the initial yield curve

**Pass criterion:** $|\hat{\mu} - P(0, T)| / P(0, T) < 2\%$ for $N = 2{,}000$ trials.

*Note: With $N = 200$ trials (test suite), tolerance is relaxed to 5%.*

### 12.2 Test 2 — ZCB Determinism at t=0

**Statement:** At $t=0$, all trials must produce the same ZCB price equal to the initial curve:

$$P(0, T)^{(i)} = P_{market}(0, T) \quad \forall\, i = 1,\ldots,N$$

This is a direct consequence of the $\alpha(t)$ calibration.

**Pass criterion:** Standard deviation of $P(0,T)$ across trials $< 10^{-6}$.

### 12.3 Test 3 — ZCB Price Bounds

**Statement:** ZCB prices must satisfy:

$$0 < P(t,T) \leq 1 \quad \forall\, t < T$$

Negative prices or prices exceeding 1 indicate numerical instability or a rate floor that is too low.

**Pass criterion:** All ZCB prices in the output file in $(0, 1.001]$.

### 12.4 Test 4 — Risk-Neutral Equity Drift

**Statement:** Under $\mathbb{Q}$, the expected log-return per step should equal:

$$\mathbb{E}\!\left[\ln\frac{S(t+\Delta)}{S(t)}\right] = \mathbb{E}[r(t)]\cdot\Delta - \frac{\sigma_E^2}{2}\Delta$$

**Pass criterion:** Mean log-return within 0.1% absolute of theoretical value.

### 12.5 Test 5 — Spread Non-Negativity

**Statement:** All credit spreads $s_i(t) \geq 0$ for all $t, i$.

**Pass criterion:** No negative values in credit spread paths.

### 12.6 Test 6 — Yield Curve Shape

**Statement:** At $t=0$, the model-implied zero curve should match the input yield curve within interpolation tolerance:

$$|R_{model}(0,T) - R_{market}(0,T)| < 1\,\text{bp}$$

### 12.7 Test 7 — Equity-Rate Correlation Realised

**Statement:** The sample correlation between equity log-returns and rate increments across all trials and steps should be within sampling error of the target $\rho_{Er}$:

$$|\hat{\rho}_{Er} - \rho_{Er}| < 0.05$$ (for $N \geq 1{,}000$)

### 12.8 Recommended Post-Generation Checks

Before using in production:

| Check | Method |
|---|---|
| Fan chart — short rates | Plot 5th/50th/95th percentile $r(t)$ per currency; verify realism |
| Fan chart — ZCB yields | Plot 10Y yield distribution at $t=5, 10, 20$ years |
| Equity distribution | Compare equity index at $t=10$ to lognormal $\mathcal{N}(\mu T, \sigma^2 T)$ |
| Deflator plot | Verify $D(0,t)$ is monotonically decreasing on average |
| Spread fan | Confirm BBB spread widens in adverse scenarios |

---

## 13. Output Format and Downstream Integration

### 13.1 DataFrame Schema

The `GlobalESGGenerator.run()` method returns a `pd.DataFrame` with:

| Column | Type | Description |
|---|---|---|
| `Trial` | int32 | Trial index, 1-based |
| `Timestep` | int32 | Monthly step index, 0-based |
| `ESG.Economies.{CCY}.ShortRate` | float32 | Instantaneous short rate |
| `ESG.Economies.{CCY}.NominalZCBP(Govt, {T}, 3)` | float32 | Government ZCB price |
| `ESG.Economies.{CCY}.NominalZCBP({Rating}, {T}, 3)` | float32 | Credit ZCB price |
| `ESG.Economies.{CCY}.NominalYieldCurves.NominalYieldCurve.CashTotalReturn` | float32 | Monthly cash return factor |
| `ESG.Assets.EquityAssets.{ticker}.TotalReturn` | float32 | Monthly equity total return factor |
| `ESG.Assets.EquityAssets.{ticker}.DividendYield.Value` | float32 | Annualised dividend yield |

Shape: $(N_{trials} \times (N_{steps}+1))$ rows.

### 13.2 Compatibility with Existing ESGScenarioProvider

The output column names are **fully compatible** with the existing `ESGScenarioProvider` (`par_model_v2/esg/esg_scenario_provider.py`) when filtering to a single currency. The provider reads Parquet files via:

```python
provider = ESGScenarioProvider(
    "data/esg/global_scenarios.parquet",
    max_tenor=30,
    ratings=["AAA", "AA", "A", "BBB"],
)
```

For multi-currency ALM applications, the `DynamicALMEngine._apply_esg_returns()` method requires the `currency` parameter to be configurable (planned fix — Section 14).

### 13.3 Saving to Parquet

```python
from par_model_v2.esg.global_esg import generate_global_esg

df = generate_global_esg(
    output_path="data/esg/global_scenarios.parquet",
    n_trials=1000,
    n_years=30,
    currencies=["USD", "EUR", "CNY"],
    seed=42,
)
```

Parquet is preferred over CSV for:
- ~5-10× smaller file size (columnar compression)
- Typed schema (no float parsing overhead)
- Column-selective reads (only load tenors/ratings needed)

### 13.4 Integration with DynamicALMEngine

Single-currency usage (current):

```python
from par_model_v2.esg.global_esg import GlobalESGConfig, GlobalESGGenerator
from par_model_v2.valuation.dynamic_alm import DynamicALMEngine

cfg = GlobalESGConfig(n_trials=1000, n_years=30, currencies=["CNY"],
                      equity_tickers=["E_CNY"])
gen = GlobalESGGenerator(cfg)
esg_df = gen.run()

engine = DynamicALMEngine()
result = engine.project_trial(
    trial=1,
    liability_cf_df=liability_df,
    esg_df=esg_df,
    saa_schedule=saa,
)
```

---

## 14. Known Limitations and Planned Enhancements

### 14.1 Current Limitations

| Limitation | Impact | Priority |
|---|---|---|
| **Single-factor rates** | Cannot capture yield curve twist/butterfly moves; underestimates long-rate vol | High |
| **Constant GBM volatility** | No equity vol smile; underestimates deep OTM option costs (e.g. VA guarantees) | High |
| **No FX model** | Multi-currency liabilities cannot be properly discounted to a single base currency | High |
| **Gaussian short rates** | Allows (floored) negative rates; not appropriate for all regulatory contexts | Medium |
| **No credit migration** | Rating transitions not modelled; spread jumps at downgrade not captured | Medium |
| **No tax / regulatory capital** | Model is pre-tax, ignores RBC/SCR constraints on asset allocation | Low |
| **Rate floor bias** | Hard floor introduces small upward bias in zero-rate expectations | Low |
| **No quasi-random sequences** | Pure Monte Carlo convergence $O(N^{-1/2})$ vs. Sobol $O(N^{-1})$ | Medium |

### 14.2 Phase 2 Enhancements (Recommended)

| Enhancement | Model | Benefit |
|---|---|---|
| **Hull-White 2-factor (HW2F)** | Two correlated OU factors for level and slope | Better yield curve dynamics; twist/butterfly scenarios |
| **Heston stochastic volatility** | $dv = \kappa_v(\bar{v}-v)dt + \xi\sqrt{v}dW^v$; $\rho_{vS}$ | Equity smile; more realistic TVOG for VA/VUL products |
| **FX model** | Garman-Kohlhagen / covered interest parity | Multi-currency liability discount; FX hedging analysis |
| **Antithetic variates** | Mirror $\mathbf{Z} \to -\mathbf{Z}$ for half of trials | ~40% reduction in RMSE for bond payoffs |
| **Sobol sequences** | Scrambled Sobol via `scipy.stats.qmc` | $O(N^{-1})$ convergence; 10× fewer trials for same accuracy |
| **Credit migration** | Markov chain rating transitions | Spread jumps; default event simulation |
| **Calibration automation** | Live market data feed → auto-calibrate at each reporting date | Reduces manual intervention; ensures market-consistency |

---

## 15. Glossary

| Term | Definition |
|---|---|
| **ATM** | At-The-Money: option struck at the current forward price |
| **Bachelier / Normal vol** | Option implied volatility under the Bachelier (arithmetic BM) model; standard for rates post-NIRP |
| **CIR** | Cox-Ingersoll-Ross (1985) square-root mean-reverting process |
| **CSM** | Contractual Service Margin (IFRS 17 term) |
| **Feller condition** | $2\kappa\theta > \sigma^2$; ensures CIR process stays strictly positive |
| **GBM** | Geometric Brownian Motion |
| **HW1F** | Hull-White 1-factor (extended Vasicek) interest rate model |
| **LLP** | Last Liquid Point: longest tenor with reliable market data (Solvency II) |
| **Martingale** | A process whose conditional expectation of future values equals the current value |
| **MCEV** | Market-Consistent Embedded Value |
| **OU** | Ornstein-Uhlenbeck: Gaussian mean-reverting process |
| **Q-measure** | Risk-neutral probability measure; discounted prices are martingales |
| **QE scheme** | Quadratic Exponential: Andersen (2007) CIR discretisation scheme |
| **RFR** | Risk-Free Rate (Solvency II context) |
| **SCR** | Solvency Capital Requirement (Solvency II) |
| **TVOG** | Time Value of Options and Guarantees |
| **UFR** | Ultimate Forward Rate (EIOPA, Solvency II) |
| **VA** | Variable Annuity (US); also Volatility Adjustment (Solvency II) |
| **ZCB** | Zero-Coupon Bond |

---

## 16. References

### Regulatory and Standards Documents

1. **IASB (2017).** IFRS 17 Insurance Contracts. International Accounting Standards Board, London.

2. **EIOPA (2015).** Technical Specification for the Preparatory Phase Part I. EIOPA-14/209. European Insurance and Occupational Pensions Authority.

3. **EIOPA (2024).** Technical Documentation of the Methodology to Derive EIOPA's Risk-Free Interest Rate Term Structures. EIOPA-BoS-19/189.

4. **IAA (2013).** Note on ESG Requirements for Insurance Companies Conducting Market-Consistent Valuations of Financial Options and Guarantees. International Actuarial Association.

5. **CFO Forum (2016).** Market Consistent Embedded Value Principles. European CFO Forum.

6. **AAA (2018).** Economic Scenario Generators: A Practical Guide. American Academy of Actuaries, Life Practice Council.

### Academic References

7. **Hull, J. & White, A. (1990).** Pricing Interest-Rate Derivative Securities. *Review of Financial Studies*, 3(4), 573–592.

8. **Cox, J.C., Ingersoll, J.E. & Ross, S.A. (1985).** A Theory of the Term Structure of Interest Rates. *Econometrica*, 53(2), 385–407.

9. **Black, F. & Scholes, M. (1973).** The Pricing of Options and Corporate Liabilities. *Journal of Political Economy*, 81(3), 637–654.

10. **Heston, S.L. (1993).** A Closed-Form Solution for Options with Stochastic Volatility with Applications to Bond and Currency Options. *Review of Financial Studies*, 6(2), 327–343.

11. **Lando, D. (1998).** On Cox Processes and Credit Risky Securities. *Review of Derivatives Research*, 2(2–3), 99–120.

12. **Nelson, C.R. & Siegel, A.F. (1987).** Parsimonious Modeling of Yield Curves. *Journal of Business*, 60(4), 473–489.

13. **Andersen, L.B.G. (2007).** Efficient Simulation of the Heston Stochastic Volatility Model. *Journal of Computational Finance*, 11(3).

14. **Higham, N.J. (1988).** Computing a Nearest Symmetric Positive Semidefinite Matrix. *Linear Algebra and its Applications*, 103, 103–118.

15. **Brigo, D. & Mercurio, F. (2006).** *Interest Rate Models — Theory and Practice*, 2nd ed. Springer Finance.

16. **Glasserman, P. (2004).** *Monte Carlo Methods in Financial Engineering*. Springer.

---

*This document is a technical specification for internal actuarial use. All model parameters marked as "default" or "proxy" must be reviewed and recalibrated for production valuations. This document does not constitute actuarial advice.*

*Prepared by: AI Actuarial 2026 Modelling Team*  
*Review required by: Appointed Actuary / Chief Actuary*
