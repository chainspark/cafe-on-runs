"""Pace tolerance: how pace relates to heart rate, temperature and time of day.

Every Toronto run is cut into full 1 km splits (the last partial km is dropped). For each split:
moving pace (stops removed), mean heart rate, elevation change, which km of the run it is, the
hourly air temperature at the split's midpoint (Open-Meteo, one Toronto series) and the local
time of day.

* Level - one row per split: pace (s/km) regressed on HR, temperature and time of day, with
  controls for km into the run, first km, elevation change and stopped time. Standard errors
  are clustered by run. A banded-temperature variant gives the pace at the same effort in
  each temperature band.
* Consistency - one row per run (at least 3 splits after km 1): the SD of the run's split paces,
  regressed on the run's mean HR, mid-run temperature and time of day, with controls for
  distance, climbing and stopped time.

Each clear effect is re-checked without outlier splits. Private output: output/pace_tolerance.html.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .analyze import fmt_pace, load_runs
from .heatmap import toronto_only
from .parse import TRACKS
from .segments import OUT, temperatures
from .tolerance import MIN_GROUP, TOD, _effects_table, ols_cluster

TZ = "America/Toronto"
TORONTO_WEATHER = (43.65, -79.38)   # one city-wide series (rounded to 0.1° before the request)
STOP_SPEED = 1.0                    # m/s; slower steps are stops (lights, pauses), not running
BANDS = ([-40, 0, 10, 20, 50], ["Below 0 °C", "0–10 °C", "10–20 °C", "20 °C +"])
BASE_BAND = "0–10 °C"

LEVEL_LABELS = {"hr": "10 bpm higher heart rate", "temp": "10 °C warmer", "midday": "Midday vs morning",
                "evening": "Evening vs morning", "km_idx": "1 km further into the run (control)",
                "first_km": "First km of the run (control)", "elev": "10 m climb in the km (control)",
                "stopped": "1 min stopped in the km (control)"}
CONS_LABELS = {"hr": "10 bpm higher run HR", "temp": "10 °C warmer", "midday": "Midday vs morning",
               "evening": "Evening vs morning", "dist": "1 km longer run (control)",
               "climb": "10 m more climb per km (control)", "stopped": "1 min more stopped (control)"}


# ---------- splits ----------
def splits() -> pd.DataFrame:
    runs = load_runs()
    t = pd.read_parquet(TRACKS)
    t = toronto_only(t[t["activity_id"].isin(runs["activity_id"])]).sort_values(["activity_id", "ts"])
    rows = []
    for aid, g in t.groupby("activity_id"):
        ts = pd.to_datetime(g["ts"], utc=True)
        sec = (ts - ts.iloc[0]).dt.total_seconds().to_numpy()
        d = g["distance_m"].to_numpy(float)
        d = d - d[0]
        hr, alt = g["hr"].to_numpy(float), g["alt_m"].to_numpy(float)
        dt, dd = np.diff(sec), np.diff(d)
        with np.errstate(divide="ignore", invalid="ignore"):
            stopped = (dd / dt) < STOP_SPEED
        km_of_step = np.floor(d[1:] / 1000).astype(int)     # a step belongs to the km it ends in
        n_full = int(d[-1] // 1000)
        for k in range(n_full):
            s = km_of_step == k
            if s.sum() < 5:
                continue
            idx = np.flatnonzero(s) + 1                      # point indices ending these steps
            moving_t = dt[s & ~stopped].sum()
            moving_d = dd[s & ~stopped].sum()
            if moving_d < 800:                               # mostly stopped / GPS gap
                continue
            mid = ts.iloc[idx[len(idx) // 2]]
            rows.append({"activity_id": aid, "km_idx": k + 1, "pace_s_km": moving_t / moving_d * 1000,
                         "hr": np.nanmean(hr[idx]), "elev_m": alt[idx[-1]] - alt[idx[0] - 1],
                         "climb_m": np.clip(np.diff(alt[np.r_[idx[0] - 1, idx]]), 0, None).sum(),
                         "stopped_s": dt[s & stopped].sum(), "mid_utc": mid})
    sp = pd.DataFrame(rows)
    temp = temperatures(*TORONTO_WEATHER, sp["mid_utc"].min().to_pydatetime(), datetime.now(timezone.utc))
    sp["temp_c"] = sp["mid_utc"].dt.round("h").map(temp)
    local = sp["mid_utc"].dt.tz_convert(TZ)
    sp["hour"] = local.dt.hour + local.dt.minute / 60
    sp["tod"] = pd.cut(sp["hour"], [x[1] for x in TOD] + [24], labels=[x[0] for x in TOD], right=False)
    sp["date"] = local.dt.strftime("%d %b %Y")
    return sp.dropna(subset=["temp_c", "hr"]).reset_index(drop=True)


def outliers(sp: pd.DataFrame) -> pd.Series:
    """Splits that are probably measurement problems, not normal running."""
    return ((sp["pace_s_km"] > 540) | (sp["pace_s_km"] < 180)      # slower than 9:00 / faster than 3:00
            | (sp["hr"] < 80) | (sp["hr"] > 205)                   # optical HR glitches
            | (sp["elev_m"].abs() > 60))                            # GPS / barometer jumps


# ---------- models ----------
def _tod_dummies(X: pd.DataFrame, tod: pd.Series) -> None:
    for name, *_ in TOD[1:]:
        X[name.lower()] = (tod == name).astype(float)


def level_X(sp: pd.DataFrame, med: dict, banded: bool = False) -> pd.DataFrame:
    X = pd.DataFrame(index=sp.index)
    X["hr"] = (sp["hr"] - med["hr"]) / 10
    if banded:
        band = pd.cut(sp["temp_c"], BANDS[0], labels=BANDS[1])
        for b in BANDS[1]:
            if b != BASE_BAND:
                X[b] = (band == b).astype(float)
    else:
        X["temp"] = (sp["temp_c"] - med["temp"]) / 10
    _tod_dummies(X, sp["tod"])
    X["km_idx"] = sp["km_idx"] - med["km_idx"]
    X["first_km"] = (sp["km_idx"] == 1).astype(float)
    X["elev"] = sp["elev_m"] / 10
    X["stopped"] = sp["stopped_s"] / 60
    return X


def per_run(sp: pd.DataFrame) -> pd.DataFrame:
    """One row per run: SD of split paces after km 1, plus the run's factors and controls."""
    s = sp[sp["km_idx"] > 1]
    g = s.groupby("activity_id")
    r = pd.DataFrame({"pace_sd": g["pace_s_km"].std(), "n": g.size(), "hr": g["hr"].mean(),
                      "temp_c": g["temp_c"].median(), "climb_per_km": g["climb_m"].mean(),
                      "stopped_s": sp.groupby("activity_id")["stopped_s"].sum(),
                      "dist_km": sp.groupby("activity_id")["km_idx"].max(),
                      "hour": sp.groupby("activity_id")["hour"].first(),
                      "date": sp.groupby("activity_id")["date"].first()})
    r = r[r["n"] >= 3].copy()
    r["tod"] = pd.cut(r["hour"], [x[1] for x in TOD] + [24], labels=[x[0] for x in TOD], right=False)
    return r.reset_index()


