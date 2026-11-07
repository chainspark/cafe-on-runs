# Architecture

Cafe on Runs builds one self-contained HTML page. The page inspects the owner's runs past
Toronto cafes using two GD&T callouts: **true position** ⌖ and **profile of a line** ⌒.

- **Python** gathers and scrubs the data, then packs it into a compact binary dataset.
- **C++**, compiled to WebAssembly, holds the dataset and does all of the geometry.
- **The browser page** draws the map, sends slider settings to the engine, and renders the results.

This document covers how the parts fit together and why they are built this way. Setup and
commands are in [README.md](README.md). Working rules (environment, privacy, conventions) are in
[CLAUDE.md](CLAUDE.md).

## 1. System overview

```
                         ┌──────────────────────────── Python (src/cafe_tolerance) ──────────────────────────┐
 Garmin Personal API ──► │ fetch ─► data/raw/*.zip ─► parse ─► data/tracks.parquet ─┐                        │
 OSM Overpass        ──► │ cafes.fetch_cafes ─► data/cafes_osm.json ─► exclusions ──┤                        │
 Toronto Open Data   ──► │ opendata ─► data/opendata/*.geojson (centreline) ────────┤                        │
                         │                       (public build: privacy.apply) ─────┤                        │
                         │                                                          ▼                        │
                         │                       cafes.export ─► cafe_blob.encode ─► generated/dataset_*.inc │
                         └──────────────────────────────────────────────────────────┬────────────────────────┘
                                                                                    │ #include
                         ┌──────────── C++20 (wasm/cafe_tol), em++ in Docker ───────▼────────────────────────┐
                         │ embedded.cpp (kDataset[]) ─► dataset ─► match ─► gdt (Engine) ◄── api.cpp (C ABI) │
                         └──────────────────────────────────────────────────────────┬────────────────────────┘
                                                                                    │ -sSINGLE_FILE
                                                       build/cafe_tol_<target>.js (wasm inlined as base64)
                                                                                    │ cafes.build inlines it
                         ┌──────────── Browser ─────────────────────────────────────▼────────────────────────┐
                         │ cafes.html: Leaflet map + glow layer + pins + panel ◄── typed-array views on heap │
                         └───────────────────────────────────────────────────────────────────────────────────┘
                                   output/cafe_tolerance.html (private)  ·  docs/index.html (public)
```

The page needs no server and loads no data at view time. Only Leaflet, fonts and map tiles come
from the network. Everything else, including the GPS data, is inside the one HTML file. It runs
from `file://`.

## 2. Components

| Path | Language | Responsibility |
|---|---|---|
| `src/cafe_tolerance/api.py` | Python | Garmin Personal API client wrapper: auth, retry/backoff |
| `fetch.py` | Python | Activity summaries → `data/activities.json`; FIT ZIPs for GPS runs → `data/raw/` |
| `parse.py` | Python | FIT `record` messages → `data/tracks.parquet` (~5 m spacing, UTC timestamps) |
| `opendata.py` | Python | Downloads and caches City of Toronto datasets; `TORONTO` bbox; `STREET_CLASSES` |
| `exclusions.py` | Python | Chain/franchise and not-a-cafe lists; `excluded(name, brand)` |
| `cafe_blob.py` | Python | CAFETOL1 encoder/decoder; rebuilds a wasm module's memory image to find the dataset |
| `cafes.py` | Python | Overpass fetch, run/street/cafe selection, export, Docker compile, page assembly |
| `cafes.html` | HTML/JS | Page template: map layers, controls, panel, calls into the engine |
| `privacy.py` | Python | Track trim (`apply`), pre-publish scan (`check`), forbidden names |
| `publish.py` | Python | Public build into `docs/` + privacy check; deletes the output if the check fails |
| `wasm/cafe_tol/geom.hpp` | C++ | Projection, point-to-segment distance, canonical normal, uniform grid |
| `dataset.{hpp,cpp}` | C++ | CAFETOL1 decode (and encode, for tests) |
| `match.{hpp,cpp}` | C++ | Street network, flattened track, point → street matching |
| `gdt.{hpp,cpp}` | C++ | `Engine`: passes, parts, yields, status, per-cafe detail, band polygons |
| `api.cpp` | C++ | `extern "C"` WebAssembly interface; owns the result buffers |
| `embedded.cpp` | C++ | Compiles the generated dataset bytes into the binary |
| `tests/test_gdt.cpp` | C++ | Tests on synthetic geometry, run under node in the container |
| `build.sh` | bash | `test` / `private` / `public` targets, run inside `emscripten/emsdk:4.0.10` |

## 3. Data flow

### 3.1 Inputs and caches (all in `data/`, gitignored)

