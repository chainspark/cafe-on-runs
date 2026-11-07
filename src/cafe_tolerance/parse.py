"""Decode downloaded FIT files into one GPS track table."""
from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path

import fitdecode
import pandas as pd

from .fetch import DATA, RAW

TRACKS = DATA / "tracks.parquet"
SEMI = 180 / 2**31
MIN_STEP_M = 5.0  # keep roughly one point every 5 m


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def parse_fit(fit_bytes: bytes) -> list[dict]:
    rows, last = [], None
    with fitdecode.FitReader(io.BytesIO(fit_bytes)) as fr:
        for frame in fr:
            if not (isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record"):
                continue
            lat = frame.get_value("position_lat", fallback=None)
            lon = frame.get_value("position_long", fallback=None)
            if lat is None or lon is None:
                continue
            lat, lon = lat * SEMI, lon * SEMI
            if last and _haversine(last[0], last[1], lat, lon) < MIN_STEP_M:
                continue
            last = (lat, lon)
            rows.append({
                "ts": frame.get_value("timestamp", fallback=None),
                "lat": lat,
                "lon": lon,
                "distance_m": frame.get_value("distance", fallback=None),
                "speed_ms": frame.get_value("enhanced_speed", fallback=None)
                or frame.get_value("speed", fallback=None),
                "hr": frame.get_value("heart_rate", fallback=None),
                "alt_m": frame.get_value("enhanced_altitude", fallback=None)
                or frame.get_value("altitude", fallback=None),
            })
    return rows


def parse() -> pd.DataFrame:
    frames = []
    zips = sorted(RAW.glob("*.zip"))
    for z in zips:
        aid = int(z.stem)
        try:
            with zipfile.ZipFile(z) as zf:
                fit_name = next(n for n in zf.namelist() if n.lower().endswith(".fit"))
                rows = parse_fit(zf.read(fit_name))
        except Exception as e:
            print(f"  ! {aid}: {e}")
            continue
        if rows:
            df = pd.DataFrame(rows)
            df.insert(0, "activity_id", aid)
            frames.append(df)
    tracks = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    tracks.to_parquet(TRACKS, index=False)
    print(f"Parsed {len(frames)}/{len(zips)} runs -> {len(tracks):,} GPS points in {TRACKS}")
    return tracks
