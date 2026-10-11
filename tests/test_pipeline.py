"""End-to-end run on SYNTHETIC raw files written in the real export layouts.

The numbers in these files are random walks, not market data. The point is to prove the
pipeline's invariants: raw data stored untouched, sample cut applied, P&L reconciling across
book, desk and group in SQL, and nothing computed from inputs that are not there.
"""

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from euets_var.book import load_book_config
from euets_var.data import connect
from euets_var.pipeline import run

BOOK_CONFIG = Path(__file__).resolve().parents[1] / "config" / "book.yaml"

EUA_STALE = pd.Timestamp("2022-04-18")
EUA_SPLICE = pd.Timestamp("2022-06-15")
UKA_HOLIDAYS = pd.to_datetime(["2022-05-02", "2022-08-29"])
FX_HOLIDAYS = pd.to_datetime(["2022-04-15", "2022-05-02"])
SAMPLE_END = pd.Timestamp("2022-12-15")


def write_investing(path: Path, prices: pd.Series, rng: np.random.Generator) -> None:
    lines = ['﻿"Date","Price","Open","High","Low","Vol.","Change %"']
    for day, price in prices.sort_index(ascending=False).items():  # newest first, like the real export
        volume = f"{rng.uniform(0.1, 30):.2f}K" if rng.random() > 0.2 else ""
        lines.append(f'"{day:%m/%d/%Y}","{price:,.2f}","0","0","0","{volume}","0.00%"')
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def raw_dir(tmp_path) -> Path:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2021-01-04", "2022-12-30")
    n = len(idx)
    eua = pd.Series(60 * np.exp(rng.normal(0, 0.025, n).cumsum()), index=idx)
    uka = pd.Series(50 * np.exp(rng.normal(0, 0.027, n).cumsum()), index=idx)
    ttf = pd.Series(40 * np.exp(rng.normal(0, 0.050, n).cumsum()), index=idx)
    fx = pd.Series(0.86 * np.exp(rng.normal(0, 0.004, n).cumsum()), index=idx)

    eua.loc[EUA_STALE] = eua.loc[: EUA_STALE - pd.Timedelta(days=1)].iloc[-1]  # flat placeholder row
    eua.loc[EUA_SPLICE:] *= 1.03  # contract switch: a 3% level jump on the splice day
    uka = uka.loc["2021-05-20":].drop(UKA_HOLIDAYS)  # later start, UK-only holidays
    fx = fx.drop(FX_HOLIDAYS)

    write_investing(tmp_path / "eua_investing.csv", eua, rng)
    write_investing(tmp_path / "uka_investing.csv", uka, rng)
    write_investing(tmp_path / "ttf_front_investing.csv", ttf, rng)
    (tmp_path / "eurgbp_ecb.csv").write_text(
        '"DATE","TIME PERIOD","UK pound sterling/Euro (EXR.D.GBP.EUR.SP00.A)"\n'
        + "\n".join(f'"{d:%Y-%m-%d}","{d:%d %b %Y}","{v:.5f}"' for d, v in fx.items())
    )
    (tmp_path / "msr_events.csv").write_text("date,description\n2021-07-14,Fit for 55 package\n")
    return tmp_path


@pytest.fixture
def cfg() -> dict:
    cfg = copy.deepcopy(load_book_config(BOOK_CONFIG))
    cfg["sample"]["data_start"] = "2021-01-01"
    cfg["sample"]["data_end"] = SAMPLE_END.date()
    cfg["data"]["stale_rows"] = {"EUA": [EUA_STALE.date()]}
    cfg["data"]["excluded_returns"] = {"EUA": [EUA_SPLICE.date()]}
    return cfg


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


def test_pnl_is_populated_at_book_desk_and_group_level(con, cfg, raw_dir):
    summary = run(con, cfg, raw_dir)
    assert not summary["skipped"]
    levels = {(level, node) for level, node, *_ in summary["nodes"]}
    assert levels == {
        ("book", "EUA_OUTRIGHT"), ("book", "EUA_UKA_SPREAD"), ("book", "CARBON_GAS_RV"),
        ("desk", "COMPLIANCE"), ("desk", "TRADING"), ("group", "GROUP"),
    }


def test_pnl_reconciles_across_levels_in_sql(con, cfg, raw_dir):
    run(con, cfg, raw_dir)
    rows, unmatched, worst = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE children_pnl_eur IS NULL), MAX(ABS(difference_eur)) "
        "FROM v_pnl_reconciliation"
    ).fetchone()
    assert rows > 0 and unmatched == 0
    assert worst < 1e-6  # EUR, on notionals of tens of millions


