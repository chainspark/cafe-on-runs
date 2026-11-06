"""City of Toronto Open Data: download once into data/opendata/ and reuse.

- `toronto-centreline-tcl`: street centreline, the *nominal* (drawing) for the profile tolerance.
- `neighbourhoods`: boundaries, used only to find the names that must never appear publicly.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .fetch import DATA

OPEN = DATA / "opendata"
CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id="
DATASETS = {"neighbourhoods": "Neighbourhoods - 4326.geojson",
            "toronto-centreline-tcl": "Centreline - Version 2 - 4326.geojson"}

# Centreline classes a runner uses (no expressways, rail, rivers, shoreline, hydro lines).
STREET_CLASSES = ["Local", "Collector", "Minor Arterial", "Major Arterial", "Trail", "Walkway"]

TORONTO = (43.581, 43.856, -79.639, -79.115)   # lat min/max, lon min/max of the City of Toronto


def download(pkg: str) -> Path:
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


def load(pkg: str) -> list[dict]:
    return json.load(open(download(pkg), encoding="utf-8"))["features"]
