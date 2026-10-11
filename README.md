# euets_var_challenger

> **Status: work in progress.** Session 1 of 5 (data layer and hypothetical P&L) is complete: all five raw files are loaded and checked, and hypothetical P&L is built at book, desk and group level. There are no risk results yet; the champion models (Session 2) are next. This README describes the design and will be updated from pipeline output as each stage is completed. See [Roadmap](#roadmap).

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

**P&L perspective.** All backtests use *hypothetical* (static-position) P&L: today's positions revalued at the next day's prices. *Actual* P&L, which includes intraday trading, fees and new deals, is out of scope. Hypothetical P&L separates the model's forecast from trading activity, which a clean VaR backtest requires.

**P&L convention.** A leg with notional *N* earns *N* × (P<sub>t</sub> / P<sub>t−1</sub> − 1), its simple return. Risk-factor returns are stored as log returns and converted. The portfolio return a model is fitted on is the node's P&L divided by its gross notional, so VaR in EUR is exactly a quantile of the P&L distribution.

**FX.** The UKA leg is GBP-denominated. EUR/GBP is treated as a separate risk factor. The leg's EUR P&L is split into a price component, an FX component and a cross term, which sum exactly to the total and are stored separately.

**One calendar.** A date is used when every instrument that has started trading has a price. Prices are never forward-filled; a day that is not common to all instruments is dropped for the whole book, and the return that then spans more than one day is flagged. Book, desk and group P&L therefore cover the same interval on every date and reconcile row by row (`v_pnl_reconciliation`).

**Contract-switch days.** The futures series are continuous series that switch from one contract to the next. The return on a switch day compares two different contracts, so it is not P&L anyone earned. That return is removed for the risk factor concerned, and every node exposed to it has no P&L on that day. A desk or group row exists only when all of its books are valued.

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
| EUA | ICE EUA futures, nearest-contract continuous (Investing.com, "Carbon Emissions Futures"); 1 lot = 1,000 t | Downloaded and checked. It is **not** a pure December series: it switches contract at irregular dates, and the returns on eight switch days are excluded |
| UKA | ICE UKA futures, front-month continuous (Investing.com, "UK Emissions Allowances"); 1 lot = 1,000 t | Downloaded and checked. Used for returns; its volume resembles the December contract only from November to mid-December |
| TTF | ICE Endex Dutch TTF front-month, continuous (Investing.com), in EUR/MWh | Downloaded and checked. The return on the first trading day after each monthly expiry is excluded (140 days) |
| EUR/GBP | ECB reference rate, GBP per 1 EUR | Downloaded and checked. Forward-filled on the three sample dates without a fixing |
| MSR events | 27 announcement dates from [msr_carbon_repricing](https://github.com/AndradaDuta/msr_carbon_repricing) | In `data/raw/msr_events.csv` |

**Raw price data is not included in this repository.** The Investing.com terms of use do not permit redistribution. [`data/raw/SOURCES.md`](data/raw/SOURCES.md) documents every series: source, contract definition, the checks run on it and how to rebuild the dataset.

**Sample periods:**
- The sample is frozen at 2026-09-25 for all series.
- Data from 2015 onwards is used as a training warm-up.
- EUA-only evaluation starts on 2019-01-01, when the MSR became operational.
- The full book is valued from 2021-05-21, the first UKA return, on 1,280 days.
- Full-book evaluation start dates are computed by the pipeline from the number of prior group returns a model needs: 2022-06-08 with 250 (1,030 evaluation days) and 2024-07-11 with 750 (530 evaluation days).
- The Compliance desk has 1,955 days from 2019-01-01.

## Roadmap

| Session | Scope | Status |
|---|---|---|
| 1 | Data layer (DuckDB), returns, calendar alignment, book hierarchy, hypothetical P&L, FX split | Done |
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

The pipeline currently:
- creates the DuckDB database and loads the raw files as downloaded
- computes log returns per risk factor on the book calendar
- writes hypothetical P&L at book, desk and group level, with its price, FX and cross components

It values only what the loaded files support: a book whose inputs are missing is reported as skipped, and no desk or group total is built from a partial set of books. The tests cover file parsing, the calendar, the roll-date rule, look-ahead in the returns, the FX decomposition and reconciliation across the hierarchy. They run on synthetic data, so they pass without the raw files. The models follow in later sessions.

Stack: Python 3.11 or later (tested on 3.11 and 3.13), pandas, numpy, scipy, statsmodels, arch, PyTorch, DuckDB, matplotlib and pytest.

## Limitations

- The book is hypothetical and linear, with no options.
- Bid–ask spreads are assumed, not observed.
- √T liquidity-horizon scaling assumes i.i.d. returns.
- UKA history starts with the UK ETS launch (2021), which shortens the full-book evaluation window.
- Prices come from free sources and are the closes the source publishes, not official exchange settlements.
- The EUA series is not a pure December contract. Eight contract-switch days are excluded; one switch is verified against published December-contract closes, and the other seven are inferred from volume alone.
- The UKA series is front-month. Its drift towards the December price is documented, not corrected.
- The TTF series is front-month. The 140 roll-day returns are excluded, so the gas book, the Trading desk and the Group have no P&L on those days (64 of them since May 2021).
- Days that are not common to all instruments are dropped, so a small share of returns span more than one trading day (21 of the 1,280 full-book days). They are flagged in the `returns` table.
- The full-book evaluation window is short. With 530 days, a correct 99% VaR model expects about five exceptions, so the coverage tests have low power there. The Compliance desk, evaluated from 2019, carries most of the evidence.

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
| 2026-09-26 | ~~EUA series: Investing.com "Carbon Emissions Futures" (front-December continuous)~~ **Superseded on 2026-10-04: the series is not front-December** | Spot / daily EUA; Ember | The source lists a December settlement and a 1,000 t contract size. The label turned out to be a poor guide to the data |
| 2026-10-04 | EUA series treated as a nearest-contract continuous. Its returns on eight contract-switch days (December 2022 to June 2026) are excluded | Winsorise the switch days (spec, Section 2); carry-adjust with an interest-rate series; rebuild the switch-day returns from published December-contract closes; find a true December series | The return on a switch day compares two different contracts. On 27 March 2023 the series shows +3.1% while the December contract moved −0.3%. Winsorising would leave most of such a jump in |
| 2026-10-04 | UKA front-month series used for returns as downloaded; its roll drift is documented, not corrected | Find a December UKA series; drop the spread book | Prices move on no-volume days with the same volatility as on traded days, so the series is not stale. No free December UKA series was found |
| 2026-10-04 | Volume is used for liquidity only where a series tracks the December contract (EUA: liquid stretches; UKA: November to mid-December). To be implemented in Session 5 | 20-day average volume on all days (spec, Section 6) | Elsewhere the volume belongs to a thin contract that nobody would use to liquidate the position |
| 2026-10-11 | Sample frozen at 2026-09-25 for all series | Roll the end date forward with each download (spec: "latest available date") | A fixed cut-off makes every reported number reproducible from the documented downloads |
| 2026-10-11 | Placeholder rows are dropped: four in the EUA file and two in the TTF file. Rule: open = high = low = close, no volume, price unchanged | Keep them as zero returns | Flat prices with no volume are not trading days; keeping them adds artificial zero returns |
| 2026-10-11 | One calendar for the whole book: a date is used when every instrument that has started trading has a price. Returns spanning more than one EUA trading day are flagged | A separate calendar per book (spec, Section 2: "drop non-common days per book"); forward-fill prices | Book, desk and group P&L cover the same interval on every date, so they reconcile row by row. Cost: the Compliance desk also loses the days on which only UKA is closed |
| 2026-10-11 | Hypothetical P&L uses simple returns; the portfolio return is P&L over gross notional. Risk-factor returns stay log returns. Deviates from the spec formula, which sums log returns across legs | Sum log returns across legs (spec, Section 3) | Log returns do not add across legs and misstate large moves. On 1 March 2022 EUA fell 16.3%; its log return is −17.7%, which would overstate the loss on the €20m book by about €0.3m. For a short leg the error understates losses |
| 2026-10-11 | UKA leg P&L is split into price, FX and cross components | Log-additive split with no cross term | Follows from simple-return P&L. The three parts sum exactly to the total and nothing is allocated arbitrarily |
| 2026-10-11 | TTF returns on the first trading day after each monthly expiry are excluded. The dates come from the exchange rule (expiry two UK business days before the delivery month), not from a hand-made list | Winsorise roll days (spec, Section 2); keep them; take the last trading day of each month | A front-month series switches contract every month. Roll-day returns average +1.3% against zero on other days, and the largest, +31.7% on 30 September 2019, would be a spurious loss of about €1.9m on the €5m short. UK bank holidays move six of the 140 dates away from the last trading day of the month |
| 2026-10-11 | A contract-switch day removes the return of the factor concerned only. Nodes not exposed to that factor keep their P&L | Remove the day for the whole book | Removing TTF roll days for the whole book would cost the Compliance desk 140 days for a gas-data artefact |
| 2026-10-11 | UKA monthly rolls are not excluded, although the series is front-month | Apply the same roll rule as for TTF | The UKA roll effect averages +0.6% with a t-statistic of 1.4, small against a daily standard deviation of 2.7%. Excluding it would remove about 64 more full-book days |
| 2026-10-11 | Added `returns` and `pnl_attribution` tables and the `v_pnl_reconciliation` view | Recompute returns in pandas on every use; store only node-level P&L | Cleaned returns, the multi-day flag and the FX contribution can be queried and reconciled in SQL |

**Open questions:**
- **Full-book evaluation window.** Proposal: compare all four models from 2024-07-11 (530 days), and also report HS and EWMA from 2022-06-08 (1,030 days). To be confirmed before Session 2.
- **Multi-day returns in the backtests.** Decide in Session 4 whether to leave them out of the coverage tests.
- **Remaining EUA switch days.** Seven of the eight are inferred from volume alone. They can be checked against published December-contract closes.
- **MSR event dates.** Five dates have month-only precision and six fall on non-trading days. To be resolved in Session 3 before they become model features.
