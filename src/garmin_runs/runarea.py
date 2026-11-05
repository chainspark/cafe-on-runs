"""Turn every run into a street graph and watch the runnable area grow, quarter by quarter.

Graph: each 25 m grid cell a run passes through is a node; each step a run makes between
neighbouring cells is an edge (stamped with the first time it was run and how many runs use it).

Runnable area (cumulative, per quarter): every cell within `buffer` metres of an edge (the
"corridor"), plus any land completely enclosed by those corridors (territory your routes loop
around). Only edges inside the City of Toronto bounding box count.
"""
from __future__ import annotations

import base64
import json
import math
import zlib
from datetime import datetime
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from scipy import ndimage

from .heatmap import ESRI, _trim
from .parse import TRACKS

OUT = Path("output")
CELL_M = 25.0                 # ground size of a graph node
MAX_STEP_CELLS = 2            # a jump further than this between consecutive cells is a GPS gap
TORONTO = (43.581, 43.856, -79.639, -79.115)   # lat min/max, lon min/max of the City of Toronto
TZ = "America/Toronto"
FIRST_QUARTER = pd.Period("2024Q1", "Q")
LAT0 = 43.7                   # Web Mercator cells are CELL_M on the ground at this latitude
R_EARTH = 6_378_137.0
CELL_MERC = CELL_M / math.cos(math.radians(LAT0))


