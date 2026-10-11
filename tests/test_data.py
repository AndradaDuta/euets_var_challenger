"""Raw-file parsing, calendar construction and returns."""

import numpy as np
import pandas as pd
import pytest

from euets_var.data import (
    EUA_DAYS,
    _parse_volume,
    book_calendar,
    compute_returns,
    evaluation_start,
    excluded_return_dates,
    front_month_roll_dates,
    parse_ecb_csv,
    parse_investing_csv,
    parse_msr_csv,
)

INVESTING_HEADER = '﻿"Date","Price","Open","High","Low","Vol.","Change %"\n'


def days(*labels: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(list(labels)))


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("23.62K", 23_620.0), ("0.04K", 40.0), ("1.5M", 1_500_000.0), ("2B", 2e9), ("350", 350.0), ("1,200", 1200.0)],
)
def test_volume_suffixes(text, expected):
    assert _parse_volume(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "-", None, float("nan")])
def test_blank_volume_is_missing(text):
    assert np.isnan(_parse_volume(text))


def test_parse_investing_csv(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text(
        INVESTING_HEADER
        + '"09/25/2026","1,086.78","86.69","87.75","86.18","23.62K","-0.21%"\n'
        + '"09/24/2026","86.96","86.00","87.00","85.63","","1.10%"\n',
        encoding="utf-8",
    )
    out = parse_investing_csv(path)
    assert out["date"].tolist() == [pd.Timestamp("2026-09-24"), pd.Timestamp("2026-09-25")]  # oldest first
    assert out["settle"].tolist() == [86.96, 1086.78]  # thousands separator removed
    assert np.isnan(out["volume"].iloc[0]) and out["volume"].iloc[1] == pytest.approx(23_620.0)


def test_parse_investing_rejects_day_first_dates(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text(INVESTING_HEADER + '"25/09/2026","86.78","1","1","1","1K","0%"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        parse_investing_csv(path)


def test_parse_investing_rejects_duplicate_dates(tmp_path):
    path = tmp_path / "x.csv"
    row = '"09/25/2026","86.78","1","1","1","1K","0%"\n'
    path.write_text(INVESTING_HEADER + row + row, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        parse_investing_csv(path)


def test_parse_ecb_csv_portal_layout(tmp_path):
    path = tmp_path / "fx.csv"
    path.write_text(
        '"DATE","TIME PERIOD","UK pound sterling/Euro (EXR.D.GBP.EUR.SP00.A)"\n'
        '"2024-01-02","02 Jan 2024","0.8665"\n'
        '"2024-01-03","03 Jan 2024",""\n'
        '"2024-01-04","04 Jan 2024","0.8632"\n'
    )
    out = parse_ecb_csv(path)
    assert out["date"].tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")]  # blank fixing dropped
    assert out["rate"].tolist() == [0.8665, 0.8632]


def test_parse_ecb_csv_sdmx_layout(tmp_path):
    path = tmp_path / "fx.csv"
    path.write_text(
        "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
        "EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2024-01-02,0.8665,A\n"
        "EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2024-01-03,0.8650,A\n"
    )
    out = parse_ecb_csv(path)
    assert out["rate"].tolist() == [0.8665, 0.8650]


def test_parse_msr_csv_ignores_extra_columns(tmp_path):
    path = tmp_path / "msr.csv"
    path.write_text("date,description,event_id\n2019-01-01,MSR becomes operational,9\n2014-01-22,Proposal,1\n")
    out = parse_msr_csv(path)
    assert out["date"].tolist() == [pd.Timestamp("2014-01-22"), pd.Timestamp("2019-01-01")]
    assert list(out.columns) == ["date", "description"]


# --- calendar --------------------------------------------------------------


def toy_prices() -> pd.DataFrame:
    """EUA trades every day. UKA starts on the 3rd day and has a holiday on the 5th."""
    idx = days("2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09")
    return pd.DataFrame(
        {
            "EUA": [100.0, 110.0, 99.0, 99.0, 101.0, 103.0, 100.0],
            "UKA": [np.nan, np.nan, 50.0, 55.0, np.nan, 44.0, 45.0],
        },
        index=idx,
    )


def test_calendar_keeps_dates_before_an_instrument_starts():
    calendar = book_calendar(toy_prices())
    assert pd.Timestamp("2024-01-01") in calendar and pd.Timestamp("2024-01-02") in calendar


def test_calendar_drops_non_common_day_once_instrument_has_started():
    calendar = book_calendar(toy_prices())
    assert pd.Timestamp("2024-01-05") not in calendar
    assert len(calendar) == 6


def test_calendar_requires_reference_instrument():
    prices = toy_prices()
    prices.loc["2024-01-08", "EUA"] = np.nan
    assert pd.Timestamp("2024-01-08") not in book_calendar(prices)
    with pytest.raises(ValueError):
        book_calendar(prices[["UKA"]])


# --- returns ---------------------------------------------------------------


def test_log_returns_match_hand_computation():
    r = compute_returns(toy_prices())
    assert r.loc["2024-01-02", "EUA"] == pytest.approx(np.log(110 / 100))
    assert r.loc["2024-01-03", "EUA"] == pytest.approx(np.log(99 / 110))
    assert r.loc["2024-01-04", "UKA"] == pytest.approx(np.log(55 / 50))
    assert np.isnan(r.loc["2024-01-03", "UKA"])  # first UKA price: no return yet
    assert pd.Timestamp("2024-01-01") not in r.index  # first calendar date has no return


def test_prices_are_not_forward_filled_and_multi_day_returns_are_flagged():
    r = compute_returns(toy_prices())
    # 5 Jan is dropped, so the 8 Jan return spans two EUA days for BOTH legs.
    assert r.loc["2024-01-08", "UKA"] == pytest.approx(np.log(44 / 55))
    assert r.loc["2024-01-08", "EUA"] == pytest.approx(np.log(103 / 99))
    assert r.loc["2024-01-08", EUA_DAYS] == 2
    assert (r[EUA_DAYS].drop(pd.Timestamp("2024-01-08")) == 1).all()


def test_fx_is_forward_filled_and_inverted():
    # EURGBP = GBP per 1 EUR. No fixing on 4 Jan (a TARGET holiday in this toy example).
    fx = pd.DataFrame(
        {"EURGBP": [0.80, 0.80, 0.84, 0.82, 0.82, 0.82]},
        index=days("2023-12-29", "2024-01-02", "2024-01-03", "2024-01-08", "2024-01-09", "2024-01-10"),
    )
    r = compute_returns(toy_prices(), fx)
    assert r.loc["2024-01-02", "GBPEUR"] == pytest.approx(0.0)  # 1 Jan filled from 29 Dec
    # EURGBP up means GBP weaker, so the GBPEUR factor return is negative.
    assert r.loc["2024-01-03", "GBPEUR"] == pytest.approx(-np.log(0.84 / 0.80))
    assert r.loc["2024-01-04", "GBPEUR"] == pytest.approx(0.0)  # no fixing: rate carried forward
    assert r.loc["2024-01-08", "GBPEUR"] == pytest.approx(-np.log(0.82 / 0.84))


def test_excluded_return_is_removed_for_that_factor_only_and_next_day_is_intact():
    r = compute_returns(toy_prices(), excluded={"EUA": ["2024-01-04"]})
    assert np.isnan(r.loc["2024-01-04", "EUA"])  # the contaminated return is gone
    assert r.loc["2024-01-04", "UKA"] == pytest.approx(np.log(55 / 50))  # other factors keep theirs
    assert r.loc["2024-01-08", "EUA"] == pytest.approx(np.log(103 / 99))  # next return starts from the new price
    assert r["EUA"].notna().sum() == len(r) - 1


def test_excluded_date_on_a_dropped_day_removes_the_covering_return():
    # The switch falls on 5 Jan, which is not a calendar date; its jump sits inside the 8 Jan return.
    r = compute_returns(toy_prices(), excluded={"EUA": ["2024-01-05"]})
    assert np.isnan(r.loc["2024-01-08", "EUA"])
    assert not np.isnan(r.loc["2024-01-08", "UKA"])
    assert not np.isnan(r.loc["2024-01-09", "EUA"])


def test_excluded_date_without_a_price_is_an_error():
    with pytest.raises(ValueError, match="no price"):
        compute_returns(toy_prices(), excluded={"EUA": ["2024-01-06"]})  # a Saturday: typo guard


def test_no_look_ahead_in_returns():
    """A return dated t must not change when later prices or FX fixings change."""
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2023-01-02", periods=60)
    prices = pd.DataFrame(
        {"EUA": 80 * np.exp(rng.normal(0, 0.02, 60).cumsum()), "UKA": 50 * np.exp(rng.normal(0, 0.02, 60).cumsum())},
        index=idx,
    )
    fx = pd.DataFrame({"EURGBP": 0.86 * np.exp(rng.normal(0, 0.004, 60).cumsum())}, index=idx)
    cut = idx[39]

    base = compute_returns(prices, fx)
    prices_later, fx_later = prices.copy(), fx.copy()
    prices_later.loc[prices_later.index > cut] *= 1.5
    fx_later.loc[fx_later.index > cut] *= 1.1
    perturbed = compute_returns(prices_later, fx_later)

    pd.testing.assert_frame_equal(base.loc[:cut], perturbed.loc[:cut])
    assert not base.loc[idx[40]:].equals(perturbed.loc[idx[40]:])  # the perturbation did bite


# --- front-month roll dates -------------------------------------------------


def test_roll_date_is_the_day_after_expiry_two_business_days_before_the_month():
    dates = pd.bdate_range("2026-06-01", "2026-07-10")
    # 1 July 2026 is a Wednesday: Tuesday 30 June is one business day before, Monday 29 June two.
    # The July contract expires on the 29th, so the 30 June return compares July with August.
    assert front_month_roll_dates(dates).tolist() == [pd.Timestamp("2026-06-30")]


def test_roll_date_when_the_month_starts_on_a_weekend():
    dates = pd.bdate_range("2022-09-01", "2022-10-14")
    # 1 October 2022 is a Saturday: Friday 30 September is one business day before, Thursday 29 two.
    assert front_month_roll_dates(dates).tolist() == [pd.Timestamp("2022-09-30")]


def test_roll_date_respects_bank_holidays():
    dates = pd.bdate_range("2020-08-03", "2020-09-11")
    # Monday 31 August 2020 was a UK bank holiday, so the September contract expired on Thursday 27 August.
    with_holiday = front_month_roll_dates(dates, holidays=[["2020-08-31"]])  # nested lists are accepted
    assert with_holiday.tolist() == [pd.Timestamp("2020-08-28")]
    # Ignoring the holiday puts the roll one trading day late.
    assert front_month_roll_dates(dates).tolist() == [pd.Timestamp("2020-08-31")]


def test_roll_date_is_the_next_date_the_series_actually_trades():
    dates = pd.bdate_range("2026-06-01", "2026-07-10").drop(pd.Timestamp("2026-06-30"))
    assert front_month_roll_dates(dates).tolist() == [pd.Timestamp("2026-07-01")]


def test_one_roll_per_month_and_none_before_the_series_starts():
    dates = pd.bdate_range("2023-01-02", "2023-12-29")
    rolls = front_month_roll_dates(dates)
    # The January 2023 contract expired in December 2022, before the series starts.
    assert len(rolls) == 12
    assert rolls[0] == pd.Timestamp("2023-01-31") and rolls[-1] == pd.Timestamp("2023-12-29")
    assert rolls.to_period("M").is_unique


def test_excluded_dates_combine_listed_dates_and_roll_rules():
    idx = pd.bdate_range("2026-06-01", "2026-07-10")
    settle = pd.DataFrame({"EUA": 80.0, "TTF": 40.0}, index=idx)
    cfg = {
        "data": {
            "excluded_returns": {"EUA": ["2026-06-22"]},
            "roll_returns": {"TTF": {"rule": "front_month", "business_days_before_delivery": 2}, "XYZ": {"rule": "front_month"}},
            "uk_bank_holidays": [],
        }
    }
    out = excluded_return_dates(settle, cfg)
    assert out["EUA"].tolist() == [pd.Timestamp("2026-06-22")]
    assert out["TTF"].tolist() == [pd.Timestamp("2026-06-30")]
    assert "XYZ" not in out  # no prices loaded for it


def test_evaluation_start_counts_prior_observations_only():
    dates = pd.bdate_range("2024-01-01", periods=10)
    # Three prior returns are needed, so the 4th date is the first that can be forecast.
    assert evaluation_start(dates, 3) == dates[3]
    assert evaluation_start(dates, 10) is None
