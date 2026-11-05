"""Interactive Leaflet heatmap of every GPS run, one coloured heat layer per year."""
from __future__ import annotations

from pathlib import Path

import folium
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
ALL_YEARS_COLOR = "#9085e9"  # violet: a palette slot no year uses, and visible on the dark map


def year_colors(years: list[int]) -> dict[int, str]:
    years = sorted(years)
    recent = years[-len(YEAR_COLORS):]  # newest years get the distinct colours
    return {y: YEAR_COLORS[recent.index(y)] if y in recent else OLDER_COLOR for y in years}


def _mix(hex_color: str, other: str, f: float) -> str:
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def heat_gradient(color: str) -> dict[float, str]:
    """Single-hue ramp for one year: dark shade -> the year colour -> light tint at the hottest."""
    return {0.2: _mix(color, "#000000", 0.45), 0.5: color,
            0.8: _mix(color, "#ffffff", 0.45), 1.0: _mix(color, "#ffffff", 0.8)}


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


def _swatch(css_background: str) -> str:
    return (f'<span style="display:inline-block;width:14px;height:6px;border-radius:3px;'
            f'background:{css_background};vertical-align:middle;margin:0 6px 2px 0"></span>')


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

    run_year = runs["start"].dt.year
    tracks = tracks.assign(year=tracks["activity_id"].map(run_year))
    years = sorted(tracks["year"].unique())
    colors = year_colors(years)

    # All years combined, in a hue no single year uses.
    # Off by default; it's an alternative view to the per-year layers, not something to stack.
    all_grad = heat_gradient(ALL_YEARS_COLOR)
    HeatMap(tracks[["lat", "lon"]].round(5).to_numpy().tolist(),
            name=f"{_swatch('linear-gradient(90deg,' + ','.join(all_grad.values()) + ')')}"
                 f"Heat all years · {tracks['activity_id'].nunique()} runs",
            show=False, radius=6, blur=6, min_opacity=0.25, max_zoom=16, gradient=all_grad).add_to(m)

    # One heat layer per year, each in its own hue (oldest first so newer years draw on top)
    for year in years:
        pts = tracks.loc[tracks["year"] == year, ["lat", "lon"]].round(5)
        n_runs = tracks.loc[tracks["year"] == year, "activity_id"].nunique()
        grad = heat_gradient(colors[year])
        HeatMap(pts.to_numpy().tolist(),
                name=f"{_swatch('linear-gradient(90deg,' + ','.join(grad.values()) + ')')}"
                     f"Heat {year} · {n_runs} runs",
                radius=6, blur=6, min_opacity=0.25, max_zoom=16, gradient=grad).add_to(m)

    # Individual route lines per year (off by default; hover shows the run)
    for year in years:
        ids = tracks.loc[tracks["year"] == year, "activity_id"].unique()
        fg = folium.FeatureGroup(name=f"{_swatch(colors[year])}Routes {year}", show=False)
        for aid in ids:
            g = tracks[tracks["activity_id"] == aid]
            r = runs.loc[aid]
            folium.PolyLine(
                g[["lat", "lon"]].round(5).to_numpy().tolist(),  # ~1 m: plenty, keeps the file small
                color=colors[year], weight=2.5, opacity=0.6,
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
    print(f"Heatmap: {n} runs, {len(tracks):,} points, {len(years)} year layer(s), "
          f"{len(areas)} area(s) -> {path}")
    return path