# ---------- geometry (Web Mercator, so the raster overlays the map exactly) ----------
def _cells(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = R_EARTH * np.radians(lon)
    y = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    return np.floor(x / CELL_MERC).astype(np.int64), np.floor(y / CELL_MERC).astype(np.int64)


def _lon(cx: float) -> float:
    return math.degrees(cx * CELL_MERC / R_EARTH)


def _lat(cy: float) -> float:
    return math.degrees(2 * math.atan(math.exp(cy * CELL_MERC / R_EARTH)) - math.pi / 2)


# ---------- graph ----------
def edges_from_tracks(tracks: pd.DataFrame, trim_m: float = 0) -> pd.DataFrame:
    """One row per unique cell-to-cell edge: ax, ay, bx, by, first (UTC), runs."""
    lat_lo, lat_hi, lon_lo, lon_hi = TORONTO
    parts = []
    for aid, g in tracks.groupby("activity_id", sort=False):
        g = _trim(g.reset_index(drop=True), trim_m).dropna(subset=["lat", "lon"])
        if len(g) < 2:
            continue
        lat, lon = g["lat"].to_numpy(float), g["lon"].to_numpy(float)
        ts = pd.to_datetime(g["ts"], utc=True).to_numpy()
        cx, cy = _cells(lat, lon)
        inside = (lat >= lat_lo) & (lat <= lat_hi) & (lon >= lon_lo) & (lon <= lon_hi)
        moved = np.r_[True, (cx[1:] != cx[:-1]) | (cy[1:] != cy[:-1])]
        cx, cy, ts, inside = cx[moved], cy[moved], ts[moved], inside[moved]
        step = np.maximum(np.abs(np.diff(cx)), np.abs(np.diff(cy)))
        ok = inside[:-1] & inside[1:] & (step <= MAX_STEP_CELLS)
        if ok.any():
            parts.append(pd.DataFrame({"ax": cx[:-1][ok], "ay": cy[:-1][ok], "bx": cx[1:][ok],
                                       "by": cy[1:][ok], "t": ts[1:][ok], "aid": aid}))
    if not parts:
        raise SystemExit("No runs inside Toronto.")
    e = pd.concat(parts, ignore_index=True)
    swap = ((e["ax"] > e["bx"]) | ((e["ax"] == e["bx"]) & (e["ay"] > e["by"]))).to_numpy()
    e.loc[swap, ["ax", "ay", "bx", "by"]] = e.loc[swap, ["bx", "by", "ax", "ay"]].to_numpy()
    out = (e.groupby(["ax", "ay", "bx", "by"])
             .agg(first=("t", "min"), runs=("aid", "nunique"), first_run=("aid", "first"))
             .reset_index())
    out.attrs["run_starts"] = e.groupby("aid")["t"].min()
    # every (edge, quarter) pair a run used, for the per-quarter (non-cumulative) coverage
    q = e["t"].dt.tz_convert(TZ).dt.tz_localize(None).dt.to_period("Q")
    out.attrs["edge_q"] = e.assign(q=q)[["ax", "ay", "bx", "by", "q"]].drop_duplicates()
    return out


def _quarters(edges: pd.DataFrame) -> tuple[pd.DataFrame, list[pd.Period]]:
    local = edges["first"].dt.tz_convert(TZ).dt.tz_localize(None)
    q = local.dt.to_period("Q")
    first_q = max(FIRST_QUARTER, q.min())
    last_q = pd.Timestamp.now(tz=TZ).tz_localize(None).to_period("Q")
    quarters = list(pd.period_range(first_q, last_q, freq="Q"))
    edges = edges.assign(local=local, qi=[max(0, (p - first_q).n) for p in q])
    return edges, quarters


# ---------- area raster ----------
def _area(mask: np.ndarray, buffer_m: float) -> tuple[np.ndarray, np.ndarray]:
    """(corridor, corridor + enclosed holes) around the marked cells."""
    if not mask.any():
        return mask, mask
    corr = ndimage.distance_transform_edt(~mask) * CELL_M <= buffer_m
    return corr, ndimage.binary_fill_holes(corr)


def _area_rasters(edges: pd.DataFrame, edge_q: pd.DataFrame, n_q: int, buffer_m: float):
    pad = math.ceil(buffer_m / CELL_M) + 4
    xs = np.r_[edges["ax"], edges["bx"]]
    ys = np.r_[edges["ay"], edges["by"]]
    x0, x1 = xs.min() - pad, xs.max() + pad
    y0, y1 = ys.min() - pad, ys.max() + pad
    w, h = int(x1 - x0 + 1), int(y1 - y0 + 1)
    # ground area of one cell, per row (Mercator cells shrink slightly with latitude)
    row_lat = np.array([_lat(y1 - r + 0.5) for r in range(h)])
    cell_km2 = (CELL_M * np.cos(np.radians(row_lat)) / math.cos(math.radians(LAT0))) ** 2 / 1e6

    corr_first = np.zeros((h, w), np.uint8)    # quarter index + 1 a cell first became corridor
    fill_first = np.zeros((h, w), np.uint8)    # ... first became runnable (corridor or enclosed)
    mask = np.zeros((h, w), bool)
    stats = []
    for k in range(n_q):
        sel = edges[edges["qi"] == k]
        for cx, cy in (("ax", "ay"), ("bx", "by")):
            mask[(y1 - sel[cy]).to_numpy(), (sel[cx] - x0).to_numpy()] = True
        corr, filled = _area(mask, buffer_m)
        corr_first[(corr_first == 0) & corr] = k + 1
        fill_first[(fill_first == 0) & filled] = k + 1
        # this quarter on its own: every street run in it, new or repeated
        own = np.zeros((h, w), bool)
        sq = edge_q[edge_q["qi"] == k]
        for cx, cy in (("ax", "ay"), ("bx", "by")):
            own[(y1 - sq[cy]).to_numpy(), (sq[cx] - x0).to_numpy()] = True
        _, own_filled = _area(own, buffer_m)
        km2 = lambda m: float((m * cell_km2[:, None]).sum())
        stats.append({"corridor_km2": km2(corr), "area_km2": km2(filled), "covered_km2": km2(own_filled)})
    grid = {"w": w, "h": h, "x0": int(x0), "y1": int(y1),
            "west": _lon(x0), "east": _lon(x1 + 1), "north": _lat(y1 + 1), "south": _lat(y0)}
    return grid, corr_first, fill_first, stats


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(zlib.compress(a.tobytes(), 9)).decode()


# ---------- pipeline ----------
def build(buffer_m: float = 100, trim_meters: float = 0) -> Path:
    tracks = pd.read_parquet(TRACKS)
    edges = edges_from_tracks(tracks, trim_meters)
    run_starts = edges.attrs["run_starts"]
    edge_q = edges.attrs["edge_q"]
    edges, quarters = _quarters(edges)
    n_q = len(quarters)
    edge_q = edge_q.assign(qi=[max(0, (p - quarters[0]).n) for p in edge_q["q"]])
    grid, corr_first, fill_first, area = _area_rasters(edges, edge_q, n_q, buffer_m)

    t0 = quarters[0].start_time
    now_local = pd.Timestamp.now(tz=TZ).tz_localize(None)
    run_q = run_starts.dt.tz_convert(TZ).dt.tz_localize(None).dt.to_period("Q")

    G = nx.Graph()
    rows = []
    for k, q in enumerate(quarters):
        sel = edges[edges["qi"] == k]
        G.add_edges_from(zip(zip(sel["ax"], sel["ay"]), zip(sel["bx"], sel["by"])))
        comps = sorted((len(c) for c in nx.connected_components(G)), reverse=True) if len(G) else [0]
        prev = rows[-1]["area_km2"] if rows else 0.0
        end = min(q.end_time, now_local)
        rows.append({
            "quarter": f"{q.year} Q{q.quarter}" + (" (to date)" if q.end_time > now_local else ""),
            "short": f"Q{q.quarter}", "year": q.year,
            "start_day": (q.start_time - t0).total_seconds() / 86400,
            "end_day": (end - t0).total_seconds() / 86400,
            "runs": int((run_q == q).sum()) if k else int((run_q <= q).sum()),
            "new_edges": len(sel),
            "nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "components": len(comps),
            "largest_share": comps[0] / max(1, G.number_of_nodes()),
            "area_km2": area[k]["area_km2"], "corridor_km2": area[k]["corridor_km2"],
            "enclosed_km2": area[k]["area_km2"] - area[k]["corridor_km2"],
            "added_km2": area[k]["area_km2"] - prev,
            "covered_km2": area[k]["covered_km2"],
        })
    stats = pd.DataFrame(rows)

    e = edges.sort_values("local")
    day = ((e["local"] - t0).dt.total_seconds() / 86400).clip(lower=0).round(2)
    payload = {
        "buffer_m": buffer_m, "cell_m": CELL_M, "tiles": ESRI,
        "grid": grid | {"corr": _b64(corr_first), "fill": _b64(fill_first)},
        "edges": {
            # grid columns/rows: column from the west edge, row from the north edge
            "xy": np.column_stack([e["ax"] - grid["x0"], grid["y1"] - e["ay"],
                                   e["bx"] - grid["x0"], grid["y1"] - e["by"]]).ravel().tolist(),
            "day": day.tolist(), "runs": e["runs"].tolist()},
        "quarters": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in rows],
        "t0": f"{t0:%Y-%m-%d}",
    }

    OUT.mkdir(exist_ok=True)
    stats.drop(columns=["short", "year", "start_day", "end_day"]).round(3).to_csv(
        OUT / "runnable_area.csv", index=False)
    path = OUT / "runnable_area.html"
    page = (TEMPLATE.replace("__BUF__", f"{buffer_m:g}")
                    .replace("__GEN__", f"{datetime.now():%d %b %Y}")
                    .replace("__DATA__", json.dumps(payload, separators=(",", ":"))))
    path.write_text(page, encoding="utf-8")
    _print(stats)
    print(f"Runnable area -> {path}\nQuarterly stats -> {OUT / 'runnable_area.csv'}")
    return path