Only `data/README.md` is tracked (`data/*` + `!data/README.md`). It documents every file below: its source, licence, refresh command and sensitivity.

| Artifact | Produced by | Notes |
|---|---|---|
| `activities.json` | `fetch` | All sports. A run is any activity whose `typeKey` contains `running` |
| `raw/<id>.zip` | `fetch` | Original FIT files. Written atomically. Runs without GPS are skipped |
| `tracks.parquet` | `parse` | `activity_id, ts, lat, lon, distance_m, speed_ms, hr, alt_m` |
| `cafes_osm.json` | `cafes.fetch_cafes` | `{"meta": {endpoint, query, osm_base, retrieved}, "cafes": [{osm_id (ways negative), name, lat, lon, brand}]}`. An older bare list is still read |
| `opendata/*.geojson` | `opendata.download` | Street centreline and neighbourhood boundaries |
| `opendata/*.source.json` | `opendata.download` | CKAN title, resource, last-modified and download date. `opendata.source()` falls back to the file date |
| `privacy.json` | garmin-runs | Privacy-zone centres used by garmin-runs. Not read by this project. **Never publish this file** |

### 3.2 Export (`cafes.export(public)`)

1. **Tracks.**
   - Read `tracks.parquet`.
   - Public build only: run `privacy.apply`, which trims the first and last 500 m of every run.
   - Keep points inside the Toronto bounding box, and runs with at least 20 such points.
2. **Runs.**
   - Split each run into **parts** wherever consecutive points are more than `GAP_M` = 50 m apart, by position or by `distance_m`. This covers privacy holes and GPS dropouts.
   - Take the date from `startTimeLocal`.
3. **Cafes.**
   - Load the Overpass cache.
   - Drop the excluded places: chains, franchises and non-cafes, matched by OSM name or brand.
   - Keep cafes within `R_MAX_M` = 150 m of any run point. This is the largest R the page offers, so data beyond it is never needed. Keep it in sync with the R slider's `max`.
   - Public build only: also drop cafes whose name contains a forbidden word.
4. **Streets (the nominals).**
   - Keep centreline pieces of the runnable classes (Local, Collector, Minor/Major Arterial, Trail, Walkway) that come within R_MAX_M of a kept cafe.
   - Public build only: drop pieces whose name contains a forbidden word.
5. **Provenance.** `cafes._meta` records build info and the three data sources, with their licences, dates and counts (§4). It raises if anything coordinate-like slips in.
6. **Encode** with `cafe_blob.encode`, then write `wasm/cafe_tol/generated/dataset_<target>.inc` as a comma-separated byte list.

Typical size (private build): 260 runs, 177k points, ~8.9k street pieces and ~570 cafes come
to about 0.73 MB of dataset. The finished page is about 1.1 MB.

### 3.3 Build (`cafes.compile_wasm`, `cafes.build`)

1. `docker run emscripten/emsdk:4.0.10 bash build.sh <target>` compiles `dataset.cpp match.cpp gdt.cpp api.cpp embedded.cpp`. The flags are `-O3 -std=c++20 -sMODULARIZE -sEXPORT_NAME=CafeTol -sSINGLE_FILE=1 -sENVIRONMENT=web -sALLOW_MEMORY_GROWTH -sINITIAL_MEMORY=64MB`, plus an explicit export list.
2. `cafes.build` inlines `build/cafe_tol_<target>.js` into `cafes.html`, escaping `</script`. It then writes `output/cafe_tolerance.html` for a private build, or `<out>/index.html` for a public one.
3. `publish` runs the public build into `docs/`, then runs `privacy.check`.

## 4. Dataset format: CAFETOL1, version 2

The format is little-endian. The writer is `cafe_blob.encode` (Python). The readers are
`dataset.cpp` (C++) and `cafe_blob.decode` (Python, used by the privacy scan).

```
magic      "CAFETOL1"                       8 bytes, a fixed name (the 1 is not the version)
version    u32 = 2                          1 is still accepted (no meta)
flags      u32                              bit 0: public (scrubbed) build
meta_len   u32, then utf8 JSON              v2 only; ≤ 64 KiB; provenance, see below
n_runs     u32
  run:     i64 activity_id, u16 year, u8 month, u8 day, u16 n_parts, n_parts × coords
n_streets  u32
  piece:   u32 centreline_id, u8 class (index into STREET_CLASSES), u16 name_len, utf8 name, coords
n_cafes    u32
  cafe:    i32 lat_e5, i32 lon_e5, i64 osm_id, u16 name_len, utf8 name
coords     u32 n, then n × (zigzag-varint Δlat, zigzag-varint Δlon) in 1e-5° (~1 m); the first pair is absolute
```

