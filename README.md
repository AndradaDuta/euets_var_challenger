# euets_var_challenger

> **Status: work in progress.** Session 1 of 5 (data layer and hypothetical P&L) is under way. There are no results yet. This README describes the design and will be updated from pipeline output as each stage is completed. See [Roadmap](#roadmap).

A market and liquidity risk engine for a hypothetical environmental-commodities book: EU and UK carbon allowances (EUA, UKA) and Dutch TTF gas.

Three families of standard VaR models will be benchmarked against a quantile-regression neural network (QRNN) challenger under a formal backtesting framework. The project also covers liquidity-adjusted VaR, limit monitoring, stress testing and a one-page daily risk report generated from SQL views.

> Hypothetical book, public data only. Not affiliated with any trading firm.

## Why this project

Carbon allowance prices are driven by regulation as much as by fundamentals. Several regime shifts stress standard risk models:
- the Market Stability Reserve (MSR)
- the 2022 energy crisis
- the launch of the UK ETS

The question is practical: **does a machine-learning challenger add anything over well-specified conventional VaR models, once it is judged by the same backtests a risk function would use?**

A null result for the challenger is an acceptable, reportable outcome. The aim is a rigorous and honestly reported comparison, not a winning model.

## The book

| Desk | Book | Position (constant EUR notional) |
|---|---|---|
| Compliance | `EUA_OUTRIGHT` | Long €20m EUA |
| Trading | `EUA_UKA_SPREAD` | Long €10m EUA / short €10m UKA |
| Trading | `CARBON_GAS_RV` | Short €5m TTF |

**Aggregation.** Risk is aggregated book → desk → group. VaR is not additive, so the report will show the sum of desk VaRs against group VaR, the resulting diversification benefit, and incremental VaR per book.

**Constant notional.** Positions are rebalanced daily to constant EUR notional. Without this, the P&L series would be dominated by the EUA price level, which rose from single digits to around €100 over the sample.

**FX.** The UKA leg is GBP-denominated. EUR/GBP is treated as a separate risk factor, and its contribution is reported separately.

**P&L perspective.** All backtests use *hypothetical* (static-position) P&L: today's positions revalued at the next day's prices. *Actual* P&L, which includes intraday trading, fees and new deals, is out of scope. Hypothetical P&L separates the model's forecast from trading activity, which a clean VaR backtest requires.

## Models (planned)

Targets are 1-day 99% and 97.5% VaR and 97.5% Expected Shortfall, at book, desk and group level. Every forecast uses information up to and including day *t* only.

| Role | Model | Key choices |
|---|---|---|
| Champion | Historical simulation | Rolling 250-day window; linear-interpolation quantile convention |
| Champion | Filtered HS | GJR-GARCH(1,1) with Student-t innovations; refit every 21 days on an expanding window (min. 750 obs.) |
| Champion | EWMA parametric | λ = 0.94, with normal and Student-t variants, to separate the effect of fat tails from volatility clustering |
| Challenger | QRNN | Small MLP; 10 quantile levels from 0.25% to 2.5%; non-crossing by construction; walk-forward training; 10-seed ensemble |

The QRNN will use the following features, all lagged:
- portfolio and EUA returns
- realised volatility
- TTF return and volatility
- the rolling EUA–TTF correlation
- days to December expiry
- MSR-event features built from announcement dates

**Known limitation, stated upfront.** With 750 training days, the 1% quantile rests on roughly 7–8 tail observations. Joint multi-quantile training, a small network and regularisation are the mitigations. Tail-data scarcity is the main reason the QRNN may not beat filtered HS.

## Evaluation (planned)

The following tests will be applied to each model and each portfolio (Compliance desk, Trading desk, Group):
- Exceedance rate, Kupiec POF, and Christoffersen independence and conditional coverage
- Basel traffic light (rolling 250 days)
- Tick (pinball) loss, with Diebold–Mariano tests using a Newey–West variance
- Acerbi–Székely ES backtest
- **VaR stability:** how often each model's VaR flips a limit status. A usable limit metric must be accurate *and* stable.

## Liquidity, limits and stress (planned)

- **Liquidity-adjusted VaR:** Bangia et al. exogenous-spread approach, with assumed spreads documented and labelled as assumptions. This also includes days-to-liquidate and liquidity-horizon VaR.
- **Limits:**
  - types: VaR, open-position and days-to-liquidate
  - status: warning at 80% and breach at 100%
  - each breach generates an automatic escalation note
- **Historical scenarios:** COVID sell-off, 2022 invasion shock, 2018 MSR-era rally and 2022 energy-crisis volatility. Windows are identified from the data, not hard-coded.
- **Hypothetical scenarios:** MSR reform shock, gas–carbon decoupling, EU–UK linkage convergence and a GBP shock.
- **Reverse stress test.**
- **Daily risk report:** a one-page report generated from DuckDB SQL views. Example reports for a stressed date and a calm date will be added.

## Data

| Series | Source | Status |
|---|---|---|
| EUA | ICE EUA front-December futures, continuous (Investing.com) | Identified; 1 lot = 1,000 t |
| UKA | ICE UKA futures (Investing.com, "UK Emissions Allowances") | Under evaluation: the available series appears to be front-month rather than December, and its liquidity has not yet been checked |
| TTF | ICE Endex Dutch TTF front-month (Investing.com) | To download |
| EUR/GBP | ECB reference rate | To download |
| MSR events | 27 announcement dates from [msr_carbon_repricing](https://github.com/AndradaDuta/msr_carbon_repricing) | Available |

**Raw data is not included in this repository.** The Investing.com terms of use do not permit redistribution. `data/raw/SOURCES.md` will document every series (source URL, download date and contract definition) so the dataset can be rebuilt.

**Sample periods:**
- Data from 2015 onwards is used as a training warm-up.
- EUA-only evaluation starts on 2019-01-01, when the MSR became operational.
- Full-book evaluation starts once 250 days of UKA history exist. The exact date will be computed from the data.

## Roadmap

| Session | Scope | Status |
|---|---|---|
| 1 | Data layer (DuckDB), returns, calendar alignment, book hierarchy, hypothetical P&L, FX split | In progress |
| 2 | HS, filtered HS, EWMA; desk and group aggregation; diversification and incremental VaR | Planned |
| 3 | QRNN features, walk-forward training, seed ensemble | Planned |
| 4 | Backtesting suite, VaR stability metric, results table and charts | Planned |
| 5 | Liquidity-adjusted VaR, limits, stress and reverse stress tests, SQL views, daily PDF report | Planned |

The full specification is in [`BUILD_SPEC.md`](BUILD_SPEC.md).

## Results

_None yet. This section will be filled only from pipeline output._

## Run

```bash
pip install -e ".[dev]"
python -m euets_var.pipeline --config config/book.yaml
pytest
```

The pipeline currently creates the DuckDB database and loads the book's reference data (instruments and hierarchy). The tests check the schema and the book configuration. Price loading, P&L and the models follow in later sessions.

Stack: Python 3.11, pandas, numpy, scipy, statsmodels, arch, PyTorch, DuckDB, matplotlib and pytest.

## Limitations

- The book is hypothetical and linear, with no options.
- Bid–ask spreads are assumed, not observed.
- √T liquidity-horizon scaling assumes i.i.d. returns.
- UKA history starts with the UK ETS launch (2021), which shortens the full-book evaluation window.
- Prices come from free sources. Continuous-contract construction and roll handling are documented in the decision log.

**Out of scope (possible extensions):**
- options and full revaluation
- Monte Carlo VaR
- actual P&L
- multivariate filtered HS
- intraday risk

## Decision log

| Date | Decision | Alternatives considered | Reason |
|---|---|---|---|
| 2026-09-26 | Backtest on a book rebalanced daily to constant EUR notional | Constant number of lots | Keeps P&L comparable across the sample instead of scaling with the EUA price level |
| 2026-09-26 | HS quantile uses linear interpolation (`np.quantile(..., method="linear")`) | Lower / nearest order statistic | With 250 observations the 1% quantile depends on just a few of the worst returns, so the convention changes the VaR number and must be fixed and stated |
| 2026-09-26 | Raw price data is not committed to the repository | Commit raw CSVs | Investing.com terms prohibit redistribution; `SOURCES.md` documents how to rebuild the dataset |
| 2026-09-26 | Added an `msr_events` table to the schema | Keep MSR dates in a CSV read directly by the feature code | MSR dates feed both the QRNN features and the MSR stress scenario; storing them in the database keeps all raw inputs in one place |
| 2026-09-26 | EUA series: Investing.com "Carbon Emissions Futures" (front-December continuous) | Spot / daily EUA; Ember | Front-December is the liquid benchmark contract; the source lists a December settlement and a 1,000 t contract size |

**Open questions:**
- **UKA series.** Choose between front-month and December. Check for stale prices and zero-volume days before deciding.
- **Roll handling.** Compute roll-day returns on the held contract where possible; otherwise flag and winsorise. To be fixed after inspecting the downloaded series.
- **Full-book evaluation start date.** To be computed from the data.
- **UKA leg's EUR P&L.** Choose between a log-additive split (exact decomposition) and an arithmetic split (leaves a cross term).
