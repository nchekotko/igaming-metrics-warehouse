import numpy as np
import pandas as pd
import pytest

from imw.experiment import (
    cuped_adjust,
    diff_in_means,
    guardrail_proportion,
    msprt_monitor,
    readout,
    sensitivity_table,
    srm_check,
    winsorize,
)


def correlated(n: int, rho: float, effect: float, seed: int):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n)
    y = rho * x + np.sqrt(1 - rho**2) * rng.normal(0, 1, n)
    t = rng.random(n) < 0.5
    return y + effect * t, x, t


@pytest.mark.parametrize("rho", [0.3, 0.6, 0.9])
def test_cuped_variance_reduction_matches_theory(rho):
    y, x, t = correlated(200_000, rho, 0.0, seed=1)
    adj, theta = cuped_adjust(y, x)
    assert theta == pytest.approx(rho, abs=0.01)
    reduction = 1 - adj.var() / y.var()
    assert reduction == pytest.approx(rho**2, abs=0.01)
    se_raw = diff_in_means(y, t).se
    se_cuped = diff_in_means(adj, t).se
    assert (se_cuped / se_raw) ** 2 == pytest.approx(1 - rho**2, abs=0.02)


def test_cuped_is_unbiased():
    effects = []
    for s in range(200):
        y, x, t = correlated(2_000, 0.7, 0.25, seed=100 + s)
        adj, _ = cuped_adjust(y, x)
        effects.append(diff_in_means(adj, t).effect)
    assert np.mean(effects) == pytest.approx(0.25, abs=0.01)


def test_cuped_with_uncorrelated_covariate_changes_nothing_much():
    rng = np.random.default_rng(3)
    y, x = rng.normal(size=50_000), rng.normal(size=50_000)
    adj, theta = cuped_adjust(y, x)
    assert abs(theta) < 0.02
    assert adj.var() == pytest.approx(y.var(), rel=0.01)


def test_winsorize_two_sided():
    y = np.arange(1000, dtype=float)
    capped, n = winsorize(y, 0.01)
    assert n == 20
    assert capped.min() == pytest.approx(np.quantile(y, 0.01))
    assert capped.max() == pytest.approx(np.quantile(y, 0.99))
    same, n0 = winsorize(y, None)
    assert n0 == 0 and np.array_equal(same, y)


def test_sensitivity_table_shape():
    y, x, t = correlated(5_000, 0.5, 0.1, seed=5)
    tbl = sensitivity_table(y, x, t)
    assert tbl["cap"].iloc[0] == "none"
    assert tbl["players_capped"].is_monotonic_increasing


def test_msprt_controls_type_one_error_under_peeking():
    rng = np.random.default_rng(11)
    rejections = naive = 0
    sims, looks, n_per_look = 400, 20, 50
    for _ in range(sims):
        a = rng.normal(size=looks * n_per_look)
        b = rng.normal(size=looks * n_per_look)
        seq = []
        naive_hit = False
        for k in range(1, looks + 1):
            m = k * n_per_look
            d = a[:m].mean() - b[:m].mean()
            v = a[:m].var(ddof=1) / m + b[:m].var(ddof=1) / m
            seq.append((d, v))
            naive_hit |= abs(d) / np.sqrt(v) > 1.96
        rejections += bool(msprt_monitor(seq, tau2=0.1**2)["reject"].any())
        naive += naive_hit
    assert rejections / sims <= 0.06  # always-valid
    assert naive / sims > 0.15  # peeking at fixed-horizon p inflates type I error


def test_msprt_detects_real_effect():
    rng = np.random.default_rng(12)
    a = rng.normal(0.3, 1, 4000)
    b = rng.normal(0.0, 1, 4000)
    seq = [(a[:m].mean() - b[:m].mean(), a[:m].var() / m + b[:m].var() / m) for m in range(200, 4001, 200)]
    assert msprt_monitor(seq, tau2=0.1**2)["reject"].any()


def test_srm():
    assert not srm_check(5000, 5050)["srm_detected"]
    assert srm_check(5000, 5600)["srm_detected"]


def test_guardrail_statuses():
    t = np.array([True] * 5000 + [False] * 5000)
    same = np.r_[np.zeros(4900), np.ones(100), np.zeros(4900), np.ones(100)]
    assert guardrail_proportion(same, t, margin=0.01)["status"] == "pass"
    harm = np.r_[np.zeros(4500), np.ones(500), np.zeros(4900), np.ones(100)]
    assert guardrail_proportion(harm, t, margin=0.01)["status"] == "breach"


def test_readout_end_to_end():
    rng = np.random.default_rng(21)
    n = 2000
    pre = rng.lognormal(3, 1, n)
    arm = np.where(rng.random(n) < 0.5, "treatment", "control")
    post = pre * 0.8 + rng.normal(0, 5, n) + (arm == "treatment") * 5
    player = pd.DataFrame(
        {
            "player_id": range(n),
            "arm": arm,
            "pre_ngr_eur": pre,
            "post_ngr_eur": post,
            "pre_deposits_eur": pre,
            "post_deposits_eur": post,
            "hit_deposit_limit_post": rng.random(n) < 0.02,
        }
    )
    daily = pd.concat(
        [
            pd.DataFrame(
                {
                    "player_id": range(n),
                    "arm": arm,
                    "day_index": d,
                    "cum_ngr_eur": post * d / 5,
                    "cum_deposits_eur": post * d / 5,
                }
            )
            for d in range(1, 6)
        ]
    )
    res = readout(player, daily)
    s = res["summary"]
    assert s["ngr_variance_reduction"] > 0.5
    assert s["ngr_p_cuped"] < s["ngr_p_raw"]
    assert len(res["sequential"]["post_ngr_eur"]) == 5
