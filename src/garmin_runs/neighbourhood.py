"""Favourite Toronto neighbourhood to run in, and the streets in it you avoid.

Favourite = the official City of Toronto neighbourhood (158 boundaries) where you've run the
most distinct runs, also checked with the first/last 500 m of every run removed so the area
around home doesn't win just because every run starts there.

Inside it, every street piece (Local / Collector / Minor & Major Arterial from the Toronto
Centreline) is sampled every 10 m. A run "runs along" a piece if it comes within 15 m of at
least 60% of the samples; a run that touches the piece but less than that only "crosses" it.
Avoidance = share of the runs that met a street which only crossed it.

Open data (Toronto Open Data portal, Open Government Licence - Toronto) is downloaded once,
city-wide, into data/opendata/ - nothing about where you run is sent anywhere.
"""
from __future__ import annotations

import html
import json
import urllib.request
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import shape

from .analyze import load_runs
from .fetch import DATA
from .heatmap import ESRI, toronto_only
from .parse import TRACKS

OUT = Path("output")
OPEN = DATA / "opendata"
CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id="
DATASETS = {"neighbourhoods": "Neighbourhoods - 4326.geojson",
            "toronto-centreline-tcl": "Centreline - Version 2 - 4326.geojson"}

STREET_CLASSES = {"Local", "Collector", "Minor Arterial", "Major Arterial"}
SAMPLE_M = 10.0        # sample each street piece every 10 m
RADIUS_M = 15.0        # a GPS point within 15 m counts as being on the street
ALONG = 0.60           # >= 60% of a piece's samples = ran along it
CORE_TRIM_M = 500.0    # for the "not just home" check: drop first/last 500 m of each run
MIN_ENCOUNTERS = 20    # only call a street avoided if you met it at least this often
LAT0 = 43.7            # local metric projection for Toronto

# Diverging preferred (cool) <-> avoided (warm), neutral grey midpoint; dark-map steps
PREF_STOPS = [(0.0, "#3987e5"), (0.25, "#86b6ef"), (0.5, "#9a9993"), (0.75, "#f0a3a3"), (1.0, "#e34948")]


# ---------- data ----------
def _download(pkg: str) -> Path:
    dest = OPEN / f"{pkg}.geojson"
    if dest.exists():
        return dest
    OPEN.mkdir(parents=True, exist_ok=True)
    res = json.load(urllib.request.urlopen(CKAN + pkg, timeout=60))["result"]["resources"]
    url = next(r["url"] for r in res if r["name"] == DATASETS[pkg])
    print(f"Downloading {DATASETS[pkg]} ...")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def _xy(lon, lat) -> np.ndarray:
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    return np.c_[lon * 111_320 * np.cos(np.radians(LAT0)), lat * 110_540]


def _tracks() -> pd.DataFrame:
    runs = load_runs()
    t = pd.read_parquet(TRACKS)
    t = toronto_only(t[t["activity_id"].isin(runs["activity_id"])]).sort_values(["activity_id", "ts"]).copy()
    t["step_m"] = t.groupby("activity_id")["distance_m"].diff().clip(lower=0, upper=50).fillna(0)
    d0 = t.groupby("activity_id")["distance_m"].transform("min")
    d1 = t.groupby("activity_id")["distance_m"].transform("max")
    t["core"] = (t["distance_m"] - d0 > CORE_TRIM_M) & (d1 - t["distance_m"] > CORE_TRIM_M)
    return t


# ---------- favourite neighbourhood ----------
def rank_neighbourhoods(t: pd.DataFrame, hoods: list[dict]) -> pd.DataFrame:
    lon, lat = t["lon"].to_numpy(), t["lat"].to_numpy()
    nb = np.full(len(t), -1)
    for i, h in enumerate(hoods):
        nb[shapely.contains_xy(h["geom"], lon, lat)] = i
    t = t.assign(nb=nb)
    t = t[t["nb"] >= 0]
    all_ = t.groupby("nb").agg(runs=("activity_id", "nunique"), km=("step_m", "sum"))
    core = t[t["core"]].groupby("nb").agg(runs_core=("activity_id", "nunique"), km_core=("step_m", "sum"))
    r = all_.join(core).fillna(0)
    r[["km", "km_core"]] /= 1000
    r.insert(0, "name", [hoods[i]["name"] for i in r.index])
    return r.sort_values(["runs", "km"], ascending=False).reset_index(drop=True)


