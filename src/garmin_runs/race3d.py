"""3D flythrough of a race: the GPS track animated over OpenStreetMap 3D buildings (MapLibre GL).

Writes one page per entry in RACES (command `race3d`, into output/).

Privacy: these pages show the *full, unscrubbed* track - start, finish and any stretch that
crosses a privacy zone (the 2026 Toronto Marathon crosses one from km 26 to the finish). They are
NOT part of `publish` yet: privacy.check scans `[lat, lon]` pairs, and this page stores separate
lon/lat arrays, so it would pass the check without actually being checked. Publishing needs an
explicit, reported exemption in privacy.check that the owner approves.

The page loads MapLibre from unpkg and vector tiles from OpenFreeMap (no API key) at view time.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .fetch import DATA
from .parse import TRACKS

OUT = Path("output")
TEMPLATE = Path(__file__).with_name("race3d.html")

# Public races approved by the owner for publishing with their full course.
RACES = [
    {"id": 22751942163, "title": "Toronto Marathon", "eyebrow": "Longest run of 2026",
     "file": "toronto-marathon-2026.html"},
]


def _race_data(race: dict) -> dict:
    acts = {a["activityId"]: a for a in json.loads((DATA / "activities.json").read_text(encoding="utf-8"))}
    a = acts[race["id"]]
    t = pd.read_parquet(TRACKS)
    s = t[t.activity_id == race["id"]].sort_values("ts").reset_index(drop=True)
    if s.empty:
        raise SystemExit(f"No track for activity {race['id']} - run fetch and parse first.")
    n = len(s)
    sec = (s.ts - s.ts.iloc[0]).dt.total_seconds().to_numpy()
    dist = s.distance_m.to_numpy()
    i = np.arange(n)
    lo, hi = np.clip(i - 40, 0, n - 1), np.clip(i + 40, 0, n - 1)   # ~400 m pace window
    pace = (sec[hi] - sec[lo]) / np.maximum(dist[hi] - dist[lo], 1) * 1000
    hr = s.hr.interpolate().bfill().ffill().fillna(0).to_numpy()
    alt = pd.Series(s.alt_m.to_numpy())
    alt = alt.where((alt - alt.rolling(1200, center=True, min_periods=1).median()).abs() < 25)  # altimeter glitches
    alt = alt.interpolate().bfill().ffill().rolling(9, center=True, min_periods=1).mean().to_numpy()
    lat, lon = s.lat.to_numpy(), s.lon.to_numpy()
    smooth = lambda v: pd.Series(v).rolling(21, center=True, min_periods=1).mean().to_numpy()  # camera path
    # progress along the line in Web Mercator, which is what MapLibre's line-progress measures
    mx, my = np.radians(lon), np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    geo = np.r_[0, np.cumsum(np.hypot(np.diff(mx), np.diff(my)))]
    r = lambda v, k: np.round(v, k).tolist()
    return {
        "lon": r(lon, 6), "lat": r(lat, 6), "slon": r(smooth(lon), 6), "slat": r(smooth(lat), 6),
        "prog": r(geo / geo[-1], 6), "t": r(sec, 1), "d": r(dist, 1), "pace": r(pace, 1),
        "hr": np.round(hr).astype(int).tolist(), "alt": r(alt, 1),
        "start": a["startTimeLocal"], "elapsed": a["duration"], "distance": a["distance"],
        "avgHR": a.get("averageHR"), "title": race["title"], "eyebrow": race["eyebrow"],
        "slug": race["file"].removesuffix(".html"),
    }


def build(out_dir: Path = OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE.read_text(encoding="utf-8")
    written = []
    for race in RACES:
        page = template.replace("__TITLE__", html.escape(race["title"])) \
                       .replace("/*__DATA__*/null", json.dumps(_race_data(race), separators=(",", ":")))
        path = out_dir / race["file"]
        path.write_text(page, encoding="utf-8")
        print(f"Wrote {path}")
        written.append(path)
    return written


def files() -> list[str]:
    return [r["file"] for r in RACES]


def nav_links(out_dir: Path) -> str:
    """Report nav links for race pages that exist in out_dir."""
    return "".join(f'<a href="{r["file"]}">{html.escape(r["title"])} in 3D →</a>'
                   for r in RACES if (out_dir / r["file"]).exists())
