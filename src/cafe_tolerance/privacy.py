"""Privacy for the public pages: hide where runs start and finish (home, work, ...).

Two layers, both applied to the GPS tracks before any public page is built:

1. Trim: drop the first/last `trim_m` metres of every run.
2. Privacy zones: places where many runs start or finish are found automatically by density
   clustering of all start/finish points. Every GPS point inside a zone is removed - including
   mid-run passes - not just the starts. Each zone's radius covers the spread of its
   starts/finishes plus a margin, and its centre is moved by a random offset (saved in
   data/privacy.json, so it is stable between publishes) so the hole's centre doesn't point
   at the door.

`check()` then scans the published HTML for coordinates and refuses to publish if any lies
within a zone's guaranteed-hidden radius around the *real* centre, or if a forbidden name
appears. Coordinates compiled into embedded WebAssembly are invisible to a text scan, so every
base64 wasm module on a page is decoded, its CAFETOL1 dataset read back out of the module's
memory image, and every run point, street point, cafe and name checked too. A wasm module
without a readable dataset fails the check.

data/privacy.json holds the real zone centres - it lives in data/ (gitignored) and must never
be published.
"""
from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .fetch import DATA
from .parse import TRACKS

ZONES_FILE = DATA / "privacy.json"
LINK_M = 250.0          # start/finish points within this distance are linked into one place
MIN_ENDPOINTS = 10      # a place needs this many starts/finishes (~5 runs) to become a zone
MIN_RADIUS_M = 600.0    # smallest zone
MARGIN_M = 300.0        # zone radius = 95th-percentile spread of its starts/finishes + this
MAX_OFFSET_M = 200.0    # random shift of each published zone centre
DEFAULT_TRIM_M = 500.0


def _xy(lat, lon) -> np.ndarray:
    """Local metres; x scaled by each point's own latitude (works in Toronto and elsewhere)."""
    lat, lon = np.atleast_1d(np.asarray(lat, dtype=float)), np.atleast_1d(np.asarray(lon, dtype=float))
    return np.c_[lon * 111_320 * np.cos(np.radians(lat)), lat * 110_540]


def _endpoints(tracks: pd.DataFrame) -> np.ndarray:
    g = tracks.sort_values(["activity_id", "ts"]).groupby("activity_id")
    return np.vstack([g[["lat", "lon"]].first().to_numpy(), g[["lat", "lon"]].last().to_numpy()])


def _clusters(points: np.ndarray) -> list[dict]:
    """Connected dense groups of start/finish points -> [{lat, lon, endpoints, radius_m}]."""
    xy = _xy(points[:, 0], points[:, 1])
    pairs = cKDTree(xy).query_pairs(LINK_M, output_type="ndarray")
    n = len(points)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, label = connected_components(graph, directed=False)
    out = []
    for lab in np.unique(label):
        members = label == lab
        if members.sum() < MIN_ENDPOINTS:
            continue
        lat, lon = points[members].mean(axis=0)
        spread = np.percentile(np.hypot(*(xy[members] - _xy(lat, lon)).T), 95)
        out.append({"lat": float(lat), "lon": float(lon), "endpoints": int(members.sum()),
                    "radius_m": float(max(MIN_RADIUS_M, spread + MARGIN_M))})
    return sorted(out, key=lambda z: -z["endpoints"])


def zones() -> list[dict]:
    """Privacy zones from *all* runs, with a stable random centre shift, saved to data/privacy.json.

    Always computed from the full history, never a subset, so every page hides the same places.
    """
    tracks = pd.read_parquet(TRACKS, columns=["activity_id", "ts", "lat", "lon"])
    saved = json.loads(ZONES_FILE.read_text()) if ZONES_FILE.exists() else []
    result = []
    for z in _clusters(_endpoints(tracks)):
        # keep the shift of a previously saved zone for the same place, so the public hole
        # doesn't move between publishes (which would let someone triangulate the centre)
        prev = next((s for s in saved
                     if np.hypot(*(_xy(s["lat"], s["lon"]) - _xy(z["lat"], z["lon"]))[0]) < z["radius_m"] / 2), None)
        if prev and "dx_m" in prev:
            dx, dy = prev["dx_m"], prev["dy_m"]
        else:
            r = MAX_OFFSET_M * math.sqrt(random.random())
            a = random.uniform(0, 2 * math.pi)
            dx, dy = r * math.cos(a), r * math.sin(a)
        result.append(z | {"dx_m": dx, "dy_m": dy})
    ZONES_FILE.write_text(json.dumps(result, indent=1))
    return result


def _hidden(lat, lon, zs: list[dict], shifted: bool, shrink_m: float = 0.0) -> np.ndarray:
    """Boolean mask: point is inside some zone (around the shifted or the real centre)."""
    p = _xy(lat, lon)
    inside = np.zeros(len(p), bool)
    for z in zs:
        c = _xy(z["lat"], z["lon"])[0] + (np.array([z["dx_m"], z["dy_m"]]) if shifted else 0)
        inside |= np.hypot(*(p - c).T) < z["radius_m"] - shrink_m
    return inside


