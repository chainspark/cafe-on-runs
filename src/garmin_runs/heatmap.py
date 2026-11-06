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


MIN_POINTS_IN_CITY = 20  # a run needs at least ~100 m inside Toronto to be drawn


def toronto_only(tracks: pd.DataFrame) -> pd.DataFrame:
    """Keep only GPS points inside the City of Toronto bounding box (shared with runarea.py)."""
    from .runarea import TORONTO  # imported here: runarea imports from this module

    lat_lo, lat_hi, lon_lo, lon_hi = TORONTO
    inside = tracks["lat"].between(lat_lo, lat_hi) & tracks["lon"].between(lon_lo, lon_hi)
    t = tracks[inside]
    counts = t.groupby("activity_id")["lat"].transform("size")
    return t[counts >= MIN_POINTS_IN_CITY]


def _swatch(css_background: str) -> str:
    return (f'<span style="display:inline-block;width:14px;height:6px;border-radius:3px;'
            f'background:{css_background};vertical-align:middle;margin:0 6px 2px 0"></span>')


def build(trim_meters: float = 0, out_dir: Path = OUT, public: bool = False) -> Path:
    """public=True: start/finish trimmed and privacy zones removed (see privacy.py)."""
    tracks = pd.read_parquet(TRACKS)
    runs = load_runs().set_index("activity_id")
    tracks = tracks[tracks["activity_id"].isin(runs.index)]
    if public:
        from . import privacy
        tracks = privacy.apply(tracks, trim_meters or privacy.DEFAULT_TRIM_M)
    else:
        tracks = pd.concat([_trim(g, trim_meters) for _, g in tracks.groupby("activity_id")])
    tracks = toronto_only(tracks)
    if tracks.empty:
        raise SystemExit("No runs inside Toronto.")

    # Esri dark-gray canvas: no API key, and (unlike tile.openstreetmap.org) works when the
    # HTML is opened straight from disk, where browsers send no Referer header.
    # Starts on the whole City of Toronto at the largest *whole* zoom level where it fits; that
    # is also the zoom-out limit. Zoom in and pan freely, but panning is held inside the city.
    # (Fractional zoom would fill the window more exactly, but the basemap tiles didn't render
    # reliably with it.) max_bounds_viscosity 1 makes the city edge a hard stop when dragging.
    m = folium.Map(tiles=None, control_scale=True, prefer_canvas=True, max_zoom=19,
                   fade_animation=False, max_bounds_viscosity=1.0)
    for layer, name in [("World_Dark_Gray_Base", "Dark basemap"), ("World_Dark_Gray_Reference", "Labels")]:
        folium.TileLayer(
            tiles=f"{ESRI}/{layer}/MapServer/tile/{{z}}/{{y}}/{{x}}",
            attr="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
            name=name, overlay=layer.endswith("Reference"), control=layer.endswith("Reference"),
            max_native_zoom=16, max_zoom=19,
        ).add_to(m)
    from .runarea import TORONTO
    lat_lo, lat_hi, lon_lo, lon_hi = TORONTO
    city = [[lat_lo, lon_lo], [lat_hi, lon_hi]]
    m.fit_bounds(city)

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

    m.get_root().html.add_child(folium.Element(
        "<style>.leaflet-container{background:#1a1a19}"
        "#back{position:fixed;top:12px;left:56px;z-index:1000;background:#1a1a19;color:#f0efec;border:1px solid #383835;border-radius:4px;padding:6px 10px;text-decoration:none;font:600 12px system-ui,-apple-system,\"Segoe UI\",sans-serif}"
        "#back:hover{background:#2c2c2a}</style>"
        '<a id="back" href="report.html">← Running report</a>'
        f"""<script>
window.addEventListener('load', () => {{
  const map = window._map = {m.get_name()}, city = L.latLngBounds({city});
  // zoom-out limit = the zoom at which the whole city fits; panning stays inside the city
  map.setMaxBounds(city.pad(0.02));
  const limit = () => {{
    const z = map.getBoundsZoom(city, false, L.point(16, 16));  // largest whole zoom that fits
    map.setMinZoom(z);
    if (map.getZoom() < z) map.setZoom(z, {{animate: false}});
    return z;
  }};
  map.setView(city.getCenter(), limit(), {{animate: false}});
  map.on('resize', limit);

  // show only Toronto: blank out everything outside the city box and outline the city
  map.createPane('outside');
  map.getPane('outside').style.zIndex = 350;          // above tiles (200), below heat/routes (400)
  map.getPane('outside').style.pointerEvents = 'none';
  const world = [[-85, -360], [-85, 360], [85, 360], [85, -360]];  // Web Mercator stops at ~±85°
  const box = [city.getSouthWest(), city.getNorthWest(), city.getNorthEast(), city.getSouthEast()];
  L.polygon([world, box], {{pane: 'outside', stroke: false, fillColor: '#1a1a19',
                            fillOpacity: 1, interactive: false}}).addTo(map);
  L.rectangle(city, {{pane: 'outside', color: '#898781', weight: 1, dashArray: '4 4',
                      fill: false, interactive: false}}).addTo(map);
}});
</script>"""))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "heatmap.html"
    m.save(path)
    n = tracks["activity_id"].nunique()
    print(f"Heatmap (Toronto): {n} runs, {len(tracks):,} points, {len(years)} year layer(s) -> {path}")
    return path
