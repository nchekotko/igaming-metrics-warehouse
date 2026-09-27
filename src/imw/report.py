"""Static HTML report (matplotlib -> base64 PNG + HTML tables). SYNTHETIC DEMO DATA."""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

from imw.config import REPORTS_DIR, SYNTHETIC_LABEL

# Reference categorical order (validated palette): blue, orange, aqua, yellow.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
CHANNEL_COLOR = {"affiliate": SERIES[0], "organic": SERIES[1], "paid_search": SERIES[2], "paid_social": SERIES[3]}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BLUES = LinearSegmentedColormap.from_list(
    "seq_blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
)
CUTOFF = "2025-09-01"
STATUS = {"pass": ("#008300", "✓ pass"), "warn": ("#b07a00", "▲ warn"), "fail": ("#c62828", "✕ fail")}


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _png(fig: plt.Figure, name: str, img_dir: Path | None) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    data = buf.getvalue()
    if img_dir is not None:
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / f"{name}.png").write_bytes(data)
    return base64.b64encode(data).decode()


def _table(df: pd.DataFrame, fmt: dict[str, str] | None = None) -> str:
    fmt = fmt or {}
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c == "status" and v in STATUS:
                color, label = STATUS[v]
                cells.append(f'<td><span class="st" style="color:{color}">{label}</span></td>')
                continue
            s = fmt[c].format(v) if c in fmt and pd.notna(v) else ("" if pd.isna(v) else str(v))
            cells.append(f"<td>{html.escape(s)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def cohort_heatmap(ret: pd.DataFrame, img_dir: Path | None) -> str:
    piv = ret.pivot(index="ftd_month", columns="months_since_ftd", values="retention_rate")
    piv = piv.drop(columns=[0], errors="ignore")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    im = ax.imshow(piv.values, cmap=BLUES, vmin=0, vmax=max(0.6, np.nanmax(piv.values)), aspect="auto")
    ax.set_xticks(range(len(piv.columns)), [f"M+{c}" for c in piv.columns])
    sizes = ret.drop_duplicates("ftd_month").set_index("ftd_month")["cohort_size"]
    ax.set_yticks(range(len(piv.index)), [f"{pd.Timestamp(i):%Y-%m}  (n={sizes[i]})" for i in piv.index])
    for (i, j), v in np.ndenumerate(piv.values):
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=8, color="white" if v > 0.3 else INK)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Retention by FTD cohort (share active in month N)", loc="left", color=INK, fontsize=11)
    fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02).outline.set_visible(False)
    return _png(fig, "cohort_retention", img_dir)


