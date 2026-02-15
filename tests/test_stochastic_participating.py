import numpy as np
import pandas as pd

from par_model_v2.liabilities import HKMortalityTable, ParticipatingProductConfig, StochasticParticipatingModel


def _build_hk_table(tmp_path):
    ages = np.arange(20, 121)
    male = np.clip(0.0004 + 0.000003 * np.exp(0.082 * np.maximum(ages - 30, 0)), 0, 0.5)
    female = np.clip(0.0003 + 0.0000025 * np.exp(0.080 * np.maximum(ages - 30, 0)), 0, 0.5)
    path = tmp_path / "hk_mortality.csv"
    pd.DataFrame({"attained_age": ages, "male": male, "female": female}).to_csv(path, index=False)
    return path


def test_hk_mortality_table_lookup(tmp_path):
    path = _build_hk_table(tmp_path)
    table = HKMortalityTable(str(path))
    assert table.qx(40, "M") > table.qx(40, "F")
    assert table.qx(80, "M") > table.qx(40, "M")


def test_stochastic_participating_shapes_and_targets(tmp_path):
    curve = {1: 0.04, 2: 0.039, 5: 0.036, 10: 0.034, 20: 0.035, 30: 0.036}
    spx = [0.12, 0.03, 0.16, 0.28, 0.10, -0.04, 0.22, 0.19, 0.15, 0.02, 0.14, 0.31, -0.18, 0.25, 0.24]

    cfg = ParticipatingProductConfig(n_scenarios=1000, projection_years=20, random_seed=123, gender="M")
    model = StochasticParticipatingModel(cfg, curve, spx, str(_build_hk_table(tmp_path)))
    out = model.run()

    assert out["short_rate_paths"].shape == (1000, 20)
    assert out["equity_return_paths"].shape == (1000, 20)
    assert len(out["premium_cf"]) == 20
    assert len(out["death_cf_mean"]) == 20
    assert np.isfinite(out["tvog_like"])
    assert out["policyholder_irr"] >= 0.005
    assert out["breakeven_ratio_yr10"] >= 0.95