def _print(s: pd.DataFrame) -> None:
    print(f"{'quarter':<18}{'runs':>5}{'covered':>9}{'area km2':>10}{'added':>8}{'corridor':>10}{'enclosed':>10}"
          f"{'nodes':>8}{'edges':>8}{'comps':>7}")
    for r in s.itertuples():
        print(f"{r.quarter:<18}{r.runs:>5}{r.covered_km2:>9.2f}{r.area_km2:>10.2f}{r.added_km2:>+8.2f}{r.corridor_km2:>10.2f}"
              f"{r.enclosed_km2:>10.2f}{r.nodes:>8}{r.edges:>8}{r.components:>7}")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Runnable Toronto</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--old:#2a78d6;--new:#eb6834;--ghost:#e1e0d9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
 --ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --old:#3987e5;--new:#d95926;--ghost:#2c2c2a}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--page);color:var(--ink);font:14px system-ui,-apple-system,"Segoe UI",sans-serif;
 display:grid;grid-template-columns:1fr 380px;height:100vh}
#map{height:100%;background:#1b1b1b}
aside{background:var(--surface);border-left:1px solid var(--border);overflow-y:auto;padding:20px 20px 28px;
 display:flex;flex-direction:column;gap:16px}
h1{font-size:18px;margin:0} .sub{color:var(--ink2);font-size:12px;margin-top:4px;line-height:1.45}
.when{font-size:13px;color:var(--ink2);display:flex;justify-content:space-between;align-items:baseline}
.when b{font-size:22px;color:var(--ink);font-weight:600}
.hero .label{color:var(--ink2);font-size:12px}
.hero .value{font-size:44px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.05}
.hero .value small{font-size:18px;font-weight:500;color:var(--ink2);margin-left:4px}
.hero .delta{font-size:13px;color:var(--ink2);margin-top:4px;font-variant-numeric:tabular-nums;min-height:18px}
.chip{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:6px}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.fact{border:1px solid var(--border);border-radius:8px;padding:8px 10px}
.fact .k{font-size:11px;color:var(--ink2)} .fact .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.controls{display:flex;gap:10px;align-items:center}
button{font:inherit;font-weight:600;border:1px solid var(--border);background:var(--page);color:var(--ink);
 border-radius:8px;padding:7px 14px;cursor:pointer;min-width:84px}
