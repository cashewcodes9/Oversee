"""Vessel type groups, data quality log and file fingerprints on source runs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- AIS ship type codes (ITU-R M.1371) grouped for reporting
    CREATE TABLE vessel_type_groups (
        code_from   INTEGER PRIMARY KEY,
        code_to     INTEGER NOT NULL,
        group_name  TEXT    NOT NULL,
        CHECK (code_from <= code_to)
    );
    INSERT INTO vessel_type_groups (code_from, code_to, group_name) VALUES
        (20, 29, 'other'),        -- wing in ground
        (30, 30, 'fishing'),
        (31, 32, 'tug'),          -- towing
        (33, 35, 'service'),      -- dredging, diving, military
        (36, 37, 'pleasure'),     -- sailing, pleasure craft
        (40, 49, 'high_speed'),
        (50, 51, 'service'),      -- pilot, search and rescue
        (52, 52, 'tug'),
        (53, 59, 'service'),      -- port tender, anti-pollution, law enforcement, medical
        (60, 69, 'passenger'),
        (70, 79, 'cargo'),
        (80, 89, 'tanker'),
        (90, 99, 'other');

    ALTER TABLE vessels ADD COLUMN vessel_group TEXT NOT NULL DEFAULT 'unknown';
    UPDATE vessels v
    SET vessel_group = g.group_name
    FROM vessel_type_groups g
    WHERE v.vessel_type BETWEEN g.code_from AND g.code_to;

    -- Rows dropped per cleaning rule per run
    CREATE TABLE data_quality_log (
        run_id        INTEGER NOT NULL REFERENCES source_runs (run_id) ON DELETE CASCADE,
        rule          TEXT    NOT NULL,
        rows_dropped  INTEGER NOT NULL,
        PRIMARY KEY (run_id, rule)
    );

    -- Fingerprint of the loaded file, so an identical file is not loaded twice
    ALTER TABLE source_runs ADD COLUMN file_sha256 TEXT;
    ALTER TABLE source_runs ADD COLUMN file_bytes BIGINT;
    CREATE INDEX source_runs_loaded_file_idx
        ON source_runs (source, file_sha256) WHERE status = 'success';
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX source_runs_loaded_file_idx;
    ALTER TABLE source_runs DROP COLUMN file_bytes, DROP COLUMN file_sha256;
    DROP TABLE data_quality_log;
    ALTER TABLE vessels DROP COLUMN vessel_group;
    DROP TABLE vessel_type_groups;
    """)
