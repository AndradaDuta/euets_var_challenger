"""End-to-end run.

Current state: creates the database and loads the book's reference data.
Later sessions add price loading, P&L, models, backtests, liquidity, stress and the report.

Usage:
    python -m euets_var.pipeline --config config/book.yaml
"""

from __future__ import annotations

import argparse

from euets_var.book import gross_notional, load_book_config, write_static_tables
from euets_var.data import REPO_ROOT, connect, init_schema


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="euets_var_challenger pipeline")
    parser.add_argument("--config", required=True, help="path to config/book.yaml")
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "euets.duckdb"), help="DuckDB file")
    args = parser.parse_args(argv)

    cfg = load_book_config(args.config)
    con = connect(args.db)
    init_schema(con)
    write_static_tables(con, cfg)

    n_books = con.execute("SELECT COUNT(*) FROM hierarchy").fetchone()[0]
    print(f"Database ready at {args.db}")
    print(f"Loaded {n_books} books; gross notional EUR {gross_notional(cfg):,.0f}")
    print("Price loading, returns and P&L: not yet implemented (Session 1 in progress).")
    con.close()


if __name__ == "__main__":
    main()