button:focus-visible,input:focus-visible{outline:2px solid var(--old);outline-offset:2px}
input[type=range]{flex:1;accent-color:var(--old)}
.chart h2,.legend h2,details summary{font-size:13px;font-weight:600;margin:0 0 6px}
svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--muted)}
.legend ul{list-style:none;margin:0;padding:0;display:grid;gap:5px;font-size:12px;color:var(--ink2)}
.ln{display:inline-block;width:16px;height:0;border-top:2px solid;vertical-align:3px;margin-right:6px}
.tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--axis);
 border-radius:6px;padding:6px 8px;font-size:12px;line-height:1.4;display:none;z-index:2000;box-shadow:0 2px 8px rgba(0,0,0,.15)}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th{color:var(--ink2);font-weight:500;text-align:left;border-bottom:1px solid var(--axis);padding:4px 6px}
td{border-bottom:1px solid var(--grid);padding:4px 6px} .num{text-align:right;font-variant-numeric:tabular-nums}
.note{font-size:11px;color:var(--muted);line-height:1.5}
.leaflet-container{font:12px system-ui,sans-serif}
@media (max-width:820px){body{display:block;height:auto}#map{height:62vh}aside{border-left:0;padding:16px}}
</style></head>
<body>
<div id="map"></div>
<aside>
 <div><h1>How far my runs reach in Toronto</h1>
  <div class="sub">Cumulative runnable area, by quarter. The area counts land within __BUF__ m of a street I've run, plus land my routes fully enclose.</div></div>
 <div class="when"><b id="qlabel">-</b><span id="date"></span></div>
 <div class="hero"><div class="label">Runnable area</div>
  <div class="value"><span id="area">0.0</span><small>km²</small></div>
  <div class="delta" id="delta"></div></div>
 <div class="controls"><button id="play" aria-label="Play or pause">Pause</button>
  <input id="scrub" type="range" min="0" step="1" aria-label="Quarter"></div>
 <div class="facts">
  <div class="fact"><div class="k">Covered this quarter</div><div class="v" id="f-cov">-</div></div>
  <div class="fact"><div class="k">Runs this quarter</div><div class="v" id="f-runs">-</div></div>
  <div class="fact"><div class="k">Enclosed by loops</div><div class="v" id="f-encl">-</div></div>
  <div class="fact"><div class="k">Graph nodes · edges</div><div class="v" id="f-graph">-</div></div>
  <div class="fact"><div class="k">Connected pieces</div><div class="v" id="f-comp">-</div></div>
  <div class="fact"><div class="k">New edges this quarter</div><div class="v" id="f-new">-</div></div>
 </div>
 <div class="chart"><h2>Runnable area to date</h2><svg id="bars" width="100%" height="170" role="img" aria-label="Cumulative runnable area per quarter"></svg></div>
 <div class="chart"><h2>Area covered in each quarter</h2>
  <div class="sub" style="margin:-4px 0 4px">Only that quarter's runs, repeats included</div>
  <svg id="bars2" width="100%" height="170" role="img" aria-label="Area covered by each quarter's runs alone"></svg></div>
 <div class="legend"><h2>Legend</h2><ul>
  <li><span class="chip" style="background:var(--old)"></span>Runnable area to date</li>
  <li><span class="chip" style="background:var(--old);opacity:.4"></span>Land enclosed by my routes</li>
  <li><span class="chip" style="background:var(--new)"></span>Area added this quarter</li>
  <li><span class="ln" style="border-color:#9a9890"></span>Routes from earlier quarters</li>
  <li><span class="ln" style="border-color:var(--new)"></span>Routes run this quarter</li>
 </ul></div>
 <details><summary>Quarterly table</summary><table><thead><tr><th>Quarter</th><th class="num">Runs</th>
  <th class="num">Covered km²</th><th class="num">To date km²</th><th class="num">Added</th></tr></thead><tbody id="tbody"></tbody></table></details>
 <div class="note">Each 25 m grid cell a run passes through is a node in the graph, and each step a run makes between neighbouring cells is an edge. Toronto here means the city's bounding box. Generated __GEN__.</div>
</aside>
<div class="tip" id="tip"></div>
<script>
const D = __DATA__;
const Q = D.quarters, G = D.grid, XY = D.edges.xy, DAY = D.edges.day, N = Q.length;
const BLUE = [57,135,229], ORANGE = [217,89,38];
const RUN_MS = 2600, HOLD_MS = 1500;
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

async function inflate(b64) {
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const s = new Blob([bin]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(s).arrayBuffer());
}
function upper(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= v) lo = m + 1; else hi = m; } return lo; }
const ease = x => x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
const T0 = Date.parse(D.t0 + 'T00:00:00Z');
const fmtDay = d => new Date(T0 + Math.floor(d) * 864e5)
  .toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'});

