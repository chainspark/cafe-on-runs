# Cafe on Runs

**GD&T tolerance analysis of GPS runs, with a C++ geometry engine compiled to WebAssembly.**

Live page: https://chainspark.github.io/cafe-on-runs/

How closely do my runs pass Toronto's cafes? Let's think of each running activity captured using
Garmin smartwatch GPS as one manufactured part. The intent never changes: follow the street past
the cafe. Yet every run comes out a little different, because of GPS noise, the sidewalk I took, a road
crossing or a detour, and tolerances decide how much of that variation is acceptable.
- **The nominal:** the City street centreline.
- **Datum A:** each cafe.
- **Accepted run:** one that meets both callouts:
  - ⌖ true position: its closest approach to the cafe
  - ⌒ profile of a line: its worst sideways deviation from the street
- **A cafe that conforms:** its share of accepted runs reaches the acceptance yield, like a batch passing inspection.

## Engineering highlights

- **Geometry engine:** about 1,200 lines of C++20 in [`wasm/cafe_tol/`](wasm/cafe_tol/), compiled with Emscripten to one WebAssembly page that re-runs the whole inspection as the sliders move.
  - **Map matching:** each GPS point snaps to the nearest centreline segment within 25 m whose heading is within 35°, ignoring direction. A uniform grid over the segments finds the candidates.
  - **Blip rejection:** a match to another street shorter than 4 points is treated as noise.
  - **Pass cutting:** a pass is a run's stretch inside R, split where the matched street changes. Changing R re-cuts every pass.
  - **Strays:** points that leave the street are measured against the pass's own street, so they count against the profile instead of being dropped.
  - **Signed offsets:** these use a canonical normal (+ north on east–west streets, + west on north–south ones), so a street's bias reads as a side of the street.
  - **Deterministic:** results are plain geometry with no fitted model, so the same data and settings always give the same answer.
- **Checker's notes:** the C++ engine decides which facts about a cafe's runs are worth pointing out (`Engine::notes`); the page only words them.
- **Run inspection:** the engine finds the runs under a click (a grid query, then segment distances) and reports one run across every cafe it passed, in route order, plus its offset profile at a cafe.
- **The lot:** the engine sums every run in the selected years as it scores the cafes, and grids the runs into a density map that counts distinct runs per cell, walking each segment so a run counts in every cell it crosses.
- **Tests:** 76 C++ checks, run in a pinned Emscripten Docker image (`bash build.sh test`).
- **Python pipeline:**
  - reads FIT files, OpenStreetMap and City of Toronto open data
  - writes a versioned binary dataset (zigzag varints at 1e-5°, with a provenance header) that is compiled into the module
- **Edge cases from real data:**
  - GPS dropouts and privacy holes split a track (any jump over 50 m)
  - a run that turns a corner makes two passes but counts as one part
  - chains and non-cafes are excluded by OSM name and brand
  - R's range of 30–150 m was set by measuring cafe setbacks and the spacing to the next parallel street ([ARCHITECTURE.md §8](ARCHITECTURE.md))
- **Privacy by construction:**
  - the public build trims each run's ends, keeps only month-and-year dates and drops the most-run neighbourhood names
  - the publish step decodes the dataset back out of the compiled WebAssembly and fails on a forbidden name or an exact date

## What this project does

1. **Collects** my runs from the Garmin Connect Personal API, every cafe in Toronto from
   OpenStreetMap, and the street centreline from City of Toronto Open Data.
2. **Packs** them into a compact binary dataset, along with notes on where each source came from.
   Public builds are scrubbed first.
3. **Compiles** that dataset into a C++20 geometry engine, built to WebAssembly.
4. **Produces** one HTML file: a map of my runs and the cafes I pass, with sliders that re-run the
   inspection live. It works from `file://` and fetches no data.

| GD&T | On the run map |
|---|---|
| Drawing (nominal) | City of Toronto street centreline |
| Datum A / basic dimension | the cafe (OpenStreetMap position), for ⌖ only |
| Part / lot | one run / all runs within R of the cafe |
| Inspection (CMM) | the C++ engine, compiled to WebAssembly |

- **⌖ True position:** Ø = 2 × the run's closest approach to the cafe. The run conforms when Ø ≤ the zone (default Ø50 m).
- **⌒ Profile of a line:** the zone is a band centred on the street centreline (equally disposed). A run's actual profile is 2 × its largest sideways offset, counting every GPS point. It conforms when that is ≤ the band (default 30 m). The callout has no datum (`⌒ | 30`): the band follows the street, and the cafe's R circle only picks which stretch is inspected.
- **Accepted run:** meets both callouts at once.
- **Conforms to both:** at least the acceptance yield (default 75%) of a cafe's runs are accepted, from at least 3 runs.
  - Other statuses: ⌖ only, ⌒ only, *each alone, not together* (both yields pass on their own, but the failures fall on different runs), neither, and too few runs.
