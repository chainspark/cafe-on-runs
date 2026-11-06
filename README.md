# garmin-runs

Personal running analytics from my own Garmin Connect data, pulled with the
[Garmin Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/).
The pages are static HTML, published with GitHub Pages from `docs/`.

- **Running report**: weekly distance, pace trend, best efforts, yearly totals
- **Route heatmap**: Toronto runs, one coloured heat layer per year
- **Most-run segments**: the 1 km stretches I run most, with every pass timed and
  matched to the hourly air temperature (Open-Meteo)

## Privacy

The public pages never show where I live:

- the first and last 500 m of every run are removed
- **privacy zones** are found automatically where many runs start or finish, and every GPS
  point inside them is removed (not just the starts). The zone centres are shifted at random,
  so the gaps don't point at an address
- `publish` checks every coordinate on the built pages and refuses to finish if any falls
  inside a private place

Raw data (`data/`), private pages (`output/`) and the neighbourhood analysis are never
committed or published.

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

Create a Personal API key at https://connect.garmin.com/app/settings/personalApiToken, then
provide it in **one** of these ways. Never commit it.

- environment variable `GARMIN_PERSONAL_API_KEY`
- file `~/.config/garmin/connect-personal-api-key`

## Updating the public pages (manual)

1. If the key has expired (you can choose 30 days to 1 year when creating it), create a new
   one and replace the env var or key file. The cached access token is checked against the
   key and renewed automatically.
2. Pull new activities and rebuild the public site:
   ```sh
   uv run python -m garmin_runs publish --fetch
   ```
   This downloads only activities that are new since last time, rebuilds `docs/`, and runs
   the privacy check.
3. Open `docs/index.html` and review it.
4. Commit and push `docs/`:
   ```sh
   git add docs && git commit -m "Update running pages" && git push
   ```

Nothing updates on its own. The site changes only when you push.

## Private commands

These write to `output/` (gitignored), for my own use:

| Command | Output |
|---|---|
| `fetch` / `parse` | download new activities / decode GPS into `data/tracks.parquet` |
| `report`, `map`, `segment` | the full, unscrubbed versions of the public pages |
| `neighbourhood` | favourite neighbourhood and the streets avoided in it (private only) |
| `area` | runnable area over time |
| `latest --download` | check that the API key works |

Run them with `uv run python -m garmin_runs <command>`.

## GitHub Pages

Once the repository is public: **Settings → Pages → Build and deployment → Deploy from a
branch → `main` / `docs`**.

Data: City of Toronto Open Data (neighbourhoods, centreline) under the Open Government Licence – Toronto.
Map tiles © Esri. Weather from Open-Meteo.
