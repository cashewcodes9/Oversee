"""Command-line entry point for the ETL pipeline.

Examples:
    oversee-etl ports data/raw/UpdatedPub150.csv
    oversee-etl download-ais 2024-01-01 2024-01-14
    oversee-etl ais data/raw/AIS_2024_01_01.zip --region socal
    oversee-etl aggregate
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

from oversee.db import get_engine
from oversee.etl import aggregate, extract, load, transform
from oversee.etl.regions import REGIONS


def cmd_ports(args: argparse.Namespace) -> None:
    with load.source_run("wpi", args.path.name) as run:
        raw = extract.read_wpi(args.path)
        ports = transform.clean_wpi(raw)
        run["rows_read"] = len(raw)
        run["rows_loaded"] = load.load_ports(ports)
    print(f"Loaded {run['rows_loaded']} ports")


def cmd_download_ais(args: argparse.Namespace) -> None:
    day = args.start
    while day <= args.end:
        print(f"Downloading {day} ...")
        print(f"  -> {extract.download_ais_day(day)}")
        day += timedelta(days=1)


def cmd_ais(args: argparse.Namespace) -> None:
    bbox = REGIONS[args.region] if args.region else None
    with load.source_run("marinecadastre_ais", args.path.name) as run:
        raw = extract.read_ais(args.path)
        positions = transform.clean_ais(raw, bbox=bbox)
        vessels, positions = transform.split_vessels(positions)
        run["rows_read"] = len(raw)
        run["rows_loaded"] = load.load_ais(vessels, positions, run["run_id"])
    print(f"Loaded {run['rows_loaded']} positions for {len(vessels)} vessels")


def cmd_aggregate(args: argparse.Namespace) -> None:
    params = aggregate.AggregateParams(
        radius_m=args.radius_km * 1000,
        max_sog_knots=args.max_sog,
        max_gap_hours=args.max_gap_hours,
        min_dwell_minutes=args.min_dwell_minutes,
    )
    with load.source_run("aggregate") as run:
        with get_engine().begin() as conn:
            counts = aggregate.run(conn, params)
        run["rows_loaded"] = counts["port_calls"]
    for table, n in counts.items():
        print(f"{table}: {n} rows")


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversee-etl")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("ports", help="Load World Port Index CSV")
    p.add_argument("path", type=Path)
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("download-ais", help="Download MarineCadastre AIS daily files")
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.set_defaults(func=cmd_download_ais)

    p = sub.add_parser("ais", help="Load one AIS file")
    p.add_argument("path", type=Path)
    p.add_argument("--region", choices=sorted(REGIONS), help="Keep only positions in region")
    p.set_defaults(func=cmd_ais)

    defaults = aggregate.DEFAULT_PARAMS
    p = sub.add_parser("aggregate", help="Build port calls and summary tables")
    p.add_argument("--radius-km", type=float, default=defaults.radius_m / 1000,
                   help="Max distance from a port to count as in port")
    p.add_argument("--max-sog", type=float, default=defaults.max_sog_knots,
                   help="Max speed over ground (knots) to count as stationary")
    p.add_argument("--max-gap-hours", type=float, default=defaults.max_gap_hours,
                   help="Reporting gap that splits one stay into two port calls")
    p.add_argument("--min-dwell-minutes", type=float, default=defaults.min_dwell_minutes,
                   help="Discard stays shorter than this")
    p.set_defaults(func=cmd_aggregate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
