from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog="cafe_tolerance",
                                description="GD&T tolerance analysis of runs past Toronto cafes (C++/WebAssembly).")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download activity summaries and run FIT files (Garmin Personal API)")
    f.add_argument("--limit", type=int, help="only list the N most recent activities")
    f.set_defaults(func=lambda a: __import__("cafe_tolerance.fetch", fromlist=["fetch"]).fetch(a.limit))

    pr = sub.add_parser("parse", help="decode FIT files into data/tracks.parquet")
    pr.set_defaults(func=lambda a: __import__("cafe_tolerance.parse", fromlist=["parse"]).parse())

    c = sub.add_parser("cafes", help="build the Cafe on Runs page (private by default)")
    c.add_argument("--public", action="store_true", help="privacy-scrubbed build (as published)")
    c.add_argument("--out", type=Path, help="output folder (default output/)")
    c.add_argument("--trim-meters", type=float, default=500, help="public build: hide the first/last N metres")
    c.add_argument("--refresh-cafes", action="store_true", help="re-download cafes from OpenStreetMap")
    c.set_defaults(func=lambda a: __import__("cafe_tolerance.cafes", fromlist=["build"]).build(
        a.out or Path("output"), a.public, a.trim_meters, a.refresh_cafes))

    pb = sub.add_parser("publish", help="build the public, privacy-checked page into docs/")
    pb.add_argument("--fetch", action="store_true", help="download new activities first")
    pb.add_argument("--trim-meters", type=float, default=500, help="hide the first/last N metres of every run")
    pb.add_argument("--out", type=Path, default=Path("docs"), help="output folder (default docs/)")
    pb.set_defaults(func=lambda a: __import__("cafe_tolerance.publish", fromlist=["publish"]).publish(
        a.fetch, a.out, a.trim_meters))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
