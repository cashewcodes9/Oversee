"""Live AIS collector: AISStream.io WebSocket -> the same tables as the batch pipeline.

Messages are buffered and flushed every `flush_seconds` through the same validation
rules and loader as batch files. One collector session is one source_runs row
(source 'aisstream'); every position it inserts carries that run_id.

AISStream is free (sign in with GitHub at https://aisstream.io, create a key under
Account), allows 3 connections per account, gives no uptime guarantee and does not
replay missed messages, so the collector reconnects with backoff.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from datetime import UTC, datetime

import pandas as pd
import websockets

from oversee.etl import load, transform
from oversee.etl.regions import BoundingBox

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
STREAM_SOURCE = "aisstream"
POSITION_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
)
STATIC_TYPE = "ShipStaticData"

logger = logging.getLogger(__name__)


def subscription(api_key: str, bbox: BoundingBox) -> dict:
    return {
        "APIKey": api_key,
        "BoundingBoxes": [[[bbox.min_lat, bbox.min_lon], [bbox.max_lat, bbox.max_lon]]],
        "FilterMessageTypes": [*POSITION_TYPES, STATIC_TYPE],
    }


def parse_time(value: str) -> datetime:
    """Parse AISStream's `2025-03-03 18:22:32.318353417 +0000 UTC` (Go format) as UTC."""
    stamp = value.split(" +")[0]
    if "." in stamp:
        whole, fraction = stamp.split(".")
        stamp = f"{whole}.{fraction[:6]}"
    return datetime.fromisoformat(stamp).replace(tzinfo=UTC)


def _text(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def parse_message(message: dict) -> tuple[str, dict] | None:
    """Turn one AISStream message into ('position' | 'static', row) in internal column names."""
    kind = message.get("MessageType")
    meta = message.get("MetaData") or {}
    body = (message.get("Message") or {}).get(kind) or {}
    if "MMSI" not in meta or "time_utc" not in meta:
        return None

    if kind in POSITION_TYPES:
        return "position", {
            "mmsi": meta["MMSI"],
            "ts": parse_time(meta["time_utc"]),
            "latitude": body.get("Latitude", meta.get("latitude")),
            "longitude": body.get("Longitude", meta.get("longitude")),
            "sog": body.get("Sog"),
            "cog": body.get("Cog"),
            "heading": body.get("TrueHeading"),
            "nav_status": body.get("NavigationalStatus"),
        }

    if kind == STATIC_TYPE:
        dims = body.get("Dimension") or {}
        length = (dims.get("A") or 0) + (dims.get("B") or 0)
        width = (dims.get("C") or 0) + (dims.get("D") or 0)
        imo = body.get("ImoNumber")
        return "static", {
            "mmsi": meta["MMSI"],
            "imo": f"IMO{imo}" if imo else None,
            "name": _text(body.get("Name")) or _text(meta.get("ShipName")),
            "call_sign": _text(body.get("CallSign")),
            "vessel_type": body.get("Type") or None,
            "length_m": length or None,
            "width_m": width or None,
        }
    return None


def batch_frames(
    positions: list[dict], statics: list[dict], bbox: BoundingBox
) -> tuple[pd.DataFrame, pd.DataFrame, Counter]:
    """Validate a buffered batch; returns (vessels, positions, dropped counts).

    Every vessel with a position gets a vessels row (attributes may be null) so the
    foreign key holds; the loader never overwrites known attributes with nulls.
    """
    frame = pd.DataFrame(positions, columns=transform.POSITION_COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame, counts = transform.validate_positions(frame, bbox)
    frame, track_counts = transform.clean_tracks(frame)
    counts.update(track_counts)

    static = pd.DataFrame(statics, columns=transform.VESSEL_COLUMNS).drop_duplicates(
        subset=["mmsi"], keep="last"
    )
    bare = pd.DataFrame({"mmsi": frame["mmsi"].unique()})
    vessels = pd.concat([static, bare[~bare["mmsi"].isin(static["mmsi"])]], ignore_index=True)
    return vessels[transform.VESSEL_COLUMNS], frame[transform.POSITION_COLUMNS], counts


class _Buffer:
    def __init__(self) -> None:
        self.positions: list[dict] = []
        self.statics: list[dict] = []

    def add(self, parsed: tuple[str, dict]) -> None:
        kind, row = parsed
        (self.positions if kind == "position" else self.statics).append(row)

    def take(self) -> tuple[list[dict], list[dict]]:
        taken = self.positions, self.statics
        self.positions, self.statics = [], []
        return taken


def _flush(buffer: _Buffer, bbox: BoundingBox, run: dict) -> None:
    positions, statics = buffer.take()
    if not positions and not statics:
        return
    vessels, frame, counts = batch_frames(positions, statics, bbox)
    inserted = load.load_ais(vessels, frame, run["run_id"])
    load.load_quality_log(run["run_id"], counts)
    run["rows_read"] = (run["rows_read"] or 0) + len(positions) + len(statics)
    run["rows_loaded"] = (run["rows_loaded"] or 0) + inserted
    logger.info(
        "live batch loaded",
        extra={"messages": len(positions) + len(statics), "positions_inserted": inserted},
    )


async def _collect(api_key: str, bbox: BoundingBox, run: dict, flush_seconds: float, deadline):
    buffer = _Buffer()
    backoff = 1
    last_flush = time.monotonic()

    def running() -> bool:
        return deadline is None or time.monotonic() < deadline

    try:
        while running():
            try:
                async with websockets.connect(AISSTREAM_URL, open_timeout=15) as ws:
                    await ws.send(json.dumps(subscription(api_key, bbox)))
                    logger.info("connected to AISStream")
                    backoff = 1
                    while running():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=flush_seconds)
                            message = json.loads(raw)
                            if "error" in message:
                                raise RuntimeError(f"AISStream refused the subscription: {message}")
                            if parsed := parse_message(message):
                                buffer.add(parsed)
                        except TimeoutError:
                            pass
                        if time.monotonic() - last_flush >= flush_seconds:
                            await asyncio.to_thread(_flush, buffer, bbox, run)
                            last_flush = time.monotonic()
            except (OSError, TimeoutError, websockets.ConnectionClosed) as exc:
                logger.warning(
                    "connection lost, reconnecting", extra={"error": str(exc), "wait_s": backoff}
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        # Runs on deadline, Ctrl+C (task cancelled) and errors alike, so no buffered data is lost
        _flush(buffer, bbox, run)


def collect(
    api_key: str, bbox: BoundingBox, minutes: float | None = None, flush_seconds: float = 60
) -> dict:
    """Run the collector until `minutes` pass (or forever) or Ctrl+C; returns run stats."""
    deadline = time.monotonic() + minutes * 60 if minutes else None
    with load.source_run(STREAM_SOURCE) as run:
        try:
            asyncio.run(_collect(api_key, bbox, run, flush_seconds, deadline))
        except KeyboardInterrupt:
            logger.info("stopped by user")
    return run