def cons_X(r: pd.DataFrame, med: dict) -> pd.DataFrame:
    X = pd.DataFrame(index=r.index)
    X["hr"] = (r["hr"] - med["hr"]) / 10
    X["temp"] = (r["temp_c"] - med["temp"]) / 10
    _tod_dummies(X, r["tod"])
    X["dist"] = r["dist_km"] - r["dist_km"].median()
    X["climb"] = r["climb_per_km"] / 10
    X["stopped"] = r["stopped_s"] / 60
    return X


def fit(sp: pd.DataFrame) -> dict:
    med = {"hr": sp["hr"].median(), "temp": sp["temp_c"].median(), "km_idx": sp["km_idx"].median()}
    runs = per_run(sp)
    return {
        "level": ols_cluster(sp["pace_s_km"], level_X(sp, med), sp["activity_id"]),
        "bands": ols_cluster(sp["pace_s_km"], level_X(sp, med, banded=True), sp["activity_id"]),
        "cons": ols_cluster(runs["pace_sd"], cons_X(runs, med), runs["activity_id"]),
        "runs": runs, "med": med,
    }


# ---------- page ----------
def _summary(full: dict, robust: dict, n_out: int, counts: pd.Series) -> str:
    lines = []
    for key, title, labels in (("level", "Pace", LEVEL_LABELS), ("cons", "Pace consistency (SD)", CONS_LABELS)):
        res = full[key]
        for r in res[(res.term != "intercept") & (res.p < 0.05)].itertuples():
            if "(control)" in labels[r.term]:
                continue
            rr = robust[key].set_index("term").loc[r.term]
            holds = rr["p"] < 0.05 and np.sign(rr["coef"]) == np.sign(r.coef)
            n = counts.get(r.term.capitalize())
            caution = (f" <span class='muted'>(only {n} {r.term} splits - treat with caution)</span>"
                       if n is not None and n < MIN_GROUP else "")
            word = "slower" if r.coef > 0 else "faster"
            if key == "cons":
                word = "more variable" if r.coef > 0 else "steadier"
            lines.append(f"<b>{title}</b>: {labels[r.term]} → {abs(r.coef):.1f} s/km {word} "
                         f"(95% CI {r.lo:+.1f} to {r.hi:+.1f}){caution} <span class='tag {'ok' if holds else 'no'}'>"
                         f"{'holds' if holds else 'does not hold'} without the {n_out} outlier splits</span>")
    # temperature can act non-linearly (heat hurts, cold doesn't): report clear bands too
    bands, rbands = full["bands"].set_index("term"), robust["bands"].set_index("term")
    for b in BANDS[1]:
        if b == BASE_BAND or b not in bands.index or bands.loc[b, "p"] >= 0.05:
            continue
        r, rr = bands.loc[b], rbands.loc[b]
        holds = rr["p"] < 0.05 and np.sign(rr["coef"]) == np.sign(r["coef"])
        lines.append(f"<b>Pace at the same heart rate</b>: {b} vs {BASE_BAND} → {abs(r['coef']):.1f} s/km "
                     f"{'slower' if r['coef'] > 0 else 'faster'} (95% CI {r['lo']:+.1f} to {r['hi']:+.1f}) "
                     f"<span class='tag {'ok' if holds else 'no'}'>{'holds' if holds else 'does not hold'} "
                     f"without the {n_out} outlier splits</span>")
    if not lines:
        return "<p>No factor has a clear (p &lt; 0.05) effect on pace or its consistency.</p>"
    return "<ul>" + "".join(f"<li>{s}</li>" for s in lines) + "</ul>"


