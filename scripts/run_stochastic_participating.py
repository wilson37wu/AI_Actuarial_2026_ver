"""Run stochastic participating product projection (1000 scenarios)."""

from __future__ import annotations

import argparse

from par_model_v2.liabilities import ParticipatingProductConfig, StochasticParticipatingModel


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hk-mortality-csv",
        required=True,
        help="Path to HK mortality CSV with columns: attained_age,male,female",
    )
    args = parser.parse_args()

    current_yield_curve = {
        1: 0.043,
        2: 0.041,
        3: 0.039,
        5: 0.037,
        7: 0.036,
        10: 0.035,
        20: 0.036,
        30: 0.037,
    }

    sp500_total_returns_15y = [
        0.151,
        0.021,
        0.160,
        0.324,
        0.137,
        0.014,
        0.120,
        0.218,
        -0.044,
        0.314,
        0.184,
        0.287,
        -0.181,
        0.262,
        0.241,
    ]

    cfg = ParticipatingProductConfig(
        issue_age=35,
        gender="M",
        projection_years=20,
        annual_premium=10_000.0,
        sum_assured=200_000.0,
        min_guaranteed_irr_yr20=0.015,
        policyholder_surplus_share=0.80,
        n_scenarios=1000,
        random_seed=42,
    )

    model = StochasticParticipatingModel(
        config=cfg,
        current_yield_curve=current_yield_curve,
        sp500_total_returns_15y=sp500_total_returns_15y,
        hk_mortality_table_csv=args.hk_mortality_csv,
    )
    out = model.run()

    print("=== Stochastic Participating Projection ===")
    print(f"Scenarios: {cfg.n_scenarios}")
    print(f"Policyholder IRR (mean cashflow basis): {out['policyholder_irr']:.4%}")
    print(f"Breakeven ratio at year 10: {out['breakeven_ratio_yr10']:.3f}")
    print(f"TVOG-like metric at year 20: {out['tvog_like']:.2f}")
