"""Download all activity summaries plus the original FIT files for outdoor runs."""
from __future__ import annotations

import json
from pathlib import Path

from .api import Garmin, is_run

DATA = Path("data")
RAW = DATA / "raw"
SUMMARIES = DATA / "activities.json"

NO_GPS_TYPES = {"treadmill_running", "indoor_running", "virtual_run"}


def has_gps(a: dict) -> bool:
    if a["activityType"]["typeKey"] in NO_GPS_TYPES:
        return False
    return bool(a.get("hasPolyline") or a.get("startLatitude") is not None)


def fetch(limit: int | None = None) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    with Garmin() as g:
        activities = []
        for a in g.iter_activities():
            activities.append(a)
            if limit and len(activities) >= limit:
                break
            if len(activities) % 100 == 0:
                print(f"  listed {len(activities)} activities...")
        SUMMARIES.write_text(json.dumps(activities, indent=1), encoding="utf-8")
        runs = [a for a in activities if is_run(a)]
        gps_runs = [a for a in runs if has_gps(a)]
        print(f"{len(activities)} activities total, {len(runs)} runs, {len(gps_runs)} with GPS")

        todo = [a for a in gps_runs if not (RAW / f"{a['activityId']}.zip").exists()]
        print(f"Downloading {len(todo)} new FIT files ({len(gps_runs) - len(todo)} already present)")
        for i, a in enumerate(todo, 1):
            aid = a["activityId"]
            try:
                data = g.download_activity(aid)
            except Exception as e:  # keep going; one bad activity shouldn't stop the run
                print(f"  ! {aid}: {e}")
                continue
            tmp = RAW / f"{aid}.zip.part"
            tmp.write_bytes(data)
            tmp.replace(RAW / f"{aid}.zip")
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