def _bands_table(res: pd.DataFrame, sp: pd.DataFrame) -> str:
    base = float(res.loc[res.term == "intercept", "coef"].iloc[0])
    band = pd.cut(sp["temp_c"], BANDS[0], labels=BANDS[1])
    counts = band.value_counts()
    rows = []
    for b in BANDS[1]:
        if b == BASE_BAND:
            diff, ci, p = 0.0, "reference", ""
        else:
            r = res.set_index("term").loc[b]
            diff, ci, p = r["coef"], f"{r['lo']:+.1f} to {r['hi']:+.1f}", f"{r['p']:.2f}"
        rows.append(f"<tr><td>{b}</td><td class='num'>{int(counts.get(b, 0)):,}</td>"
                    f"<td class='num'>{fmt_pace((base + diff) / 60)} /km</td>"
                    f"<td class='num'>{diff:+.1f} s</td><td class='num muted'>{ci}</td><td class='num'>{p}</td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th>Air temperature</th><th class="num">Splits</th>'
            '<th class="num">Pace at the same effort</th><th class="num">vs 0–10 °C</th><th class="num">95% CI</th>'
            f'<th class="num">p</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def build() -> Path:
    sp = splits()
    out = outliers(sp)
    full, robust = fit(sp), fit(sp[~out].reset_index(drop=True))
    runs = full["runs"]
    counts = sp["tod"].value_counts()

    data = {
        "split": {"hr": sp["hr"].round(1).tolist(), "temp": sp["temp_c"].round(1).tolist(),
                  "hour": sp["hour"].round(2).tolist(), "pace": (sp["pace_s_km"] / 60).round(3).tolist(),
                  "label": [f"{d} · km {k}" for d, k in zip(sp["date"], sp["km_idx"])]},
        "run": {"temp": runs["temp_c"].round(1).tolist(), "hour": runs["hour"].round(2).tolist(),
                "sd": runs["pace_sd"].round(1).tolist(),
                "label": [f"{d} · {int(k)} km" for d, k in zip(runs["date"], runs["dist_km"])]},
    }
    tod_note = " · ".join(f"{k} {int(counts.get(k, 0)):,}" for k, *_ in TOD)
    page = (TEMPLATE
            .replace("__NSPLITS__", f"{len(sp):,}").replace("__NRUNS__", str(sp["activity_id"].nunique()))
            .replace("__PACE__", fmt_pace(sp["pace_s_km"].median() / 60))
            .replace("__HR__", f"{sp['hr'].median():.0f}")
            .replace("__SD__", f"{runs['pace_sd'].median():.0f}")
            .replace("__NCONS__", str(len(runs)))
            .replace("__TOD__", tod_note).replace("__NOUT__", str(int(out.sum())))
            .replace("__SUMMARY__", _summary(full, robust, int(out.sum()), counts))
            .replace("__LEVEL__", _effects_table(full["level"], "s/km", LEVEL_LABELS, "splits"))
            .replace("__BANDS__", _bands_table(full["bands"], sp))
            .replace("__CONS__", _effects_table(full["cons"], "s/km", CONS_LABELS, "runs"))
            .replace("__DATA__", json.dumps(data)))
    OUT.mkdir(exist_ok=True)
    path = OUT / "pace_tolerance.html"
    path.write_text(page, encoding="utf-8")
    sp.drop(columns=["mid_utc"]).to_csv(OUT / "pace_tolerance_splits.csv", index=False)

    print(f"{len(sp):,} splits from {sp['activity_id'].nunique()} runs; median pace "
          f"{fmt_pace(sp['pace_s_km'].median() / 60)} /km, HR {sp['hr'].median():.0f}; time of day: {tod_note}")
    for key, title in (("level", "Pace level (s/km)"), ("bands", "Pace by temperature band (s/km)"),
                       ("cons", "Pace consistency: SD of split paces (s/km)")):
        r = full[key]
        print(f"\n{title}  n={r.attrs['n']:,} clusters={r.attrs['clusters']} R²={r.attrs['r2']:.2f}")
        print(r.round(3).to_string(index=False))
    print(f"\nWithout {int(out.sum())} outlier splits (clear effects only):")
    for key in ("level", "cons"):
        r = robust[key]
        r = r[(r.term != "intercept") & (r.p < 0.05)]
        print(f"  {key}: " + (", ".join(f"{t} {c:+.1f} (p={p:.3f})" for t, c, p in zip(r.term, r.coef, r.p)) or "none"))
    print(f"\n-> {path}")
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pace Tolerance</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s1soft:rgba(42,120,214,.28)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s1soft:rgba(57,135,229,.30)}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:17px;margin:0 0 4px} h3{font-size:14px;margin:16px 0 4px}
.muted,.note{color:var(--ink2)} .note{font-size:12px;margin:2px 0 8px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:20px}
a{color:var(--s1);text-decoration:none;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:12px;margin-bottom:12px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.tile .label{color:var(--ink2);font-size:12px} .tile .value{font-size:24px;font-weight:600;margin-top:4px}
.tile .sub{color:var(--muted);font-size:12px;margin-top:2px}
.card{margin-bottom:12px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:8px;margin-top:8px}
.chart{height:260px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--ink2);font-weight:500;text-align:left;border-bottom:1px solid var(--axis);padding:6px 8px}
td{border-bottom:1px solid var(--grid);padding:6px 8px}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cicol{width:140px} svg.ci{width:130px;height:12px;display:block}
svg.ci .zero{stroke:var(--axis);stroke-width:1} svg.ci .bar{stroke:var(--muted);stroke-width:2}
svg.ci .bar.strong{stroke:var(--s1)} svg.ci .dot{fill:var(--muted)} svg.ci .dot.strong{fill:var(--s1)}
ul{margin:6px 0 0;padding-left:20px} li{margin:6px 0}
.tag{font-size:11px;border-radius:4px;padding:1px 6px;margin-left:6px;border:1px solid var(--axis);color:var(--ink2);white-space:nowrap}
.tag.ok::before{content:"✓ "} .tag.no::before{content:"✗ "}
</style></head><body><main>
<header><div><h1>Pace tolerance</h1>
<div class="muted">How your pace responds to heart rate, temperature and time of day · __NSPLITS__ one-km splits from __NRUNS__ Toronto runs</div></div>
<div><a href="report.html">← Running report</a> &nbsp; <a href="tolerance.html">Segment tolerance</a></div></header>

