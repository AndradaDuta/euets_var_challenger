"""Book definition: hierarchy, positions, hypothetical P&L and its attribution.

P&L convention
--------------
The book is rebalanced every day to constant EUR notional. A leg with signed notional ``N``
earns ``N * R`` over one day, where ``R = P_t / P_{t-1} - 1`` is the SIMPLE return of its EUR
value. Risk-factor returns are stored as log returns, so ``R = exp(r) - 1``.

Simple returns are used for P&L because they are what a position actually earns and because
they add up across legs; log returns do not. On a +30% day a short position loses 30% of its
notional, not ln(1.30) = 26%.

A leg quoted in a foreign currency has EUR value ``price * fx``, hence

    1 + R_eur = (1 + R_price) * (1 + R_fx)
    N * R_eur = N * R_price  +  N * R_fx  +  N * R_price * R_fx
                  PRICE          FX              CROSS

The three components are stored separately and sum exactly to the leg's EUR P&L.

All P&L here is HYPOTHETICAL: yesterday's closing positions revalued at today's prices,
with no intraday trading, fees or new deals.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from euets_var.data import insert_frame

PRICE, FX, CROSS = "PRICE", "FX", "CROSS"
ATTRIBUTION_COLUMNS = ["date", "book", "instrument", "component", "pnl_eur"]
PNL_COLUMNS = ["date", "level", "node", "hypothetical_pnl_eur"]


# ---------------------------------------------------------------------------
# Configuration and static reference data
# ---------------------------------------------------------------------------


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


def node_gross_notional(cfg: dict) -> dict[tuple[str, str], float]:
    """Gross EUR notional for every node: {(level, node): sum of |N| over its legs}.

    Legs are not netted across books: the group holds EUA in two books and both count in full.
    """
    gross: dict[tuple[str, str], float] = {("group", cfg["group"]): 0.0}
    for book, spec in cfg["books"].items():
        book_gross = float(sum(abs(n) for n in spec["positions"].values()))
        gross[("book", book)] = book_gross
        gross[("desk", spec["desk"])] = gross.get(("desk", spec["desk"]), 0.0) + book_gross
        gross[("group", cfg["group"])] += book_gross
    return gross


def write_static_tables(con: duckdb.DuckDBPyConnection, cfg: dict) -> None:
    """Load instruments and hierarchy. Re-running replaces rows instead of duplicating them."""
    con.executemany("INSERT OR REPLACE INTO instruments VALUES (?, ?, ?, ?)", instrument_rows(cfg))
    con.executemany("INSERT OR REPLACE INTO hierarchy VALUES (?, ?, ?)", hierarchy_rows(cfg))


# ---------------------------------------------------------------------------
# Hypothetical P&L
# ---------------------------------------------------------------------------


def fx_factor_for(currency: str, base_currency: str) -> str | None:
    """Risk factor a leg in ``currency`` is exposed to, e.g. 'GBPEUR'. None for base-currency legs."""
    return None if currency == base_currency else f"{currency}{base_currency}"


def leg_pnl(returns: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict[str, str]]:
    """Hypothetical EUR P&L per book leg and component.

    ``returns`` is the frame from ``data.compute_returns`` (log returns per risk factor).

    A book is valued on a date only if every one of its risk factors has a return that day.
    A book whose inputs are not loaded at all is skipped and reported, never approximated.

    Returns ``(attribution, skipped)``: a long frame with columns
    ``date, book, instrument, component, pnl_eur``, and ``{book: reason}`` for skipped books.
    """
    base = cfg["base_currency"]
    frames: list[pd.DataFrame] = []
    skipped: dict[str, str] = {}

    for book, spec in cfg["books"].items():
        legs = []
        for instrument, notional in spec["positions"].items():
            legs.append((instrument, float(notional), fx_factor_for(cfg["instruments"][instrument]["currency"], base)))
        needed = [name for instrument, _, fx in legs for name in (instrument, fx) if name is not None]
        missing = [name for name in dict.fromkeys(needed) if name not in returns.columns]
        if missing:
            skipped[book] = f"no data loaded for {', '.join(missing)}"
            continue

        simple = np.expm1(returns[list(dict.fromkeys(needed))].dropna(how="any"))
        for instrument, notional, fx in legs:
            components = {PRICE: notional * simple[instrument]}
            if fx is not None:
                components[FX] = notional * simple[fx]
                components[CROSS] = notional * simple[instrument] * simple[fx]
            for component, series in components.items():
                frames.append(
                    pd.DataFrame(
                        {
                            "date": series.index,
                            "book": book,
                            "instrument": instrument,
                            "component": component,
                            "pnl_eur": series.to_numpy(),
                        }
                    )
                )

    if not frames:
        return pd.DataFrame(columns=ATTRIBUTION_COLUMNS), skipped
    return pd.concat(frames, ignore_index=True)[ATTRIBUTION_COLUMNS], skipped


def _sum_complete(wide: pd.DataFrame, columns: list[str]) -> pd.Series | None:
    """Row sum over ``columns``, kept only where every column has a value. None if a column is absent."""
    if any(c not in wide.columns for c in columns):
        return None
    return wide[columns].sum(axis=1, min_count=len(columns)).dropna()


def node_pnl(attribution: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    """Aggregate leg P&L to book, desk and group level.

    A desk or group row exists on a date only if all of its books are valued that day. A partial
    sum would look like a P&L but would silently leave out part of the book.

    Returns ``(pnl, skipped)``: a long frame with columns ``date, level, node,
    hypothetical_pnl_eur``, and ``{(level, node): reason}`` for nodes that could not be built.
    """
    books = list(cfg["books"])
    by_book = (
        attribution.groupby(["date", "book"])["pnl_eur"].sum().unstack("book")
        if not attribution.empty
        else pd.DataFrame(columns=[])
    )

    desks: dict[str, list[str]] = {}
    for book, spec in cfg["books"].items():
        desks.setdefault(spec["desk"], []).append(book)

    nodes: list[tuple[str, str, list[str]]] = [("book", book, [book]) for book in books]
    nodes += [("desk", desk, members) for desk, members in desks.items()]
    nodes.append(("group", cfg["group"], books))

    frames: list[pd.DataFrame] = []
    skipped: dict[tuple[str, str], str] = {}
    for level, node, members in nodes:
        series = _sum_complete(by_book, members)
        if series is None:
            absent = [b for b in members if b not in by_book.columns]
            skipped[(level, node)] = f"book(s) not valued: {', '.join(absent)}"
            continue
        frames.append(
            pd.DataFrame(
                {"date": series.index, "level": level, "node": node, "hypothetical_pnl_eur": series.to_numpy()}
            )
        )

    if not frames:
        return pd.DataFrame(columns=PNL_COLUMNS), skipped
    return pd.concat(frames, ignore_index=True)[PNL_COLUMNS], skipped


def portfolio_returns(pnl: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Daily portfolio return per node: hypothetical P&L divided by the node's gross notional.

    Wide frame, one column per node (book, desk and group names are distinct). This is the
    series the VaR models are fitted on; VaR in EUR is ``-quantile * gross notional``.
    It equals ``sum_i N_i * R_i / sum_i |N_i|`` with simple leg returns ``R_i``.
    """
    gross = node_gross_notional(cfg)
    names = [node for _, node in gross]
    if len(set(names)) != len(names):
        raise ValueError("book, desk and group names must be distinct")
    wide = pnl.pivot(index="date", columns="node", values="hypothetical_pnl_eur")
    wide.columns.name = None
    scale = pd.Series({node: gross[(level, node)] for level, node in gross})
    return wide.div(scale.reindex(wide.columns), axis=1)


