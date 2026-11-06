"""Tolerance analysis of the most-run segment: path, physiological and time tolerance.

For every timed pass through segment #1 (both directions, last 2 years):

* Path tolerance - sideways distance of each GPS point from the segment line
  (mean and 95th percentile, metres).
* Physiological tolerance - average heart rate on the pass, and HR drift
  (mean HR in the last third of the pass minus the first third, bpm).
* Time consistency - % slower (+) or faster (-) than your median time on the segment.

Each measure is regressed on the factors together, so every effect is "with the others held
constant": pace (min/km; not used for time, where pace *is* the outcome), air temperature
(per 10 °C), time of day (midday / evening vs morning) and direction. Passes from the same
run aren't independent, so standard errors are cluster-robust by run (CR1, t with G-1 df).

Private output only (output/tolerance.html): the segment is near home.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from scipy import stats

from .analyze import fmt_dur, fmt_pace
from .segments import OUT, select

TZ = "America/Toronto"
TOD = [("Morning", 0, 11), ("Midday", 11, 16), ("Evening", 16, 24)]
LAT0 = 43.7


def _xy(lat, lon) -> np.ndarray:
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    return np.c_[lon * 111_320 * np.cos(np.radians(LAT0)), lat * 110_540]


# ---------- per-pass measurements ----------
def measure(seg: dict, tracks: pd.DataFrame) -> pd.DataFrame:
    line = shapely.LineString(_xy(seg["path"][:, 0], seg["path"][:, 1]))
    p = seg["passes"].copy()
    tts = pd.to_datetime(tracks["ts"], utc=True)
    rows = []
    for r in p.itertuples():
        m = (tracks["activity_id"] == r.activity_id) & (tts >= r.start_utc) & (tts <= r.end_utc)
        g = tracks[m]
        dev = shapely.distance(shapely.points(_xy(g["lat"], g["lon"])), line)
        hr = g["hr"].to_numpy(dtype=float)
        third = max(1, len(hr) // 3)
        drift = np.nanmean(hr[-third:]) - np.nanmean(hr[:third]) if np.isfinite(hr).sum() >= 6 else np.nan
        run_d = tracks.loc[tracks["activity_id"] == r.activity_id, "distance_m"]
        rows.append({"dev_mean_m": float(np.mean(dev)), "dev_p95_m": float(np.percentile(dev, 95)),
                     "hr_drift": float(drift), "n_points": len(g),
                     "km_in": float(g["distance_m"].iloc[0] - run_d.min()) / 1000})
    p = pd.concat([p.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    local = p["start_utc"].dt.tz_convert(TZ)
    p["hour"] = local.dt.hour + local.dt.minute / 60
    p["tod"] = pd.cut(p["hour"], [t[1] for t in TOD] + [24], labels=[t[0] for t in TOD], right=False)
    p["time_pct"] = (p["secs"] / p["secs"].median() - 1) * 100
    return p


# ---------- regression with cluster-robust SEs ----------
def ols_cluster(y: pd.Series, X: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    ok = y.notna() & X.notna().all(axis=1)
    y, X, groups = y[ok].to_numpy(float), X[ok], groups[ok].to_numpy()
    names = ["intercept"] + list(X.columns)
    A = np.c_[np.ones(len(X)), X.to_numpy(float)]
    n, k = A.shape
    XtX_inv = np.linalg.pinv(A.T @ A)
    beta = XtX_inv @ A.T @ y
    e = y - A @ beta
    G = len(np.unique(groups))
    meat = np.zeros((k, k))
    for g in np.unique(groups):
        s = A[groups == g].T @ e[groups == g]
        meat += np.outer(s, s)
    c = G / (G - 1) * (n - 1) / (n - k)
    se = np.sqrt(np.diag(c * XtX_inv @ meat @ XtX_inv))
    tval = beta / se
    df = G - 1
    pval = 2 * stats.t.sf(np.abs(tval), df)
    q = stats.t.ppf(0.975, df)
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    out = pd.DataFrame({"term": names, "coef": beta, "lo": beta - q * se, "hi": beta + q * se, "p": pval})
    out.attrs.update(n=n, clusters=G, r2=r2)
    return out


def design(p: pd.DataFrame, with_pace: bool) -> pd.DataFrame:
    fwd = p["direction"].mode()[0]
    X = pd.DataFrame(index=p.index)
    if with_pace:
        X["pace"] = p["pace_min_km"] - p["pace_min_km"].median()   # per 1 min/km slower
    X["temp"] = (p["temp_c"] - p["temp_c"].median()) / 10          # per 10 °C warmer
    for name, *_ in TOD[1:]:
        X[name.lower()] = (p["tod"] == name).astype(float)           # vs morning
    X["reverse"] = (p["direction"] != fwd).astype(float)            # vs the more common direction
    X["km_in"] = p["km_in"] - p["km_in"].median()                   # control: fatigue / HR drift
    return X


MODELS = [
    # key, label, unit, outcome column, uses pace, group
    ("dev_mean_m", "Mean sideways deviation", "m", True, "Path tolerance"),
    ("dev_p95_m", "95th-pct sideways deviation", "m", True, "Path tolerance"),
    ("avg_hr", "Average heart rate", "bpm", True, "Physiological tolerance"),
    ("hr_drift", "HR drift across the pass", "bpm", True, "Physiological tolerance"),
    ("time_pct", "Time vs your median", "%", False, "Time consistency"),
]
TERM_LABEL = {"pace": "1 min/km slower", "temp": "10 °C warmer", "midday": "Midday vs morning",
              "evening": "Evening vs morning", "reverse": "Reverse vs usual direction",
              "km_in": "1 km further into the run (control)"}
MIN_GROUP = 10  # fewer passes than this in a time-of-day group -> flag the result


def outliers(p: pd.DataFrame) -> pd.Series:
    """Passes that are probably measurement problems or stops, not normal running."""
    return ((p["pace_min_km"] > 9.0)          # walked / stopped on the segment
            | (p["avg_hr"] > 200)            # optical HR spike
            | (p["dev_mean_m"] > 8.0))       # GPS wandered off the line


def analyse(p: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {key: ols_cluster(p[key], design(p, pace), p["activity_id"]) for key, _, _, pace, _ in MODELS}


def _verdict(r: pd.Series) -> str:
    if r["p"] < 0.05:
        return "clear effect"
    if r["p"] < 0.10:
        return "weak hint"
    return "no clear effect"


# ---------- page ----------
def _effects_table(res: pd.DataFrame, unit: str, labels: dict[str, str] | None = None,
                   rows_name: str = "passes") -> str:
    labels = labels or TERM_LABEL
    rows = []
    span = max(1e-9, float(np.nanmax(np.abs(res.loc[res.term != "intercept", ["lo", "hi"]].to_numpy()))))
    for r in res[res.term != "intercept"].itertuples():
        # mini CI bar: centred zero line, bar from lo to hi, dot at coef
        def x(v): return 50 + 50 * v / span
        strong = r.p < 0.05
        bar = (f'<svg class="ci" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true">'
               f'<line x1="50" x2="50" y1="0" y2="12" class="zero"/>'
               f'<line x1="{x(r.lo):.1f}" x2="{x(r.hi):.1f}" y1="6" y2="6" class="{"bar strong" if strong else "bar"}"/>'
               f'<circle cx="{x(r.coef):.1f}" cy="6" r="2.6" class="{"dot strong" if strong else "dot"}"/></svg>')
        rows.append(
            f"<tr><td>{labels[r.term]}</td><td class='num'>{r.coef:+.1f} {unit}</td>"
            f"<td class='num muted'>{r.lo:+.1f} to {r.hi:+.1f}</td><td>{bar}</td>"
            f"<td class='num'>{r.p:.2f}</td><td>{'<b>' if strong else ''}{_verdict(pd.Series({'p': r.p}))}"
            f"{'</b>' if strong else ''}</td></tr>")
    a = res.attrs
    source = "" if rows_name == "runs" else f" from {a['clusters']} runs"
    return (f'<div class="tablewrap"><table><thead><tr><th>Factor</th><th class="num">Effect</th>'
            f'<th class="num">95% CI</th><th class="cicol">CI (0 = no effect)</th><th class="num">p</th><th>Verdict</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="note">{a["n"]:,} {rows_name}{source} · R² {a["r2"]:.2f}</p>')


def _summary_lines(results: dict[str, pd.DataFrame], counts: pd.Series,
                   robust: dict[str, pd.DataFrame], n_out: int) -> list[str]:
    lines = []
    for key, label, unit, _, _ in MODELS:
        res = results[key]
        for r in res[(res.term != "intercept") & (res.p < 0.05)].itertuples():
            n = counts.get(r.term.capitalize())
            caution = (f" <span class='muted'>(only {n} {r.term} passes - treat with caution)</span>"
                       if n is not None and n < MIN_GROUP else "")
            rr = robust[key].set_index("term").loc[r.term]
            holds = rr["p"] < 0.05 and np.sign(rr["coef"]) == np.sign(r.coef)
            tag = (f" <span class='tag {'ok' if holds else 'no'}'>{'holds' if holds else 'does not hold'} "
                   f"without the {n_out} outlier passes</span>")
            lines.append(f"<b>{label}</b>: {TERM_LABEL[r.term]} → {r.coef:+.1f} {unit} "
                         f"(95% CI {r.lo:+.1f} to {r.hi:+.1f}){caution}{tag}")
    return lines


def build(length_m: float = 1000, years: float = 2) -> Path:
    segs, tracks, since = select(length_m, years, top=1)
    seg = segs[0]
    p = measure(seg, tracks)
    results = analyse(p)
    out_mask = outliers(p)
    robust = analyse(p[~out_mask])

    fwd = p["direction"].mode()[0]
    rev = next((d for d in p["direction"].unique() if d != fwd), "reverse")
    series = [{"name": d, "idx": p.index[p["direction"] == d].tolist()} for d in (fwd, rev)]
    data = {
        "series": [s["name"] for s in series],
        "groups": [{
            "pace": p.loc[s["idx"], "pace_min_km"].round(3).tolist(),
            "temp": p.loc[s["idx"], "temp_c"].round(1).tolist(),
            "hour": p.loc[s["idx"], "hour"].round(2).tolist(),
            "dev": p.loc[s["idx"], "dev_mean_m"].round(2).tolist(),
            "hr": [None if np.isnan(v) else round(v, 1) for v in p.loc[s["idx"], "avg_hr"]],
            "tpct": p.loc[s["idx"], "time_pct"].round(1).tolist(),
            "label": [f"{d:%d %b %Y} · {fmt_dur(sec)} ({fmt_pace(pc)} /km) · {t:.0f} °C"
                      for d, sec, pc, t in zip(p.loc[s["idx"], "local_start"], p.loc[s["idx"], "secs"],
                                               p.loc[s["idx"], "pace_min_km"], p.loc[s["idx"], "temp_c"])],
        } for s in series],
    }

    counts = p["tod"].value_counts().reindex([t[0] for t in TOD]).fillna(0).astype(int)
    tod_note = " · ".join(f"{k} {v}" for k, v in counts.items())
    spread = p.groupby("tod", observed=True)["time_pct"].agg(["count", "std"])
    spread_rows = "".join(f"<tr><td>{k}</td><td class='num'>{int(r['count'])}</td>"
                          f"<td class='num'>{r['std']:.1f}%</td></tr>" for k, r in spread.iterrows())
    summary = _summary_lines(results, counts, robust, int(out_mask.sum()))
    kin = p.groupby("direction")["km_in"].median()
    dirnote = (f"Direction and distance into the run are linked: {html.escape(fwd)} passes start a median "
               f"{kin.get(fwd, float('nan')):.1f} km into a run, {html.escape(rev)} passes "
               f"{kin.get(rev, float('nan')):.1f} km in. Both are in the model, but they can't be fully "
               f"separated, so a direction effect may still partly be fatigue (or wind and terrain).")
    summary_html = ("<ul>" + "".join(f"<li>{s}</li>" for s in summary) + "</ul>") if summary else \
        "<p>No factor has a clear (p &lt; 0.05) effect on any tolerance measure.</p>"

    groups = {}
    for key, label, unit, _, grp in MODELS:
        groups.setdefault(grp, []).append(f"<h3>{label}</h3>" + _effects_table(results[key], unit))

    page = (TEMPLATE
            .replace("__N__", str(len(p))).replace("__RUNS__", str(p["activity_id"].nunique()))
            .replace("__LEN__", f"{seg['length_m']:.0f}").replace("__SINCE__", f"{since:%d %b %Y}")
            .replace("__FWD__", html.escape(fwd)).replace("__REV__", html.escape(rev))
            .replace("__DEV__", f"{p['dev_mean_m'].median():.1f}")
            .replace("__HR__", f"{p['avg_hr'].median():.0f}")
            .replace("__IQR__", f"{p['time_pct'].quantile(0.75) - p['time_pct'].quantile(0.25):.1f}")
            .replace("__MEDIAN__", fmt_dur(p["secs"].median()))
            .replace("__TOD__", tod_note).replace("__SUMMARY__", summary_html)
            .replace("__PATH__", "".join(groups["Path tolerance"]))
            .replace("__PHYS__", "".join(groups["Physiological tolerance"]))
            .replace("__TIME__", "".join(groups["Time consistency"]))
            .replace("__SPREAD__", spread_rows)
            .replace("__DIRNOTE__", dirnote).replace("__NOUT__", str(int(out_mask.sum())))
            .replace("__DATA__", json.dumps(data)))
    OUT.mkdir(exist_ok=True)
    path = OUT / "tolerance.html"
    path.write_text(page, encoding="utf-8")
    p.drop(columns=["start_utc", "end_utc"]).to_csv(OUT / "tolerance_passes.csv", index=False)

    print(f"Segment #1: {len(p)} passes, {p['activity_id'].nunique()} runs; time of day: {tod_note}")
    for key, label, unit, _, _ in MODELS:
        r = results[key]
        print(f"\n{label} ({unit})  n={r.attrs['n']} R²={r.attrs['r2']:.2f}")
        print(r[r.term != "intercept"].round(3).to_string(index=False))
    print(f"\nWithout {int(out_mask.sum())} outlier passes (clear effects only):")
    for key, label, unit, _, _ in MODELS:
        r = robust[key]
        r = r[(r.term != "intercept") & (r.p < 0.05)]
        print(f"  {label}: " + (", ".join(f"{t} {c:+.1f} {unit} (p={pv:.3f})"
                                         for t, c, pv in zip(r.term, r.coef, r.p)) or "none"))
    print(f"\n-> {path}")
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Segment Tolerance</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926}}
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
.chart{height:240px}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin:4px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:5px;margin-right:6px;vertical-align:middle}
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
<header><div><h1>Segment tolerance</h1>
<div class="muted">Most-run segment (__LEN__ m) · __N__ passes from __RUNS__ runs since __SINCE__ · both directions</div></div>
<div><a href="segment.html">← Segments</a> &nbsp; <a href="report.html">Running report</a></div></header>

<section class="tiles">
 <div class="tile"><div class="label">Typical sideways deviation</div><div class="value">__DEV__ m</div><div class="sub">median of each pass's mean</div></div>
 <div class="tile"><div class="label">Typical heart rate</div><div class="value">__HR__ bpm</div><div class="sub">median pass average</div></div>
 <div class="tile"><div class="label">Typical time</div><div class="value">__MEDIAN__</div><div class="sub">middle 50% of passes within __IQR__% of each other</div></div>
 <div class="tile"><div class="label">Passes by time of day</div><div class="value" style="font-size:16px;margin-top:8px">__TOD__</div><div class="sub">morning &lt; 11:00 · evening ≥ 16:00</div></div>
</section>

<div class="card"><h2>What matters</h2>
<p class="note">Factors with a clear effect (p &lt; 0.05), each measured with the others held constant. Each is re-checked without __NOUT__ outlier passes (pace slower than 9:00/km, average HR above 200 bpm, or GPS more than 8 m off the line).</p>__SUMMARY__</div>

<div class="card"><h2>1 · Path tolerance</h2>
<p class="note">How far each pass strays sideways from the segment line. Includes GPS error, so it's how tightly the watch records you on the line.</p>
<div class="legend"><span><i style="background:var(--s1)"></i>__FWD__</span><span><i style="background:var(--s2)"></i>__REV__</span></div>
<div class="charts"><div id="dev-pace" class="chart"></div><div id="dev-temp" class="chart"></div><div id="dev-hour" class="chart"></div></div>
__PATH__</div>

<div class="card"><h2>2 · Physiological tolerance</h2>
<p class="note">Heart rate on the pass. Because pace is in the model, the temperature and time-of-day effects are "at the same pace": how much harder the segment is on you.</p>
<div class="legend"><span><i style="background:var(--s1)"></i>__FWD__</span><span><i style="background:var(--s2)"></i>__REV__</span></div>
<div class="charts"><div id="hr-pace" class="chart"></div><div id="hr-temp" class="chart"></div><div id="hr-hour" class="chart"></div></div>
__PHYS__</div>

<div class="card"><h2>3 · Time consistency</h2>
<p class="note">How much slower (+) or faster (−) than your median time each pass was. Pace is the outcome here, so it isn't a factor.</p>
<div class="legend"><span><i style="background:var(--s1)"></i>__FWD__</span><span><i style="background:var(--s2)"></i>__REV__</span></div>
<div class="charts"><div id="t-temp" class="chart"></div><div id="t-hour" class="chart"></div></div>
__TIME__
<h3>Spread by time of day</h3>
<div class="tablewrap"><table><thead><tr><th>Time of day</th><th class="num">Passes</th><th class="num">SD of time vs median</th></tr></thead><tbody>__SPREAD__</tbody></table></div></div>

<p class="note">__DIRNOTE__</p>
<p class="note">Method: one linear regression per measure with all factors together (pace per 1 min/km, temperature per 10 °C, time of day vs morning, direction), plus distance into the run as a control for fatigue. Passes from the same run aren't independent, so confidence intervals are cluster-robust by run. With about 70 passes, "no clear effect" means no effect big enough to detect, not proof there is none. This page runs 30 tests (5 measures × 6 factors), so about one or two "clear effects" are expected by chance alone: trust the ones that are large, make physical sense, and have tight intervals. Air temperature is hourly, from Open-Meteo.</p>
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
    margin: {l: 46, r: 8, t: 6, b: 40},
    hoverlabel: {bgcolor: css('--surface'), bordercolor: css('--axis'), font: {color: css('--ink')}},
    xaxis: Object.assign({}, ax, {title: Object.assign({}, ax.title, {text: xt})}, x),
    yaxis: Object.assign({}, ax, {title: Object.assign({}, ax.title, {text: yt})}, y)};
}
function scatter(id, xk, yk, xt, yt, x = {}, y = {}) {
  const cols = [css('--s1'), css('--s2')];
  const traces = D.groups.map((g, i) => ({type: 'scatter', mode: 'markers', x: g[xk], y: g[yk], text: g.label,
    name: D.series[i], marker: {color: cols[i], size: 8, opacity: 0.8, line: {color: css('--surface'), width: 1}},
    hovertemplate: '%{text}<br>' + yt + ': %{y}<extra>' + D.series[i] + '</extra>'}));
  Plotly.react(id, traces, layout(xt, yt, x, y), {displayModeBar: false, responsive: true});
}
const paceAxis = () => { const v = D.groups.flatMap(g => g.pace), t = [];
  const step = Math.max(...v) - Math.min(...v) > 3 ? 1 : 0.5;
  for (let p = Math.floor(Math.min(...v) / step) * step; p <= Math.max(...v) + step; p += step) t.push(p);
  return {autorange: 'reversed', tickvals: t, ticktext: t.map(pf)}; };
const hourAxis = {tickvals: [6, 9, 12, 15, 18, 21], ticktext: ['06', '09', '12', '15', '18', '21']};
function render() {
  scatter('dev-pace', 'pace', 'dev', 'pace (min/km) → faster', 'deviation (m)', paceAxis());
  scatter('dev-temp', 'temp', 'dev', 'air temperature (°C)', 'deviation (m)');
  scatter('dev-hour', 'hour', 'dev', 'start time (hour)', 'deviation (m)', hourAxis);
  scatter('hr-pace', 'pace', 'hr', 'pace (min/km) → faster', 'avg HR (bpm)', paceAxis());
  scatter('hr-temp', 'temp', 'hr', 'air temperature (°C)', 'avg HR (bpm)');
  scatter('hr-hour', 'hour', 'hr', 'start time (hour)', 'avg HR (bpm)', hourAxis);
  scatter('t-temp', 'temp', 'tpct', 'air temperature (°C)', 'time vs median (%)');
  scatter('t-hour', 'hour', 'tpct', 'start time (hour)', 'time vs median (%)', hourAxis);
}
render();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
</script></body></html>
"""
