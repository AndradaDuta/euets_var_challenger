"""Hypothetical P&L: simple-return convention, FX attribution and aggregation up the hierarchy."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from euets_var.book import (
    CROSS,
    FX,
    PRICE,
    leg_pnl,
    load_book_config,
    node_gross_notional,
    node_pnl,
    portfolio_returns,
)
from euets_var.data import compute_returns

BOOK_CONFIG = Path(__file__).resolve().parents[1] / "config" / "book.yaml"


@pytest.fixture
def cfg() -> dict:
    return load_book_config(BOOK_CONFIG)


def random_market(n: int = 120, seed: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic prices (EUA, UKA in GBP, TTF) and EURGBP fixings. Not market data."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    prices = pd.DataFrame(
        {
            "EUA": 80 * np.exp(rng.normal(0, 0.025, n).cumsum()),
            "UKA": 50 * np.exp(rng.normal(0, 0.027, n).cumsum()),
            "TTF": 40 * np.exp(rng.normal(0, 0.050, n).cumsum()),
        },
        index=idx,
    )
    fx = pd.DataFrame({"EURGBP": 0.86 * np.exp(rng.normal(0, 0.004, n).cumsum())}, index=idx)
    return prices, fx


def test_gross_notional_per_node(cfg):
    gross = node_gross_notional(cfg)
    assert gross[("book", "EUA_OUTRIGHT")] == 20e6
    assert gross[("desk", "COMPLIANCE")] == 20e6
    assert gross[("desk", "TRADING")] == 25e6  # 10m EUA + 10m UKA + 5m TTF, not netted
    assert gross[("group", "GROUP")] == 45e6


def test_pnl_uses_simple_returns(cfg):
    prices = pd.DataFrame({"EUA": [100.0, 130.0, 91.0]}, index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    attribution, _ = leg_pnl(compute_returns(prices), cfg)
    outright = attribution[attribution["book"] == "EUA_OUTRIGHT"].set_index("date")["pnl_eur"]
    assert outright.loc["2024-01-02"] == pytest.approx(20e6 * 0.30)  # +30%, not ln(1.30)
    assert outright.loc["2024-01-03"] == pytest.approx(20e6 * -0.30)  # 130 -> 91 is -30%


def test_fx_decomposition_sums_to_total(cfg):
    """PRICE + FX + CROSS must equal the P&L computed directly from EUR-converted prices."""
    prices, fx = random_market()
    attribution, skipped = leg_pnl(compute_returns(prices, fx), cfg)
    assert not skipped

    uka = attribution[(attribution["book"] == "EUA_UKA_SPREAD") & (attribution["instrument"] == "UKA")]
    assert set(uka["component"]) == {PRICE, FX, CROSS}
    by_component = uka.pivot(index="date", columns="component", values="pnl_eur")

    notional = cfg["books"]["EUA_UKA_SPREAD"]["positions"]["UKA"]
    uka_in_eur = prices["UKA"] / fx["EURGBP"]  # EURGBP is GBP per 1 EUR
    direct = (notional * uka_in_eur.pct_change()).dropna()

    np.testing.assert_allclose(by_component.sum(axis=1), direct, rtol=1e-10, atol=1e-6)
    # The components mean what they say.
    np.testing.assert_allclose(by_component[PRICE], notional * prices["UKA"].pct_change().dropna(), rtol=1e-10)
    np.testing.assert_allclose(by_component[FX], notional * (1 / fx["EURGBP"]).pct_change().dropna(), rtol=1e-10)
    assert (by_component[CROSS].abs() < by_component[PRICE].abs() + 1e-9).all()


def test_eur_legs_have_price_component_only(cfg):
    prices, fx = random_market()
    attribution, _ = leg_pnl(compute_returns(prices, fx), cfg)
    eur_legs = attribution[attribution["instrument"].isin(["EUA", "TTF"])]
    assert set(eur_legs["component"]) == {PRICE}


def test_book_pnl_sums_to_desk_and_group(cfg):
    prices, fx = random_market()
    attribution, _ = leg_pnl(compute_returns(prices, fx), cfg)
    pnl, skipped = node_pnl(attribution, cfg)
    assert not skipped
    wide = pnl.pivot(index="date", columns="node", values="hypothetical_pnl_eur")

    np.testing.assert_allclose(wide["COMPLIANCE"], wide["EUA_OUTRIGHT"], rtol=1e-12)
    np.testing.assert_allclose(wide["TRADING"], wide["EUA_UKA_SPREAD"] + wide["CARBON_GAS_RV"], rtol=1e-12, atol=1e-6)
    np.testing.assert_allclose(wide["GROUP"], wide["COMPLIANCE"] + wide["TRADING"], rtol=1e-12, atol=1e-6)
    np.testing.assert_allclose(
        wide["GROUP"], wide[["EUA_OUTRIGHT", "EUA_UKA_SPREAD", "CARBON_GAS_RV"]].sum(axis=1), rtol=1e-12, atol=1e-6
    )
    assert not wide.isna().any().any()  # one calendar: every node has a row on every date


def test_portfolio_return_is_weighted_sum_of_simple_leg_returns(cfg):
    prices, fx = random_market()
    returns = compute_returns(prices, fx)
    attribution, _ = leg_pnl(returns, cfg)
    pnl, _ = node_pnl(attribution, cfg)
    r_p = portfolio_returns(pnl, cfg)

    simple = np.expm1(returns[["EUA", "UKA", "TTF", "GBPEUR"]])
    uka_eur = (1 + simple["UKA"]) * (1 + simple["GBPEUR"]) - 1
    # Group: +30m EUA, -10m UKA, -5m TTF over 45m gross.
    expected = (30e6 * simple["EUA"] - 10e6 * uka_eur - 5e6 * simple["TTF"]) / 45e6
    np.testing.assert_allclose(r_p["GROUP"], expected, rtol=1e-10, atol=1e-14)
    np.testing.assert_allclose(r_p["COMPLIANCE"], simple["EUA"], rtol=1e-10, atol=1e-14)


def test_missing_input_skips_the_book_and_its_parents(cfg):
    """Without FX the spread book cannot be valued, and neither can anything that contains it."""
    prices, _ = random_market()
    attribution, skipped_books = leg_pnl(compute_returns(prices), cfg)
    assert "GBPEUR" in skipped_books["EUA_UKA_SPREAD"]

    pnl, skipped_nodes = node_pnl(attribution, cfg)
    nodes = set(pnl["node"])
    assert nodes == {"EUA_OUTRIGHT", "COMPLIANCE", "CARBON_GAS_RV"}  # no partial TRADING or GROUP
    assert ("desk", "TRADING") in skipped_nodes and ("group", "GROUP") in skipped_nodes


def test_desk_and_group_start_only_when_every_book_is_valued(cfg):
    prices, fx = random_market()
    prices.loc[prices.index[:40], "UKA"] = np.nan  # UKA starts trading later
    attribution, _ = leg_pnl(compute_returns(prices, fx), cfg)
    pnl, _ = node_pnl(attribution, cfg)
    first = pnl.groupby("node")["date"].min()
    assert first["COMPLIANCE"] == prices.index[1]
    assert first["EUA_UKA_SPREAD"] == prices.index[41]  # first UKA return
    assert first["TRADING"] == first["GROUP"] == prices.index[41]
