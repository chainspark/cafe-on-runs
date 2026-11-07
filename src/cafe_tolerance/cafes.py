"""Cafe tolerance: export the dataset, compile the C++ engine to WebAssembly, and build the page.

    python -m cafe_tolerance cafes             -> output/cafe_tolerance.html (private, full tracks)
    python -m cafe_tolerance cafes --public    -> privacy-scrubbed page (publish.py uses this)

Pipeline: tracks.parquet (+ privacy.apply when public) -> runs split into parts at gaps; City
street centreline pieces near the cafes (the nominals); OSM cafes near the runs (the datums)
-> cafe_blob.encode -> wasm/cafe_tol/generated/dataset_<target>.inc -> em++ in the pinned
emscripten Docker image -> a single self-contained HTML page (the wasm is inlined, so it works
from file://).

The dataset also carries provenance (sources, licences, dates, counts; never coordinates), which
the page shows under "Data sources". The generated .inc and the build output hold GPS data: both
are gitignored.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import cafe_blob, exclusions, privacy
from .fetch import DATA, SUMMARIES
from .opendata import STREET_CLASSES, TORONTO, load
from .parse import TRACKS

OUT = Path("output")
ROOT = Path(__file__).resolve().parents[2]
WASM = ROOT / "wasm" / "cafe_tol"
TEMPLATE = Path(__file__).with_name("cafes.html")
IMAGE = "emscripten/emsdk:4.0.10"

CAFES_CACHE = DATA / "cafes_osm.json"
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "cafe-tolerance/0.1 (personal running analysis)"

R_MAX_M = 150.0          # largest search radius the page offers (keep in sync with the R slider); data beyond it is never needed
GAP_M = 50.0             # a jump this long splits a track into parts (privacy holes, GPS dropouts)
MIN_POINTS_IN_CITY = 20  # a run needs ~100 m inside Toronto to count
LAT0 = 43.7


def _xy(lat, lon) -> np.ndarray:
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    return np.c_[lon * 111_320 * np.cos(np.radians(LAT0)), lat * 110_540]


# ---------- inputs ----------
def _read_cafe_cache() -> tuple[list[dict], dict]:
    """(cafes, meta). The cache is {"meta": ..., "cafes": [...]}; an older bare list is still read."""
    raw = json.loads(CAFES_CACHE.read_text(encoding="utf-8"))
    return (raw, {}) if isinstance(raw, list) else (raw["cafes"], raw.get("meta") or {})


def fetch_cafes(refresh: bool = False) -> list[dict]:
    """Every amenity=cafe in the City of Toronto bounding box, from OpenStreetMap (cached)."""
    if CAFES_CACHE.exists() and not refresh:
        cached, _ = _read_cafe_cache()
        if all("brand" in c for c in cached):
            return cached
        print("Cafe cache predates brand tags; refreshing it.")
    s, n, w, e = TORONTO[0], TORONTO[1], TORONTO[2], TORONTO[3]
    query = f'[out:json][timeout:90];(node["amenity"="cafe"]({s},{w},{n},{e});way["amenity"="cafe"]({s},{w},{n},{e}););out center tags;'
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": query}).encode(),
                                 headers={"User-Agent": USER_AGENT})
    print("Fetching cafes from OpenStreetMap (Overpass) ...")
    for attempt in range(3):
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=120))
            elements = resp["elements"]
            break
        except Exception as ex:  # Overpass is busy now and then
            if attempt == 2:
                raise
            print(f"  Overpass error ({ex}); retrying")
            time.sleep(10 * (attempt + 1))
    cafes = []
    for el in elements:
        lat, lon = (el["lat"], el["lon"]) if el["type"] == "node" else (el["center"]["lat"], el["center"]["lon"])
        tags = el.get("tags") or {}
        cafes.append({"osm_id": el["id"] if el["type"] == "node" else -el["id"],  # ways negative
                      "name": tags.get("name") or "Unnamed cafe", "lat": lat, "lon": lon,
                      "brand": tags.get("brand") or tags.get("brand:en") or ""})
    meta = {"endpoint": OVERPASS, "query": query,  # the query stays in this private cache, never in the page
            "osm_base": (resp.get("osm3s") or {}).get("timestamp_osm_base", ""),
            "retrieved": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    DATA.mkdir(exist_ok=True)
    tmp = CAFES_CACHE.with_suffix(".part")
    tmp.write_text(json.dumps({"meta": meta, "cafes": cafes}), encoding="utf-8")
    tmp.replace(CAFES_CACHE)
    print(f"  {len(cafes):,} cafes cached in {CAFES_CACHE} (OSM data as of {meta['osm_base'] or 'unknown'})")
    return cafes


def _file_date(path: Path) -> str:
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def _meta(public: bool, trim_m: float, runs: list[cafe_blob.Run], n_pts: int, n_fetched: int,
          n_excluded: int, n_near: int, n_cafes: int, n_streets: int) -> dict:
    """Provenance compiled into the dataset and shown on the page: sources, licences, dates, counts.

    Never coordinates (privacy.COORD_LIKE is enforced here and again by privacy.check), never any
    activities.json field other than dates.
    """
    from .opendata import source as street_source

    dates = [dt.date(r.year, r.month, max(1, r.day)) for r in runs]  # public runs carry day 0
    years = f"{min(dates).year}–{max(dates).year}" if dates else "none"
    run_detail = f"{len(runs):,} runs, {n_pts:,} GPS points, {years}, inside the City of Toronto."
    if public:
        run_detail += (f" Public build: the first and last {trim_m:.0f} m of every run removed; "
                       "dates kept to month and year.")
    _, cmeta = _read_cafe_cache()
    if cmeta.get("osm_base"):
        c_kind, c_date = "OSM data as of", cmeta["osm_base"]
    elif cmeta.get("retrieved"):
        c_kind, c_date = "downloaded", cmeta["retrieved"]
    else:
        c_kind, c_date = "cached file date", _file_date(CAFES_CACHE)
    cafe_detail = (f"{n_fetched:,} places tagged amenity=cafe in Toronto; {n_excluded:,} chains, franchises or "
                   f"non-cafes excluded; ")
    cafe_detail += f"{n_cafes:,} shown." if public else f"{n_near:,} within {R_MAX_M:.0f} m of a run, all shown."
    st = street_source("toronto-centreline-tcl")
    st_kind = "cached file date" if st.get("fallback") else "downloaded"
    meta = {
        "schema": 1,
        "built": dt.date.today().isoformat(),
        "build": "public" if public else "private",
        "sources": [
            {"id": "runs", "data": "Runs (GPS tracks)",
             "source": "Garmin Connect, via the Garmin Connect Personal API", "source_url": "https://connect.garmin.com/",
             "licence": "The author's own activity data; not licensed for reuse", "licence_url": "",
             "date_kind": "runs through", "date": max(dates).isoformat() if dates else "", "detail": run_detail},
            {"id": "cafes", "data": "Cafes (the datums)",
             "source": "OpenStreetMap contributors, via the Overpass API",
             "source_url": "https://www.openstreetmap.org/copyright",
             "licence": "ODbL 1.0", "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
             "date_kind": c_kind, "date": c_date, "detail": cafe_detail},
            {"id": "streets", "data": "Street centreline (the nominals)",
             "source": "City of Toronto Open Data: Toronto Centreline (TCL)",
             "source_url": "https://open.toronto.ca/dataset/toronto-centreline-tcl/",
             "licence": "Open Government Licence – Toronto", "licence_url": "https://open.toronto.ca/open-data-licence/",
             "date_kind": st_kind, "date": st.get("retrieved", ""),
             "detail": f"{n_streets:,} centreline pieces ({', '.join(STREET_CLASSES)}) "
                       f"within {R_MAX_M:.0f} m of a shown cafe."},
        ],
    }
    text = json.dumps(meta, ensure_ascii=False)
    if privacy.COORD_LIKE.search(text):
        raise ValueError("provenance metadata must not contain coordinate-like numbers")
    return meta


def _tracks(public: bool, trim_m: float) -> pd.DataFrame:
    t = pd.read_parquet(TRACKS, columns=["activity_id", "ts", "lat", "lon", "distance_m"])
    if public:
        t = privacy.apply(t, trim_m)
    lat_lo, lat_hi, lon_lo, lon_hi = TORONTO
    t = t[t["lat"].between(lat_lo, lat_hi) & t["lon"].between(lon_lo, lon_hi)]
    t = t[t.groupby("activity_id")["lat"].transform("size") >= MIN_POINTS_IN_CITY]
    return t.sort_values(["activity_id", "ts"]).reset_index(drop=True)


def _runs(t: pd.DataFrame, public: bool = False) -> list[cafe_blob.Run]:
    """One Run per activity, split into parts at gaps. Public builds keep only the year and month of
    each run (day 0), so the page can't show, and the data doesn't hold, the day you ran somewhere."""
    acts = {a["activityId"]: a for a in json.loads(SUMMARIES.read_text(encoding="utf-8"))}
    runs = []
    for aid, g in t.groupby("activity_id", sort=True):
        ll = g[["lat", "lon"]].to_numpy()
        xy = _xy(ll[:, 0], ll[:, 1])
        step = np.r_[0, np.hypot(*np.diff(xy, axis=0).T)]
        dd = np.r_[0, np.diff(g["distance_m"].to_numpy())]
        cut = np.flatnonzero((step > GAP_M) | (np.nan_to_num(dd) > GAP_M))
        parts = [p for p in np.split(ll, cut) if len(p) >= 2]
        start = pd.to_datetime((acts.get(aid) or {}).get("startTimeLocal") or g["ts"].iloc[0])
        runs.append(cafe_blob.Run(int(aid), start.year, start.month, 0 if public else start.day, parts))
    return runs


def _streets(near: np.ndarray, forbidden: list[str]) -> list[cafe_blob.Street]:
    """Centreline pieces (the nominals) within R_MAX of any kept cafe."""
    tree = cKDTree(_xy(near[:, 0], near[:, 1]))
    classes = {c: i for i, c in enumerate(STREET_CLASSES)}
    out = []
    for f in load("toronto-centreline-tcl"):
        p = f["properties"]
        name = p.get("LINEAR_NAME_FULL")
        if p.get("FEATURE_CODE_DESC") not in classes or not name or name == "None":
            continue
        if any(w.lower() in name.lower() for w in forbidden):
            continue
        geom = f["geometry"]
        lines = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for line in lines:
            c = np.asarray(line, dtype=float)[:, :2]
            pts = np.c_[c[:, 1], c[:, 0]]  # lat, lon
            if len(pts) < 2:
                continue
            xy = _xy(pts[:, 0], pts[:, 1])
            # densify to 25 m so long pieces passing close to a cafe are found
            seg = np.hypot(*np.diff(xy, axis=0).T)
            dense = np.vstack([xy[:-1] + (xy[1:] - xy[:-1]) * f_ for f_ in np.linspace(0, 1, max(2, int(seg.max() // 25) + 2))])
            if not np.isfinite(tree.query(dense, distance_upper_bound=R_MAX_M + 25)[0]).any():
                continue
            out.append(cafe_blob.Street(int(p.get("CENTRELINE_ID") or 0), classes[p["FEATURE_CODE_DESC"]], name, pts))
    return out


def export(public: bool = False, trim_m: float = privacy.DEFAULT_TRIM_M, refresh_cafes: bool = False) -> Path:
    """Write wasm/cafe_tol/generated/dataset_<target>.inc and return its path."""
    target = "public" if public else "private"
    t = _tracks(public, trim_m)
    runs = _runs(t, public)
    pts = np.vstack([p for r in runs for p in r.parts])
    forbidden = privacy.forbidden_names() if public else []

    fetched = fetch_cafes(refresh_cafes)
    raw, dropped = [], {}
    for c in fetched:  # no big chains / franchises, nothing that isn't really a cafe
        hit = exclusions.excluded(c["name"], c.get("brand"))
        if hit:
            dropped[hit] = dropped.get(hit, 0) + 1
        else:
            raw.append(c)
    for category in ("chain", "not a cafe"):
        items = sorted(((n, label) for (cat, label), n in dropped.items() if cat == category), reverse=True)
        if items:
            print(f"Excluded ({category}): " + ", ".join(f"{label} {n}" for n, label in items))
    c_ll = np.array([[c["lat"], c["lon"]] for c in raw])
    near_run = np.isfinite(cKDTree(_xy(pts[:, 0], pts[:, 1])).query(_xy(c_ll[:, 0], c_ll[:, 1]),
                                                                     distance_upper_bound=R_MAX_M)[0])
    keep = near_run.copy()
    if public:  # cafes named after a most-run neighbourhood would point at home
        keep &= np.array([not any(w.lower() in c["name"].lower() for w in forbidden) for c in raw])
    cafes = [cafe_blob.Cafe(c["lat"], c["lon"], int(c["osm_id"]), c["name"]) for c, k in zip(raw, keep) if k]
    print(f"Cafes: {len(fetched):,} in Toronto, {len(raw):,} after exclusions, "
          f"{int(near_run.sum()):,} within {R_MAX_M:.0f} m of a run, "
          f"{len(cafes):,} exported")

    streets = _streets(np.array([[c.lat, c.lon] for c in cafes]).reshape(-1, 2), forbidden)
    meta = _meta(public, trim_m, runs, len(pts), len(fetched), sum(dropped.values()), int(near_run.sum()),
                 len(cafes), len(streets))
    ds = cafe_blob.Dataset(cafe_blob.FLAG_PUBLIC if public else 0, runs, streets, cafes, meta)
    blob = cafe_blob.encode(ds)
    inc = WASM / "generated" / f"dataset_{target}.inc"
    cafe_blob.write_inc(blob, inc)
    print(f"Dataset ({target}): {len(runs)} runs, {len(pts):,} points, {len(streets):,} street pieces, "
          f"{len(cafes):,} cafes, {len(json.dumps(meta, ensure_ascii=False).encode()):,} bytes of provenance "
          f"-> {len(blob) / 1e6:.2f} MB")
    return inc


def compile_wasm(target: str) -> Path:
    """Run build.sh in the pinned emscripten image; returns the built JS (wasm inlined)."""
    cmd = ["docker", "run", "--rm", "-v", f"{WASM.as_posix()}:/src", "-w", "/src", IMAGE, "bash", "build.sh", target]
    print(f"Compiling C++ -> WebAssembly ({target}) in {IMAGE} ...")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        raise SystemExit("Docker is not installed or not on PATH - it is needed to compile the C++.")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"WASM build failed (exit {e.returncode}). Is Docker running?")
    return WASM / "build" / f"cafe_tol_{target}.js"


def build(out_dir: Path = OUT, public: bool = False, trim_m: float = privacy.DEFAULT_TRIM_M,
          refresh_cafes: bool = False) -> Path:
    target = "public" if public else "private"
    export(public, trim_m, refresh_cafes)
    js = compile_wasm(target).read_text(encoding="utf-8").replace("</script", "<\\/script")
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__WASM__*/", js)
            .replace("__MODE__", "public" if public else "private")
            .replace("__BUILT__", html.escape(dt.date.today().isoformat())))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("index.html" if public else "cafe_tolerance.html")
    path.write_text(page, encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return path