- **Small deltas:** points are about 5 m apart, so most deltas fit in one byte.
- **Truncated input:** `decode` rejects it.
- **Version mismatch:** the engine refuses to load the dataset.
- **Changing the format:** bump `VERSION`/`SUPPORTED_VERSIONS` in `cafe_blob.py` and `kVersion`/`kMinVersion` in `dataset.hpp`. Update both readers, `find_in_wasm`, and the round-trip and version tests.

**Provenance meta** (`schema: 1`). It holds strings and integers only, and **never coordinates**: `cafes._meta` and `privacy.check` both reject any decimal with 3+ places.

```json
{"schema": 1, "built": "2026-11-06", "build": "public",
 "sources": [{"id": "runs|cafes|streets", "data": "…", "source": "…", "source_url": "https://…",
              "licence": "…", "licence_url": "https://…", "date_kind": "runs through|OSM data as of|downloaded|cached file date",
              "date": "…", "detail": "counts in words"}]}
```

Public builds report only cafes found, excluded and shown, with no near-run count. The rule dates from the privacy zones, whose size that gap would have hinted at.

## 5. The C++ engine

### 5.1 Coordinates

Every point is projected to local metres with an equirectangular projection at a fixed
reference latitude: `x = lon · 111320 · cos(43.7°)`, `y = lat · 110540`. This is the same
projection the Python analyses use. Across the City of Toronto the error is well under 1%, which
is small compared with GPS noise and with the tolerances involved.

### 5.2 `Engine::load`: work done once at start-up

1. **`build_network`:**
   - Projects every street piece.
   - Interns street names into `names` and gives each piece a `piece_name` id.
   - Inserts every segment's bounding box into a 25 m uniform grid (`seg_grid`).
2. **`flatten_runs`:** concatenates every run part into flat arrays `p`, `ll`, `pt_part`, with `part_start`/`part_run` offsets. Everything after this addresses points by global index.
3. **`match_points`:** for each point, takes its heading from its neighbours (i−1 → i+1) within the same part, then finds the nearest segment such that:
   - the distance is ≤ `kMatchM` = 25 m, and
   - |cos(angle between heading and segment)| ≥ cos 35°, which ignores direction.

   Each matched point stores its piece, street name, signed offset and canonical normal.
4. **Blip smoothing:** within a part, a stretch of fewer than `kMinStreetPts` = 4 points matched to a street that differs from both its neighbours is reset to unmatched. Without this, briefly clipping a cross street at an intersection would split a pass.
5. **Point grid:** every run point goes into a 50 m grid (`pt_grid_`), which makes "points within R of this cafe" quick to answer.

**Canonical normal.** The City digitises centreline pieces in either direction. Offsets are
therefore measured along a normal whose orientation is fixed: it points north for east–west
streets and west for north–south streets. That makes the sign of the bias mean "which side of
the street", whichever way each piece was drawn.

### 5.3 `Engine::set_radius(R)`: re-cut passes, run when R changes

For each cafe:

1. Collect the run points within R and sort them by index. Runs of consecutive indices in the same part are the contiguous stretches.
2. Split each stretch where the matched **street name** changes, carrying unmatched points along with the current street. Each piece is a **pass** of one run on one street, and needs at least 3 points.
3. **`measure`** each pass:
   - **`d`:** the exact closest approach of the pass polyline to the cafe (point-to-segment).
   - **`prof`:** 2 × max |offset| over **all** of the pass's points. A stray point that matched nothing or another street is measured against the pass's own street (`offset_to_street`, searched within 300 m).
   - **`mean_off`** (the bias), the mean normal, and the centreline pieces used.

Passes are stored per cafe, so the other sliders never re-cut them.

### 5.4 `Engine::evaluate(Settings)`: re-score, run on every other slider or year change

For each cafe:

1. **Group passes into parts.** Passes are filtered by the year bitmask (bit = year − 2000), then grouped into **parts**, one per run.
   - ⌖ uses the run's closest pass: Ø = 2 × min d.
   - ⌒ uses the run's worst pass: max prof.
2. **Accept or reject each run.** A run is accepted when Ø ≤ Ø_tol **and** prof ≤ band.
3. **Work out the yields.** There are three: ⌖, ⌒ and combined, plus the best and median Ø.
4. **`classify`** gives the status:

| Status | Condition (yields vs acceptance level a) |
|---|---|
| `kFew` | fewer than 3 runs |
| `kBoth` | combined yield ≥ a |
| `kApart` | ⌖ ≥ a and ⌒ ≥ a, but combined < a (the failures fall on different runs) |
| `kPosOnly` / `kProfOnly` | only that callout reaches a |
| `kNeither` | neither does |

