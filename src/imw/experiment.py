"""A/B readout for the synthetic CRM reactivation experiment (SYNTHETIC DEMO DATA).

Components
----------
* SRM check (chi-square on arm sizes) — run before looking at any outcome.
* Welch difference in means with a normal CI.
* CUPED: ``y_adj = y - theta * (x - mean(x))`` with ``theta = cov(x, y) / var(x)`` estimated on the
  pooled sample; ``x`` is the same metric in the pre-period. Variance shrinks by ``1 - rho^2``.
* Winsorisation as a *sensitivity analysis*, never silently: the effect is re-estimated for a grid
  of two-sided caps (NGR has both tails: whales and big wins) and every row shows how much of the
  metric was clipped.
* Sequential monitoring with mSPRT (Johari et al., 2017): a Gaussian mixture likelihood ratio on
  the running difference in means, giving always-valid p-values that can be checked daily.
* Harm guardrail: share of players hitting a deposit limit — a non-inferiority style test on the
  difference in proportions with a pre-registered margin.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ALPHA = 0.05


@dataclass(frozen=True)
class Estimate:
    effect: float
    se: float
    ci_low: float
    ci_high: float
    p_value: float
    mean_treatment: float
    mean_control: float
    n_treatment: int
    n_control: int

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def diff_in_means(y: np.ndarray, treated: np.ndarray, alpha: float = ALPHA) -> Estimate:
    y = np.asarray(y, dtype=float)
    t = np.asarray(treated, dtype=bool)
    yt, yc = y[t], y[~t]
    nt, nc = len(yt), len(yc)
    if nt < 2 or nc < 2:
        raise ValueError("need at least two units per arm")
    effect = float(yt.mean() - yc.mean())
    se = math.sqrt(yt.var(ddof=1) / nt + yc.var(ddof=1) / nc)
    z = stats.norm.ppf(1 - alpha / 2)
    p = float(2 * stats.norm.sf(abs(effect) / se)) if se > 0 else float("nan")
    return Estimate(effect, se, effect - z * se, effect + z * se, p, float(yt.mean()), float(yc.mean()), nt, nc)


def cuped_theta(y: np.ndarray, x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    vx = x.var(ddof=1)
    if vx == 0:
        return 0.0
    return float(np.cov(x, y, ddof=1)[0, 1] / vx)


def cuped_adjust(y: np.ndarray, x: np.ndarray, theta: float | None = None) -> tuple[np.ndarray, float]:
    """Return CUPED-adjusted outcome and theta. theta is pooled across arms (no leakage of arm)."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    th = cuped_theta(y, x) if theta is None else theta
    return y - th * (x - x.mean()), th