// ---------- map ----------
const map = L.map('map', {zoomAnimation: false, zoomSnap: 0.25, attributionControl: true});
L.tileLayer(D.tiles + '/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {maxNativeZoom: 16, maxZoom: 18, attribution: 'Tiles &copy; Esri'}).addTo(map);
map.createPane('viz').style.zIndex = 450;
map.createPane('labels').style.zIndex = 500;
map.getPane('labels').style.pointerEvents = 'none';
L.tileLayer(D.tiles + '/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {pane: 'labels', maxNativeZoom: 16, maxZoom: 18, opacity: .8}).addTo(map);
map.fitBounds([[G.south, G.west], [G.north, G.east]], {padding: [10, 10]});

const canvas = L.DomUtil.create('canvas', '', map.getPane('viz'));
const ctx = canvas.getContext('2d');
const off = document.createElement('canvas'); off.width = G.w; off.height = G.h;
const octx = off.getContext('2d');
const img = octx.createImageData(G.w, G.h);
let CORR, FILL, areaKey = '';

function paintArea(level, newK, f) {   // level: quarters fully shown in blue; newK: quarter fading in orange
  const key = level + ':' + newK + ':' + Math.round(f * 12);
  if (key === areaKey) return; areaKey = key;
  const px = img.data;
  for (let i = 0, n = G.w * G.h; i < n; i++) {
    const c = CORR[i], fl = FILL[i], o = i * 4;
    let col = null, a = 0;
    if (fl && fl <= level) { col = BLUE; a = (c && c <= level) ? 0.50 : 0.20; }
    else if (newK >= 0 && fl === newK + 1 && f > 0) { col = ORANGE; a = (c === newK + 1 ? 0.62 : 0.30) * f; }
    if (col) { px[o] = col[0]; px[o + 1] = col[1]; px[o + 2] = col[2]; px[o + 3] = a * 255; } else px[o + 3] = 0;
  }
  octx.putImageData(img, 0, 0);
}

let state = {k: 0, phase: 'run', p: 0};   // phase 'run' (p = progress through quarter) or 'hold' (p = fade)
function curDay() { const q = Q[state.k]; return state.phase === 'hold' ? q.end_day : q.start_day + (q.end_day - q.start_day) * state.p; }

function draw() {
  if (!CORR) return;
  const size = map.getSize(), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== size.x * dpr || canvas.height !== size.y * dpr) {
    canvas.width = size.x * dpr; canvas.height = size.y * dpr;
    canvas.style.width = size.x + 'px'; canvas.style.height = size.y + 'px';
  }
  L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size.x, size.y);
  const p0 = map.latLngToContainerPoint([G.north, G.west]), p1 = map.latLngToContainerPoint([G.south, G.east]);
  const sx = (p1.x - p0.x) / G.w, sy = (p1.y - p0.y) / G.h;

  const {k, phase, p} = state;
  if (phase === 'hold') paintArea(k, k, ease(Math.min(1, p))); else paintArea(k, -1, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(off, p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);

  const t = curDay(), qs = Q[k].start_day;
  const iOld = upper(DAY, qs - 1e-9), iNow = upper(DAY, t), iHead = upper(DAY, t - 6);
  const lw = Math.max(0.9, Math.min(3, sx * 0.35));
  const seg = (a, b) => { ctx.beginPath();
    for (let i = a; i < b; i++) { const j = i * 4;
      ctx.moveTo(p0.x + (XY[j] + .5) * sx, p0.y + (XY[j + 1] + .5) * sy);
      ctx.lineTo(p0.x + (XY[j + 2] + .5) * sx, p0.y + (XY[j + 3] + .5) * sy); }
    ctx.stroke(); };
  ctx.lineCap = 'round';
  ctx.lineWidth = lw; ctx.strokeStyle = 'rgba(200,198,190,.55)'; seg(0, Math.min(iOld, iNow));
  ctx.strokeStyle = 'rgb(240,120,70)'; seg(Math.min(iOld, iNow), iNow);
  if (phase === 'run') { ctx.lineWidth = lw * 2.2; ctx.strokeStyle = 'rgba(255,240,225,.95)'; seg(Math.max(iHead, iOld), iNow); }
}
map.on('move zoom resize viewreset', draw);

// ---------- panel ----------
const $ = id => document.getElementById(id);
const scrub = $('scrub'); scrub.max = N - 1;
const km = v => v.toFixed(1);
function panel() {
  const {k, phase, p} = state, q = Q[k], prev = k ? Q[k - 1].area_km2 : 0;
  const f = phase === 'hold' ? ease(Math.min(1, p)) : 0;
  $('qlabel').textContent = q.quarter;
  $('date').textContent = fmtDay(curDay());
  $('area').textContent = km(prev + (q.area_km2 - prev) * f);
  $('delta').innerHTML = phase === 'hold'
    ? `<span class="chip" style="background:var(--new)"></span>+${km(q.added_km2)} km² this quarter`
    : (k ? `${km(prev)} km² at the end of ${Q[k - 1].quarter}` : 'Starting out');
  $('f-runs').textContent = q.runs;
  $('f-cov').textContent = km(q.covered_km2) + ' km²';
  $('f-new').textContent = q.new_edges.toLocaleString();
  const shown = phase === 'hold' ? q : (k ? Q[k - 1] : null);
  $('f-encl').textContent = shown ? km(shown.enclosed_km2) + ' km²' : '-';
  $('f-graph').textContent = shown ? `${shown.nodes.toLocaleString()} · ${shown.edges.toLocaleString()}` : '-';
  $('f-comp').textContent = shown ? shown.components.toLocaleString() : '-';
  scrub.value = k;
  const done = phase === 'hold' ? k : k - 1, ff = phase === 'hold' ? f : 0;
  bars($('bars'), Q.map(q => q.area_km2), Q.map((q, i) => i ? Q[i - 1].area_km2 : 0), done, ff);
  bars($('bars2'), Q.map(q => q.covered_km2), Q.map(() => 0), done, ff);
}

const tip = $('tip');
// top[i]: bar height; base[i]: the part carried over from before (drawn blue, new part on top)
function bars(svg, top, base, done, f) {
  const W = svg.clientWidth || 340, H = 170, ml = 30, mb = 34, mt = 8, iw = W - ml - 4, ih = H - mb - mt;
  const max = Math.max(...top) || 1;
  const step = max > 40 ? 20 : max > 16 ? 10 : max > 6 ? 5 : 2;
  const y = v => mt + ih - v / (Math.ceil(max / step) * step) * ih;
  const bw = iw / N, w = Math.max(4, bw - 6);
  let s = '';
  for (let v = 0; v <= Math.ceil(max / step) * step + 1e-9; v += step)
    s += `<line x1="${ml}" x2="${W - 4}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${ml - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  Q.forEach((q, i) => {
    const x = ml + i * bw + (bw - w) / 2, prev = base[i], gap = prev > 0 ? 2 : 0;
    if (i <= done) {
      const t = i === done ? prev + (top[i] - prev) * f : top[i];
      s += rect(x, y(prev), w, y(0) - y(prev), 'var(--old)', i, false);
      s += rect(x, y(t), w, Math.max(0, y(prev) - y(t) - gap), i === done ? 'var(--new)' : 'var(--old)', i, true);
    } else s += rect(x, y(top[i]), w, y(0) - y(top[i]), 'var(--ghost)', i, true);
    s += `<text x="${x + w / 2}" y="${H - mb + 14}" text-anchor="middle">${q.short}</text>`;
    if (i === 0 || q.short === 'Q1') s += `<text x="${x + w / 2}" y="${H - mb + 28}" text-anchor="middle" style="fill:var(--ink2)">${q.year}</text>`;
    s += `<rect x="${ml + i * bw}" y="${mt}" width="${bw}" height="${ih}" fill="transparent" data-i="${i}" style="cursor:pointer"/>`;
  });
  svg.innerHTML = s;
}
function rect(x, y, w, h, fill, i, top) {
  if (h <= 0) return '';
  const r = top ? Math.min(4, h, w / 2) : 0;   // rounded data-end on the top segment only
  return `<path d="M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z" fill="${fill}"/>`;
}
for (const svg of [$('bars'), $('bars2')]) {
  svg.addEventListener('mousemove', e => {
    const i = e.target.dataset && e.target.dataset.i; if (i === undefined) { tip.style.display = 'none'; return; }
    const q = Q[+i];
    tip.innerHTML = `<b>${q.quarter}</b> · ${q.runs} runs<br>Covered this quarter: ${km(q.covered_km2)} km²` +
      `<br>Runnable to date: ${km(q.area_km2)} km² (+${km(q.added_km2)})`;
    tip.style.display = 'block'; tip.style.left = Math.min(e.clientX + 12, innerWidth - 230) + 'px'; tip.style.top = (e.clientY + 12) + 'px';
  });
  svg.addEventListener('mouseleave', () => tip.style.display = 'none');
  svg.addEventListener('click', e => { const i = e.target.dataset && e.target.dataset.i; if (i !== undefined) jump(+i); });
}

$('tbody').innerHTML = Q.map(q => `<tr><td>${q.quarter}</td><td class="num">${q.runs}</td><td class="num">${km(q.covered_km2)}</td><td class="num">${km(q.area_km2)}</td><td class="num">+${km(q.added_km2)}</td></tr>`).join('');

// ---------- animation ----------
let playing = !reduce, last = null;
function jump(k) { state = {k, phase: 'hold', p: 1}; playing = false; $('play').textContent = 'Play'; panel(); draw(); }
scrub.addEventListener('input', () => jump(+scrub.value));
$('play').addEventListener('click', () => {
  if (!playing && state.k === N - 1 && state.phase === 'hold' && state.p >= 1) state = {k: 0, phase: 'run', p: 0};
  playing = !playing; $('play').textContent = playing ? 'Pause' : 'Play'; last = null;
});
addEventListener('keydown', e => { if (e.code === 'Space' && e.target === document.body) { e.preventDefault(); $('play').click(); } });
function tick(ts) {
  if (playing) {
    const dt = last == null ? 0 : Math.min(100, ts - last);
    state.p += dt / (state.phase === 'run' ? RUN_MS : HOLD_MS);
    if (state.p >= 1) {
      if (state.phase === 'run') state = {k: state.k, phase: 'hold', p: 0};
      else if (state.k < N - 1) state = {k: state.k + 1, phase: 'run', p: 0};
      else { state.p = 1; playing = false; $('play').textContent = 'Replay'; }
    }
    panel(); draw();
  }
  last = ts; requestAnimationFrame(tick);
}
addEventListener('resize', panel);
(async () => {
  [CORR, FILL] = await Promise.all([inflate(G.corr), inflate(G.fill)]);
  if (reduce) state = {k: N - 1, phase: 'hold', p: 1};
  const h = location.hash.match(/^#(\d+)(?:\.(\d+))?$/);   // #3.6 = paused 60% through quarter 4
  if (h) { state = {k: Math.min(N - 1, +h[1]), phase: h[2] ? 'run' : 'hold', p: h[2] ? +('0.' + h[2]) : 1}; playing = false; }
  $('play').textContent = playing ? 'Pause' : 'Play';
  panel(); draw(); requestAnimationFrame(tick);
})();
</script>
</body></html>
"""