### 5.5 `Engine::detail(cafe, Settings)`: the selected cafe only

- **Passes and parts:** the year-filtered passes, and the parts (runs).
- **Per-street features:** passes, ⌖ count, ⌒ count, both count, p95 profile, bias and mean normal.
- **Drawing geometry:**
  - **nominals:** the centreline pieces used, densified to 5 m and clipped to R
  - **bands:** for each nominal, the ±band/2 polygon around it
- **`geometry = false`** skips the drawing geometry. The notes use it.
- **Feature order:** most passes first, ties by name id, so "the main street" is stable.

### 5.6 `Engine::notes(Detail, Settings)`: the checker's notes

The engine owns the rules, their thresholds (`kNote*` in `gdt.hpp`) and the numbers. The page owns only the wording. The rules run in this order, and at most `kNoteMax` (3) notes are kept, most striking first:

| Kind | Fires when | Values (a, b, c, street) |
|---|---|---|
| `kNoteDoor` (1) | a run within 2 m of the cafe, from 10 runs | closest approach |
| `kNoteSide` (2) | ≥ 90% of ≥ 10 passes on the main street on one side | side (0 N, 1 S, 2 W, 3 E), passes on it, passes, street id |
| `kNoteApart` (3) | status "each alone, not together" | none |
| `kNoteNearBand` (4) | ⌒ p95 over the band by ≤ 8 m while ≥ 80% of runs hold it, from 10 runs | p95, runs holding, runs |
| `kNoteOneDetour` (5) | exactly one run fails both callouts, from 15 runs | runs |
| `kNoteAllAccepted` (6) | every run accepted, from 10 runs | runs |

On the real data about one cafe in eight gets a note. `tests/test_gdt.cpp` covers each rule, its threshold and the cap.

### 5.7 One run across every cafe: `runs_at`, `run_report`, `run_profile`

- **`runs_at(p, r, year_mask)`:** the runs under a map click. A point-grid query of r + 15 m finds candidate points, then each point and its segment to the next point in the same part is measured, so a click between two GPS points still hits. It keeps the nearest distance per run, filtered by year, nearest first. The page passes r = 10 screen pixels in metres.
- **`run_report(run, s)`:** for every cafe, the run's passes there are folded exactly like `parts_of` (min d, max prof) and scored with the settings. Each row keeps the street and foot of the nearest pass and its distance along the run, `pt_along_[qi] + |p[qi] − q|`, and rows are sorted by that distance (route order).
  - `Pass::qi` is the segment start of the closest approach, set in `measure`.
  - `pt_along_` and `run_len_` are cumulative distances per point and per run, built once in `load`. A gap between parts counts as a straight line.
- **`run_profile(cafe, run)`:** the points of the run's nearest pass at that cafe, each as (x, offset). Offsets come from the same `offset_of` helper `measure` uses, so strays are measured against the pass's street. x is the position along the street relative to the cafe, on the canonical normal turned clockwise: + east on E–W streets, + north on N–S ones.

### 5.8 The lot: `LotStats` and `heat`

- **`LotStats`:** `evaluate(s)` fills it from the same parts it scores the cafes with, so it always matches the cafe statuses: runs in the year mask (every run, not only those near a cafe), total, median and longest km, run–cafe parts and how many met both callouts, the median number of cafes a run passed within R, the run that passed the most, and runs per calendar month from the earliest run's month (empty months included).
- **`heat(cols, rows, sw, ne, year_mask)`:** a grid over a box, row 0 north, counting **distinct runs** per cell. Each segment between consecutive points of a part is walked at half a cell, so a run counts in every cell it crosses, not only where it logged a point. A per-cell stamp of the last run counted makes an out-and-back run count once; it works because parts are stored run by run.
- `tests/test_gdt.cpp` covers both: the year mask, the callouts, empty months, an empty lot, shared cells, out-and-back, a two-point segment that crosses every cell, and a bad box.

### 5.9 Cost

- **`load`:** linear in points, with one grid lookup per point.
- **`set_radius`:** one grid query per cafe, then work linear in the points inside R.
- **`evaluate`:** linear in passes.

With the current data, every step feels instant in the browser.

## 6. WebAssembly interface (`api.cpp`)

Every call returns either a number or a pointer into a buffer owned by `api.cpp`. The page
wraps each pointer in a fresh typed-array view (`HEAPF64`, `HEAPF32`, `HEAP32`) **after every
call**. Memory growth replaces the heap buffer, so a view kept from earlier can go stale.
Strings are read with `UTF8ToString`.

