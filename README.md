# Cafe Tolerance

**GD&T tolerance analysis of GPS runs, with a C++ geometry engine compiled to WebAssembly.**

How closely do my runs pass Toronto's cafes, specified the way a drawing would? Think of each run
as one part from the same process. The design intent never changes: follow the street past the
cafe. Yet every run comes out a little different, because of GPS noise, the sidewalk I took, a road
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
- **Tests:** 39 C++ checks, run in a pinned Emscripten Docker image (`bash build.sh test`).
- **Python pipeline:**
  - reads FIT files, OpenStreetMap and City of Toronto open data
  - writes a versioned binary dataset (zigzag varints at 1e-5°, with a provenance header) that is compiled into the module
- **Edge cases from real data:**
  - GPS dropouts and privacy holes split a track (any jump over 50 m)
  - a run that turns a corner makes two passes but counts as one part
  - chains and non-cafes are excluded by OSM name and brand
  - R's range of 30–150 m was set by measuring cafe setbacks and the spacing to the next parallel street ([ARCHITECTURE.md §8](ARCHITECTURE.md))
- **Privacy by construction:**
  - the public build trims each run's ends and removes privacy zones
  - the publish step decodes the dataset back out of the compiled WebAssembly and fails if any point falls in a private zone

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

- **Glow:** every run, drawn additively. Brighter streets are run more often.
- **Pins:** cafes coloured by status. Names appear from zoom 16. Click a pin or a table row to select a cafe.
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
- **Controls:**
  - R (30–150 m), Ø (10–200 m, never more than 2R), band (10–60 m) and acceptance (50–100%)
  - year chips, "All / Conform to both", and search
  - the URL hash `#cafe=<osm id>&show=both` reopens a view
- **Tooltips:** every parameter, column header, frame and status explains itself. Hover over it, focus it with the keyboard, or tap it on a phone. Each tip includes the default and range where there is one. The position zone and profile band tips also draw a small diagram, with one conforming run and one failing run, labelled with the current slider value and updated as the slider moves.
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

The public build never shows where I live.

- **Trims and zones:**
  - The first and last 500 m of every run are removed.
  - Places where many runs start or finish become privacy zones, and every point inside them is removed.
  - Zone centres are shifted at random, and the shifts are kept stable in `data/privacy.json`.
- **Dropped cafes:** any cafe within a zone radius + 150 m (the largest R), so no pass can touch a zone, and any street piece inside a zone.
- **Dropped names:** the names of my most-run neighbourhoods never appear.
- **The publish check:** `publish` scans the page text and also decodes the dataset compiled into the wasm, checking every coordinate and name. A wasm module without a readable dataset fails the check.

`data/`, `output/`, `docs/`, `wasm/cafe_tol/generated/` and `wasm/cafe_tol/build/` hold GPS data. They are gitignored and never committed. Publishing is manual: run `publish`, review `docs/`, then commit it yourself.

Attribution and licences: see [Data sources](#data-sources) above and [data/README.md](data/README.md).
