# cafe-tolerance

A GD&T-style tolerance analysis of the owner's Garmin runs past Toronto cafes.
- The geometry engine is C++, compiled to WebAssembly, with the dataset embedded in the build.
- Python does fetching, parsing, export, the build and page assembly.
- This project started as a slimmed-down copy of `garmin-runs`, on its own orphan branch `cafe-tolerance`.

## Environment
- **Python:** 3.12+, in a uv-managed `.venv`.
  - Run code with `.venv\Scripts\python.exe -m cafe_tolerance <cmd>`.
  - Sync with `py -3.9 -m uv sync`. uv is pip-installed under the system Python 3.9 and is not on PATH.
- **Shells:** Windows. In Git Bash, `docker run -v` with a Windows path needs `MSYS_NO_PATHCONV=1`. `cafes.compile_wasm` calls Docker directly and doesn't.
- **C++ toolchain:** only the pinned Docker image `emscripten/emsdk:4.0.10` (no local compiler, no Node). Tests:
  `docker run --rm -v "C:/personal/cafe-tolerance/wasm/cafe_tol:/src" -w /src emscripten/emsdk:4.0.10 bash build.sh test`

## Auth (Garmin Personal API)
- The key is read from the env var `GARMIN_PERSONAL_API_KEY` or the file `~/.config/garmin/connect-personal-api-key`.
- **Never print, log or commit it.** To check it exists, print only presence or length.
- It's exchanged for a 24 h bearer token, cached at `~/.config/garmin/connect-personal-api-bearer-token.json`.

## Pipeline
- **`fetch` / `parse`** are carried over unchanged from garmin-runs.
  - FIT ZIPs go to `data/raw/`.
  - `data/tracks.parquet` has the columns `activity_id, ts (UTC), lat, lon, distance_m, speed_ms, hr, alt_m`, at about one point every 5 m.
- **`cafes.export(public)`:**
  - **Tracks:** Toronto bounding box only (`opendata.TORONTO`). When public, `privacy.apply` runs first. Tracks are split into parts wherever there's a jump of more than 50 m (privacy holes or GPS gaps).
  - **Cafes:** OSM `amenity=cafe` from Overpass, cached in `data/cafes_osm.json` (`--refresh-cafes` re-fetches). Only cafes within 150 m (`R_MAX_M`, the largest R; keep it in sync with the slider) of a run are kept. Way ids are stored negative.
  - **Exclusions:** `exclusions.py` drops big chains and franchises, plus non-cafes such as Tokyo Smoke, by normalised OSM `name` or `brand`. The cache stores `brand`, and an old cache without it is re-fetched. Keep small local multi-location roasters (Jimmy's, Dark Horse, Pilot, Balzac's, ...) unless the owner says otherwise. Audit for false positives after changing patterns.
  - **Streets:** centreline pieces (`opendata.STREET_CLASSES`) within 150 m of a kept cafe.
  - The output is `wasm/cafe_tol/generated/dataset_<private|public>.inc`, which is **gitignored because it holds GPS data**.
- **`cafes.compile_wasm`** runs `build.sh <target>` in Docker and writes `wasm/cafe_tol/build/cafe_tol_<target>.js` (also gitignored), with the wasm inlined (`-sSINGLE_FILE`) so the page works from `file://`.
- **`cafes.build`** inlines that JS into `cafes.html` and writes `output/cafe_tolerance.html`, or `<out>/index.html` when public.

## GD&T model (keep the C++, the page text and the README in sync)
- **Point matching:** each point goes to the nearest centreline piece within 25 m whose heading is within 35°, ignoring direction. A match to another street shorter than 4 points is treated as a blip and left unmatched.
- **Pass:** a run's contiguous stretch within R of a cafe, split where the matched street *name* changes. Unmatched (stray) points are measured against the pass's street.
- **Part = run:**
  - ⌖ uses the run's closest approach (Ø = 2d).
  - ⌒ uses its worst pass (2 × max |offset|).
  - A run is accepted when it meets both callouts.
- **Status** (`Engine::classify`):
  - both
  - ⌖ only
  - ⌒ only
  - apart (both yields pass alone, but the combined yield fails)
  - neither
  - few (< 3 runs)
- **Offsets use a canonical normal:** + is north for E–W streets and west for N–S streets, so the bias can be read as a side of the street.
- **Year filter:** a bitmask with bit (year − 2000).
- **Checker's notes** (`Engine::notes`, `kNote*` constants): the C++ owns the rules, thresholds and numbers. The page's `NOTE_TEXT` owns only the wording, one sentence per kind.
- **Run inspection** (`runs_at`, `run_report`, `run_profile`): same split. The C++ finds and scores one run across every cafe (folded like `parts_of`); the page draws it and words it.

## Privacy
- `data/`, `output/` and `docs/` are gitignored. **Never commit or publish them, `generated/` or `build/`.** The one exception is `data/README.md` (`data/*` + `!data/README.md`): notes on the data sources with no data in them. Keep it current when inputs change.
- **Provenance meta** (`cafes._meta`, shown under About → Data sources) must never hold coordinates, or any `activities.json` field except dates. Public meta reports no near-run counts. The page's About prose names no Toronto place but Toronto, because the forbidden-name scan matches substrings.
- **The public page lives in `docs/index.html`,** built by `publish`. Updates are **manual**: the owner reviews and commits. Don't automate it.
- **`privacy.check`:**
  - scans the page text **and decodes every base64 wasm module**
  - rebuilds the module's memory image from the data section, finds `CAFETOL1` with a version 1 or 2 header, and decodes it
  - scans the provenance metadata too: forbidden words, `[lat, lon]` pairs, and any decimal with 3+ places
  - recognises a base64 wasm module of any length
  - zone-tests every run point, street point and cafe
  - name-scans the cafe and street names
  - **fails** on a wasm with no readable dataset
  - **fails** on a run that carries a day of the month: public builds store only year and month (`cafes._runs(public=True)` writes day 0). The page shows "Jun 2025".

  If the check fails, fix the cause. Don't loosen the check.
- **Forbidden names:** the top-5 neighbourhoods by run count (`privacy.forbidden_names`).
- **`data/privacy.json` holds the real zone centres and the random shifts.** It was copied from garmin-runs so both sites hide the same places with the same shifts. Keep it stable, because deleting it re-rolls the shifts.

## Page
- One dark drawing-sheet theme. Checker's notes are light yellow (`--note`); keep that colour apart from the reds of rejection and the purple of runs.
- A new note kind needs a sentence in `NOTE_TEXT`. Rule changes go in C++ (see GD&T model) and the README list.
- The map's zoom limit copies `opendata.TORONTO` into `CITY`. Change both together.

## Visual check
- Drive headless Edge over the DevTools protocol (`uv run --no-project --with websocket-client`), using a fresh `--user-data-dir` and waiting in real time so tiles load.
- Edge's "Tracking Prevention blocked access to storage" warnings about unpkg are harmless.
