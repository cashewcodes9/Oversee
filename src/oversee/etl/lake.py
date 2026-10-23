"""Raw data lake: one Parquet file per AIS day, written and queried with DuckDB.

The lake root is a local directory (default `data/lake`) or an S3 URI. Locally
`s3://oversee-lake` is served by the SeaweedFS `lake` service in docker-compose;
the same code works against AWS S3 by dropping the endpoint settings.

Layout: <root>/ais/date=YYYY-MM-DD/ais-YYYY-MM-DD.parquet holds every column of the
source file for all US waters, unfiltered. The SHA-256 and size of the source file
are stored in the Parquet key-value metadata, so a day loaded from the lake is
recognised as the same file that `oversee-etl ais` would load directly.
"""

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from oversee.config import Settings, get_settings
from oversee.etl import extract
from oversee.etl.regions import BoundingBox

# Same text columns as extract.AIS_TEXT_COLUMNS, so IMOs like "IMO1234567" stay strings
AIS_TEXT_TYPES = {"imo": "VARCHAR", "call_sign": "VARCHAR", "vessel_name": "VARCHAR"}
LAKE_CHUNK_VECTORS = 250  # DuckDB vectors are 2048 rows, so about 500k rows per chunk

logger = logging.getLogger(__name__)


def _literal(value: object) -> str:
    """SQL string literal; DuckDB does not accept parameters in COPY or CREATE SECRET."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class LakeFile:
    uri: str
    rows: int
    source_sha256: str
    source_bytes: int


@dataclass
class Lake:
    root: str
    con: duckdb.DuckDBPyConnection

    @property
    def is_remote(self) -> bool:
        return "://" in self.root

    def ais_day_uri(self, day: date) -> str:
        return f"{self.root.rstrip('/')}/ais/date={day.isoformat()}/ais-{day.isoformat()}.parquet"

    def ais_day(self, day: date) -> LakeFile | None:
        """Metadata of the stored day, or None if the day is not in the lake."""
        uri = self.ais_day_uri(day)
        # Glob the folder: on S3 a glob of the exact key raises on 404 instead of matching none
        folder = uri.rsplit("/", 1)[0]
        try:
            files = self.con.execute("SELECT file FROM glob(?)", [f"{folder}/*.parquet"]).fetchall()
        except duckdb.HTTPException as exc:
            if exc.status_code == 404:  # bucket not created yet; S3 servers create it on write
                return None
            raise
        if not any(f.replace("\\", "/") == uri.replace("\\", "/") for (f,) in files):
            return None
        rows = self.con.execute(
            "SELECT sum(num_rows) FROM parquet_file_metadata(?)", [uri]
        ).fetchone()[0]
        meta = {
            key.decode(): value.decode()
            for key, value in self.con.execute(
                "SELECT key, value FROM parquet_kv_metadata(?)", [uri]
            ).fetchall()
        }
        return LakeFile(uri, int(rows), meta["source_sha256"], int(meta["source_bytes"]))

    def ingest_ais_file(self, path: Path, day: date) -> LakeFile:
        """Convert one raw AIS file to Parquet in the lake, replacing that day if present."""
        sha, size = extract.file_fingerprint(path)
        uri = self.ais_day_uri(day)
        # Local writes go through a .part file like downloads; an S3 PUT is atomic already
        target = uri
        if not self.is_remote:
            Path(uri).parent.mkdir(parents=True, exist_ok=True)
            target = uri + ".part"
        types = ", ".join(f"{_literal(k)}: {_literal(v)}" for k, v in AIS_TEXT_TYPES.items())
        self.con.execute(
            f"""
            COPY (SELECT * FROM read_csv({_literal(path)}, types = {{{types}}}))
            TO {_literal(target)} (
                FORMAT parquet, COMPRESSION zstd,
                KV_METADATA {{source_sha256: {_literal(sha)}, source_bytes: {_literal(size)}}}
            )
            """
        )
        if target != uri:
            os.replace(target, uri)
        stored = self.ais_day(day)
        logger.info(
            "AIS day written to lake",
            extra={"uri": uri, "rows": stored.rows, "source_bytes": size},
        )
        return stored

    def read_ais_day(
        self,
        day: date,
        bbox: BoundingBox | None = None,
        chunk_vectors: int = LAKE_CHUNK_VECTORS,
    ) -> Iterator[pd.DataFrame]:
        """Stream a stored day in source column names, keeping only rows inside bbox.

        The region filter runs in DuckDB, so rows outside it never reach pandas.
        """
        where, params = "", [self.ais_day_uri(day)]
        if bbox is not None:
            where = "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
            params += [bbox.min_lat, bbox.max_lat, bbox.min_lon, bbox.max_lon]
        result = self.con.execute(f"SELECT * FROM read_parquet(?) {where}", params)
        while not (chunk := result.fetch_df_chunk(chunk_vectors)).empty:
            yield chunk


def open_lake(settings: Settings | None = None) -> Lake:
    """Connect DuckDB to the configured lake root, with S3 credentials when it is remote."""
    settings = settings or get_settings()
    con = duckdb.connect()
    # DuckDB creates the spill folder itself, but not its parents
    Path(settings.duckdb_temp_dir).parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit = {_literal(settings.duckdb_memory_limit)}")
    con.execute(f"SET temp_directory = {_literal(settings.duckdb_temp_dir)}")
    if settings.lake_root.startswith("s3://"):
        if settings.s3_access_key_id:
            options = {
                "KEY_ID": settings.s3_access_key_id,
                "SECRET": settings.s3_secret_access_key or "",
                "REGION": settings.s3_region,
                "URL_STYLE": settings.s3_url_style,
                "USE_SSL": str(settings.s3_use_ssl).lower(),
            }
            if settings.s3_endpoint:
                options["ENDPOINT"] = settings.s3_endpoint
            body = ", ".join(f"{k} {_literal(v)}" for k, v in options.items())
            con.execute(f"CREATE SECRET lake (TYPE s3, {body})")
        else:
            # AWS: environment variables, ~/.aws profile or instance role
            con.execute("CREATE SECRET lake (TYPE s3, PROVIDER credential_chain)")
    return Lake(settings.lake_root, con)
