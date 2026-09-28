"""Book definition: hierarchy, positions, hypothetical P&L vectors, FX split.

Implemented: reading the book config and writing the static reference tables
(instruments, hierarchy).
Planned (Session 1): positions per date, constant-notional portfolio returns,
book/desk/group P&L, and the UKA-leg FX decomposition.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml


def load_book_config(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def hierarchy_rows(cfg: dict) -> list[tuple[str, str, str]]:
    """(book, desk, entity) for every book."""
    return [(book, spec["desk"], cfg["group"]) for book, spec in cfg["books"].items()]


def instrument_rows(cfg: dict) -> list[tuple[str, str, float | None, str]]:
    """(instrument, currency, lot_size_t, description) for every instrument."""
    return [
        (name, spec["currency"], spec["lot_size_t"], spec["description"])
        for name, spec in cfg["instruments"].items()
    ]


def gross_notional(cfg: dict) -> float:
    """Sum of absolute EUR notionals across all books and legs (the VaR scaling base)."""
    return float(sum(abs(n) for spec in cfg["books"].values() for n in spec["positions"].values()))


def write_static_tables(con: duckdb.DuckDBPyConnection, cfg: dict) -> None:
    """Load instruments and hierarchy. Re-running replaces rows instead of duplicating them."""
    con.executemany("INSERT OR REPLACE INTO instruments VALUES (?, ?, ?, ?)", instrument_rows(cfg))
    con.executemany("INSERT OR REPLACE INTO hierarchy VALUES (?, ?, ?)", hierarchy_rows(cfg))
