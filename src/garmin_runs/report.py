"""Static HTML report: stat tiles, charts (plotly.js), and tables."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from . import race3d
from .analyze import fmt_dur, fmt_pace, summarize

OUT = Path("output")
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _tile(label: str, value: str, sub: str = "", wide: bool = False) -> str:
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    cls = "tile wide" if wide else "tile"
    return f'<div class="{cls}"><div class="label">{label}</div><div class="value">{value}</div>{sub_html}</div>'


def _table(head: list[str], rows: list[list[str]], num_cols: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"num" if i in num_cols else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i in num_cols else ""}">{html.escape(str(c))}</td>'
                         for i, c in enumerate(r)) + "</tr>"
        for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _neighbourhoods() -> tuple[pd.DataFrame | None, int]:
    """Toronto neighbourhood ranking (by runs); (None, 0) if the boundary data can't be loaded."""
    try:
        import shapely
        from shapely.geometry import shape

        from .neighbourhood import _download, _tracks, rank_neighbourhoods
        feats = json.load(open(_download("neighbourhoods"), encoding="utf-8"))["features"]
        hoods = [{"name": f["properties"]["AREA_NAME"], "geom": shape(f["geometry"])} for f in feats]
        for h in hoods:
            shapely.prepare(h["geom"])
        t = _tracks()
        return rank_neighbourhoods(t, hoods), t["activity_id"].nunique()
    except Exception as e:  # offline on first run, etc. - the rest of the report still builds
        print(f"  (neighbourhoods skipped: {e})")
        return None, 0