def test_group_exists_only_on_dates_every_book_is_valued(con, cfg, raw_dir):
    run(con, cfg, raw_dir)
    first_group = con.execute("SELECT MIN(date) FROM pnl WHERE level = 'group'").fetchone()[0]
    first_spread = con.execute("SELECT MIN(date) FROM pnl WHERE node = 'EUA_UKA_SPREAD'").fetchone()[0]
    assert first_group == first_spread == pd.Timestamp("2021-05-21").date()  # first UKA return
    # After that, every node shares one calendar.
    counts = con.execute(
        "SELECT COUNT(DISTINCT n) FROM (SELECT COUNT(*) AS n FROM pnl WHERE date >= ? GROUP BY node)", [first_group]
    ).fetchone()[0]
    assert counts == 1


def test_cleaning_rules_are_applied_but_raw_rows_are_kept(con, cfg, raw_dir):
    run(con, cfg, raw_dir)
    # Raw table: everything as downloaded, including rows after the sample end and the stale row.
    assert con.execute("SELECT MAX(date) FROM prices").fetchone()[0] > SAMPLE_END.date()
    assert con.execute("SELECT COUNT(*) FROM prices WHERE instrument = 'EUA' AND date = ?", [EUA_STALE.date()]).fetchone()[0] == 1
    # Derived tables: cut at the sample end, no return on the stale, splice or UK-holiday dates.
    assert con.execute("SELECT MAX(date) FROM returns").fetchone()[0] == SAMPLE_END.date()
    assert con.execute("SELECT MAX(date) FROM pnl").fetchone()[0] == SAMPLE_END.date()
    for day in [EUA_STALE, EUA_SPLICE, *UKA_HOLIDAYS]:
        assert con.execute("SELECT COUNT(*) FROM returns WHERE date = ?", [day.date()]).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM pnl WHERE date = ?", [day.date()]).fetchone()[0] == 0
    # The days after the two UK holidays carry flagged multi-day returns.
    assert con.execute("SELECT COUNT(DISTINCT date) FROM returns WHERE eua_days > 1 AND date > '2021-05-20'").fetchone()[0] == 2


def test_splice_jump_does_not_reach_the_pnl(con, cfg, raw_dir):
    """With the splice day excluded, no EUA return should carry the artificial 3% jump."""
    run(con, cfg, raw_dir)
    cfg_unclean = copy.deepcopy(cfg)
    cfg_unclean["data"]["excluded_returns"] = {}
    other = connect(":memory:")
    run(other, cfg_unclean, raw_dir)
    q = "SELECT SUM(log_return) FROM returns WHERE factor = 'EUA'"
    clean, unclean = con.execute(q).fetchone()[0], other.execute(q).fetchone()[0]
    other.close()
    # The two runs differ by exactly the splice-day return, which holds the 3% jump plus that day's move.
    splice_return = unclean - clean
    assert abs(splice_return - np.log(1.03)) < 0.15
    assert con.execute("SELECT COUNT(*) FROM returns WHERE date = ?", [EUA_SPLICE.date()]).fetchone()[0] == 0


def test_rerun_is_idempotent(con, cfg, raw_dir):
    run(con, cfg, raw_dir)
    tables = ["prices", "fx_rates", "msr_events", "returns", "positions", "pnl_attribution", "pnl"]
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    total_before = con.execute("SELECT SUM(hypothetical_pnl_eur) FROM pnl").fetchone()[0]
    run(con, cfg, raw_dir)
    after = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after and all(n > 0 for n in after.values())
    assert con.execute("SELECT SUM(hypothetical_pnl_eur) FROM pnl").fetchone()[0] == pytest.approx(total_before)


def test_missing_files_are_reported_and_nothing_is_made_up(con, cfg, raw_dir):
    (raw_dir / "eurgbp_ecb.csv").unlink()
    (raw_dir / "ttf_front_investing.csv").unlink()
    summary = run(con, cfg, raw_dir)
    missing = {f["name"] for f in summary["files"] if f["status"] == "missing"}
    assert missing == {"EURGBP", "TTF"}
    nodes = {node for _, node, *_ in summary["nodes"]}
    assert nodes == {"EUA_OUTRIGHT", "COMPLIANCE"}  # only what the loaded data supports
    assert ("group", "GROUP") in summary["skipped"]


def test_evaluation_start_needs_enough_group_history(con, cfg, raw_dir):
    summary = run(con, cfg, raw_dir)
    hs = summary["eval_starts"]["hs"]
    n_group = con.execute("SELECT COUNT(*) FROM pnl WHERE level = 'group'").fetchone()[0]
    assert hs["n_days"] == n_group - hs["min_obs"]
    prior = con.execute("SELECT COUNT(*) FROM pnl WHERE level = 'group' AND date < ?", [hs["start"]]).fetchone()[0]
    assert prior == hs["min_obs"]  # exactly 250 returns strictly before the first forecast date
    assert summary["eval_starts"]["fhs_qrnn"]["start"] is None  # under 750 group returns in this toy sample
