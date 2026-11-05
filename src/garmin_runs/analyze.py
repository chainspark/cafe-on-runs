"""Turn activity summaries + GPS tracks into run metrics."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .api import is_run
from .fetch import SUMMARIES
from .parse import TRACKS

BEST_EFFORTS_M = {"1K": 1000, "5K": 5000, "10K": 10000, "Half": 21097.5}


def load_runs() -> pd.DataFrame:
    acts = json.loads(SUMMARIES.read_text(encoding="utf-8"))
    rows = []
    for a in acts:
        if not is_run(a):
            continue
        dist = a.get("distance") or 0
        dur = a.get("movingDuration") or a.get("duration") or 0
        rows.append({
            "activity_id": a["activityId"],
            "name": a.get("activityName") or "",
            "type": a["activityType"]["typeKey"],
            "start": pd.to_datetime(a.get("startTimeLocal")),
            "km": dist / 1000,
            "secs": dur,
            "elev_gain_m": a.get("elevationGain"),
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "cadence": a.get("averageRunningCadenceInStepsPerMinute"),
            "lat": a.get("startLatitude"),
            "lon": a.get("startLongitude"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["km"] > 0.2].sort_values("start").reset_index(drop=True)
    df["pace_min_km"] = df["secs"] / 60 / df["km"]
    df.loc[(df["pace_min_km"] < 2.5) | (df["pace_min_km"] > 15), "pace_min_km"] = np.nan
    return df


def _best_time(dist: np.ndarray, t: np.ndarray, target: float) -> float | None:
    """Fastest elapsed seconds to cover `target` metres within one run (two-pointer)."""
    if len(dist) < 2 or dist[-1] - dist[0] < target:
        return None
    best, j = np.inf, 0
    for i in range(len(dist)):
        while dist[i] - dist[j] >= target:
            best = min(best, t[i] - t[j])
            j += 1
    return float(best) if np.isfinite(best) else None


def best_efforts(tracks: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    out = []
    starts = runs.set_index("activity_id")["start"]
    for aid, g in tracks.dropna(subset=["distance_m", "ts"]).groupby("activity_id"):
        d = g["distance_m"].to_numpy(dtype=float)
        ts = pd.to_datetime(g["ts"])
        t = (ts - ts.iloc[0]).dt.total_seconds().to_numpy()
        for label, m in BEST_EFFORTS_M.items():
            s = _best_time(d, t, m)
            if s and s / (m / 1000) > 150:  # sanity: faster than 2:30/km is GPS noise
                out.append({"effort": label, "secs": s, "activity_id": aid,
                            "date": starts.get(aid)})
    df = pd.DataFrame(out)
    if df.empty:
        return df
    return (df.sort_values("secs").groupby("effort", sort=False).head(1)
              .set_index("effort").reindex(BEST_EFFORTS_M).dropna(how="all").reset_index())


def summarize() -> dict:
    runs = load_runs()
    tracks = pd.read_parquet(TRACKS) if TRACKS.exists() else pd.DataFrame()
    if runs.empty:
        raise SystemExit("No runs found - run `fetch` first.")

    s = runs.set_index("start")
    weekly = s["km"].resample("W-MON", label="left", closed="left").sum()
    monthly = s["km"].resample("MS").sum()
    yearly = s.groupby(s.index.year)["km"].agg(["sum", "count"])
    dow = runs.groupby(runs["start"].dt.dayofweek)["km"].agg(["count", "sum"]).reindex(range(7), fill_value=0)
    hour = runs.groupby(runs["start"].dt.hour).size().reindex(range(24), fill_value=0)
    pace = runs.dropna(subset=["pace_min_km"])[["start", "km", "pace_min_km", "name", "avg_hr"]]
    pace = pace.assign(rolling=pace.set_index("start")["pace_min_km"].rolling("90D", min_periods=5).median().to_numpy())

    totals = {
        "runs": len(runs),
        "km": runs["km"].sum(),
        "hours": runs["secs"].sum() / 3600,
        "avg_pace": runs["secs"].sum() / 60 / runs["km"].sum(),
        "longest_km": runs["km"].max(),
        "longest_name": runs.loc[runs["km"].idxmax(), "name"],
        "longest_date": runs.loc[runs["km"].idxmax(), "start"],
        "first": runs["start"].min(),
        "last": runs["start"].max(),
        "elev_m": runs["elev_gain_m"].sum(),
        "best_week_km": weekly.max(),
        "best_week": weekly.idxmax(),
    }
    return {
        "runs": runs, "tracks": tracks, "weekly": weekly, "monthly": monthly,
        "yearly": yearly, "dow": dow, "hour": hour, "pace": pace,
        "best": best_efforts(tracks, runs) if not tracks.empty else pd.DataFrame(),
        "totals": totals,
    }


def fmt_pace(min_per_km: float) -> str:
    if not min_per_km or np.isnan(min_per_km):
        return "-"
    m = int(min_per_km)
    s = int(round((min_per_km - m) * 60))
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}"


def fmt_dur(secs: float) -> str:
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