def build(out_dir: Path = OUT, public: bool = False) -> Path:
    """public=True: no neighbourhood tile/card/link (they would reveal where you live)."""
    s = summarize()
    t, runs = s["totals"], s["runs"]

    pace = s["pace"]
    data = {
        "weekly": {"x": [d.strftime("%Y-%m-%d") for d in s["weekly"].index],
                   "y": s["weekly"].round(2).tolist()},
        "pace": {"x": pace["start"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
                 "y": pace["pace_min_km"].round(3).tolist(),
                 "roll": pace["rolling"].round(3).tolist(),
                 "text": [f"{n}<br>{k:.2f} km · {fmt_pace(p)} /km"
                          for n, k, p in zip(pace["name"], pace["km"], pace["pace_min_km"])]},
        "dist": runs["km"].round(2).tolist(),
        "dow": {"x": DOW, "y": s["dow"]["count"].astype(int).tolist()},
        "hour": {"x": [f"{h:02d}:00" for h in range(24)], "y": s["hour"].astype(int).tolist()},
        "hr": {"x": pace.dropna(subset=["avg_hr"])["pace_min_km"].round(3).tolist(),
               "y": pace.dropna(subset=["avg_hr"])["avg_hr"].tolist(),
               "text": [f"{n}<br>{fmt_pace(p)} /km · {h:.0f} bpm" for n, p, h in
                        pace.dropna(subset=["avg_hr"])[["name", "pace_min_km", "avg_hr"]].itertuples(index=False)]},
    }

    tiles = "".join([
        _tile("Runs", f"{t['runs']:,}", f"{t['first']:%b %Y} – {t['last']:%b %Y}"),
        _tile("Distance", f"{t['km']:,.0f} km"),
        _tile("Time running", f"{t['hours']:,.0f} h"),
        _tile("Average pace", f"{fmt_pace(t['avg_pace'])} /km"),
        _tile("Longest run", f"{t['longest_km']:.1f} km", f"{t['longest_date']:%d %b %Y}"),
        _tile("Biggest week", f"{t['best_week_km']:.1f} km", f"week of {t['best_week']:%d %b %Y}"),
    ])

    hoods, toronto_runs = (None, 0) if public else _neighbourhoods()
    if hoods is not None and not hoods.empty:
        fav = hoods.iloc[0]
        tiles += _tile("Favourite neighbourhood",
                       f'<a class="tilelink" href="neighbourhood.html">{html.escape(fav["name"])}</a>',
                       f"{int(fav['runs'])} of {toronto_runs} Toronto runs · {fav['km']:.0f} km", wide=True)
        hoods_html = _table(
            ["#", "Neighbourhood", "Runs", "km"],
            [[i + 1, r["name"], int(r["runs"]), f"{r['km']:.0f}"] for i, r in hoods.head(5).iterrows()],
            {0, 2, 3},
        ) + '<p class="note"><a href="neighbourhood.html">Streets you prefer and avoid there →</a></p>'
    else:
        hoods_html = "<p class='muted'>Neighbourhood data unavailable (needs a one-time download from Toronto Open Data).</p>"

    best = s["best"]
    best_html = _table(
        ["Distance", "Time", "Pace", "Date"],
        [[r.effort, fmt_dur(r.secs), f"{fmt_pace(r.secs / 60 / {'1K': 1, '5K': 5, '10K': 10, 'Half': 21.0975}[r.effort])} /km",
          f"{r.date:%d %b %Y}" if pd.notna(r.date) else "-"] for r in best.itertuples()],
        {1, 2},
    ) if not best.empty else "<p class='muted'>No GPS tracks parsed yet.</p>"

    y = s["yearly"]
    yearly_html = _table(
        ["Year", "Runs", "Distance (km)", "Avg per run (km)"],
        [[int(yr), int(r["count"]), f"{r['sum']:,.1f}", f"{r['sum'] / r['count']:.1f}"]
         for yr, r in y.sort_index(ascending=False).iterrows()],
        {1, 2, 3},
    )

    recent = runs.sort_values("start", ascending=False).head(15)
    recent_html = _table(
        ["Date", "Name", "km", "Time", "Pace", "Avg HR"],
        [[f"{r.start:%Y-%m-%d}", r.name, f"{r.km:.2f}", fmt_dur(r.secs), fmt_pace(r.pace_min_km),
          f"{r.avg_hr:.0f}" if pd.notna(r.avg_hr) else "-"] for r in recent.itertuples()],
        {2, 3, 4, 5},
    )

    page = TEMPLATE.replace("__TILES__", tiles).replace("__BEST__", best_html) \
        .replace("__YEARLY__", yearly_html).replace("__RECENT__", recent_html) \
        .replace("__NEIGHBOURHOOD_CARD__", "" if public else NEIGHBOURHOOD_CARD).replace("__HOODS__", hoods_html) \
        .replace("__DATA__", json.dumps(data)) \
        .replace("__RACE_LINKS__", race3d.nav_links(out_dir))
    if public:
        page = page.replace('<a href="neighbourhood.html">Favourite neighbourhood →</a>', "")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    print(f"Report -> {path}")
    return path


NEIGHBOURHOOD_CARD = (' <div class="card"><h2>Top neighbourhoods</h2>'
                      '<p class="note">Toronto, by number of runs passing through</p>__HOODS__</div>')

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Running Report</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s1soft:rgba(42,120,214,.35)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s1soft:rgba(57,135,229,.40)}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:15px;margin:0 0 2px}
.muted,.note{color:var(--ink2)}
.note{font-size:12px;margin:0 0 8px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:20px}
header a{color:var(--s1);font-weight:600;text-decoration:none}
.pages{display:flex;flex-wrap:wrap;gap:6px 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:12px;margin-bottom:16px}
.tile.wide{grid-column:1/-1}
@media (min-width:900px){.tiles{grid-template-columns:repeat(4,1fr)}.tile.wide{grid-column:span 2}}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.tile .label{color:var(--ink2);font-size:12px}
.tile .value{font-size:26px;font-weight:600;margin-top:4px}
.tile .value a.tilelink{color:inherit;font-size:20px;text-decoration:underline;text-decoration-color:var(--axis);text-underline-offset:4px}
.tile .sub{color:var(--muted);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:12px;margin-bottom:12px}
.card.wide{margin-bottom:12px}
.chart{height:280px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--ink2);font-weight:500;text-align:left;border-bottom:1px solid var(--axis);padding:6px 8px}
td{border-bottom:1px solid var(--grid);padding:6px 8px}
.num{text-align:right;font-variant-numeric:tabular-nums}
</style></head>
<body><main>
<header><div><h1>Running Report</h1><div class="muted">From Garmin Connect, via the Personal API</div></div>
<nav class="pages"><a href="heatmap.html">Route heatmap →</a><a href="segment.html">Most-run segments →</a>__RACE_LINKS__<a href="neighbourhood.html">Favourite neighbourhood →</a></nav></header>

<section class="tiles">__TILES__</section>

<div class="card wide"><h2>Weekly distance</h2><p class="note">km per week (weeks start Monday)</p><div id="weekly" class="chart"></div></div>
<div class="card wide"><h2>Pace per run</h2><p class="note">min/km, faster is higher · line = 90-day rolling median</p><div id="pace" class="chart"></div></div>

<div class="grid">
 <div class="card"><h2>Run distances</h2><p class="note">number of runs by distance (km)</p><div id="dist" class="chart"></div></div>
 <div class="card"><h2>Heart rate vs pace</h2><p class="note">average HR (bpm) per run</p><div id="hr" class="chart"></div></div>
 <div class="card"><h2>Runs by weekday</h2><p class="note">number of runs</p><div id="dow" class="chart"></div></div>
 <div class="card"><h2>Runs by start hour</h2><p class="note">number of runs, local time</p><div id="hour" class="chart"></div></div>