| Function | Returns |
|---|---|
| `ct_init()` | 0 on success. On failure, read the message with `ct_error()` |
| `ct_flags()` | Dataset flags (bit 0 = public) |
| `ct_meta()` | UTF-8 JSON provenance (§4); `""` for a v1 dataset |
| `ct_run_count`, `ct_run_years`, `ct_run_ids` | Runs: i32 year[], f64 activity id[] |
| `ct_point_count`, `ct_points` | f32 [lat, lon] × points (drawing only) |
| `ct_part_count`, `ct_part_start`, `ct_part_run` | i32 offsets (n_parts + 1) and i32 run index per part |
| `ct_cafe_count`, `ct_cafe_latlon`, `ct_cafe_osm`, `ct_cafe_name(i)` | f64 [lat, lon] × cafes, f64 OSM id, name |
| `ct_street_name(i)` | Street name for a name id |
| `ct_set_radius(R)` | Re-cuts passes (§5.3) |
| `ct_evaluate(tol, band, acceptance, year_mask)` | f64 × 7 per cafe: runs, status, ⌖ yield, ⌒ yield, combined yield, best Ø, median Ø |
| `ct_detail(i)` | Number of passes. Fills the detail buffers using the settings of the last `ct_evaluate` |
| `ct_detail_passes` | f64 × 9 per pass: run, name id, start, end (point indices), d, q_lat, q_lon, prof, mean offset |
| `ct_detail_part_count`, `ct_detail_parts` | f64 × 5 per run: run, d, prof, ⌖ ok, ⌒ ok |
| `ct_detail_feature_count`, `ct_detail_features` | f64 × 9 per street: name id, passes, ⌖, ⌒, both, p95, bias, nx, ny |
| `ct_detail_line_*`, `ct_detail_band_*` | Polylines and polygons: i32 offsets (count + 1), then f32 [lat, lon] pairs |
| `ct_detail_line_names()` | i32 street name id per nominal line / band, which ties the ⌒ annotations to a street |
| `ct_notes(i)`, `ct_notes_data()` | Number of checker's notes for cafe i, then f64 × 5 per note: kind, a, b, c, street id (§5.6). Uses the settings of the last `ct_evaluate` |
| `ct_note_counts()` | i32 note count per cafe, for the table's ① marks. Uses the settings of the last `ct_evaluate` |
| `ct_run_dates()`, `ct_run_lengths()` | i32 yyyymmdd per run (dd = 00 in public builds), f64 metres per run |
| `ct_runs_at(lat, lon, r)`, `ct_runs_at_data()` | Number of runs within r m of a point, then f64 × 2 per run: run, distance. Nearest first, year mask of the last `ct_evaluate` (§5.7) |
| `ct_run_report(run)`, `ct_run_report_data()` | Number of cafes the run passed, then f64 × 9 per cafe in route order: cafe, d, prof, ⌖ ok, ⌒ ok, along (m), street id, q_lat, q_lon |
| `ct_run_profile(cafe, run)`, `ct_run_profile_data()`, `ct_run_profile_street()` | Number of points, then f64 × 2 per point: x along the street, offset; and the street name id |
| `ct_lot()` | The lot as of the last `ct_evaluate`: f64 runs, km, median km, longest km, parts, accepted, median cafes, most cafes, its run, first month (year × 12 + month − 1), n months; then n run counts |
| `ct_heat(cols, rows, s, w, n, e)` | i32 × cols × rows distinct-run counts over the box, row 0 north, year mask of the last `ct_evaluate` (§5.8) |

When adding a function, mark it `EMSCRIPTEN_KEEPALIVE`, add it to the export list in
`build.sh`, and document its buffer layout here.

## 7. The page (`cafes.html`)

- **Engine start-up:** `await CafeTol()` → `ct_init` → copy the static arrays (points, parts, cafes) → `ct_set_radius(75)` → `ct_evaluate`.
- **Map layers:**
  - Esri dark-gray base and reference tiles. OSM tiles are blocked from `file://`, and CartoDB needs a key.
  - The **glow layer**, a custom `L.Layer` drawing to its own canvas. It strokes every run part with `globalCompositeOperation = "lighter"`, so overlapping runs brighten the way the route heatmap does. It is redrawn on `moveend`/`zoomend` and when the year filter changes.
  - **Cafe pins:** canvas `circleMarker`s coloured by status. In "Conform to both" mode, the other pins are hidden.
  - **Selection overlay:** the R circle, the band polygons and nominals (dashed chain lines), the passes (green when the run is accepted, red otherwise), closest-approach dots and the Ø zone circle.
  - **GD&T annotations** (toggle, on by default): an `annot` pane above the pins and labels, with `pointer-events: none` so it never blocks a click. It holds:
    - a datum-A symbol on the cafe
    - a `⌖` frame on a leader to the Ø zone, with the actual values
    - a dimension to the best run's closest point
    - for the top 3 streets, a band-width dimension and a `⌒` frame

    The callouts are laid out in screen pixels and redrawn on every zoom. Frames sit in columns just outside the R circle, are stacked so they never overlap, and switch sides or slide inwards to stay on the map.
