"""Interactive Leaflet heatmap of every GPS run."""
from __future__ import annotations

from pathlib import Path

import folium
import numpy as np
import pandas as pd
from folium.plugins import HeatMap

from .analyze import load_runs
from .parse import TRACKS

OUT = Path("output")
ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"

# Categorical slots (dark-surface steps), assigned to years oldest-first so a year
# keeps its colour as new years are added. The first three are colour-blind safe as
# a set; beyond that colours start to collide, so years 4+ reuse a neutral grey.
YEAR_COLORS = ["#3987e5", "#d95926", "#199e70"]
OLDER_COLOR = "#9a9993"


def year_colors(years: list[int]) -> dict[int, str]:
    years = sorted(years)
    recent = years[-len(YEAR_COLORS):]  # newest years get the distinct colours
    return {y: YEAR_COLORS[recent.index(y)] if y in recent else OLDER_COLOR for y in years}


def _trim(g: pd.DataFrame, meters: float) -> pd.DataFrame:
    """Drop the first/last `meters` of a run so start/end points (home) stay private."""
    if meters <= 0 or g["distance_m"].isna().all():
        return g
    d = g["distance_m"].ffill().bfill()
    return g[(d >= d.iloc[0] + meters) & (d <= d.iloc[-1] - meters)]


def _areas(tracks: pd.DataFrame) -> list[dict]:
    """Group runs into areas (~50 km cells) so far-apart places get their own jump button."""
    first = tracks.groupby("activity_id")[["lat", "lon"]].median()
    first["cell"] = list(zip((first["lat"] / 0.5).round(), (first["lon"] / 0.5).round()))
    areas = []
    for _, ids in first.groupby("cell").groups.items():
        pts = tracks[tracks["activity_id"].isin(ids)]
        lo, hi = pts[["lat", "lon"]].quantile(0.01), pts[["lat", "lon"]].quantile(0.99)
        areas.append({"runs": len(ids), "bounds": [[lo.lat, lo.lon], [hi.lat, hi.lon]],
                      "center": (pts["lat"].median(), pts["lon"].median())})
    return sorted(areas, key=lambda a: -a["runs"])


def build(trim_meters: float = 0) -> Path:
    tracks = pd.read_parquet(TRACKS)
    runs = load_runs().set_index("activity_id")
    tracks = tracks[tracks["activity_id"].isin(runs.index)]
    tracks = pd.concat([_trim(g, trim_meters) for _, g in tracks.groupby("activity_id")])
    areas = _areas(tracks)

    # Esri dark-gray canvas: no API key, and (unlike tile.openstreetmap.org) works when the
    # HTML is opened straight from disk, where browsers send no Referer header.
    m = folium.Map(tiles=None, control_scale=True, prefer_canvas=True, max_zoom=19)
    for layer, name in [("World_Dark_Gray_Base", "Dark basemap"), ("World_Dark_Gray_Reference", "Labels")]:
        folium.TileLayer(
            tiles=f"{ESRI}/{layer}/MapServer/tile/{{z}}/{{y}}/{{x}}",
            attr="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
            name=name, overlay=layer.endswith("Reference"), control=layer.endswith("Reference"),
            max_native_zoom=16, max_zoom=19,
        ).add_to(m)
    m.fit_bounds(areas[0]["bounds"])

    # Single-hue heat layer (off by default: its blue would read as a year colour)
    HeatMap(tracks[["lat", "lon"]].to_numpy().tolist(), name="Heat (all years)", show=False,
            radius=6, blur=6, min_opacity=0.25, max_zoom=16,
            gradient={0.2: "#184f95", 0.45: "#3987e5", 0.7: "#86b6ef", 1.0: "#ffffff"}).add_to(m)

    years = runs.loc[tracks["activity_id"].unique(), "start"].dt.year
    colors = year_colors(years.unique().tolist())
    # Oldest added first so newer years draw on top
    for year in sorted(years.unique()):
        ids = years[years == year].index
        color = colors[year]
        swatch = (f'<span style="display:inline-block;width:14px;height:4px;border-radius:2px;'
                  f'background:{color};vertical-align:middle;margin:0 6px 2px 0"></span>')
        fg = folium.FeatureGroup(name=f"{swatch}{year} · {len(ids)} runs")
        for aid in ids:
            g = tracks[tracks["activity_id"] == aid]
            r = runs.loc[aid]
            folium.PolyLine(
                g[["lat", "lon"]].to_numpy().tolist(), color=color, weight=2.5, opacity=0.6,
                tooltip=f"{r['name']} · {r['start']:%Y-%m-%d} · {r['km']:.1f} km",
            ).add_to(fg)
        fg.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # "Jump to area" buttons for runs in far-apart places
    buttons = "".join(
        f'<button onclick="window._map.fitBounds({a["bounds"]})">'
        f'{a["center"][0]:.2f}, {a["center"][1]:.2f} · {a["runs"]} runs</button>'
        for a in areas
    )
    m.get_root().html.add_child(folium.Element(f"""
<style>
 .leaflet-container{{background:#1a1a19}}
 #areas{{position:fixed;bottom:64px;left:12px;z-index:1000;display:flex;flex-direction:column;gap:4px;
        font:12px system-ui,-apple-system,"Segoe UI",sans-serif}}
 #areas b{{color:#f0efec;text-shadow:0 1px 2px #000}}
 #areas button{{background:#1a1a19;color:#f0efec;border:1px solid #383835;border-radius:4px;
               padding:4px 8px;cursor:pointer;text-align:left}}
 #areas button:hover{{background:#2c2c2a}}
</style>
<div id="areas"><b>Jump to area</b>{buttons}</div>
<script>window.addEventListener('load',()=>{{window._map={m.get_name()};}});</script>
"""))

    OUT.mkdir(exist_ok=True)
    path = OUT / "heatmap.html"
    m.save(path)
    n = tracks["activity_id"].nunique()
    print(f"Heatmap: {n} runs, {len(tracks):,} points, {len(areas)} area(s) -> {path}")
    return path