</div>

<div class="grid">
 <div class="card"><h2>Best efforts</h2><p class="note">fastest stretch inside any run, from GPS</p>__BEST__</div>
 <div class="card"><h2>By year</h2><p class="note">&nbsp;</p>__YEARLY__</div>
__NEIGHBOURHOOD_CARD__
</div>
<div class="card wide"><h2>Recent runs</h2>__RECENT__</div>
</main>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const paceFmt = v => { const m = Math.floor(v), s = Math.round((v - m) * 60); return s === 60 ? `${m+1}:00` : `${m}:${String(s).padStart(2,'0')}`; };

function base(extra = {}) {
  const ax = {gridcolor: css('--grid'), linecolor: css('--axis'), zeroline: false,
              tickfont: {color: css('--muted'), size: 11}, fixedrange: true};
  return Object.assign({
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: {family: 'system-ui,-apple-system,Segoe UI,sans-serif', color: css('--ink2')},
    margin: {l: 44, r: 12, t: 8, b: 32}, showlegend: false, bargap: 0.15,
    hoverlabel: {bgcolor: css('--surface'), bordercolor: css('--axis'), font: {color: css('--ink')}},
    xaxis: Object.assign({}, ax, {showgrid: false}), yaxis: Object.assign({}, ax),
  }, extra);
}
const cfg = {displayModeBar: false, responsive: true};

function render() {
  const s1 = css('--s1'), soft = css('--s1soft'), surf = css('--surface');
  const bar = {type: 'bar', marker: {color: s1, line: {color: surf, width: 1}}};

  Plotly.react('weekly', [Object.assign({}, bar, {x: D.weekly.x, y: D.weekly.y,
      hovertemplate: 'Week of %{x|%d %b %Y}<br><b>%{y:.1f} km</b><extra></extra>'})],
    base({xaxis: Object.assign(base().xaxis, {type: 'date', fixedrange: false,
          rangeslider: {visible: false}}), dragmode: 'pan',
          yaxis: Object.assign(base().yaxis, {ticksuffix: ' km'})}), cfg);

  const ticks = []; for (let v = 3; v <= 12; v += 0.5) ticks.push(v);
  Plotly.react('pace', [
    {type: 'scatter', mode: 'markers', x: D.pace.x, y: D.pace.y, text: D.pace.text,
     marker: {color: soft, size: 8, line: {color: surf, width: 1}},
     hovertemplate: '%{x|%d %b %Y}<br>%{text}<extra></extra>'},
    {type: 'scatter', mode: 'lines', x: D.pace.x, y: D.pace.roll, line: {color: s1, width: 2},
     hovertemplate: '90-day median: %{customdata} /km<extra></extra>', customdata: D.pace.roll.map(paceFmt)}],
    base({yaxis: Object.assign(base().yaxis, {autorange: 'reversed', tickvals: ticks, ticktext: ticks.map(paceFmt)}),
          xaxis: Object.assign(base().xaxis, {type: 'date'})}), cfg);

  Plotly.react('dist', [{type: 'histogram', x: D.dist, xbins: {size: 1}, marker: bar.marker,
      hovertemplate: '%{x} km<br><b>%{y} runs</b><extra></extra>'}],
    base({xaxis: Object.assign(base().xaxis, {ticksuffix: ' km'})}), cfg);

  Plotly.react('hr', [{type: 'scatter', mode: 'markers', x: D.hr.x, y: D.hr.y, text: D.hr.text,
      marker: {color: soft, size: 8, line: {color: s1, width: 1}}, hovertemplate: '%{text}<extra></extra>'}],
    base({xaxis: Object.assign(base().xaxis, {autorange: 'reversed', title: {text: 'pace (min/km) → faster', font: {size: 11, color: css('--muted')}},
          tickvals: ticks, ticktext: ticks.map(paceFmt)}), yaxis: Object.assign(base().yaxis, {ticksuffix: ''})}), cfg);

  Plotly.react('dow', [Object.assign({}, bar, {x: D.dow.x, y: D.dow.y,
      hovertemplate: '%{x}<br><b>%{y} runs</b><extra></extra>'})], base(), cfg);
  Plotly.react('hour', [Object.assign({}, bar, {x: D.hour.x, y: D.hour.y,
      hovertemplate: '%{x}<br><b>%{y} runs</b><extra></extra>'})],
    base({xaxis: Object.assign(base().xaxis, {tickvals: D.hour.x.filter((_, i) => i % 3 === 0)})}), cfg);
}
render();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
</script>
</body></html>
"""