- **What each control calls:**

| Control | Engine calls |
|---|---|
| R slider | `ct_set_radius`, then `ct_evaluate` and `ct_detail` |
| Ø, band and acceptance sliders | `ct_evaluate`, then `ct_detail` |
| Year chips | `ct_evaluate` and `ct_detail`, plus a glow redraw |
| Search, "All / Conform to both" | none: the JS only filters the table and pins |
| Selecting a cafe | `ct_detail` |

- **Theme:** one dark drawing sheet (no light scheme): CSS tokens on `:root`, a `.frame` grid with zone markers around the `.sheet`, Barlow Semi Condensed lettering, Barlow body, B612 Mono values and Caveat for the checker's pen (`--note`, light yellow, kept apart from the reds of rejection and the purple of runs).
- **Status symbols:** `SHAPES` draws each status as an SVG symbol for the legend and chips. `Pin` (a `L.CircleMarker` subclass) draws the same shapes on the canvas renderer, while hit-testing stays the circle's.
- **Checker's notes:** the rules are in the engine (§5.6). The page holds only `NOTE_TEXT`, one sentence per kind, filled with the note's values.
  - **Panel:** the selected cafe's notes come from `ct_notes`.
  - **Map:** the first note is written above the R circle, and hidden when R is under 30 px.
  - **Table:** the ① marks come from one `ct_note_counts` call, cached per setting.
- **Views:** `state.mode` is `"cafe"` or `"run"`.
  - **The rule:** `select(i)` always enters the cafe view; leaving the run view saves `lastRun` and zooms to the cafe. `selectRun(run)` always enters the run view and fits the map to the whole run.
  - **Opening:** the page always opens in the cafe view; a `#run=` hash only sets `lastRun` for "↩ back to run".
  - **Chrome:** `viewChrome()` keeps the view bar, map border, corner label, back button, panel tabs and legend in step with the mode.
  - **Fit:** a custom Leaflet control (⤢) refits to the selection.
- **Run inspection:**
  - **Picking:** a map click that misses a pin calls `ct_runs_at`, in either view. One hit opens that run; several open a Leaflet popup listing them by date. Hovering a row draws that run dashed. Pin clicks stop propagation, so they never count as run clicks.
  - **Pins in the run view:** `drawPins` switches the `Pin` canvas symbol to `kind: "num"` (number in route order; filled = accepted, hollow = rejected) for the run's cafes and `kind: "faint"` for every other cafe. Labels show from zoom 16, or on hover.
  - **The run's layer:** the run is drawn on a `runs` pane (z 610, between the cafe overlay and the pins) with arrows (`divIcon`s rotated to the screen bearing; Mercator keeps angles, so they need no redraw on zoom), S / F markers and closest-approach dots.
  - **What hides:** the cafe overlay, annotations and the cafe detail. `lastDetail` is still computed, for the step list and picker. The Glow layer dims.
  - **The report:** the panel's **Run report** tab renders the report, the route strip (marks placed at along / length, stacked on three lanes when close), and the profile from `ct_run_profile`.
  - **Runs at this cafe:** built from `ct_detail` parts.
  - **URL:** the hash carries `run=<index + 1>` while in the run view.