- **Bias:** each street reports its mean signed offset, which shows which sidewalk I favour.

Runs are never merged or averaged, because the profile band exists to measure their spread. Big
chains, franchises and places tagged cafe that aren't are excluded
(`src/cafe_tolerance/exclusions.py`, matched by OSM name and `brand`). Small local multi-location
roasters stay in.

## Reading the page

The page is a dark engineering drawing sheet: a zone-marked border, drawing lettering and a title block.

- **Map:** zoom-out stops where the view shows only the City of Toronto, and panning stays inside it.
- **Glow:** every run, drawn additively. Brighter streets are run more often.
- **Pins:** cafes drawn as status symbols, shape as well as colour: ● both, ◐ ⌖ only, ◑ ⌒ only, ◎ apart, ○ neither, · few. Names appear from zoom 16. Click a pin or a table row to select a cafe.
- **Selecting a cafe** draws its inspection on the map:
  - the R circle
  - the centreline (nominal) and its profile band
  - each pass: green when the run is accepted, red when it isn't
  - the closest-approach dots
  - the Ø zone
  - **GD&T annotations** (toggle):
    - a datum-A symbol on the cafe
    - a `⌖ | Ø50 | A` feature control frame on a leader to the position zone, with the actual best and median Ø and the run yield
    - a dimension line to the best run's closest point
    - for the top three streets, a band-width dimension and a `⌒ | 30` frame with the street's p95 actual and pass yield

  The panel shows both feature control frames with actual values and yields, a per-street table with bias, and a histogram of Ø.
- **Checker's notes** (light-yellow handwriting): written only when a rule finds something worth pointing out, at most three per cafe. The first is also written on the map, and a ① in the table marks those cafes. The C++ engine applies the rules (`Engine::notes`), and the page words them. The rules, with thresholds set so that about one cafe in eight gets a note:
  - a run within 2 m of the cafe (from 10 runs)
  - ≥ 90% of ≥ 10 passes on one side of the main street
  - status "each alone, not together"
  - ⌒ p95 over the band by at most 8 m, while ≥ 80% of runs hold it (from 10 runs)
  - exactly one run failing both callouts (from 15 runs)
  - every run accepted (from 10 runs)
- **Two views:** selecting a cafe shows the **cafe view** (datum, blue); selecting a run shows the **run view** (part, white). The page always opens in the cafe view. A bar above the map shows which view is on, the map's top edge and corner label take its colour, and the panel tabs (Cafes / Run report) switch views too.
  - **Cafe view:** zoomed on one cafe, with its R circle, band, zone, annotations and notes; every run is the purple glow, every cafe a status symbol.
  - **Run view:** the map fits the whole run, drawn in orange with arrows and S / F markers; the glow dims. The cafes it passed are numbered in route order (filled = accepted, hollow = rejected; names from zoom 16 or on hover), and every other cafe is a faint dot you can still click. The cafe overlay is hidden.
  - **Both views:** zoom with + / −, **⤢** refits to the selection, drag to pan, and any run or cafe on the map can be selected.
- **Inspecting one run** (a part):
  - **Click a run** anywhere on the map, in either view. Where runs overlap, a list shows every run under the click by date; hovering a row draws that run dashed, and "accepted / rejected here" gives its verdict at the selected cafe.
  - **Runs at this cafe**, under the selected cafe's frames: one row per run with its Ø, ⌒ actual and verdicts. A row opens the run view.
  - **Run report:** the date, length, cafes passed, accepted and rejected, the best Ø and worst ⌒, a **route strip** (the run as one line, with a mark where each cafe was passed), and the cafes in route order. ◀ ▶ step through the runs at the cafe you came from, by date; ✕ goes back to its cafe view. Any cafe (pin, strip mark or row) opens its cafe view, and "↩ back to run" returns.
  - **This run at the cafe:** the run's sideways offset along the street inside R, against the band.
  - `#run=<n>` in the URL marks a run to go back to; the page still opens in the cafe view. It is the run's position in the build, never a Garmin activity id.
- **Controls:**
  - R (30–150 m), Ø (10–200 m, never more than 2R), band (10–60 m) and acceptance (50–100%)
  - year chips, "All / Conform to both", and search
  - the URL hash `#cafe=<osm id>&show=both` reopens a view
