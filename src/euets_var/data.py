"""Data layer: DuckDB connection and schema, raw loading, returns, calendar alignment.

Implemented: database connection and schema creation.
Planned (Session 1): raw price and FX loading, log returns, roll handling,
alignment on the EUA trading calendar.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "sql" / "schema.sql"


def connect(db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open (or create) the DuckDB database. ':memory:' gives a throwaway database for tests."""
    return duckdb.connect(str(db_path))


def init_schema(con: duckdb.DuckDBPyConnection, schema_path: str | Path = SCHEMA_PATH) -> None:
    """Create all tables from sql/schema.sql. Safe to run more than once."""
    con.execute(Path(schema_path).read_text())
