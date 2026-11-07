"""Build the public, privacy-safe page into docs/ (index.html + .nojekyll).

Updating is manual: `python -m cafe_tolerance publish --fetch` pulls new activities, rebuilds the
scrubbed page and runs the privacy check (page text *and* the dataset compiled into the wasm).
Review docs/, then commit and push yourself. If the check fails, the generated files are deleted.
"""
from __future__ import annotations

from pathlib import Path

from . import cafes, privacy

SITE = Path("docs")
PAGES = ["index.html", ".nojekyll"]


def publish(fetch_new: bool = False, out_dir: Path = SITE, trim_m: float = privacy.DEFAULT_TRIM_M) -> Path:
    if fetch_new:
        from . import fetch, parse
        fetch.fetch()
        parse.parse()

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in PAGES:  # only ever remove files this command generates
        (out_dir / name).unlink(missing_ok=True)

    cafes.build(out_dir, public=True, trim_m=trim_m)
    (out_dir / ".nojekyll").touch()

    try:
        privacy.check(sorted(out_dir.glob("*.html")), privacy.forbidden_names())
    except SystemExit:
        for name in PAGES:  # don't leave an unsafe page lying around to be committed
            (out_dir / name).unlink(missing_ok=True)
        raise
    print(f"\nPublic page ready in {out_dir}/ - open {out_dir / 'index.html'} to review, then commit and push.")
    return out_dir