def apply(tracks: pd.DataFrame, trim_m: float = DEFAULT_TRIM_M) -> pd.DataFrame:
    """Tracks with start/finish trimmed and every privacy-zone point removed."""
    t = tracks.sort_values(["activity_id", "ts"])
    d = t.groupby("activity_id")["distance_m"]
    d0, d1 = d.transform("min"), d.transform("max")
    t = t[(t["distance_m"] - d0 >= trim_m) & (d1 - t["distance_m"] >= trim_m)]
    zs = zones()
    if zs:
        t = t[~_hidden(t["lat"], t["lon"], zs, shifted=True)]
    print(f"Privacy: {len(zs)} zone(s) hidden, first/last {trim_m:.0f} m of each run trimmed "
          f"-> {len(t):,} of {len(tracks):,} points kept")
    return t


# [lat, lon] pairs as written by JSON (lat first, both with decimals)
_PAIR = re.compile(r"\[\s*(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})\s*\]")
# a base64-encoded wasm module ("\0asm" -> "AGFzbQ"), however the loader wraps it
_WASM_B64 = re.compile(r"AGFzbQ[A-Za-z0-9+/]{100,}={0,2}")


def forbidden_names(top: int = 5) -> list[str]:
    """Strings that must never appear on a public page: the names of the most-run neighbourhoods
    (they would point at home)."""
    words = []
    try:
        import shapely
        from shapely.geometry import shape

        from .opendata import load

        t = pd.read_parquet(TRACKS, columns=["activity_id", "lat", "lon"]).iloc[::5]
        counts = []
        for f in load("neighbourhoods"):
            g = shape(f["geometry"])
            inside = shapely.contains_xy(g, t["lon"].to_numpy(), t["lat"].to_numpy())
            counts.append((t.loc[inside, "activity_id"].nunique(), f["properties"]["AREA_NAME"]))
        words += [name for n, name in sorted(counts, reverse=True)[:top] if n]
    except Exception as e:
        print(f"  (couldn't load neighbourhood names for the check: {e})")
    return words


def _embedded_points(text: str, label: str, problems: list[str]) -> tuple[np.ndarray, list[str]]:
    """Coordinates and names inside every base64 wasm module on the page.

    A module whose dataset can't be read is a failure: anything published must be scannable.
    """
    import base64

    from .cafe_blob import find_in_wasm

    pts, names, n_mod = [], [], 0
    for m in _WASM_B64.finditer(text):
        n_mod += 1
        try:
            found = find_in_wasm(base64.b64decode(m.group(0)))
        except Exception as e:
            problems.append(f"{label}: embedded wasm #{n_mod} could not be read ({e})")
            continue
        if not found:
            problems.append(f"{label}: embedded wasm #{n_mod} has no recognisable dataset - can't verify it")
        for ds in found:
            pts.append(ds.all_points())
            names += ds.names()
    if n_mod:
        n = sum(len(p) for p in pts)
        print(f"  {label}: binary scan of {n_mod} embedded wasm module(s), {n:,} coordinates, {len(names):,} names")
    return (np.vstack(pts) if pts else np.empty((0, 2))), names


def check(files: list[Path], forbidden: list[str]) -> None:
    """Raise if a published page leaks a location near a real zone, or a forbidden string.

    Around each *real* centre, radius minus the maximum shift is guaranteed hidden whatever the
    shift was, so any published coordinate inside that is a leak. Coordinates are read from the
    page text *and* from datasets compiled into embedded WebAssembly.
    """
    zs = zones()
    problems = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        binary_pts, binary_names = _embedded_points(text, f.name, problems)
        haystack = (text + "\n" + "\n".join(binary_names)).lower()
        for word in forbidden:
            if word and word.lower() in haystack:
                problems.append(f"{f.name}: mentions '{word}'")
        pairs = np.array([(float(a), float(b)) for a, b in _PAIR.findall(text)
                          if -90 <= float(a) <= 90 and -180 <= float(b) <= 180]).reshape(-1, 2)
        pts = np.vstack([pairs, binary_pts])
        if zs and len(pts):
            n = int(_hidden(pts[:, 0], pts[:, 1], zs, shifted=False, shrink_m=MAX_OFFSET_M).sum())
            if n:
                problems.append(f"{f.name}: {n} coordinate(s) inside a private place")
    if problems:
        raise SystemExit("Privacy check FAILED - nothing should be published:\n  " + "\n  ".join(problems))
    print(f"Privacy check passed: {len(files)} page(s), no coordinates inside {len(zs)} private "
          f"place(s), no forbidden names.")
