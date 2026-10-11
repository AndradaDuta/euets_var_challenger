"""Checks on the project setup: database schema and book configuration."""

from pathlib import Path

import duckdb
import pytest

from euets_var.book import gross_notional, hierarchy_rows, load_book_config, write_static_tables
from euets_var.data import connect, init_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOK_CONFIG = REPO_ROOT / "config" / "book.yaml"

EXPECTED_TABLES = {
    "instruments", "hierarchy", "limits", "prices", "fx_rates", "msr_events", "returns",
    "positions", "pnl_attribution", "pnl", "risk_results", "stress_results",
}


@pytest.fixture
def con():
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def test_schema_creates_all_tables(con):
    tables = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tables == EXPECTED_TABLES


def test_schema_is_rerunnable(con):
    init_schema(con)  # second run must not fail


def test_prices_reject_duplicate_date_instrument(con):
    con.execute("INSERT INTO prices VALUES ('2024-01-02', 'EUA', 80.0, 1000, 'test')")
    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO prices VALUES ('2024-01-02', 'EUA', 81.0, 1000, 'test')")


def test_book_matches_spec():
    cfg = load_book_config(BOOK_CONFIG)
    assert gross_notional(cfg) == 45_000_000  # 20m + 10m + 10m + 5m
    desks = {book: desk for book, desk, _ in hierarchy_rows(cfg)}
    assert desks == {
        "EUA_OUTRIGHT": "COMPLIANCE",
        "EUA_UKA_SPREAD": "TRADING",
        "CARBON_GAS_RV": "TRADING",
    }


def test_every_position_uses_a_known_instrument():
    cfg = load_book_config(BOOK_CONFIG)
    known = set(cfg["instruments"])
    for spec in cfg["books"].values():
        assert set(spec["positions"]) <= known


def test_static_tables_load_without_duplicates(con):
    cfg = load_book_config(BOOK_CONFIG)
    write_static_tables(con, cfg)
    write_static_tables(con, cfg)  # re-running replaces, does not duplicate
    assert con.execute("SELECT COUNT(*) FROM hierarchy").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 3