- **Zoom limit:** the map's `maxBounds` is the City of Toronto box (`opendata.TORONTO`, copied into the page), and `minZoom` is `getBoundsZoom(CITY, true)`, recomputed on resize, so the view never shows beyond the city.
- **URL hash:** `#cafe=<osm id>&show=both` selects a cafe and the filter on load, and the hash is updated on every selection. Use it to share a view or take screenshots.
- **Tooltips:** `data-tip` keys on every control, column header, frame and status point into one `TIPS` table. A single `position: fixed` element (`role=tooltip`, linked with `aria-describedby`) shows on mouse hover, keyboard focus or a tap, so scrolling tables never clip it. Escape or moving away hides it. Non-interactive anchors get a dotted underline and `tabindex=0`. A `TIPS` entry may carry a fourth item, a function returning an inline SVG infographic (`figPos`, `figPro`): a schematic zone or band, one conforming and one failing run, and a mini feature control frame, scaled to the current `state.tol` / `state.band`. The figures use `currentColor` for the zone and band and `--tip-ok` / `--tip-bad` for the runs. An `input` event redraws an open slider tip, and on touch a tap on a slider or its label shows the tip, because a tapped range input takes no focus.
- **The lot (About → What this is):** `renderLot()` runs on every `refresh()`. Four stat tiles from `ct_lot`, a runs-per-month column chart (an SVG drawn at its real width, redrawn by a `ResizeObserver`), and a run-density map in two `ct_heat` passes: a coarse one over the `CITY` box in 200 m cells finds where the runs go, then a fine one over that box (plus a 4-cell margin, kept between 0.6 and 1.1 times as tall as wide) puts one cell on each device pixel, so the density reads as route lines. Each cell is shaded on a log scale, blended between the stops of one purple ramp (validated as an ordinal ramp against the sheet), over a blurred copy of itself for a soft glow. It is redrawn when the year mask or the width changes. A bar shows a data tip (`showDataTip`, the same `#tip` element); hovering the map counts the runs within 50 m with `ct_runs_at`, and a click centres the map there at zoom 15 without selecting anything. The "most cafes" date opens that run in the run view.
- **About this project:** a static section that explains the project in plain language. Its sections sit in balanced CSS columns (`columns: 3 320px`), so a tall section leaves no hole beside it. It must name no Toronto place but Toronto, because the forbidden-name scan matches substrings. The header's "About" link scrolls with `scrollIntoView`, so the `#cafe=…` hash is kept.
- **Data sources:** a table rendered from `ct_meta()` with DOM nodes only. Metadata is never parsed as HTML, and only `https:` links are made. The tile and Leaflet rows are static, because they aren't part of the dataset.

## 8. GD&T model

| GD&T concept | Implementation |
|---|---|
| Drawing / nominal | City of Toronto street centreline piece |
| Datum A, basic dimension | Cafe position (OSM), exact. Referenced by ⌖ only |
| Feature | A street (by name) near the cafe |
| Part / lot | One run / all runs within R of the cafe |
| ⌖ true position | Ø = 2 × closest approach of the run. Conforms if Ø ≤ Ø_tol |
| ⌒ profile of a line, equally disposed, no datum | 2 × max \|offset\| from the centreline, the run's worst pass. Conforms if ≤ band. Form only (`⌒ \| band`): the band follows the street, and R picks the stretch inspected |
| Accepted part | Meets both callouts |
| Lot acceptance | Combined yield ≥ acceptance level, with ≥ 3 runs |
| Unequal disposition | Reported as **bias** (mean signed offset), not toleranced |

| Setting | Default | Range | Fixed constants |
|---|---|---|---|
| Search radius R | 75 m | 30–150 m | Match distance 25 m · heading 35° |
| Position zone Ø | 50 m | 10–200 m, at most 2R | Blip length 4 points · stray search 300 m |
| Profile band | 30 m | 10–60 m | Minimum runs 3 · minimum pass points 3 |
| Acceptance yield | 75% | 50–100% | Track gap 50 m · R_MAX 150 m |

**Why R is 30–150 m.** These figures were measured on the 572 cafes within 300 m of a run (Nov 2026).
- **The lower end:**
  - A cafe sits 11–50 m from its own street's centreline (median 14 m, p75 20 m, p90 32 m).
  - Below 30 m, R doesn't cover the cafe's own street for many cafes, and a pass is only a few points long.
- **The upper end:**
  - The next parallel street with another name is 84 m away at the median (p75 111 m, p90 152 m).
  - Of the runs R picks up, the share whose closest point is on the cafe's own street falls as R grows: 87% at 30 m, 76% at 75 m, 62% at 150 m, and 44% at 300 m.
  - Past 150 m, R mostly adds runs on other streets.
- **The cap on Ø:** Ø is capped at 2R, because at Ø = 2R every run within R conforms to ⌖ and the callout no longer tests anything.

## 9. Privacy architecture

Two builds come from the same code:

| | Private (`cafes`) | Public (`cafes --public`, `publish`) |
|---|---|---|
| Tracks | Full | 500 m start/finish trim |
| Run dates | Full | Month and year only (day stored as 0) |
| Cafes | All within 150 m of a run | Also drops cafes whose name holds a forbidden word |
| Streets | All near kept cafes | Also drops forbidden names |
| Output | `output/cafe_tolerance.html` | `docs/index.html` (reviewed and committed by hand) |
| Check | none | `privacy.check` must pass, or the files are deleted |

**No privacy zones.** Earlier public builds also removed every point inside "privacy zones"
around places where many runs start or finish. They were removed at the owner's request on
2026-11-07. `data/privacy.json` still exists, for garmin-runs, but this project no longer reads it.

**The check scans two places.** The GPS data sits inside base64-encoded WebAssembly, where a
text scan of the HTML can't see it. `privacy.check` therefore looks in both:

