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

from oversee.etl import extract, load, transform
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
    # TODO: detect port calls (positions within a port radius at low speed),
    # derive vessel_routes, then rebuild port_activity_daily and route_summary.
    raise NotImplementedError("Aggregation step not implemented yet")


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

    p = sub.add_parser("aggregate", help="Build port calls and summary tables")
    p.set_defaults(func=cmd_aggregate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
