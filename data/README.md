# data/

Every input the project uses lives in this folder. All of it is private, bulky, or both, so the
folder is gitignored (`data/*`). This README is the only file here under version control, through
`!data/README.md`. It contains no coordinates or personal details. Keep it that way.

Run commands from the repository root as `python -m cafe_tolerance <command>`.

## Files

| File | What it holds | Source / provider | Licence / terms | Produced by | Refresh | Sensitivity |
|---|---|---|---|---|---|---|
| `activities.json` | Summaries of every Garmin activity, all sports: type, start time, distance, … | Garmin Connect, via the Garmin Connect Personal API (the author's own account) | The author's personal data; not for redistribution | `fetch.fetch` | `fetch` | **Private.** Includes start positions and account details |
| `raw/<activity id>.zip` | Original FIT files, one per run with GPS | Garmin Connect Personal API | as above | `fetch.fetch` | `fetch` (downloads only new runs) | **Private.** Full GPS tracks |
| `tracks.parquet` | GPS points decoded from the FIT files (`activity_id, ts, lat, lon, distance_m, speed_ms, hr, alt_m`) | Derived from `raw/` | as above | `parse.parse` | `parse` | **Private.** Full GPS tracks |
| `cafes_osm.json` | Places tagged `amenity=cafe` in the City of Toronto bounding box (OSM id, name, position, brand), plus `meta`: endpoint, query, OSM data timestamp and download time | OpenStreetMap, via the Overpass API (overpass-api.de) | ODbL 1.0, © OpenStreetMap contributors | `cafes.fetch_cafes` | `cafes --refresh-cafes` | Public data, kept out of git because it is a cache |
| `opendata/toronto-centreline-tcl.geojson` | Toronto Centreline (TCL) in WGS84: every street, trail and walkway piece | City of Toronto Open Data (CKAN) | Open Government Licence – Toronto | `opendata.download` | Delete the file, then build | Public data, about 90 MB |
| `opendata/neighbourhoods.geojson` | Neighbourhood boundaries | City of Toronto Open Data (CKAN) | Open Government Licence – Toronto | `opendata.download` (through `privacy.forbidden_names`) | Delete the file, then build | Public data. Used only to decide which names a public page must never show |
| `opendata/*.source.json` | Provenance of each download: dataset title, resource name, last-modified date, download date | CKAN metadata | as above | `opendata.download` | Written with each download | Public. Files downloaded before these sidecars existed fall back to the file date |
| `privacy.json` | Privacy zones: real centres, radii and random shifts | Derived from `tracks.parquet` by `privacy.zones` | n/a | `cafes --public`, `publish` | Rewritten on every public build, keeping the saved shifts | **SECRET.** Reveals where runs start and finish. Never commit, publish or share it, and don't delete it, because that re-rolls the shifts |

## What reaches a page

Only a subset of this data is compiled into a page:

- runs inside the City of Toronto
- cafes within 150 m of a run (the largest search radius), after the exclusions (`src/cafe_tolerance/exclusions.py`)
- the centreline pieces near those cafes
- a short provenance record: sources, licences, dates and counts. It never contains coordinates,
  and the privacy check fails if any decimal with three or more places appears in it.

The page lists that provenance under **About this project → Data sources**.

Public builds are scrubbed first: the start and finish of each run are trimmed, privacy zones
are removed, and forbidden names are dropped. `publish` then decodes the data back out of the
page and refuses to publish anything that falls inside a private place. See the Privacy section
of the top-level README.

## Attribution

- Cafe data © OpenStreetMap contributors, available under the Open Database Licence (ODbL) 1.0:
  https://www.openstreetmap.org/copyright
- Contains information licensed under the Open Government Licence – Toronto:
  https://open.toronto.ca/open-data-licence/
- Run data: the author's own Garmin Connect activities. Not licensed for reuse.
- Map tiles on the page (not stored here) © Esri.

## Rebuilding from scratch

1. Set a Garmin Personal API key (see the top-level README). Never put it in this folder.
2. Run `fetch`, then `parse`.
3. Run `cafes`. On first use it downloads the cafes and the City datasets.
4. Run `publish`, for the public page only.