1. **Page text:** every forbidden word.
2. **Every embedded wasm module:**
   1. Find each base64 run starting `AGFzbQ` (that's `\0asm`), whatever its length, and decode it. Even a tiny module could hide data.
   2. Rebuild the module's initial linear memory from its data section (`cafe_blob.wasm_memory_image`). The optimiser may split a static array into several segments, so the raw file can't simply be searched.
   3. Find every `CAFETOL1` with a supported (1 or 2) version header. The decoder's own magic constant is also in memory, so it gets skipped.
   4. Decode each dataset. A run carrying a day of the month (an exact date) fails the check.
   5. Scan all cafe and street names **and the provenance metadata**, both raw and with escaped spelling, for forbidden words. Any decimal with 3+ places in the metadata fails the check.

   **A wasm module without a readable dataset fails the check.** Anything published must be scannable.

The forbidden names are the 5 neighbourhoods with the most runs (`privacy.forbidden_names`).

## 10. Design decisions

| Decision | Why |
|---|---|
| **Runs are never merged** | Averaging runs into one line would hide the very spread the profile callout measures. Each run is a part, inspected against the same drawing |
| **The nominal comes from the City centreline** | In GD&T the nominal is design intent. It's never averaged from the parts measured against it. The centreline also gives each street a stable identity |
| **A feature is a street name, not a centreline piece** | Pieces end at every intersection. Grouping by piece would cut one run along one street into a new pass at every block |
| **Matching checks heading** | Near an intersection the nearest centreline is often the cross street. The 35° heading check, ignoring direction, keeps a run on the street it is actually following |
| **A part is a run** (min d, max prof) | A run that turns a corner near a cafe produces two passes. Counting both would double-count one run |
| **Strays count against the pass's street** | Dropping unmatched points would hide exactly the deviations the profile should catch, such as crossing the road to the door |
| **The dataset is compiled into the wasm** | This was a requirement. It also makes the page a single file with no data fetches |
| **`-sSINGLE_FILE`** | Browsers block `fetch()` of a separate `.wasm` file from `file://` |
| **Passes are cut once per R, scored many times** | Only R changes which points belong to which pass. Tolerance, band, acceptance and year sliders only re-score |
| **Toolchain in pinned Docker** | No local compiler or Node is needed, and builds are reproducible. Tests run under the node in the image |
| **Chains are excluded at export** | Keeps the analysis on independent cafes. Matching both name and OSM `brand` catches renamed franchise stores |

## 11. Limitations

- **Street identity is by name.** Two different streets with the same name inside one R would be treated as one feature. Unnamed trails and paths aren't in the centreline classes, so runs on them stay unmatched and are measured against the nearest named street.
- **Sidewalk bias.** Centrelines run down the middle of the road, so runs carry a built-in offset of roughly half the road's width. The default band (30 m) allows for it, and the bias column reports it. A wide arterial can still fail a narrow band without the run being loose.
- **Fixed projection and year range.** The projection is tuned to Toronto, and the year mask covers 2000–2031.
- **Data freshness.** The page shows each source's date (Data sources), so staleness is visible. The cafe list is only as good as OSM: closed or mis-tagged cafes stay until `--refresh-cafes`, and some chains have no `brand` tag and are caught only by name. Centreline files downloaded before provenance sidecars existed show their file date.
- **Precision.** Page coordinates are float32 (~0.5 m), which is fine for drawing. All measurements are made in double precision in the engine.

## 12. Extending

| To change… | Edit |
|---|---|
| Which places count as cafes | `exclusions.py`. Afterwards, audit the excluded names for false positives |
| Matching thresholds | the constants in `match.hpp`. Add a test in `tests/test_gdt.cpp` |
| A tolerance definition or status rule | `gdt.cpp` (`measure`, `parts_of`, `classify`), plus `cafes.html`, README.md and §8 above |
| Engine outputs | `api.cpp`, the export list in `build.sh`, the readers in `cafes.html`, and §6 above |
| A checker's-note rule or threshold | `Engine::notes` and the `kNote*` constants in `gdt.{hpp,cpp}`, a test, §5.6 and the README list. A new kind also needs its sentence in `NOTE_TEXT` in `cafes.html` |
| The dataset format | `cafe_blob.py` (`VERSION`, `SUPPORTED_VERSIONS`, `find_in_wasm`), `dataset.{hpp,cpp}` (`kVersion`, `kMinVersion`), tests, and §4 above |
| Data-source notes | `cafes._meta` (embedded provenance), `opendata.source`, and `data/README.md` |
| The public scrub | `cafes.export` and `privacy.py`. Never loosen `privacy.check`; fix the cause instead |