# ---------- streets ----------
def street_usage(t: pd.DataFrame, poly, centreline: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    pieces = []
    for f in centreline:
        p = f["properties"]
        if p["FEATURE_CODE_DESC"] not in STREET_CLASSES or not p.get("LINEAR_NAME_FULL"):
            continue
        g = shape(f["geometry"])
        if not g.intersects(poly):
            continue
        for part in getattr(g.intersection(poly), "geoms", [g.intersection(poly)]):
            if part.geom_type != "LineString":
                continue
            c = np.asarray(part.coords)[:, :2]
            line = shapely.LineString(_xy(c[:, 0], c[:, 1]))
            if line.length < 20:
                continue
            pieces.append({"name": p["LINEAR_NAME_FULL"], "cls": p["FEATURE_CODE_DESC"],
                           "coords": c, "line": line, "len_m": line.length})

    minx, miny, maxx, maxy = poly.buffer(0.003).bounds
    near = t[t["lon"].between(minx, maxx) & t["lat"].between(miny, maxy)]
    tree = cKDTree(_xy(near["lon"], near["lat"]))
    rid = near["activity_id"].to_numpy()

    for s in pieces:
        n = max(2, int(s["line"].length // SAMPLE_M) + 1)
        samples = np.array([s["line"].interpolate(d).coords[0] for d in np.linspace(0, s["line"].length, n)])
        hits = tree.query_ball_point(samples, RADIUS_M)
        per_run: dict[int, set[int]] = {}
        for k, h in enumerate(hits):
            for r in set(rid[h]):
                per_run.setdefault(int(r), set()).add(k)
        s["along"] = {r for r, ks in per_run.items() if len(ks) / n >= ALONG}
        s["touch"] = set(per_run) - s["along"]
        s["covered"] = float(np.mean([len(h) > 0 for h in hits]))

    rows = []
    for name, grp in pd.DataFrame(pieces).groupby("name"):
        along = set().union(*grp["along"])
        cross = set().union(*grp["touch"]) - along
        met = len(along) + len(cross)
        rows.append({"street": name, "class": grp["cls"].mode()[0], "len_m": grp["len_m"].sum(),
                     "covered_pct": 100 * (grp["len_m"] * grp["covered"]).sum() / grp["len_m"].sum(),
                     "runs_along": len(along), "runs_crossing": len(cross), "met": met,
                     "avoid": len(cross) / met if met else np.nan})
    streets = pd.DataFrame(rows).sort_values(["avoid", "runs_crossing"], ascending=[False, False])
    for s in pieces:  # per-piece score for the map, using the whole street's numbers
        row = streets.loc[streets["street"] == s["name"]].iloc[0]
        s.update(avoid=row["avoid"], runs_along=row["runs_along"], runs_crossing=row["runs_crossing"])
    return streets.reset_index(drop=True), pieces


def verdict(r: pd.Series) -> str:
    if r["met"] == 0:
        return "Never run"
    if r["met"] < MIN_ENCOUNTERS:
        return "Too few runs to tell"
    if r["avoid"] >= 0.9:
        return "Avoided"
    if r["avoid"] >= 0.6:
        return "Rarely run along"
    return "Preferred"


# ---------- page ----------
def _color(a: float) -> str:
    if np.isnan(a):
        return "#5a5955"
    for (t0, c0), (t1, c1) in zip(PREF_STOPS, PREF_STOPS[1:]):
        if a <= t1:
            f = (a - t0) / (t1 - t0)
            x = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
            y = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{round(p + (q - p) * f):02x}" for p, q in zip(x, y))
    return PREF_STOPS[-1][1]


def _map(poly, pieces: list[dict], t: pd.DataFrame, name: str) -> str:
    m = folium.Map(tiles=None, control_scale=True, max_zoom=19, fade_animation=False)
    for layer in ("World_Dark_Gray_Base", "World_Dark_Gray_Reference"):
        folium.TileLayer(tiles=f"{ESRI}/{layer}/MapServer/tile/{{z}}/{{y}}/{{x}}",
                         attr="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
                         overlay=layer.endswith("Reference"), control=False,
                         max_native_zoom=16, max_zoom=19).add_to(m)
    # your runs, faint, for context
    inside = t[shapely.contains_xy(poly.buffer(0.001), t["lon"].to_numpy(), t["lat"].to_numpy())]
    runs_fg = folium.FeatureGroup(name="Your runs (faint)")
    for _, g in inside.groupby("activity_id"):
        folium.PolyLine(g[["lat", "lon"]].round(5).to_numpy().tolist(), color="#f0efec",
                        weight=1.5, opacity=0.07, interactive=False).add_to(runs_fg)
    runs_fg.add_to(m)
    # neighbourhood outline
    folium.GeoJson(shapely.geometry.mapping(poly), name=name, style_function=lambda _: {
        "color": "#c3c2b7", "weight": 1.5, "dashArray": "5 5", "fill": False}, interactive=False).add_to(m)
    # streets coloured by preference
    st_fg = folium.FeatureGroup(name="Streets: preferred → avoided")
    for s in pieces:
        latlon = s["coords"][:, ::-1].round(6).tolist()
        tip = (f"<b>{html.escape(s['name'])}</b><br>{s['runs_along']} runs along · "
               f"{s['runs_crossing']} only crossed"
               + ("" if np.isnan(s["avoid"]) else f"<br>{s['avoid'] * 100:.0f}% of encounters were crossings"))
        folium.PolyLine(latlon, color="#0d0d0d", weight=8, opacity=0.9, interactive=False).add_to(st_fg)
        folium.PolyLine(latlon, color=_color(s["avoid"]), weight=5, opacity=1, tooltip=tip).add_to(st_fg)
    st_fg.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    b = poly.bounds
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]], padding=(10, 10))
    return m.get_root().render()


