# garmin-runs

Pulls the owner's own Garmin Connect data through the **Garmin Personal API**. The code downloads activity summaries and the original FIT files for runs, parses the GPS tracks, and builds HTML outputs: a route heatmap, a running report, and a most-run segments report.

## Environment
- **Python 3.12+** is required, because `garmin-connect-personal-api-python` needs it. The system Python is 3.9, so the project uses a **uv**-managed venv in `.venv`.
  - Run code with `.venv\Scripts\python.exe -m garmin_runs <cmd>`.
  - Install or sync with `py -3.9 -m uv sync`. uv is pip-installed under Python 3.9 and is not on PATH.
- The shell is Windows PowerShell 5.1. Inline `python -c "..."` with nested quotes gets mangled, so put anything non-trivial in a `.py` file in the scratchpad and run that.
- Node is not installed.

## Auth
- The client's `resolve_api_key()` looks for the key in this order:
  1. the `GARMIN_PERSONAL_API_KEY` env var
  2. the file `~/.config/garmin/connect-personal-api-key`
- **Never print, log or commit the key.** To check it exists, print only presence or length.
- If the env var "isn't set", the usual cause is that VS Code was started before the variable was set. Ask the user to restart VS Code or to use the key file.
- The key is exchanged for a 24-hour bearer JWT, which the client caches at `~/.config/garmin/connect-personal-api-bearer-token.json`.
  - There is no refresh token.
  - `api.Garmin._call` forces one re-auth on 401 and backs off on 429 and 5xx.

## Personal API facts (verified)
- Host: `https://personal.connectapi.garmin.com`. The API is read-only.
- Endpoints used, through the official client (`garmin_personal_api.GarminPersonalApiClient`):
  - `search_activities(start, limit, ...)` calls `GET /activitylist-service/activities/search/activities`. It returns newest first; page through it until an empty list comes back.
  - `download_activity(id)` calls `GET /download-service/files/activity/{id}` and returns **ZIP bytes** containing `<id>_ACTIVITY.fit`.
  - `get_activity(id)` calls `GET /activity-service/activity/{id}`.
  - For other paths, use the generic `client.get(path, params=...)`. Endpoint reference: https://developer.garmin.com/gc-developer-program/personal/swagger-docs/ (the page renders with JS and can't be fetched; Garmin's GitHub client README is easier to read).
- **No temperature or weather data is available.**
  - Activity summaries have no temperature fields.
  - FIT `record` messages from this watch hold only these fields: `timestamp, position_lat, position_long, distance, enhanced_speed, enhanced_altitude, heart_rate, cadence, ...`.
  - `/activity-service/activity/{id}/weather` returns 404.
  - Temperatures therefore come from Open-Meteo (see Segments).
- Summary fields used:
  - `activityId`, `activityName`, `activityType.typeKey`, `startTimeLocal`
  - `distance` (m), `duration` / `movingDuration` (s)
  - `averageHR`, `maxHR`, `elevationGain`, `startLatitude` / `startLongitude`, `hasPolyline`

## Fetch → parse pipeline
- **Fetch** (`fetch.py`, command `fetch [--limit N]`):
  - Writes every activity summary, all sports, to `data/activities.json`.
  - A run is any activity whose `typeKey` contains `"running"`.
  - Downloads FIT ZIPs for runs that have GPS. Types with no GPS are skipped: `treadmill_running`, `indoor_running`, `virtual_run`.
  - Writes each ZIP to `data/raw/<id>.zip` atomically: it writes `.zip.part` first, then renames it.
  - Re-runs are incremental and skip ZIPs that already exist.
  - A full history pull takes a few minutes, about 280 runs. Run it in the background and log to `data/fetch.log`.
- **Parse** (`parse.py`, command `parse`):
  - Decodes the FIT files with `fitdecode`, reading `record` messages only.
  - Converts lat/lon from semicircles to degrees: × 180/2³¹.
  - Prefers `enhanced_speed` / `enhanced_altitude` and falls back to the plain fields.
  - Downsamples to about one point every 5 m.
  - Writes a single `data/tracks.parquet` with columns `activity_id, ts (UTC), lat, lon, distance_m, speed_ms, hr, alt_m`.
- `all` runs fetch, then parse, then map, then report.
- **Gotcha (pandas 3):** datetime columns may not be nanosecond resolution, so `astype("int64") / 1e9` gives wrong seconds. Use `(ts - ts.iloc[0]).dt.total_seconds()`.
- **Time zones:** the summary `startTimeLocal` is local and naive; FIT `ts` is UTC-aware.

## Data and privacy
- `data/` and `output/` are gitignored and contain precise GPS traces of the owner's runs, including the area around their home. Don't commit or publish them.
- The `map --trim-meters N` option hides the first and last N metres of each run.

## Outputs and other modules (brief)
- **`heatmap.py`** writes `output/heatmap.html`.
  - Folium with Esri dark-gray tiles.
  - **Don't use** `tile.openstreetmap.org`: it shows "Access blocked" when the page is opened from `file://`, because no Referer is sent. **Don't use** CartoDB either: it now needs an API key.
  - Each year's routes get their own color.
- **`report.py`** writes `output/report.html`: plotly.js loaded from a CDN, with CSS-variable light and dark palettes.
- **`segments.py`** writes `output/segment.html` and `segment_passes.csv` (command `segment [--length 1000] [--years 2] [--top 5]`).
  - Segments are found on a 25 m grid. Candidate stretches are filtered: the ends must be at least 50% of the length apart, and the pace must be faster than 9 min/km.
  - Segments are ranked by runs with a **timed** pass, and parallel paths are rejected.
  - Hourly air temperature comes from Open-Meteo, archive plus forecast APIs, with the location rounded to 0.1°. It is cached in `data/weather_*.json`. The user approved this external call.
- **Visual check:** screenshot an output with headless Edge (`msedge --headless=new --screenshot=... file:///...`), using a fresh `--user-data-dir` so cached tiles don't hide problems.
