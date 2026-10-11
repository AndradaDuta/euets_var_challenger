"""End-to-end run.

Current state (Session 1): builds the database, loads the raw files, computes returns on the
book calendar and writes hypothetical P&L at book, desk and group level with its attribution.
Later sessions add the models, backtests, liquidity, stress tests and the report.

Usage:
    python -m euets_var.pipeline --config config/book.yaml
"""

from __future__ import annotations

import argparse

import duckdb
import pandas as pd

from euets_var.book import gross_notional, leg_pnl, load_book_config, node_pnl, write_pnl_tables, write_static_tables
from euets_var.data import (
    EUA_DAYS,
    REPO_ROOT,
    build_returns,
    connect,
    evaluation_start,
    init_schema,
    init_views,
    load_raw,
    write_returns,
)


def run(con: duckdb.DuckDBPyConnection, cfg: dict, raw_dir: str | None = None) -> dict:
    """Run every implemented stage against an open connection. Returns a summary for printing and tests."""
    init_schema(con)
    init_views(con)
    write_static_tables(con, cfg)

    files = load_raw(con, cfg, raw_dir)
    loaded_prices = [f["name"] for f in files if f["kind"] == "prices" and f["status"] == "loaded"]
    reference = cfg["data"].get("reference_instrument", "EUA")
    if reference not in loaded_prices:
        write_pnl_tables(con, pd.DataFrame(), pd.DataFrame(), cfg)
        con.execute("DELETE FROM returns")
        return {"files": files, "returns": None, "skipped": {}, "nodes": [], "max_break": None, "eval_starts": {}}

    returns = build_returns(con, cfg)
    write_returns(con, returns)

    attribution, skipped_books = leg_pnl(returns, cfg)
    pnl, skipped_nodes = node_pnl(attribution, cfg)
    write_pnl_tables(con, attribution, pnl, cfg)

    nodes = con.execute(
        """
        SELECT level, node, COUNT(*) AS n_days, MIN(date) AS first_date, MAX(date) AS last_date
        FROM pnl
        GROUP BY level, node
        ORDER BY CASE level WHEN 'book' THEN 1 WHEN 'desk' THEN 2 ELSE 3 END, node
        """
    ).fetchall()
    max_break = con.execute("SELECT MAX(ABS(difference_eur)) FROM v_pnl_reconciliation").fetchone()[0]
    unmatched = con.execute("SELECT COUNT(*) FROM v_pnl_reconciliation WHERE children_pnl_eur IS NULL").fetchone()[0]

    # Evaluation start dates are computed from the group series, i.e. the full book.
    group_dates = pd.DatetimeIndex(pnl.loc[pnl["level"] == "group", "date"])
    eval_starts = {}
    for label, min_obs in (("hs", cfg["models"]["hs"]["window"]), ("fhs_qrnn", cfg["models"]["fhs"]["min_obs"])):
        start = evaluation_start(group_dates, min_obs)
        eval_starts[label] = {
            "min_obs": min_obs,
            "start": None if start is None else start.date(),
            "n_days": 0 if start is None else int((group_dates >= start).sum()),
        }

    return {
        "files": files,
        "returns": returns,
        "skipped": {**skipped_nodes, **{("book", b): r for b, r in skipped_books.items()}},
        "nodes": nodes,
        "max_break": max_break,
        "unmatched": unmatched,
        "eval_starts": eval_starts,
    }


def print_summary(summary: dict, cfg: dict, db_path: str) -> None:
    print(f"Database: {db_path}")
    print(f"Book: {len(cfg['books'])} books, gross notional EUR {gross_notional(cfg):,.0f}")
    print(f"Sample: {cfg['sample']['data_start']} to {cfg['sample']['data_end']}")

    print("\nRaw files")
    for f in summary["files"]:
        if f["status"] == "loaded":
            print(f"  {f['name']:<7} {f['file']:<26} {f['rows']:>5} rows  {f['first']} to {f['last']}")
        else:
            print(f"  {f['name']:<7} {f['file']:<26} MISSING")

    returns = summary["returns"]
    if returns is None:
        print("\nNo EUA prices loaded, so no returns or P&L were computed.")
        return

    factors = [c for c in returns.columns if c != EUA_DAYS]
    print(f"\nReturns: {len(returns)} calendar dates, {returns.index.min().date()} to {returns.index.max().date()}")
    for factor in factors:
        r = returns[factor].dropna()
        if r.empty:
            continue
        print(f"  {factor:<7} {len(r):>5} returns from {r.index.min().date()}")
    print(f"  multi-day returns (non-common days dropped): {int((returns[EUA_DAYS] > 1).sum())}")
    removed = {f: int(returns.loc[returns[f].first_valid_index():, f].isna().sum()) for f in factors}
    removed = {f: n for f, n in removed.items() if n}
    if removed:
        print("  returns removed on contract-switch days: " + ", ".join(f"{f} {n}" for f, n in removed.items()))

    print("\nHypothetical P&L")
    if not summary["nodes"]:
        print("  none written")
    for level, node, n_days, first, last in summary["nodes"]:
        print(f"  {level:<6} {node:<16} {n_days:>5} days  {first} to {last}")
    if summary["max_break"] is not None:
        print(f"  reconciliation: largest break across levels EUR {summary['max_break']:.6f}")
    if summary.get("unmatched"):
        print(f"  WARNING: {summary['unmatched']} P&L rows have no children to reconcile against")

    if summary["skipped"]:
        print("\nNot computed")
        for (level, node), reason in summary["skipped"].items():
            print(f"  {level:<6} {node:<16} {reason}")

    print("\nFull-book evaluation start (computed from the group series)")
    for label, info in summary["eval_starts"].items():
        if info["start"] is None:
            print(f"  {label:<9} needs {info['min_obs']} prior returns: not available yet")
        else:
            print(f"  {label:<9} needs {info['min_obs']} prior returns: {info['start']} ({info['n_days']} evaluation days)")

    missing = [f["file"] for f in summary["files"] if f["status"] == "missing"]
    if missing:
        print(
            "\nPROVISIONAL: missing " + ", ".join(missing) + ". The calendar is built from the files present, "
            "so dates and P&L can change once the missing files are added."
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="euets_var_challenger pipeline")
    parser.add_argument("--config", required=True, help="path to config/book.yaml")
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "euets.duckdb"), help="DuckDB file")
    parser.add_argument("--raw-dir", default=None, help="folder with the raw CSV files (default: data.raw_dir)")
    args = parser.parse_args(argv)

    cfg = load_book_config(args.config)
    con = connect(args.db)
    try:
        summary = run(con, cfg, args.raw_dir)
    finally:
        con.close()
    print_summary(summary, cfg, args.db)


if __name__ == "__main__":
    main()
