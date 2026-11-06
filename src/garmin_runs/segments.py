"""Find the stretches of road you've run most often, then time every pass through them."""
from __future__ import annotations

import html
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .analyze import fmt_dur, fmt_pace, load_runs
from .fetch import DATA
from .heatmap import toronto_only
from .parse import TRACKS

OUT = Path("output")
CELL_M = 25.0          # grid size for matching paths
COVER = 0.90           # a run "uses" a segment if it touches >= 90% of its cells
STEP_M = 100.0         # candidate windows start every 100 m along each run
END_RADIUS_M = 35.0    # how close a pass must come to the segment's start/end points
MIN_SPAN = 0.5         # straight-line start->end must be >= 50% of the segment length
MAX_PACE = 9.0         # min/km; slower candidate windows are walking/stopped, not running
MAX_GAP_M = 50.0       # a jump bigger than this between points = gap, not a continuous stretch
EXACT_CANDIDATES = 4000  # candidates that get an exact run count
MAX_OVERLAP = 0.2      # a new segment may share at most 20% of its path with a chosen one
WEATHER_ROUND = 1      # decimals for the weather lookup point (~10 km): one series per city

# Categorical slots, dark-surface steps (map) and light/dark pairs (page swatches).
# Beyond 3 these are not colour-blind-safe on their own, so every segment also
# carries its rank number on the map and in every heading.
SEG_COLORS_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
SEG_COLORS_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
TEMP_BANDS = ([-40, 0, 10, 20, 50], ["Below 0 °C", "0–10 °C", "10–20 °C", "20 °C +"])