- **Tooltips:** every parameter, column header, frame and status explains itself. Hover over it, focus it with the keyboard, or tap it on a phone. Each tip includes the default and range where there is one. The position zone and profile band tips also draw a small diagram, with one conforming run and one failing run, labelled with the current slider value and updated as the slider moves.
- **The lot**, under "What this is" in the About section: every run in the selected years, scored with the current settings. It shows runs, distance, cafes per run (with a link to the run that passed the most) and the share of run–cafe pairs accepted, plus runs per month and a density map of the runs, brighter where more distinct runs overlap. Hover over it to count the runs nearby; click it to zoom the main map there.
- **About this project**, at the bottom of the page, explains all of this in plain language and lists the data sources recorded in that build.

## Data sources

| Data | Source | Licence |
|---|---|---|
| Runs | Garmin Connect, via the Garmin Connect Personal API | My own data, not licensed for reuse |
| Cafes | © OpenStreetMap contributors, via the Overpass API | ODbL 1.0 |
| Street centreline | City of Toronto Open Data: Toronto Centreline (TCL) | Open Government Licence – Toronto |
| Map tiles (display only) | Esri World Dark Gray Canvas | Esri terms |

Each build embeds the dates and counts for these sources, and the page shows them under **Data
sources**. File-by-file details, refresh commands and sensitivity are in
[data/README.md](data/README.md).

## How it is built

```
Garmin Personal API ──► data/raw/*.zip ──► data/tracks.parquet
OpenStreetMap (Overpass) ──► data/cafes_osm.json          ┐
Toronto Open Data centreline ──► data/opendata/            ├─► cafes.py export ─► CAFETOL1 dataset
                                                           ┘        │
             wasm/cafe_tol/*.cpp  +  generated/dataset.inc ◄────────┘
                         │  em++ (emscripten/emsdk Docker image, -sSINGLE_FILE)
                         ▼
            one self-contained HTML page (Leaflet + inlined wasm)
```

- **`wasm/cafe_tol/`** is the C++20 engine:
  - `geom.hpp`: projection, distances, grid index
  - `dataset.*`: decoder
  - `match.*`: matches points to streets by distance and heading
  - `gdt.*`: passes, parts, yields and status
  - `api.cpp`: the WebAssembly interface
  - `tests/`: synthetic tests
- **The dataset is compiled into the binary:** `embedded.cpp` `#include`s the bytes `cafes.py` generates.
- **Python** (`src/cafe_tolerance/`) does everything else: fetching, parsing, export, the Docker build, page assembly and privacy.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the components, data flow, dataset format, WebAssembly interface, privacy design and design decisions.

## Setup

- Python 3.12+ with [uv](https://docs.astral.sh/uv/): `uv sync`
- Docker, to compile the C++. The first build pulls `emscripten/emsdk:4.0.10`.
- A Garmin Personal API key, in **one** of these places. Never commit it.
  - the environment variable `GARMIN_PERSONAL_API_KEY`
  - the file `~/.config/garmin/connect-personal-api-key`

## Commands

```sh
python -m cafe_tolerance fetch            # activity summaries + FIT files -> data/
python -m cafe_tolerance parse            # FIT -> data/tracks.parquet
python -m cafe_tolerance cafes            # private page -> output/cafe_tolerance.html
python -m cafe_tolerance cafes --public   # privacy-scrubbed page
python -m cafe_tolerance publish [--fetch] [--out docs]   # public page + privacy check
```

Run the C++ tests in the same image:

```sh
docker run --rm -v "$PWD/wasm/cafe_tol:/src" -w /src emscripten/emsdk:4.0.10 bash build.sh test
```

The URL hash `#cafe=<osm id>&show=both` opens the page on one cafe, with only the cafes that conform to both.

## Privacy

- **Trim:** the first and last 500 m of every run are removed, so a run's own start and finish don't show.
- **No privacy zones:** the public build doesn't hide the areas where runs often start or finish. That was decided on 2026-11-07, and the earlier zones were removed.
- **Dropped names:** the names of my most-run neighbourhoods never appear, so cafes and streets named after them are left out.
- **Dates:** a public build stores only the month and year of each run (the day is 0 in the data), so the run report shows "Jun 2025".
- **The publish check:** `publish` scans the page text and also decodes the dataset compiled into the wasm. It fails on:
  - a forbidden name in the text, the cafe and street names or the provenance metadata
  - an exact run date
  - coordinate-like metadata
  - a wasm module without a readable dataset

`data/`, `output/`, `docs/`, `wasm/cafe_tol/generated/` and `wasm/cafe_tol/build/` hold GPS data. They are gitignored and never committed. Publishing is manual:

1. Run `publish`, then review `docs/index.html`.
2. Copy `docs/index.html` and `docs/.nojekyll` onto the `gh-pages` branch, which holds nothing else, then commit and push it.
3. GitHub Pages serves that branch (Settings → Pages → Deploy from a branch → `gh-pages` / root).

The built page never goes on `main`.

Attribution and licences: see [Data sources](#data-sources) above and [data/README.md](data/README.md).
