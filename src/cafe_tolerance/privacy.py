"""Privacy for the public pages.

Applied to the GPS tracks before any public page is built:

- Trim: drop the first/last `trim_m` metres of every run, so a run's own start and finish don't show.

There are no privacy zones: at the owner's request (2026-11-07) public pages no longer hide the
areas where runs often start or finish. Exports also keep only the month and year of each run
(`cafes._runs(public=True)`), and leave out the names of the most-run neighbourhoods.

`check()` then refuses to publish a page if a forbidden name appears in its text, in the names
compiled into it, or in its provenance metadata. Data compiled into embedded WebAssembly is
invisible to a text scan, so every base64 wasm module on a page is decoded and its CAFETOL1
dataset read back out of the module's memory image. A run carrying a day of the month, metadata
with any decimal of 3+ places, or a wasm module without a readable dataset fails the check.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from .parse import TRACKS

DEFAULT_TRIM_M = 500.0


def apply(tracks: pd.DataFrame, trim_m: float = DEFAULT_TRIM_M) -> pd.DataFrame:
    """Tracks with the first and last `trim_m` metres of every run removed."""
    t = tracks.sort_values(["activity_id", "ts"])
    d = t.groupby("activity_id")["distance_m"]
    d0, d1 = d.transform("min"), d.transform("max")
    t = t[(t["distance_m"] - d0 >= trim_m) & (d1 - t["distance_m"] >= trim_m)]
    print(f"Privacy: first/last {trim_m:.0f} m of each run trimmed -> {len(t):,} of {len(tracks):,} points kept")
    return t


# dataset metadata (provenance) may never contain a decimal with 3+ places: no coordinates, ever
COORD_LIKE = re.compile(r"\d\.\d{3,}")
# a base64-encoded wasm module ("\0asm" -> "AGFzbQ"), however the loader wraps it; any length,
# since even a tiny module could hide data, so every one must be scanned
_WASM_B64 = re.compile(r"AGFzbQ[A-Za-z0-9+/]{2,}={0,2}")


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


def _embedded(text: str, label: str, problems: list[str]) -> tuple[list[str], list[str]]:
    """Names and metadata (as JSON text) inside every base64 wasm module on the page.

    A module whose dataset can't be read is a failure: anything published must be scannable.
    """
    import base64

    from .cafe_blob import find_in_wasm

    names, metas, n_mod, n_pts = [], [], 0, 0
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
            exact = sum(1 for r in ds.runs if r.day)
            if exact:  # published pages keep only the month and year of a run
                problems.append(f"{label}: embedded wasm #{n_mod} holds {exact} exact run dates (day of the month)")
            n_pts += len(ds.all_points())
            names += ds.names()
            if ds.meta:
                metas.append(json.dumps(ds.meta, ensure_ascii=False))
                metas.append(json.dumps(ds.meta))  # escaped form too, so no spelling hides a word
    if n_mod:
        print(f"  {label}: binary scan of {n_mod} embedded wasm module(s), {n_pts:,} coordinates, {len(names):,} names, "
              f"{sum(len(m.encode()) for m in metas[::2]):,} bytes of metadata")
    return names, metas


def check(files: list[Path], forbidden: list[str]) -> None:
    """Raise if a published page holds a forbidden name, an exact run date, coordinate-like
    metadata, or a wasm module whose dataset can't be read."""
    problems = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        binary_names, metas = _embedded(text, f.name, problems)
        meta_text = "\n".join(metas)
        if COORD_LIKE.search(meta_text):
            problems.append(f"{f.name}: embedded dataset metadata contains a coordinate-like number")
        haystack = (text + "\n" + "\n".join(binary_names) + "\n" + meta_text).lower()
        for word in forbidden:
            if word and word.lower() in haystack:
                problems.append(f"{f.name}: mentions '{word}'")
    if problems:
        raise SystemExit("Privacy check FAILED - nothing should be published:\n  " + "\n  ".join(problems))
    print(f"Privacy check passed: {len(files)} page(s), no forbidden names, no exact run dates.")