# ---------- geometry ----------
def _xy(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Approximate metres (locally accurate; fine for 25 m cells)."""
    return lon * 111_320 * np.cos(np.radians(lat)), lat * 110_540


def _cells(lat, lon) -> np.ndarray:
    x, y = _xy(np.asarray(lat), np.asarray(lon))
    return np.stack([np.floor(x / CELL_M), np.floor(y / CELL_M)], axis=1).astype(np.int64)


def _dilate(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {(cx + dx, cy + dy) for cx, cy in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)}


def _dist_m(lat1, lon1, lat2, lon2):
    x1, y1 = _xy(lat1, lon1)
    x2, y2 = _xy(lat2, lon2)
    return np.hypot(x1 - x2, y1 - y2)


def _compass(start: np.ndarray, end: np.ndarray) -> str:
    """'eastbound' etc. for the forward direction start -> end."""
    dy = (end[0] - start[0]) * 110_540
    dx = (end[1] - start[1]) * 111_320 * math.cos(math.radians(start[0]))
    deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    names = ["northbound", "northeastbound", "eastbound", "southeastbound",
             "southbound", "southwestbound", "westbound", "northwestbound"]
    return names[int((deg + 22.5) // 45) % 8]


_OPPOSITE = {"northbound": "southbound", "southbound": "northbound", "eastbound": "westbound",
             "westbound": "eastbound", "northeastbound": "southwestbound",
             "southwestbound": "northeastbound", "southeastbound": "northwestbound",
             "northwestbound": "southeastbound"}


# ---------- finding segments ----------
def find_segments(tracks: pd.DataFrame, length_m: float, n: int = 5) -> list[dict]:
    by_run = {aid: g.reset_index(drop=True) for aid, g in tracks.groupby("activity_id")}

    # inverted index: dilated cell -> runs that pass through it
    index: dict[tuple[int, int], set[int]] = defaultdict(set)
    for aid, g in by_run.items():
        for c in _dilate({tuple(c) for c in _cells(g["lat"], g["lon"])}):
            index[c].add(aid)
    popularity = {c: len(r) for c, r in index.items()}

    # candidate windows of `length_m` along every run
    candidates = []
    for aid, g in by_run.items():
        d = g["distance_m"].to_numpy(dtype=float)
        if np.isnan(d).all() or d[-1] - d[0] < length_m:
            continue
        cells = _cells(g["lat"], g["lon"])
        lat, lon = g["lat"].to_numpy(), g["lon"].to_numpy()
        t = pd.to_datetime(g["ts"], utc=True)
        for s in np.arange(d[0], d[-1] - length_m, STEP_M):
            i, j = np.searchsorted(d, [s, s + length_m])
            j = min(j, len(d) - 1)
            # must be a full-length, continuous stretch: a window starting inside a GPS gap or a
            # privacy hole would otherwise be silently shorter than length_m
            if d[j] - d[i] < 0.95 * length_m or np.diff(d[i:j + 1]).max(initial=0) > MAX_GAP_M:
                continue
            # must actually go somewhere (not a loop/zig-zag around the start area)...
            if _dist_m(lat[i], lon[i], lat[j], lon[j]) < MIN_SPAN * length_m:
                continue
            # ...and be run, not walked or paused at lights
            if (t[j] - t[i]).total_seconds() / 60 / (length_m / 1000) > MAX_PACE:
                continue
            seq = list(dict.fromkeys(map(tuple, cells[i:j + 1])))
            if len(seq) < length_m / CELL_M * 0.6:   # GPS gap in this window
                continue
            # cheap score: a low percentile of cell popularity bounds how many runs share it
            est = np.percentile([popularity[c] for c in seq], 10)
            candidates.append((est, aid, i, j, seq))

    # exact "how many runs use this window" for the most promising candidates
    candidates.sort(key=lambda c: -c[0])
    scored = []
    for est, aid, i, j, seq in candidates[:EXACT_CANDIDATES]:
        hits = Counter(r for c in seq for r in index[c])
        users = {r for r, k in hits.items() if k >= COVER * len(seq)}
        scored.append((len(users), users, aid, i, j, seq))
    scored.sort(key=lambda s: -s[0])

    # greedily take the most-used windows that don't overlap ones already chosen
    chosen: list[dict] = []
    for _, users, aid, i, j, seq in scored:
        # wide zone (~50-75 m) so a parallel street/path isn't reported as a separate segment
        if any(sum(c in s["wide_zone"] for c in seq) > MAX_OVERLAP * len(seq) for s in chosen):
            continue
        g = by_run[aid]
        zone = _dilate(set(seq))
        chosen.append({"runs": users, "cells": seq, "zone": zone, "wide_zone": _dilate(_dilate(zone)),
                       "path": g.loc[i:j, ["lat", "lon"]].to_numpy(),
                       "start": g.loc[i, ["lat", "lon"]].to_numpy(dtype=float),
                       "end": g.loc[j, ["lat", "lon"]].to_numpy(dtype=float),
                       "length_m": float(g.loc[j, "distance_m"] - g.loc[i, "distance_m"])})
        if len(chosen) == n:
            break
    if not chosen:
        raise SystemExit("No runs long enough to contain a segment of that length.")
    return chosen


# ---------- timing each pass ----------
def _visits(near: np.ndarray, dist_to: np.ndarray) -> list[int]:
    """Indices of closest approach for each separate visit within the radius."""
    out, i, n = [], 0, len(near)
    while i < n:
        if near[i]:
            j = i
            while j < n and near[j]:
                j += 1
            out.append(i + int(np.argmin(dist_to[i:j])))
            i = j
        else:
            i += 1
    return out


def passes(tracks: pd.DataFrame, seg: dict) -> pd.DataFrame:
    seg_cells = seg["zone"]
    fwd, rev = seg["compass"], _OPPOSITE[seg["compass"]]
    rows = []
    for aid in seg["runs"]:
        g = tracks[tracks["activity_id"] == aid].reset_index(drop=True)
        lat, lon = g["lat"].to_numpy(), g["lon"].to_numpy()
        d = g["distance_m"].to_numpy(dtype=float)
        ts = pd.to_datetime(g["ts"], utc=True)
        ds, de = _dist_m(lat, lon, *seg["start"]), _dist_m(lat, lon, *seg["end"])
        vs, ve = _visits(ds < END_RADIUS_M, ds), _visits(de < END_RADIUS_M, de)
        cells = _cells(lat, lon)
        for a in vs:
            for b in ve:
                lo, hi = sorted((a, b))
                span = d[hi] - d[lo]
                if not (0.85 * seg["length_m"] <= span <= 1.25 * seg["length_m"]):
                    continue
                inside = np.mean([tuple(c) in seg_cells for c in cells[lo:hi + 1]])
                if inside < 0.9:
                    continue
                secs = (ts[hi] - ts[lo]).total_seconds()
                hr = g.loc[lo:hi, "hr"]
                rows.append({
                    "activity_id": aid,
                    "start_utc": ts[lo],
                    "direction": fwd if a < b else rev,
                    "secs": secs,
                    "pace_min_km": secs / 60 / (seg["length_m"] / 1000),
                    "avg_hr": float(np.nanmean(hr)) if hr.notna().any() else np.nan,
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(["activity_id", "start_utc"]).sort_values("start_utc").reset_index(drop=True)


# ---------- weather ----------
def _om_hourly(url: str, params: dict) -> pd.Series:
    r = requests.get(url, params=params, timeout=30, headers={"User-Agent": "garmin-runs/0.1"})
    r.raise_for_status()
    h = r.json()["hourly"]
    return pd.Series(h["temperature_2m"], index=pd.to_datetime(h["time"], utc=True), dtype=float)


def temperatures(lat: float, lon: float, start: datetime, end: datetime) -> pd.Series:
    """Hourly 2 m air temperature (°C, UTC index) from Open-Meteo, cached in data/."""
    lat, lon = round(lat, WEATHER_ROUND), round(lon, WEATHER_ROUND)
    cache = DATA / f"weather_{lat}_{lon}.json"
    if cache.exists():
        s = pd.read_json(cache, typ="series")
        s.index = pd.to_datetime(s.index, utc=True)
        if s.index.min() <= pd.Timestamp(start).floor("h") and s.index.max() >= pd.Timestamp(end).floor("h"):
            return s
    base = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m", "timezone": "GMT"}
    s = _om_hourly("https://archive-api.open-meteo.com/v1/archive",
                   base | {"start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}"})
    # the archive lags a few days; fill the most recent days from the forecast API
    recent = _om_hourly("https://api.open-meteo.com/v1/forecast", base | {"past_days": 14, "forecast_days": 1})
    s = s.combine_first(recent).dropna()
    s.to_json(cache, date_format="iso")
    return s


# ---------- pipeline ----------
def run(length_m: float = 1000, years: float = 2, top: int = 5,
        out_dir: Path = OUT, public: bool = False) -> Path:
    """public=True: segments are found on privacy-scrubbed tracks and no CSV is written."""
    since = datetime.now(timezone.utc) - timedelta(days=round(365.25 * years))
    runs = load_runs()
    names = runs.set_index("activity_id")
    tracks = pd.read_parquet(TRACKS)
    ts = pd.to_datetime(tracks["ts"], utc=True)
    recent_ids = set(runs.loc[runs["start"] >= since.replace(tzinfo=None), "activity_id"])
    tracks = tracks[tracks["activity_id"].isin(recent_ids) & (ts >= since)]
    tracks = toronto_only(tracks)  # segments (and their map) are Toronto-only
    if public:
        from . import privacy
        tracks = privacy.apply(tracks)

    # Over-fetch candidates, time them, then rank by runs with a *timed* pass: a run that
    # merely passes near a segment (e.g. on a parallel path) doesn't count.
    segs = find_segments(tracks, length_m, top * 3)
    for seg in segs:
        seg["compass"] = _compass(seg["start"], seg["end"])
        seg["passes"] = passes(tracks, seg)
        seg["runs"] = set(seg["passes"]["activity_id"]) if not seg["passes"].empty else set()
    segs = sorted((s for s in segs if s["runs"]), key=lambda s: (-len(s["runs"]), -len(s["passes"])))[:top]

    all_passes = []
    now = datetime.now(timezone.utc)
    for rank, seg in enumerate(segs, 1):
        seg["rank"] = rank
        p = seg["passes"]
        mid = seg["path"][len(seg["path"]) // 2]
        temp = temperatures(mid[0], mid[1], p["start_utc"].min().to_pydatetime(), now)
        p["temp_c"] = p["start_utc"].dt.round("h").map(temp)
        p["run_name"] = p["activity_id"].map(names["name"])
        p["local_start"] = p["activity_id"].map(names["start"])
        p.insert(0, "segment", rank)
        seg["passes"] = p
        all_passes.append(p)

    out_dir.mkdir(parents=True, exist_ok=True)
    _print_summary(segs)
    path = _html(segs, since, out_dir)
    print(f"Segment report -> {path}")
    if not public:
        out_csv = out_dir / "segment_passes.csv"
        pd.concat(all_passes).to_csv(out_csv, index=False)
        print(f"All passes -> {out_csv}")
    return path


def _print_summary(segs: list[dict]) -> None:
    print(f"{'#':>2} {'runs':>5} {'passes':>6} {'best':>6} {'median':>7} {'temp med':>9}  forward dir / start")
    for s in segs:
        p = s["passes"]
        if p.empty:
            print(f"{s['rank']:>2} {len(s['runs']):>5}      0  (no timed passes)")
            continue
        print(f"{s['rank']:>2} {len(s['runs']):>5} {len(p):>6} {fmt_dur(p['secs'].min()):>6} "
              f"{fmt_dur(p['secs'].median()):>7} {p['temp_c'].median():>7.0f}°C  "
              f"{s['compass']} from {s['start'][0]:.5f},{s['start'][1]:.5f}")


# ---------- HTML ----------
def _fmt_temp(t: float) -> str:
    return "" if pd.isna(t) else f"{t + 0.0:.1f}".replace("-0.0", "0.0")


def _map_html(segs: list[dict]) -> str:
    import folium

    from .heatmap import ESRI
    m = folium.Map(tiles=None, control_scale=True)
    folium.TileLayer(tiles=f"{ESRI}/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",
                     attr="Tiles &copy; Esri", max_native_zoom=16, max_zoom=19).add_to(m)
    folium.TileLayer(tiles=f"{ESRI}/World_Dark_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}",
                     attr="Esri", overlay=True, max_native_zoom=16, max_zoom=19).add_to(m)
    for s in segs:
        color = SEG_COLORS_DARK[(s["rank"] - 1) % len(SEG_COLORS_DARK)]
        tip = f"#{s['rank']} · {len(s['runs'])} runs"
        folium.PolyLine(s["path"].tolist(), color="#0d0d0d", weight=9, opacity=0.8).add_to(m)  # surface ring
        folium.PolyLine(s["path"].tolist(), color=color, weight=6, opacity=1, tooltip=tip).add_to(m)
        folium.CircleMarker(s["start"].tolist(), radius=4, color="#0d0d0d", weight=2, fill=True,
                            fill_color="#f0efec", fill_opacity=1,
                            tooltip=f"#{s['rank']} start ({s['compass']})").add_to(m)
        mid = s["path"][len(s["path"]) // 2].tolist()
        folium.Marker(mid, tooltip=tip, icon=folium.DivIcon(
            icon_size=(22, 22), icon_anchor=(11, 11),
            html=f'<div style="width:22px;height:22px;border-radius:11px;background:{color};'
                 f'border:2px solid #0d0d0d;color:#fff;font:600 12px system-ui,sans-serif;'
                 f'display:flex;align-items:center;justify-content:center">{s["rank"]}</div>')).add_to(m)
    pts = np.vstack([s["path"] for s in segs])
    pad = 0.003
    m.fit_bounds([[pts[:, 0].min() - pad, pts[:, 1].min() - pad], [pts[:, 0].max() + pad, pts[:, 1].max() + pad]])
    return m.get_root().render()


def _bands(p: pd.DataFrame) -> pd.Series:
    return pd.cut(p["temp_c"], bins=TEMP_BANDS[0], labels=TEMP_BANDS[1])


def _swatch(rank: int) -> str:
    return f'<span class="sw" style="--c:var(--seg{rank})">{rank}</span>'


def _overview_rows(segs: list[dict]) -> str:
    out = []
    for s in segs:
        p = s["passes"]
        if p.empty:
            out.append(f'<tr><td>{_swatch(s["rank"])}</td><td>{s["compass"]}</td>'
                       f'<td class="num">{len(s["runs"])}</td><td class="num">0</td>'
                       + '<td class="num">-</td>' * 4 + "</tr>")
            continue
        bands = _bands(p)
        by_band = p.groupby(bands, observed=True)["pace_min_km"].median()
        fastest_band = by_band.idxmin() if len(by_band) else "-"
        out.append(
            f'<tr><td><a href="#seg{s["rank"]}">{_swatch(s["rank"])}</a></td>'
            f'<td>{s["compass"]} / {_OPPOSITE[s["compass"]]}</td>'
            f'<td class="num">{len(s["runs"])}</td><td class="num">{len(p)}</td>'
            f'<td class="num">{fmt_dur(p["secs"].min())}</td><td class="num">{fmt_dur(p["secs"].median())}</td>'
            f'<td class="num">{p["temp_c"].median():.0f} °C</td><td>{fastest_band}</td></tr>')
    return "".join(out)


def _section(s: dict) -> str:
    p, r = s["passes"], s["rank"]
    head = (f'<h2 class="segh" id="seg{r}">{_swatch(r)} Segment {r} '
            f'<span class="muted">· {s["length_m"]:.0f} m · {len(s["runs"])} runs</span></h2>')
    if p.empty:
        return head + '<p class="muted">No complete passes could be timed on this segment.</p>'
    best = p.loc[p["secs"].idxmin()]
    nf = int((p["direction"] == s["compass"]).sum())
    tiles = [
        ("Passes", f"{len(p)}", f"{nf} {s['compass']}, {len(p) - nf} {_OPPOSITE[s['compass']]}"),
        ("Best time", fmt_dur(best.secs), f"{fmt_pace(best.pace_min_km)} /km · {best.local_start:%d %b %Y}"),
        ("Median time", fmt_dur(p["secs"].median()), f"{fmt_pace(p['pace_min_km'].median())} /km"),
        ("Median temperature", f"{p['temp_c'].median():.0f} °C",
         f"range {p['temp_c'].min():.0f} to {p['temp_c'].max():.0f} °C"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="label">{a}</div><div class="value">{b}</div><div class="sub">{c}</div></div>'
        for a, b, c in tiles)

    bands = _bands(p)
    bt =p.groupby(bands, observed=True).agg(n=("secs", "size"), pace=("pace_min_km", "median"),
                                             secs=("secs", "median"))
    band_rows = "".join(
        f'<tr><td>{b}</td><td class="num">{int(x.n)}</td><td class="num">{fmt_dur(x.secs)}</td>'
        f'<td class="num">{fmt_pace(x.pace)}</td></tr>' for b, x in bt.iterrows())
    rows = "".join(
        f"<tr><td>{x.local_start:%Y-%m-%d}</td><td>{html.escape(str(x.run_name))}</td><td>{x.direction}</td>"
        f'<td class="num">{fmt_dur(x.secs)}</td><td class="num">{fmt_pace(x.pace_min_km)}</td>'
        f'<td class="num">{_fmt_temp(x.temp_c)}</td>'
        f'<td class="num">{"" if pd.isna(x.avg_hr) else f"{x.avg_hr:.0f}"}</td></tr>'
        for x in p.sort_values("start_utc", ascending=False).itertuples())

    return f"""{head}
<section class="tiles">{tiles_html}</section>
<div class="grid">
 <div class="card"><h3>Pace over time</h3><p class="note">min/km, faster is higher</p><div id="time{r}" class="chart"></div></div>
 <div class="card"><h3>Pace vs temperature</h3><p class="note">min/km against air temperature (°C)</p><div id="temp{r}" class="chart"></div></div>
</div>
<div class="grid">
 <div class="card"><h3>By temperature</h3><p class="note">median per band</p>
  <div class="tablewrap"><table><thead><tr><th>Temperature</th><th class="num">Passes</th><th class="num">Time</th><th class="num">Pace /km</th></tr></thead><tbody>{band_rows}</tbody></table></div></div>
 <div class="card"><details><summary><h3>Every pass ({len(p)})</h3></summary>
  <div class="tablewrap"><table><thead><tr><th>Date</th><th>Run</th><th>Direction</th><th class="num">Time</th><th class="num">Pace</th><th class="num">°C</th><th class="num">HR</th></tr></thead><tbody>{rows}</tbody></table></div></details></div>
</div>"""


def _chart_data(segs: list[dict]) -> list[dict]:
    out = []
    for s in segs:
        p = s["passes"]
        if p.empty:
            continue
        out.append({
            "rank": s["rank"],
            "date": [f"{d:%Y-%m-%d %H:%M}" for d in p["local_start"]],
            "pace": p["pace_min_km"].round(3).tolist(),
            "temp": [None if pd.isna(t) else round(t, 1) for t in p["temp_c"]],
            "label": [f"{d:%d %b %Y} · {dr} · {fmt_dur(sec)} ({fmt_pace(pc)} /km)"
                      + ("" if pd.isna(t) else f" · {t:.0f} °C")
                      for d, dr, sec, pc, t in zip(p["local_start"], p["direction"], p["secs"],
                                                   p["pace_min_km"], p["temp_c"])],
        })
    return out


def _html(segs: list[dict], since: datetime, out_dir: Path = OUT) -> Path:
    seg_vars_light = "".join(f"--seg{i + 1}:{c};" for i, c in enumerate(SEG_COLORS_LIGHT))
    seg_vars_dark = "".join(f"--seg{i + 1}:{c};" for i, c in enumerate(SEG_COLORS_DARK))
    page = (SEGMENT_TEMPLATE
            .replace("__SEGVARS_LIGHT__", seg_vars_light)
            .replace("__SEGVARS_DARK__", seg_vars_dark)
            .replace("__N__", str(len(segs)))
            .replace("__LEN__", f"{segs[0]['length_m'] / 1000:.0f} km" if segs[0]["length_m"] >= 950
                     else f"{segs[0]['length_m']:.0f} m")
            .replace("__SINCE__", f"{since:%d %b %Y}")
            .replace("__MAP__", html.escape(_map_html(segs), quote=True))
            .replace("__OVERVIEW__", _overview_rows(segs))
            .replace("__SECTIONS__", "\n".join(_section(s) for s in segs))
            .replace("__DATA__", json.dumps(_chart_data(segs))))
    path = out_dir / "segment.html"
    path.write_text(page, encoding="utf-8")
    return path


SEGMENT_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Most-Run Segments</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s1soft:rgba(42,120,214,.35);__SEGVARS_LIGHT__}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s1soft:rgba(57,135,229,.40);__SEGVARS_DARK__}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:18px;margin:0} h3{font-size:15px;margin:0 0 2px;display:inline}
.segh{margin:40px 0 12px;display:flex;align-items:center;gap:10px;scroll-margin-top:16px}
.muted,.note{color:var(--ink2)} .note{font-size:12px;margin:2px 0 8px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:20px}
a{color:var(--s1);text-decoration:none} header a{font-weight:600}
.sw{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:11px;
    background:var(--c);color:#fff;font-weight:600;font-size:12px;border:2px solid var(--surface);flex:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.tile .label{color:var(--ink2);font-size:12px} .tile .value{font-size:26px;font-weight:600;margin-top:4px}
.tile .sub{color:var(--muted);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:12px;margin-bottom:12px}
.card.wide{margin-bottom:12px}
.chart{height:280px}
iframe{width:100%;height:440px;border:0;border-radius:6px}
summary{cursor:pointer;color:var(--ink)} summary h3{color:var(--ink)}
.tablewrap{overflow-x:auto;max-height:520px;overflow-y:auto;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--ink2);font-weight:500;text-align:left;border-bottom:1px solid var(--axis);padding:6px 8px;position:sticky;top:0;background:var(--surface)}
td{border-bottom:1px solid var(--grid);padding:6px 8px}
.num{text-align:right;font-variant-numeric:tabular-nums}
</style></head>
<body><main>
<header><div><h1>Your top __N__ most-run __LEN__ segments</h1>
<div class="muted">Runs since __SINCE__ · temperature is hourly air temperature from Open-Meteo</div></div>
<a href="report.html">← Running report</a></header>

<div class="card wide"><h3>Where they are</h3><p class="note">Numbered by how many runs use each one · white dot = start of the forward direction</p>
<iframe srcdoc="__MAP__"></iframe></div>

<div class="card wide"><h3>Ranking</h3><p class="note">click a number to jump to that segment</p>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Direction</th><th class="num">Runs</th><th class="num">Timed passes</th>
<th class="num">Best</th><th class="num">Median</th><th class="num">Median temp</th><th>Fastest in</th></tr></thead>
<tbody>__OVERVIEW__</tbody></table></div></div>

__SECTIONS__
</main>
<script>
const SEGS = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pf = v => { const m = Math.floor(v), s = Math.round((v - m) * 60); return s === 60 ? `${m+1}:00` : `${m}:${String(s).padStart(2,'0')}`; };
function layout(S, x) {
  const ax = {gridcolor: css('--grid'), linecolor: css('--axis'), zeroline: false, fixedrange: true,
              tickfont: {color: css('--muted'), size: 11}};
  const lo = Math.floor(Math.min(...S.pace) * 2) / 2, hi = Math.ceil(Math.max(...S.pace) * 2) / 2, t = [];
  const step = hi - lo > 4 ? 1 : 0.5;
  for (let v = lo; v <= hi; v += step) t.push(v);
  return {paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', showlegend: false,
    font: {family: 'system-ui,-apple-system,Segoe UI,sans-serif', color: css('--ink2')},
    margin: {l: 44, r: 12, t: 8, b: 36},
    hoverlabel: {bgcolor: css('--surface'), bordercolor: css('--axis'), font: {color: css('--ink')}},
    xaxis: Object.assign({}, ax, {showgrid: false}, x),
    yaxis: Object.assign({}, ax, {autorange: 'reversed', tickvals: t, ticktext: t.map(pf)})};
}
function render() {
  const cfg = {displayModeBar: false, responsive: true};
  for (const S of SEGS) {
    const c = css('--seg' + S.rank);
    const mk = {color: c, opacity: 0.55, size: 9, line: {color: css('--surface'), width: 1}};
    Plotly.react('time' + S.rank, [{type: 'scatter', mode: 'markers', x: S.date, y: S.pace, text: S.label,
      marker: mk, hovertemplate: '%{text}<extra></extra>'}], layout(S, {type: 'date'}), cfg);
    Plotly.react('temp' + S.rank, [{type: 'scatter', mode: 'markers', x: S.temp, y: S.pace, text: S.label,
      marker: mk, hovertemplate: '%{text}<extra></extra>'}], layout(S, {ticksuffix: ' °C', showgrid: true}), cfg);
  }
}
render();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
</script>
</body></html>
"""
