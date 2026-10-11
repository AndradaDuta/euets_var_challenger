"""Data layer: DuckDB connection and schema, raw loading, cleaning, calendar and returns.

Flow
----
1. ``load_raw``         raw CSV files -> ``prices``, ``fx_rates``, ``msr_events`` (stored untouched)
2. ``read_prices``      raw prices -> wide frame, cut at the sample end, stale rows removed
3. ``book_calendar``    the dates on which the whole book can be valued
4. ``compute_returns``  log returns per risk factor on that calendar
5. ``write_returns``    -> ``returns``

Conventions
-----------
* Risk-factor returns are daily log returns, ``r_t = ln(P_t / P_{t-1})``, between consecutive
  calendar dates. A return dated t uses prices up to and including t only.
* Prices are never forward-filled. FX is the only series that may be forward-filled.
* FX is stored as quoted by the ECB (``EURGBP`` = GBP per 1 EUR). The risk factor is the
  inverse, ``GBPEUR`` = EUR per 1 GBP, so a positive return means the foreign currency gained.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "sql" / "schema.sql"
VIEWS_DIR = REPO_ROOT / "sql" / "views"

EUA_DAYS = "eua_days"  # column: reference-calendar trading days covered by each return

_VOLUME_MULTIPLIER = {"K": 1e3, "M": 1e6, "B": 1e9}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def connect(db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open (or create) the DuckDB database. ':memory:' gives a throwaway database for tests."""
    return duckdb.connect(str(db_path))


def init_schema(con: duckdb.DuckDBPyConnection, schema_path: str | Path = SCHEMA_PATH) -> None:
    """Create all tables from sql/schema.sql. Safe to run more than once."""
    con.execute(Path(schema_path).read_text())


def init_views(con: duckdb.DuckDBPyConnection, views_dir: str | Path = VIEWS_DIR) -> None:
    """Create or replace every view defined in sql/views/*.sql."""
    for path in sorted(Path(views_dir).glob("*.sql")):
        con.execute(path.read_text())


def insert_frame(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame, sql: str, params: list | None = None) -> None:
    """Run an ``INSERT ... SELECT ... FROM incoming`` statement against a pandas frame."""
    con.register("incoming", frame)
    try:
        con.execute(sql, params or [])
    finally:
        con.unregister("incoming")


# ---------------------------------------------------------------------------
# Parsing raw files
# ---------------------------------------------------------------------------


def _parse_volume(text: object) -> float:
    """'23.62K' -> 23620.0; '' or '-' -> NaN (the source has no volume for that day)."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return np.nan
    s = str(text).strip().replace(",", "")
    if s in ("", "-"):
        return np.nan
    suffix = s[-1].upper()
    if suffix in _VOLUME_MULTIPLIER:
        return float(s[:-1]) * _VOLUME_MULTIPLIER[suffix]
    return float(s)


def parse_investing_csv(path: str | Path, date_format: str = "%m/%d/%Y") -> pd.DataFrame:
    """Read an Investing.com 'Historical Data' export.

    Returns columns ``date``, ``settle`` and ``volume``, sorted by date. ``settle`` is the
    source's 'Price' column, i.e. the closing price it publishes, which is not guaranteed to
    equal the official exchange settlement. ``volume`` is in lots, NaN where the source is blank.

    The date format is explicit on purpose: a file exported with day-first dates fails loudly
    instead of being silently misread.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    missing = {"Date", "Price"} - set(raw.columns)
    if missing:
        raise ValueError(f"{path}: not an Investing.com export, missing column(s) {sorted(missing)}")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["Date"].str.strip(), format=date_format),
            "settle": pd.to_numeric(raw["Price"].str.replace(",", "", regex=False), errors="raise"),
            "volume": raw["Vol."].map(_parse_volume) if "Vol." in raw.columns else np.nan,
        }
    )
    out = out.sort_values("date").reset_index(drop=True)
    if out["date"].duplicated().any():
        dupes = out.loc[out["date"].duplicated(), "date"].dt.date.tolist()
        raise ValueError(f"{path}: duplicate dates {dupes[:5]}")
    if out["settle"].isna().any() or (out["settle"] <= 0).any():
        raise ValueError(f"{path}: missing or non-positive prices")
    return out