<section class="tiles">
 <div class="tile"><div class="label">Typical split pace</div><div class="value">__PACE__ /km</div><div class="sub">moving pace, stops removed</div></div>
 <div class="tile"><div class="label">Typical heart rate</div><div class="value">__HR__ bpm</div><div class="sub">median split average</div></div>
 <div class="tile"><div class="label">Typical pace variation</div><div class="value">±__SD__ s/km</div><div class="sub">SD of a run's splits after km 1 (__NCONS__ runs)</div></div>
 <div class="tile"><div class="label">Splits by time of day</div><div class="value" style="font-size:16px;margin-top:8px">__TOD__</div><div class="sub">morning &lt; 11:00 · evening ≥ 16:00</div></div>
</section>

<div class="card"><h2>What matters</h2>
<p class="note">Factors with a clear effect (p &lt; 0.05), each measured with the others held constant. Each is re-checked without __NOUT__ outlier splits (pace slower than 9:00 or faster than 3:00 /km, HR outside 80–205 bpm, or more than 60 m of elevation change in a km).</p>__SUMMARY__</div>

<div class="card"><h2>1 · Pace level</h2>
<p class="note">Each dot is one kilometre. Pace axis is reversed: higher is faster. Effects are in seconds per km: + is slower, − is faster.</p>
<div class="charts"><div id="p-hr" class="chart"></div><div id="p-temp" class="chart"></div><div id="p-hour" class="chart"></div></div>
<h3>Effects on pace</h3>__LEVEL__
<p class="note">Heart rate and pace drive each other: you're faster when your heart rate is higher, and your heart rate rises because you're faster. So read the heart-rate line as an association. Because heart rate is in the model, the temperature and time-of-day effects are <b>at the same heart rate</b>: how much speed the same effort buys you.</p>
<h3>Pace at the same effort, by air temperature</h3>
<p class="note">Predicted pace for a morning km at your median heart rate, mid-run, flat, with no stops.</p>__BANDS__</div>

