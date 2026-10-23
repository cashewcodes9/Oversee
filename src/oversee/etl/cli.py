"""Command-line entry point for the ETL pipeline.

Examples:
    oversee-etl run --region socal --start 2025-03-03 --end 2025-03-16 --delete-raw
    oversee-etl stream --region socal                  # live AISStream feed, Ctrl+C to stop
    oversee-etl ports data/raw/wpi.csv
    oversee-etl download-ais 2025-03-03 2025-03-09
    oversee-etl ais data/raw/ais-2025-03-03.csv.zst --region socal
    oversee-etl lake-ingest 2025-03-03 2025-03-09 --delete-raw
    oversee-etl lake-load 2025-03-03 2025-03-09 --region socal
    oversee-etl aggregate
"""

import argparse
import logging
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from oversee.config import get_settings
from oversee.etl import aggregate, extract, pipeline, stream
from oversee.etl.lake import open_lake
from oversee.etl.regions import REGIONS
from oversee.log import setup_logging

logger = logging.getLogger("oversee.etl")


def cmd_run(args: argparse.Namespace) -> None:
    if args.end < args.start:
        sys.exit("--end must not be before --start")
    pipeline.run_window(
        args.start,
        args.end,
        REGIONS[args.region],
        refresh_ports=args.refresh_ports,
        delete_raw=args.delete_raw,
    )


def cmd_stream(args: argparse.Namespace) -> None:
    api_key = get_settings().aisstream_api_key
    if not api_key:
        sys.exit(
            "AISSTREAM_API_KEY is not set. Get a free key at https://aisstream.io "
            "(sign in with GitHub, then Account) and add it to .env"
        )
    stream.collect(api_key, REGIONS[args.region], args.minutes, args.flush_seconds)


def cmd_ports(args: argparse.Namespace) -> None:
    path = args.path or extract.download_wpi(refresh=True)
    pipeline.load_ports_file(path, force=args.force)


def cmd_download_ais(args: argparse.Namespace) -> None:
    day = args.start
    while day <= args.end:
        extract.download_ais_day(day)
        day += timedelta(days=1)


def cmd_ais(args: argparse.Namespace) -> None:
    bbox = REGIONS[args.region] if args.region else None
    pipeline.load_ais_file(args.path, bbox, force=args.force)


def _days(args: argparse.Namespace) -> Iterator[date]:
    if args.end < args.start:
        sys.exit("END must not be before START")
    day = args.start
    while day <= args.end:
        yield day
        day += timedelta(days=1)


def cmd_lake_ingest(args: argparse.Namespace) -> None:
    lake = open_lake()
    for day in _days(args):
        pipeline.ingest_ais_day(lake, day, delete_raw=args.delete_raw)


def cmd_lake_load(args: argparse.Namespace) -> None:
    lake = open_lake()
    for day in _days(args):
        pipeline.load_ais_day(lake, day, REGIONS[args.region], force=args.force)


def cmd_aggregate(args: argparse.Namespace) -> None:
    pipeline.rebuild_aggregates(
        aggregate.AggregateParams(
            radius_m=args.radius_km * 1000,
            max_sog_knots=args.max_sog,
            max_gap_hours=args.max_gap_hours,
            min_dwell_minutes=args.min_dwell_minutes,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversee-etl")
    sub = parser.add_subparsers(required=True)
    regions = sorted(REGIONS)

    p = sub.add_parser("run", help="Ports, AIS days START..END, then aggregates, end to end")
    p.add_argument("--region", choices=regions, required=True)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--refresh-ports", action="store_true", help="Re-download the World Port Index")
    p.add_argument(
        "--delete-raw", action="store_true", help="Delete each AIS file once it is in the lake"
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("stream", help="Collect live AIS from AISStream into the database")
    p.add_argument("--region", choices=regions, required=True)
    p.add_argument("--minutes", type=float, help="Stop after this many minutes (default: run on)")
    p.add_argument("--flush-seconds", type=float, default=60, help="Write to the DB this often")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("ports", help="Load the World Port Index (downloads it if no PATH)")
    p.add_argument("path", type=Path, nargs="?")
    p.add_argument("--force", action="store_true", help="Load even if this file was loaded")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("download-ais", help="Download MarineCadastre AIS daily files")
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.set_defaults(func=cmd_download_ais)

    p = sub.add_parser("ais", help="Load one AIS file")
    p.add_argument("path", type=Path)
    p.add_argument("--region", choices=regions, help="Keep only positions in region")
    p.add_argument("--force", action="store_true", help="Load even if this file was loaded")
    p.set_defaults(func=cmd_ais)

    p = sub.add_parser("lake-ingest", help="Download AIS days START..END into the Parquet lake")
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.add_argument(
        "--delete-raw", action="store_true", help="Delete each AIS file once it is in the lake"
    )
    p.set_defaults(func=cmd_lake_ingest)

    p = sub.add_parser("lake-load", help="Load AIS days START..END from the lake for a region")
    p.add_argument("start", type=date.fromisoformat)
    p.add_argument("end", type=date.fromisoformat)
    p.add_argument("--region", choices=regions, required=True)
    p.add_argument("--force", action="store_true", help="Load even if this file was loaded")
    p.set_defaults(func=cmd_lake_load)

    defaults = aggregate.DEFAULT_PARAMS
    p = sub.add_parser("aggregate", help="Build port calls and summary tables")
    p.add_argument(
        "--radius-km",
        type=float,
        default=defaults.radius_m / 1000,
        help="Max distance from a port to count as in port",
    )
    p.add_argument(
        "--max-sog",
        type=float,
        default=defaults.max_sog_knots,
        help="Max speed over ground (knots) to count as stationary",
    )
    p.add_argument(
        "--max-gap-hours",
        type=float,
        default=defaults.max_gap_hours,
        help="Reporting gap that splits one stay into two port calls",
    )
    p.add_argument(
        "--min-dwell-minutes",
        type=float,
        default=defaults.min_dwell_minutes,
        help="Discard stays shorter than this",
    )
    p.set_defaults(func=cmd_aggregate)

    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    args.func(args)


if __name__ == "__main__":
    main()