# ---------------------------------------------------------------------------
# Writing to DuckDB
# ---------------------------------------------------------------------------


def write_pnl_tables(con: duckdb.DuckDBPyConnection, attribution: pd.DataFrame, pnl: pd.DataFrame, cfg: dict) -> None:
    """Replace ``positions``, ``pnl_attribution`` and ``pnl`` with the given results.

    ``positions`` gets the constant EUR notional of every leg on every date its book is valued.
    """
    con.execute("DELETE FROM positions")
    con.execute("DELETE FROM pnl_attribution")
    con.execute("DELETE FROM pnl")
    if attribution.empty:
        return

    notionals = pd.DataFrame(
        [
            (book, instrument, float(notional))
            for book, spec in cfg["books"].items()
            for instrument, notional in spec["positions"].items()
        ],
        columns=["book", "instrument", "notional_eur"],
    )
    positions = attribution[["date", "book", "instrument"]].drop_duplicates().merge(notionals, on=["book", "instrument"])

    insert_frame(
        con, positions, "INSERT INTO positions SELECT CAST(date AS DATE), book, instrument, notional_eur FROM incoming"
    )
    insert_frame(
        con,
        attribution,
        "INSERT INTO pnl_attribution SELECT CAST(date AS DATE), book, instrument, component, pnl_eur FROM incoming",
    )
    insert_frame(
        con, pnl, "INSERT INTO pnl SELECT CAST(date AS DATE), level, node, hypothetical_pnl_eur FROM incoming"
    )