<div class="card"><h2>2 · Pace consistency</h2>
<p class="note">Each dot is one run: how much its kilometre splits vary (standard deviation, km 1 excluded). Lower is steadier.</p>
<div class="charts"><div id="c-temp" class="chart"></div><div id="c-hour" class="chart"></div></div>
<h3>Effects on pace variation</h3>__CONS__</div>

<p class="note">Method: linear regressions with all factors together. The level model has one row per km, with standard errors clustered by run because kms of the same run aren't independent. The consistency model has one row per run. Moving pace excludes steps slower than 1 m/s (lights, pauses). Air temperature is hourly, from Open-Meteo, for Toronto. With many tests on one page, an occasional "clear effect" can appear by chance: trust the large, physically sensible ones with tight intervals.</p>
</main>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pf = v => { const m = Math.floor(v), s = Math.round((v - m) * 60); return s === 60 ? `${m+1}:00` : `${m}:${String(s).padStart(2,'0')}`; };
function layout(xt, yt, x = {}, y = {}) {
  const ax = {gridcolor: css('--grid'), linecolor: css('--axis'), zeroline: false, fixedrange: true,
              tickfont: {color: css('--muted'), size: 11}, title: {font: {size: 11, color: css('--muted')}}};
  return {paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', showlegend: false,
    font: {family: 'system-ui,-apple-system,Segoe UI,sans-serif', color: css('--ink2')},
    margin: {l: 48, r: 8, t: 6, b: 40},
    hoverlabel: {bgcolor: css('--surface'), bordercolor: css('--axis'), font: {color: css('--ink')}},
    xaxis: Object.assign({}, ax, {title: Object.assign({}, ax.title, {text: xt})}, x),
    yaxis: Object.assign({}, ax, {title: Object.assign({}, ax.title, {text: yt})}, y)};
}
function dots(id, src, xk, yk, xt, yt, x, y, fmtY) {
  const s = D[src];
  Plotly.react(id, [{type: 'scattergl', mode: 'markers', x: s[xk], y: s[yk], text: s.label,
    customdata: s[yk].map(fmtY),
    marker: {color: css('--s1soft'), size: src === 'split' ? 4 : 7, line: {width: 0}},
    hovertemplate: '%{text}<br>' + yt + ': %{customdata}<extra></extra>'}],
    layout(xt, yt, x, y), {displayModeBar: false, responsive: true});
}
const paceTicks = () => { const t = []; for (let p = 4; p <= 8; p += 0.5) t.push(p);
  return {autorange: false, range: [8.25, 3.9], tickvals: t, ticktext: t.map(pf)}; };
const hourAxis = {tickvals: [6, 9, 12, 15, 18, 21], ticktext: ['06', '09', '12', '15', '18', '21']};
function render() {
  dots('p-hr', 'split', 'hr', 'pace', 'heart rate (bpm)', 'pace (min/km)', {}, paceTicks(), v => pf(v) + ' /km');
  dots('p-temp', 'split', 'temp', 'pace', 'air temperature (°C)', 'pace (min/km)', {}, paceTicks(), v => pf(v) + ' /km');
  dots('p-hour', 'split', 'hour', 'pace', 'time of day (hour)', 'pace (min/km)', hourAxis, paceTicks(), v => pf(v) + ' /km');
  dots('c-temp', 'run', 'temp', 'sd', 'air temperature (°C)', 'split pace SD (s/km)', {}, {rangemode: 'tozero'}, v => v + ' s/km');
  dots('c-hour', 'run', 'hour', 'sd', 'time of day (hour)', 'split pace SD (s/km)', hourAxis, {rangemode: 'tozero'}, v => v + ' s/km');
}
render();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
</script></body></html>
"""