def parse_ecb_csv(path: str | Path) -> pd.DataFrame:
    """Read an ECB Data Portal export of one daily reference-rate series.

    Handles the two layouts the portal produces: the 'Download CSV' button
    (``DATE, TIME PERIOD, <series name>``) and the SDMX API (``..., TIME_PERIOD, OBS_VALUE, ...``).
    Returns columns ``date`` and ``rate``. Days without a fixing (TARGET holidays) are dropped;
    filling them is the job of ``compute_returns``, not of the loader.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]

    date_col = next((c for c in ("DATE", "TIME_PERIOD") if c in raw.columns), None)
    if "OBS_VALUE" in raw.columns:
        value_col = "OBS_VALUE"
    else:
        candidates = [c for c in raw.columns if c not in ("DATE", "TIME PERIOD", "TIME_PERIOD")]
        value_col = candidates[0] if len(candidates) == 1 else None
    if date_col is None or value_col is None:
        raise ValueError(f"{path}: unrecognised ECB export layout, columns {list(raw.columns)}")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_col].str.strip(), format="%Y-%m-%d"),
            "rate": pd.to_numeric(raw[value_col].str.strip(), errors="coerce"),
        }
    )
    out = out.dropna(subset=["rate"]).sort_values("date").reset_index(drop=True)
    if out.empty:
        raise ValueError(f"{path}: no numeric observations found")
    if out["date"].duplicated().any():
        raise ValueError(f"{path}: duplicate dates")
    if (out["rate"] <= 0).any():
        raise ValueError(f"{path}: non-positive FX rates")
    return out


def parse_msr_csv(path: str | Path) -> pd.DataFrame:
    """Read the MSR event list: columns ``date`` (YYYY-MM-DD) and ``description``."""
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    missing = {"date", "description"} - set(raw.columns)
    if missing:
        raise ValueError(f"{path}: missing column(s) {sorted(missing)}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["date"].str.strip(), format="%Y-%m-%d"),
            "description": raw["description"].str.strip(),
        }
    ).sort_values("date").reset_index(drop=True)
    if out["date"].duplicated().any():
        raise ValueError(f"{path}: duplicate event dates")
    return out


_PRICE_PARSERS = {"investing": parse_investing_csv}
_FX_PARSERS = {"ecb": parse_ecb_csv}

# Sanity ranges per FX pair, to catch an inverted or wrong series before it reaches the P&L.
_FX_PLAUSIBLE_RANGE = {"EURGBP": (0.5, 1.2)}


# ---------------------------------------------------------------------------
# Loading raw files into DuckDB
# ---------------------------------------------------------------------------


def load_raw(con: duckdb.DuckDBPyConnection, cfg: dict, raw_dir: str | Path | None = None) -> list[dict]:
    """Rebuild ``prices``, ``fx_rates`` and ``msr_events`` from the files in the raw folder.

    Every row of every file is stored as downloaded: no date cut, no cleaning. The three tables
    are emptied first, so the database always mirrors the raw folder. A file that is not there
    is reported as missing and skipped; nothing is invented in its place.

    Returns one status record per expected file, for the pipeline to print.
    """
    data_cfg = cfg["data"]
    folder = Path(raw_dir) if raw_dir is not None else REPO_ROOT / data_cfg["raw_dir"]
    status: list[dict] = []

    def record(kind: str, name: str, file: str, frame: pd.DataFrame | None) -> None:
        entry = {"kind": kind, "name": name, "file": file, "status": "missing", "rows": 0, "first": None, "last": None}
        if frame is not None:
            entry.update(
                status="loaded",
                rows=len(frame),
                first=frame["date"].min().date(),
                last=frame["date"].max().date(),
            )
        status.append(entry)

    con.execute("DELETE FROM prices")
    for instrument, spec in data_cfg["prices"].items():
        path = folder / spec["file"]
        if not path.exists():
            record("prices", instrument, spec["file"], None)
            continue
        frame = _PRICE_PARSERS[spec["format"]](path)
        insert_frame(
            con,
            frame,
            "INSERT INTO prices SELECT CAST(date AS DATE), ?, settle, volume, ? FROM incoming",
            [instrument, spec.get("source", spec["file"])],
        )
        record("prices", instrument, spec["file"], frame)

    con.execute("DELETE FROM fx_rates")
    for pair, spec in data_cfg.get("fx", {}).items():
        path = folder / spec["file"]
        if not path.exists():
            record("fx", pair, spec["file"], None)
            continue
        frame = _FX_PARSERS[spec["format"]](path)
        low, high = _FX_PLAUSIBLE_RANGE.get(pair, (0.0, np.inf))
        if frame["rate"].min() < low or frame["rate"].max() > high:
            raise ValueError(
                f"{path}: {pair} rates outside the plausible range [{low}, {high}]. "
                "Check that the series is quoted the right way round."
            )
        insert_frame(con, frame, "INSERT INTO fx_rates SELECT CAST(date AS DATE), ?, rate FROM incoming", [pair])
        record("fx", pair, spec["file"], frame)

    con.execute("DELETE FROM msr_events")
    msr_spec = data_cfg.get("msr_events")
    if msr_spec:
        path = folder / msr_spec["file"]
        if path.exists():
            frame = parse_msr_csv(path)
            insert_frame(con, frame, "INSERT INTO msr_events SELECT CAST(date AS DATE), description FROM incoming")
            record("msr_events", "MSR", msr_spec["file"], frame)
        else:
            record("msr_events", "MSR", msr_spec["file"], None)

    return status


# ---------------------------------------------------------------------------
# Reading cleaned prices and FX back out
# ---------------------------------------------------------------------------


def _as_timestamps(dates: Iterable | None) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(list(dates or [])))


def sample_bounds(cfg: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(data_start, data_end) from the config."""
    sample = cfg["sample"]
    return pd.Timestamp(sample["data_start"]), pd.Timestamp(sample["data_end"])