def ngr_by_channel(ch: pd.DataFrame, img_dir: Path | None) -> str:
    fig, ax = plt.subplots(figsize=(8, 3.8))
    _style(ax)
    for name, color in CHANNEL_COLOR.items():
        s = ch[ch["channel"] == name].sort_values("activity_month")
        if s.empty:
            continue
        x = pd.to_datetime(s["activity_month"])
        ax.plot(x, s["ngr_eur"] / 1000, color=color, linewidth=2, marker="o", markersize=4, label=name)
    ax.axhline(0, color=INK2, linewidth=0.8)
    ax.axvline(pd.Timestamp("2025-06-01"), color=INK2, linestyle=":", linewidth=1)
    ax.text(pd.Timestamp("2025-06-03"), ax.get_ylim()[1] * 0.95, "NGR v2 from Jun", fontsize=8, color=INK2)
    ax.set_ylabel("NGR, kEUR (reported)", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper left", bbox_to_anchor=(0, -0.12))
    ax.set_title("Reported NGR by channel (point-in-time attribution)", loc="left", color=INK, fontsize=11)
    return _png(fig, "ngr_by_channel", img_dir)


def restatement_chart(rs: pd.DataFrame, img_dir: Path | None) -> str:
    fig, ax = plt.subplots(figsize=(8, 3.4))
    _style(ax)
    months = sorted(rs["activity_month"].unique())
    x = np.arange(len(months))
    w = 0.38
    for k, (ver, color) in enumerate(((1, SERIES[0]), (2, SERIES[1]))):
        vals = [
            rs[(rs.activity_month == m) & (rs.metric_version == ver)]["ngr_restated_eur"].sum() / 1000 for m in months
        ]
        ax.bar(x + (k - 0.5) * (w + 0.02), vals, width=w, color=color, label=f"NGR v{ver}")
    rep = [rs[rs.activity_month == m]["ngr_reported_eur"].iloc[0] / 1000 for m in months]
    ax.plot(x, rep, color=INK, marker="D", markersize=4, linewidth=0, label="reported (applicable)")
    ax.set_xticks(x, [f"{pd.Timestamp(m):%b}" for m in months])
    ax.set_ylabel("kEUR", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper left", bbox_to_anchor=(0, -0.1))
    ax.set_title("Same months, two definitions: NGR v1 vs v2 restated", loc="left", color=INK, fontsize=11)
    return _png(fig, "ngr_restatement", img_dir)


def sequential_chart(seq: list[dict[str, Any]], img_dir: Path | None) -> str:
    df = pd.DataFrame(seq)
    fig, ax = plt.subplots(figsize=(8, 3.2))
    _style(ax)
    ax.plot(df["day_index"], df["naive_p"], color=SERIES[1], linewidth=2, label="naive fixed-horizon p")
    ax.plot(df["day_index"], df["always_valid_p"], color=SERIES[0], linewidth=2, label="mSPRT always-valid p")
    ax.axhline(0.05, color=INK2, linestyle=":", linewidth=1)
    ax.set_yscale("log")
    ax.set_xlabel("experiment day", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper left", bbox_to_anchor=(0, -0.18))
    ax.set_title("Sequential monitoring, deposits per player", loc="left", color=INK, fontsize=11)
    return _png(fig, "sequential_deposits", img_dir)


def build_report(db: Path, out: Path, img_dir: Path | None = None) -> Path:
    con = duckdb.connect(str(db), read_only=True)
    try:
        q = lambda s: con.sql(s).df()  # noqa: E731
        ret = q("select * from marts.mart_cohort_retention order by 1, 2")
        # complete months only (the last simulated day's sessions spill into September)
        ch = q(f"select * from marts.mart_ngr_monthly_by_channel where activity_month < '{CUTOFF}' order by 1, 2")
        rs = q(f"select * from marts.mart_metric_restatement where activity_month < '{CUTOFF}' order by 1, 2")
        dq = q(
            "select check_name, entity, value, warn_above, fail_above, status from dq.dq_summary "
            "order by case status when 'fail' then 0 when 'warn' then 1 else 2 end, check_name, entity"
        )
        aff = q("""select affiliate_id, any_value(deal_type) deal, sum(ftd) ftd, sum(qftd) qftd,
                          sum(ftd_reattributed_later) reattributed, sum(ngr_eur) ngr_eur,
                          sum(affiliate_cost_eur) cost_eur, sum(contribution_eur) contribution_eur
                   from marts.mart_affiliate_performance group by 1 order by contribution_eur""")
        kpi = q("""select count(distinct player_id) filter (where is_active) active_players,
                          sum(deposits_eur) deposits, sum(ggr_eur) ggr, sum(ngr_eur) ngr
                   from marts.fct_player_daily""").iloc[0]
        ltv = q("select * from marts.mart_cohort_ltv order by 1, 2")
    finally:
        con.close()

    exp_path = REPORTS_DIR / "experiment_readout.json"
    exp = json.loads(exp_path.read_text(encoding="utf-8")) if exp_path.exists() else None

    img = {
        "cohort": cohort_heatmap(ret, img_dir),
        "channel": ngr_by_channel(ch, img_dir),
        "restate": restatement_chart(rs, img_dir),
    }
    restate_tbl = (
        rs[rs.activity_month < pd.Timestamp("2025-06-01")]
        .groupby("metric_version")
        .agg(ngr_restated_eur=("ngr_restated_eur", "sum"), ngr_reported_eur=("ngr_reported_eur", "sum"))
        .reset_index()
    )
    restate_tbl["delta_pct"] = restate_tbl["ngr_restated_eur"] / restate_tbl["ngr_reported_eur"] - 1
    ltv_last = (
        ltv.sort_values("months_since_ftd")
        .groupby("ftd_month")
        .tail(1)[["ftd_month", "cohort_size", "months_since_ftd", "cumulative_ngr_per_ftd_eur"]]
    )
    ltv_last["ftd_month"] = pd.to_datetime(ltv_last["ftd_month"]).dt.strftime("%Y-%m")

    exp_html = "<p>Run <code>imw experiment</code> first.</p>"
    if exp:
        img["seq"] = sequential_chart(exp["sequential"]["post_deposits_eur"], img_dir)
        s = exp["summary"]
        sens = pd.DataFrame(exp["metrics"]["post_ngr_eur"]["sensitivity"])[
            [
                "cap",
                "players_capped",
                "share_of_abs_metric_clipped",
                "effect",
                "p_value",
                "cuped_effect",
                "cuped_ci_low",
                "cuped_ci_high",
                "cuped_p_value",
                "variance_reduction",
            ]
        ]
        g = exp["guardrail"]
        exp_html = f"""
        <p>{s["n_players"]} randomised players, SRM detected: <b>{s["srm_detected"]}</b>.
        Deposits/player (CUPED): <b>{s["deposits_effect_cuped"]:+.2f} EUR</b>, p={s["deposits_p_cuped"]:.2g},
        variance reduction {s["deposits_variance_reduction"]:.0%}.
        NGR/player (CUPED): <b>{s["ngr_effect_cuped"]:+.2f} EUR</b>, p={s["ngr_p_cuped"]:.2g},
        variance reduction {s["ngr_variance_reduction"]:.1%} (heavy tails kill the pre/post correlation).</p>
        <p>Guardrail <i>{g["metric"]}</i>: treatment {g["rate_treatment"]:.2%} vs control
        {g["rate_control"]:.2%}, diff {g["diff"]:+.2%}, one-sided 95% upper {g["one_sided_high"]:+.2%},
        margin {g["margin"]:.0%} → <span class="st" style="color:{
            STATUS.get({"pass": "pass", "breach": "fail"}.get(g["status"], "warn"))[0]
        }">{g["status"]}</span></p>
        <h3>Winsorisation sensitivity — NGR per player</h3>
        {_table(sens, {c: "{:,.3f}" for c in sens.columns if c not in ("cap", "players_capped")})}
        <img src="data:image/png;base64,{img["seq"]}" alt="sequential monitoring">"""

    n_fail = int((dq.status == "fail").sum())
    n_warn = int((dq.status == "warn").sum())
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>iGaming Metrics Warehouse</title>
<style>
:root {{ --ink:{INK}; --ink2:{INK2}; --grid:{GRID}; --surface:{SURFACE}; }}
body {{ margin:0; background:var(--surface); color:var(--ink); font:14px/1.5 system-ui, sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }}
.banner {{ background:#fff4d6; border:1px solid #eda100; padding:8px 12px; border-radius:6px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ border:1px solid var(--grid); border-radius:8px; padding:12px; background:#fff; }}
.kpi b {{ display:block; font-size:22px; }} .kpi span {{ color:var(--ink2); font-size:12px; }}
img {{ max-width:100%; height:auto; display:block; margin:12px 0; }}
table {{ border-collapse:collapse; width:100%; font-size:12px; margin:8px 0 16px; display:block; overflow-x:auto; }}
th, td {{ border-bottom:1px solid var(--grid); padding:4px 8px; text-align:right; white-space:nowrap; }}
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align:left; }}
.st {{ font-weight:600; }} h2 {{ margin-top:36px; }} code {{ background:#f0efec; padding:0 4px; }}
</style></head><body><main>
<h1>iGaming Metrics Warehouse — report</h1>
<p class="banner"><b>{html.escape(SYNTHETIC_LABEL)}</b>. Numbers illustrate the pipeline, not any real operator.</p>
<div class="kpis">
 <div class="kpi"><span>Active players</span><b>{int(kpi.active_players):,}</b></div>
 <div class="kpi"><span>Deposits, EUR</span><b>{kpi.deposits:,.0f}</b></div>
 <div class="kpi"><span>GGR, EUR</span><b>{kpi.ggr:,.0f}</b></div>
 <div class="kpi"><span>NGR reported, EUR</span><b>{kpi.ngr:,.0f}</b></div>
 <div class="kpi"><span>DQ checks</span><b>{len(dq) - n_fail - n_warn} ✓ · {n_warn} ▲ · {n_fail} ✕</b></div>
</div>
<h2>Data quality status</h2>
{_table(dq, {"value": "{:.4f}", "warn_above": "{:g}", "fail_above": "{:g}"})}
<h2>Cohort retention</h2>
<img src="data:image/png;base64,{img["cohort"]}" alt="cohort retention heatmap">
<h3>Cumulative NGR per FTD (LTV to date, NGR v{int(ltv["ngr_metric_version"].iloc[0])} for all cohorts)</h3>
{_table(ltv_last, {"cumulative_ngr_per_ftd_eur": "{:,.1f}"})}
<h2>NGR by channel</h2>
<img src="data:image/png;base64,{img["channel"]}" alt="NGR by channel">
<h2>Metric definition change (restatement)</h2>
<img src="data:image/png;base64,{img["restate"]}" alt="NGR restatement">
<p>Jan–May totals: reported (v1) vs restated under each version.</p>
{_table(restate_tbl, {"ngr_restated_eur": "{:,.0f}", "ngr_reported_eur": "{:,.0f}", "delta_pct": "{:+.1%}"})}
<h2>Affiliate performance (deal attribution at FTD)</h2>
{_table(aff, {c: "{:,.0f}" for c in ("ngr_eur", "cost_eur", "contribution_eur")})}
<h2>CRM experiment readout</h2>
{exp_html}
</main></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out