def _table(head: list[str], rows: list[list[str]], num: set[int]) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>'
                                     for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def build(name: str | None = None) -> Path:
    hoods = [{"name": f["properties"]["AREA_NAME"], "geom": shape(f["geometry"])}
             for f in json.load(open(_download("neighbourhoods"), encoding="utf-8"))["features"]]
    for h in hoods:
        shapely.prepare(h["geom"])
    t = _tracks()
    ranking = rank_neighbourhoods(t, hoods)
    fav = name or ranking.iloc[0]["name"]
    poly = next(h["geom"] for h in hoods if h["name"] == fav)
    centreline = json.load(open(_download("toronto-centreline-tcl"), encoding="utf-8"))["features"]
    streets, pieces = street_usage(t, poly, centreline)
    streets["verdict"] = streets.apply(verdict, axis=1)

    favrow = ranking[ranking["name"] == fav].iloc[0]
    core_rank = int(ranking.sort_values("km_core", ascending=False)["name"].tolist().index(fav)) + 1
    total_runs = t["activity_id"].nunique()

    rank_html = _table(
        ["#", "Neighbourhood", "Runs", "km", "Runs*", "km*"],
        [[str(i + 1), ("<b>" + html.escape(r["name"]) + "</b>") if r["name"] == fav else html.escape(r["name"]),
          f"{int(r['runs'])}", f"{r['km']:.0f}", f"{int(r['runs_core'])}", f"{r['km_core']:.0f}"]
         for i, r in ranking.head(10).iterrows()], {0, 2, 3, 4, 5})

    avoided = streets[streets["verdict"].isin(["Avoided", "Rarely run along"])]
    avoid_html = _table(
        ["Street", "Type", "Verdict", "Runs along", "Only crossed", "Crossing share", "Length"],
        [[f'<span class="sw" style="background:{_color(r["avoid"])}"></span>{html.escape(r["street"])}',
          r["class"], r["verdict"], f"{int(r['runs_along'])}", f"{int(r['runs_crossing'])}",
          f"{r['avoid'] * 100:.0f}%", f"{r['len_m']:.0f} m"] for _, r in avoided.iterrows()],
        {3, 4, 5, 6}) if not avoided.empty else "<p class='muted'>No street meets the avoidance threshold.</p>"

    all_html = _table(
        ["Street", "Type", "Verdict", "Runs along", "Only crossed", "Crossing share", "Length"],
        [[f'<span class="sw" style="background:{_color(r["avoid"])}"></span>{html.escape(r["street"])}',
          r["class"], r["verdict"], f"{int(r['runs_along'])}", f"{int(r['runs_crossing'])}",
          "-" if np.isnan(r["avoid"]) else f"{r['avoid'] * 100:.0f}%", f"{r['len_m']:.0f} m"]
         for _, r in streets.sort_values("runs_along", ascending=False).iterrows()], {3, 4, 5, 6})

    grad = ", ".join(c for _, c in PREF_STOPS)
    top_avoid = avoided.head(3)["street"].tolist()
    page = TEMPLATE.format(
        fav=html.escape(fav), runs=int(favrow["runs"]), total=total_runs, km=favrow["km"],
        runs_core=int(favrow["runs_core"]), km_core=favrow["km_core"], core_rank=core_rank,
        n_hoods=len(ranking), n_streets=len(streets), n_avoid=len(avoided),
        top_avoid=", ".join(html.escape(s) for s in top_avoid) or "none",
        grad=grad, map=html.escape(_map(poly, pieces, t, fav), quote=True),
        ranking=rank_html, avoided=avoid_html, all_streets=all_html,
        radius=int(RADIUS_M), along=int(ALONG * 100), min_enc=MIN_ENCOUNTERS, trim=int(CORE_TRIM_M))
    OUT.mkdir(exist_ok=True)
    path = OUT / "neighbourhood.html"
    path.write_text(page, encoding="utf-8")
    streets.to_csv(OUT / "neighbourhood_streets.csv", index=False)

    print(f"Favourite neighbourhood: {fav} - {int(favrow['runs'])} of {total_runs} Toronto runs, "
          f"{favrow['km']:.0f} km (#{core_rank} by km even without first/last {CORE_TRIM_M:.0f} m)")
    print(streets[["street", "class", "runs_along", "runs_crossing", "verdict"]].to_string(index=False))
    print(f"-> {path}")
    return path


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Favourite Neighbourhood</title>
<style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--accent:#2a78d6}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--accent:#3987e5}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--page);color:var(--ink);font:14px system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:15px;margin:0 0 2px}}
.muted,.note{{color:var(--ink2)}} .note{{font-size:12px;margin:2px 0 8px}}
header{{margin-bottom:20px}} a{{color:var(--accent);text-decoration:none}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px}}
.tile,.card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
.tile .label{{color:var(--ink2);font-size:12px}} .tile .value{{font-size:24px;font-weight:600;margin-top:4px}}
.tile .sub{{color:var(--muted);font-size:12px;margin-top:2px}}
.card{{margin-bottom:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:12px}}
iframe{{width:100%;height:560px;border:0;border-radius:6px}}
.legend{{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--ink2);margin:6px 0 10px;flex-wrap:wrap}}
.legend .bar{{width:220px;height:8px;border-radius:4px;background:linear-gradient(90deg,{grad})}}
.sw{{display:inline-block;width:14px;height:6px;border-radius:3px;margin-right:8px;vertical-align:middle}}
.tablewrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{color:var(--ink2);font-weight:500;text-align:left;border-bottom:1px solid var(--axis);padding:6px 8px}}
td{{border-bottom:1px solid var(--grid);padding:6px 8px}}
td:first-child{{white-space:nowrap}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
details summary{{cursor:pointer;font-weight:600}}
</style></head><body><main>
<header><div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h1>Your favourite neighbourhood: {fav}</h1><a href="report.html" style="font-weight:600">← Running report</a></div>
<div class="muted">From all your Toronto runs · City of Toronto neighbourhood boundaries and street centreline (Toronto Open Data)</div></header>

<section class="tiles">
 <div class="tile"><div class="label">Runs through it</div><div class="value">{runs} of {total}</div><div class="sub">more than any of the {n_hoods} neighbourhoods you've run in</div></div>
 <div class="tile"><div class="label">Distance run inside it</div><div class="value">{km:.0f} km</div><div class="sub">{km_core:.0f} km without the first/last {trim} m of each run (#{core_rank})</div></div>
 <div class="tile"><div class="label">Streets you avoid</div><div class="value">{n_avoid} of {n_streets}</div><div class="sub">{top_avoid}</div></div>
</section>

<div class="card"><h2>Streets you prefer and avoid</h2>
<p class="note">Each street is coloured by how you treat it when your run meets it: do you run along it, or just cross it? Hover a street for its numbers. The faint white lines are your runs.</p>
<div class="legend"><span>Preferred · mostly run along</span><span class="bar"></span><span>Avoided · mostly only crossed</span>
<span><span class="sw" style="background:#5a5955"></span>never met</span></div>
<iframe srcdoc="{map}"></iframe></div>

<div class="grid">
 <div class="card"><h2>Streets you avoid or don't prefer</h2><p class="note">Met by at least {min_enc} runs; "Avoided" = 90%+ of those runs only crossed it, "Rarely run along" = 60–90%.</p>{avoided}</div>
 <div class="card"><h2>Why this neighbourhood</h2><p class="note">Top 10 by number of runs. * = without the first/last {trim} m of every run, so home doesn't win by default.</p>{ranking}</div>
</div>

<div class="card"><details><summary>Every street in {fav}</summary>{all_streets}</details></div>

<p class="note">How it's measured: every street piece is sampled every 10 m. A run "ran along" a piece if it came within {radius} m of at least {along}% of its samples; a run that touched the piece but less than that only crossed it. Streets are Local, Collector and Arterial roads (expressways, ramps, laneways and trails excluded). Where a trail runs right beside a road (e.g. the Martin Goodman Trail beside Lake Shore Blvd), runs on the trail can register on the road.</p>
</main></body></html>
"""
