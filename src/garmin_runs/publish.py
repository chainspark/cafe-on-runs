"""Build the public, privacy-safe site into docs/ (served by GitHub Pages from main:/docs).

Published: report (as index.html too), heatmap, most-run segments, and a 3D flythrough of each
owner-approved public race in race3d.RACES (full course, exempt from the coordinate scan).
Never published: the neighbourhood page, CSVs, data/ (raw GPS, privacy zones, weather cache).

Updating is manual: `python -m garmin_runs publish --fetch` pulls new activities with the
current Garmin Personal API key, rebuilds, and runs the privacy check. Review docs/, then commit
and push yourself.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import heatmap, privacy, race3d, report, segments

SITE = Path("docs")
PAGES = ["index.html", "report.html", "heatmap.html", "segment.html", ".nojekyll", *race3d.files()]


def _forbidden() -> list[str]:
    """Strings that must never appear on a public page."""
    words = ["neighbourhood.html", "Favourite neighbourhood"]
    try:  # the names of your most-run neighbourhoods would point at home
        from .report import _neighbourhoods
        ranking, _ = _neighbourhoods()
        if ranking is not None:
            words += ranking["name"].head(5).tolist()
    except Exception as e:
        print(f"  (couldn't load neighbourhood names for the check: {e})")
    return words


def publish(fetch_new: bool = False, out_dir: Path = SITE, trim_m: float = privacy.DEFAULT_TRIM_M) -> Path:
    if fetch_new:
        from . import fetch, parse
        fetch.fetch()
        parse.parse()

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in PAGES:  # only ever remove files this command generates
        (out_dir / name).unlink(missing_ok=True)

    heatmap.build(trim_m, out_dir=out_dir, public=True)
    segments.run(out_dir=out_dir, public=True)
    race3d.build(out_dir)  # before the report, which links to the race pages that exist
    report.build(out_dir=out_dir, public=True)
    shutil.copyfile(out_dir / "report.html", out_dir / "index.html")
    (out_dir / ".nojekyll").touch()  # serve files as-is, no Jekyll processing

    try:
        privacy.check(sorted(out_dir.glob("*.html")), _forbidden(), public_races=set(race3d.files()))
    except SystemExit:
        for name in PAGES:  # don't leave unsafe pages lying around to be committed
            (out_dir / name).unlink(missing_ok=True)
        raise
    print(f"\nPublic site ready in {out_dir}/ - open {out_dir / 'index.html'} to review, then commit and push.")
    return out_dir
