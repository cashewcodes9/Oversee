from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "oversee"
    postgres_password: str = "oversee"
    postgres_db: str = "oversee"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Free key from https://aisstream.io (sign in with GitHub, then Account)
    aisstream_api_key: str | None = None

    # Raw data lake: a local directory or s3://bucket (see oversee.etl.lake)
    lake_root: str = "data/lake"
    # Endpoint and keys for an S3-compatible server such as the compose `lake` service;
    # leave unset for AWS S3, which then uses the standard AWS credential chain
    s3_endpoint: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = True
    s3_url_style: Literal["vhost", "path"] = "vhost"
    # DuckDB otherwise assumes 80% of physical RAM is free; past this cap it spills to disk
    duckdb_memory_limit: str = "2GB"
    duckdb_temp_dir: str = "data/tmp/duckdb"

    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