def read_prices(con: duckdb.DuckDBPyConnection, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Prices and volumes in wide form (dates x instruments), restricted to the sample.

    Rows listed under ``data.stale_rows`` are removed here. Nothing is filled: a date on which
    an instrument has no price stays NaN.
    """
    start, end = sample_bounds(cfg)
    long = con.execute(
        "SELECT date, instrument, settle, volume FROM prices WHERE date BETWEEN ? AND ? ORDER BY date",
        [start.date(), end.date()],
    ).df()
    long["date"] = pd.to_datetime(long["date"])

    order = [name for name in cfg["instruments"] if name in set(long["instrument"])]
    settle = long.pivot(index="date", columns="instrument", values="settle").reindex(columns=order)
    volume = long.pivot(index="date", columns="instrument", values="volume").reindex(columns=order)
    settle.columns.name = volume.columns.name = None

    for instrument, dates in (cfg["data"].get("stale_rows") or {}).items():
        if instrument not in settle.columns:
            continue
        stale = _as_timestamps(dates).intersection(settle.index)  # a row the source no longer prints is fine
        settle.loc[stale, instrument] = np.nan
        volume.loc[stale, instrument] = np.nan

    settle = settle.dropna(how="all")
    return settle, volume.reindex(settle.index)


def read_fx(con: duckdb.DuckDBPyConnection, cfg: dict) -> pd.DataFrame:
    """FX rates in wide form (dates x pairs) up to the sample end. Empty frame if none are loaded.

    Rates before the sample start are kept so that the first calendar date can be filled
    from the latest earlier fixing.
    """
    _, end = sample_bounds(cfg)
    long = con.execute("SELECT date, pair, rate FROM fx_rates WHERE date <= ? ORDER BY date", [end.date()]).df()
    if long.empty:
        return pd.DataFrame()
    long["date"] = pd.to_datetime(long["date"])
    wide = long.pivot(index="date", columns="pair", values="rate")
    wide.columns.name = None
    return wide


# ---------------------------------------------------------------------------
# Calendar and returns
# ---------------------------------------------------------------------------


def book_calendar(settle: pd.DataFrame, reference: str = "EUA") -> pd.DatetimeIndex:
    """The dates on which the whole book can be valued.

    A date is kept when every instrument that has started trading has a price on it. An
    instrument 'has started' from its first available price, so before the UK ETS existed the
    missing UKA price does not remove any dates; afterwards a UK-only holiday does. The
    reference instrument must always have a price, which anchors the calendar on EUA trading days.

    One calendar is used for every node of the hierarchy, so book, desk and group P&L always
    cover the same interval and add up row by row.
    """
    if reference not in settle.columns:
        raise ValueError(f"reference instrument {reference!r} has no prices loaded")
    has_price = settle.notna()
    started = has_price.cummax()
    keep = (has_price | ~started).all(axis=1) & has_price[reference]
    return settle.index[keep]


def fx_factor_name(pair: str) -> str:
    """'EURGBP' (GBP per 1 EUR) -> 'GBPEUR' (EUR per 1 GBP), the factor a GBP position is exposed to."""
    return pair[3:] + pair[:3]


def compute_returns(
    settle: pd.DataFrame,
    fx: pd.DataFrame | None = None,
    excluded: Mapping[str, Iterable] | None = None,
    reference: str = "EUA",
) -> pd.DataFrame:
    """Daily log returns per risk factor on the book calendar.

    Columns: one per instrument (return in the instrument's own currency), one per FX factor
    (e.g. ``GBPEUR``), and ``eua_days``, the number of reference-calendar trading days the return
    covers. ``eua_days > 1`` marks a multi-day return, created when a non-common day was dropped.

    ``excluded`` maps a factor to dates whose return must not be used (contract-splice days).
    The return covering such a date is removed for every factor, so the book has no P&L that day.
    The following day's return is unaffected: it compares two prices of the new contract.
    """
    calendar = book_calendar(settle, reference)
    prices = settle.loc[calendar]
    returns = np.log(prices).diff()

    if fx is not None and not fx.empty:
        for pair in fx.columns:
            rate = fx[pair].dropna()
            # Forward-fill FX only: carry the latest fixing onto calendar dates without one.
            on_calendar = rate.reindex(rate.index.union(calendar)).ffill().reindex(calendar)
            returns[fx_factor_name(pair)] = -np.log(on_calendar).diff()

    reference_dates = settle.index[settle[reference].notna()]
    position = pd.Series(reference_dates.get_indexer(calendar), index=calendar)
    returns[EUA_DAYS] = position.diff()

    returns = returns.iloc[1:].copy()  # the first calendar date has no previous price
    returns[EUA_DAYS] = returns[EUA_DAYS].astype(int)

    to_drop = []
    for factor, dates in (excluded or {}).items():
        if factor not in settle.columns:
            continue
        for day in _as_timestamps(dates):
            if day not in settle.index or np.isnan(settle.at[day, factor]):
                raise ValueError(f"excluded_returns for {factor}: no price on {day.date()}")
            # The return that covers `day` is the first calendar return dated on or after it.
            covering = returns.index[returns.index >= day]
            if len(covering):
                to_drop.append(covering[0])
    return returns.drop(index=pd.DatetimeIndex(to_drop).unique())


def write_returns(con: duckdb.DuckDBPyConnection, returns: pd.DataFrame) -> None:
    """Replace the ``returns`` table with the given frame (long format, NaN rows skipped)."""
    factors = [c for c in returns.columns if c != EUA_DAYS]
    long = (
        returns.rename_axis("date")
        .reset_index()
        .melt(id_vars=["date", EUA_DAYS], value_vars=factors, var_name="factor", value_name="log_return")
        .dropna(subset=["log_return"])
    )
    con.execute("DELETE FROM returns")
    insert_frame(
        con,
        long,
        f"INSERT INTO returns SELECT CAST(date AS DATE), factor, log_return, {EUA_DAYS} FROM incoming",
    )


def build_returns(con: duckdb.DuckDBPyConnection, cfg: dict) -> pd.DataFrame:
    """Read cleaned prices and FX from the database and compute the returns frame."""
    settle, _ = read_prices(con, cfg)
    return compute_returns(
        settle,
        read_fx(con, cfg),
        excluded=cfg["data"].get("excluded_returns"),
        reference=cfg["data"].get("reference_instrument", "EUA"),
    )


def evaluation_start(dates: pd.DatetimeIndex, min_obs: int) -> pd.Timestamp | None:
    """First date that can be forecast using at least ``min_obs`` earlier observations.

    ``dates`` are the dates on which the portfolio has a return. The forecast for date t uses
    returns up to t-1 only, so the first forecastable date is the one with ``min_obs`` returns
    strictly before it. Returns None if the history is too short.
    """
    dates = pd.DatetimeIndex(dates).sort_values()
    return dates[min_obs] if len(dates) > min_obs else None
