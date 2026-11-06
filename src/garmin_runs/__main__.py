from __future__ import annotations

import argparse
import io
import zipfile

from .api import Garmin, is_run


def cmd_latest(args: argparse.Namespace) -> None:
    """Find the most recent run, print its summary, and check its FIT download."""
    with Garmin() as g:
        run = next((a for a in g.iter_activities(page_size=50) if is_run(a)), None)
        if run is None:
            print("No running activities found.")
            return
        km = (run.get("distance") or 0) / 1000
        secs = run.get("duration") or 0
        pace = secs / km / 60 if km else 0
        print(f"Most recent run: {run.get('activityName')} (id {run['activityId']})")
        print(f"  type:     {run['activityType']['typeKey']}")
        print(f"  start:    {run.get('startTimeLocal')}")
        print(f"  distance: {km:.2f} km")
        print(f"  duration: {int(secs // 60)}:{int(secs % 60):02d}")
        if pace:
            print(f"  pace:     {int(pace)}:{int(pace % 1 * 60):02d} /km")
        print(f"  has GPS:  {run.get('hasPolyline', run.get('startLatitude') is not None)}")
        if args.download:
            data = g.download_activity(run["activityId"])
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                print(f"  download: {len(data):,} bytes, files: {z.namelist()}")


def main() -> None:
    p = argparse.ArgumentParser(prog="garmin-runs")
    sub = p.add_subparsers(dest="cmd", required=True)
    latest = sub.add_parser("latest", help="show the most recent running activity")
    latest.add_argument("--download", action="store_true", help="also test the FIT download")
    latest.set_defaults(func=cmd_latest)

    f = sub.add_parser("fetch", help="download activity summaries and run FIT files")
    f.add_argument("--limit", type=int, help="only list the N most recent activities")
    f.set_defaults(func=lambda a: __import__("garmin_runs.fetch", fromlist=["fetch"]).fetch(a.limit))

    pr = sub.add_parser("parse", help="decode FIT files into data/tracks.parquet")
    pr.set_defaults(func=lambda a: __import__("garmin_runs.parse", fromlist=["parse"]).parse())

    hm = sub.add_parser("map", help="build output/heatmap.html")
    hm.add_argument("--trim-meters", type=float, default=0,
                    help="hide the first/last N metres of each run (privacy)")
    hm.set_defaults(func=lambda a: __import__("garmin_runs.heatmap", fromlist=["build"]).build(a.trim_meters))

    rp = sub.add_parser("report", help="build output/report.html")
    rp.set_defaults(func=lambda a: __import__("garmin_runs.report", fromlist=["build"]).build())

    sg = sub.add_parser("segment", help="find your most-run segment and time every pass")
    sg.add_argument("--length", type=float, default=1000, help="segment length in metres")
    sg.add_argument("--years", type=float, default=2, help="look back this many years")
    sg.add_argument("--top", type=int, default=5, help="how many non-overlapping segments to report")
    sg.set_defaults(func=lambda a: __import__("garmin_runs.segments", fromlist=["run"]).run(a.length, a.years, a.top))

    ar = sub.add_parser("area", help="graph of every run + animated quarterly runnable area in Toronto")
    ar.add_argument("--buffer", type=float, default=100,
                    help="metres either side of a run street that count as runnable")
    ar.add_argument("--trim-meters", type=float, default=0,
                    help="ignore the first/last N metres of each run (privacy)")
    ar.set_defaults(func=lambda a: __import__("garmin_runs.runarea", fromlist=["build"]).build(a.buffer, a.trim_meters))

    nb = sub.add_parser("neighbourhood", help="favourite Toronto neighbourhood and the streets you avoid in it")
    nb.add_argument("--name", help="analyse this neighbourhood instead of the favourite (exact City name)")
    nb.set_defaults(func=lambda a: __import__("garmin_runs.neighbourhood", fromlist=["build"]).build(a.name))

    pb = sub.add_parser("publish", help="build the public, privacy-safe site into docs/ (for GitHub Pages)")
    pb.add_argument("--fetch", action="store_true", help="download new activities first (uses your current API key)")
    pb.add_argument("--trim-meters", type=float, default=500, help="hide the first/last N metres of every run")
    pb.add_argument("--out", default="docs", help="output folder (default docs/, served by GitHub Pages)")
    pb.set_defaults(func=lambda a: __import__("garmin_runs.publish", fromlist=["publish"]).publish(
        a.fetch, __import__("pathlib").Path(a.out), a.trim_meters))

    al = sub.add_parser("all",help="fetch, parse, map, report")
    al.add_argument("--trim-meters", type=float, default=0)

    def _all(a):
        from . import fetch, heatmap, parse, report
        fetch.fetch()
        parse.parse()
        heatmap.build(a.trim_meters)
        report.build()

    al.set_defaults(func=_all)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