def winsorize(y: np.ndarray, q: float | None, lower: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    """Two-sided winsorisation at quantiles (q, 1-q). Returns (capped, number of capped units).

    Quantiles are computed on the pooled sample (both arms) so the cap does not depend on arm.
    ``q=None`` means no capping.
    """
    y = np.asarray(y, dtype=float)
    if q is None or q <= 0:
        return y.copy(), 0
    ref = y if lower is None else lower
    lo, hi = np.quantile(ref, q), np.quantile(ref, 1 - q)
    capped = np.clip(y, lo, hi)
    return capped, int(np.sum((y < lo) | (y > hi)))


def sensitivity_table(
    y: np.ndarray,
    x: np.ndarray,
    treated: np.ndarray,
    caps: tuple[float | None, ...] = (None, 0.001, 0.005, 0.01, 0.025, 0.05),
) -> pd.DataFrame:
    rows = []
    abs_total = float(np.abs(y).sum()) or 1.0
    for q in caps:
        yw, n_capped = winsorize(y, q)
        xw, _ = winsorize(x, q)
        raw = diff_in_means(yw, treated)
        adj, theta = cuped_adjust(yw, xw)
        cup = diff_in_means(adj, treated)
        rows.append(
            {
                "cap": "none" if q is None else f"p{100 * q:g} / p{100 * (1 - q):g}",
                "players_capped": n_capped,
                "share_of_abs_metric_clipped": float(np.abs(y - yw).sum() / abs_total),
                "effect": raw.effect,
                "ci_low": raw.ci_low,
                "ci_high": raw.ci_high,
                "p_value": raw.p_value,
                "cuped_effect": cup.effect,
                "cuped_ci_low": cup.ci_low,
                "cuped_ci_high": cup.ci_high,
                "cuped_p_value": cup.p_value,
                "cuped_theta": theta,
                "variance_reduction": 1 - (cup.se / raw.se) ** 2 if raw.se > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def msprt_lambda(delta: float, v: float, tau2: float) -> float:
    """Gaussian-mixture likelihood ratio for H0: effect = 0, given estimate ``delta`` with variance ``v``.

    Lambda = sqrt(v / (v + tau2)) * exp(tau2 * delta^2 / (2 v (v + tau2))).
    """
    if v <= 0:
        return 1.0
    log_l = 0.5 * math.log(v / (v + tau2)) + tau2 * delta**2 / (2 * v * (v + tau2))
    return math.exp(min(log_l, 700.0))


def msprt_monitor(looks: list[tuple[float, float]], tau2: float, alpha: float = ALPHA) -> pd.DataFrame:
    """Always-valid p-values over a sequence of looks ``(delta_k, var_k)``.

    p_k = min(p_{k-1}, 1 / Lambda_k); stop at the first look with p_k <= alpha.
    """
    p_prev = 1.0
    rows = []
    for k, (delta, v) in enumerate(looks, start=1):
        lam = msprt_lambda(delta, v, tau2)
        p_prev = min(p_prev, 1.0 / lam)
        rows.append(
            {
                "look": k,
                "effect": delta,
                "se": math.sqrt(v),
                "lambda": lam,
                "always_valid_p": p_prev,
                "reject": p_prev <= alpha,
            }
        )
    return pd.DataFrame(rows)


def sequential_readout(daily: pd.DataFrame, metric: str, tau: float, alpha: float = ALPHA) -> pd.DataFrame:
    """mSPRT over experiment days using cumulative per-player outcomes (one look per day)."""
    looks = []
    days = sorted(daily["day_index"].unique())
    for d in days:
        snap = daily[daily["day_index"] == d]
        est = diff_in_means(snap[metric].to_numpy(), (snap["arm"] == "treatment").to_numpy())
        looks.append((est.effect, est.se**2))
    out = msprt_monitor(looks, tau**2, alpha)
    out.insert(0, "day_index", days)
    # naive fixed-horizon p-value at each look, to show why peeking at it is misleading
    out["naive_p"] = [
        float(2 * stats.norm.sf(abs(e) / s)) if s > 0 else float("nan")
        for e, s in zip(out["effect"], out["se"], strict=True)
    ]
    return out


def srm_check(n_treatment: int, n_control: int, expected_share: float = 0.5) -> dict[str, float]:
    n = n_treatment + n_control
    exp = np.array([n * expected_share, n * (1 - expected_share)])
    chi2 = float(((np.array([n_treatment, n_control]) - exp) ** 2 / exp).sum())
    p = float(stats.chi2.sf(chi2, df=1))
    return {"n_treatment": n_treatment, "n_control": n_control, "chi2": chi2, "p_value": p, "srm_detected": p < 0.001}


def guardrail_proportion(hit: np.ndarray, treated: np.ndarray, margin: float, alpha: float = ALPHA) -> dict[str, Any]:
    """Harm guardrail on a proportion (higher = worse).

    status: ``breach`` if the lower one-sided bound of (p_t - p_c) is above the margin
    (harm demonstrated), ``pass`` if the upper bound is below the margin (harm ruled out),
    otherwise ``inconclusive`` (cannot rule out harm -> do not ship on revenue alone).
    """
    h = np.asarray(hit, dtype=float)
    t = np.asarray(treated, dtype=bool)
    pt, pc = h[t].mean(), h[~t].mean()
    se = math.sqrt(pt * (1 - pt) / t.sum() + pc * (1 - pc) / (~t).sum())
    z = stats.norm.ppf(1 - alpha)
    diff = pt - pc
    lo, hi = diff - z * se, diff + z * se
    status = "breach" if lo > margin else ("pass" if hi < margin else "inconclusive")
    return {
        "metric": "share_hitting_deposit_limit",
        "rate_treatment": pt,
        "rate_control": pc,
        "diff": diff,
        "one_sided_low": lo,
        "one_sided_high": hi,
        "margin": margin,
        "status": status,
    }


def load_experiment(db: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        player = con.sql("select * from marts.mart_experiment_player").df()
        daily = con.sql(
            "select player_id, arm, day_index, cum_ngr_eur, cum_deposits_eur from marts.mart_experiment_daily"
        ).df()
    finally:
        con.close()
    return player, daily


def readout(
    player: pd.DataFrame, daily: pd.DataFrame, guardrail_margin: float = 0.01, tau_in_pre_sd: float = 0.1
) -> dict[str, Any]:
    treated = (player["arm"] == "treatment").to_numpy()
    srm = srm_check(int(treated.sum()), int((~treated).sum()))
    results: dict[str, Any] = {"label": "SYNTHETIC DEMO DATA", "srm": srm, "metrics": {}, "sequential": {}}
    for metric, pre in (("post_ngr_eur", "pre_ngr_eur"), ("post_deposits_eur", "pre_deposits_eur")):
        y = player[metric].to_numpy(dtype=float)
        x = player[pre].to_numpy(dtype=float)
        raw = diff_in_means(y, treated)
        adj, theta = cuped_adjust(y, x)
        cup = diff_in_means(adj, treated)
        sens = sensitivity_table(y, x, treated)
        results["metrics"][metric] = {
            "raw": raw.as_dict(),
            "cuped": cup.as_dict(),
            "theta": theta,
            "corr_pre_post": float(np.corrcoef(x, y)[0, 1]),
            "variance_reduction": 1 - (cup.se / raw.se) ** 2,
            "sensitivity": sens.to_dict(orient="records"),
        }
        # mSPRT mixing scale is pre-registered in units of the PRE-period sd (known before launch).
        tau = tau_in_pre_sd * float(np.std(x, ddof=1) or 1.0)
        cum = "cum_ngr_eur" if metric == "post_ngr_eur" else "cum_deposits_eur"
        results["sequential"][metric] = sequential_readout(daily, cum, tau).to_dict(orient="records")
    results["guardrail"] = guardrail_proportion(player["hit_deposit_limit_post"].to_numpy(), treated, guardrail_margin)
    ngr = results["metrics"]["post_ngr_eur"]
    dep = results["metrics"]["post_deposits_eur"]
    seq_ngr = results["sequential"]["post_ngr_eur"]
    first_reject = next((r["day_index"] for r in seq_ngr if r["reject"]), None)
    results["summary"] = {
        "n_players": len(player),
        "srm_detected": srm["srm_detected"],
        "ngr_effect_raw": ngr["raw"]["effect"],
        "ngr_p_raw": ngr["raw"]["p_value"],
        "ngr_effect_cuped": ngr["cuped"]["effect"],
        "ngr_p_cuped": ngr["cuped"]["p_value"],
        "ngr_variance_reduction": ngr["variance_reduction"],
        "deposits_effect_cuped": dep["cuped"]["effect"],
        "deposits_p_cuped": dep["cuped"]["p_value"],
        "deposits_variance_reduction": dep["variance_reduction"],
        "msprt_ngr_first_reject_day": first_reject,
        "msprt_ngr_final_always_valid_p": seq_ngr[-1]["always_valid_p"],
        "guardrail_status": results["guardrail"]["status"],
        "guardrail_diff": results["guardrail"]["diff"],
    }
    return results
