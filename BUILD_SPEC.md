# BUILD_SPEC — `euets_var_challenger`

Daily market and liquidity risk engine for a hypothetical environmental-commodities book, with a quantile-regression neural network (QRNN) tested as a challenger against standard VaR models.

This document is the source of truth for the build. Anything not specified here is a design decision to be made explicitly and written into Section 12 (Decision log).

---

## 1. Objective and scope

**Objective.** Estimate 1-day 99% and 97.5% VaR and 97.5% ES at book, desk and group level for a small EUA / UKA / TTF book. Then test whether a QRNN challenger improves on three champion models under a formal backtesting framework, and add liquidity-adjusted VaR, limit monitoring, stress testing and a one-page daily risk report.

**In scope**
- Three champion models: historical simulation (HS), filtered historical simulation (FHS), and EWMA parametric
- One challenger model: QRNN
- Backtesting: coverage, independence, traffic light, tick loss with Diebold–Mariano, ES backtest, and VaR stability
- Liquidity-adjusted VaR and days-to-liquidate
- Open-position, VaR and liquidity limits with warning and breach logic
- Historical, hypothetical and reverse stress tests
- DuckDB data layer, with the SQL-driven daily report

**Out of scope** (mention in README as extensions)
- Options and non-linear products
- Full revaluation
- Monte Carlo VaR
- Actual (trading-inclusive) P&L
- Multivariate FHS
- Intraday risk

**Success criterion.** The project succeeds if the comparison is rigorous and honestly reported, whichever model wins. A null result for the challenger is an acceptable, reportable outcome.

---

## 2. Data

| Risk factor | Instrument / proxy | Candidate free source | Notes |
|---|---|---|---|
| EUA | ICE EUA Dec futures, continuous | Investing.com historical data; Ember carbon price viewer | **Verify** availability, history length and whether the series is front-Dec or spot |
| UKA | ICE UKA Dec futures, continuous | Investing.com | UK ETS launched in 2021, so there is **no data before its launch** |
| TTF | ICE Endex Dutch TTF front-month | Investing.com | Front-month roll every month; document it |
| EUR/GBP | ECB reference rate | ECB Data Portal | Reliable; use for all GBP→EUR conversion |
| Volume | Daily volume per instrument, if available | Same as prices | Needed for days-to-liquidate; fall back to documented assumptions if missing |
| MSR events | 27 MSR announcement dates | Thesis dataset (`msr_carbon_repricing`) | Reused as a feature and a stress calibration |

**Rules**
1. Record source, download date and series definition for every file in `data/raw/SOURCES.md`.
2. **Returns** are daily log returns: `r_t = ln(P_t / P_{t-1})`.
   - If the series is a continuous contract with visible roll gaps, compute the roll-day return on the *held* contract when possible.
   - Otherwise, flag and winsorise roll days, and log it in the Decision log.
3. **Calendar:** align on the EUA trading calendar. Forward-fill FX only. Never forward-fill prices; drop non-common days per book instead.
4. Store everything raw in DuckDB (Section 8) before any transformation.

**Sample periods**
- Data start: 2015-01 or earliest available, used as training warm-up.
- EUA-only evaluation starts 2019-01-01, when the MSR became operational.
- Full-book evaluation starts once 250 days of UKA history exist (approx. mid-2022). **Compute the exact date from the data.**
- Evaluation end: latest available date.

---

## 3. The book

Hierarchy: **Group → Desk → Book.**

| Desk | Book | Position (EUR notional, constant) |
|---|---|---|
| Compliance | `EUA_OUTRIGHT` | Long €20m EUA |
| Trading | `EUA_UKA_SPREAD` | Long €10m EUA / short €10m UKA (EUR-equivalent) |
| Trading | `CARBON_GAS_RV` | Short €5m TTF |

**Constant-notional convention for backtesting.** Positions are rebalanced daily to constant EUR notional, so the P&L series is not dominated by the EUA price level moving from around €5 to around €100 over the sample.

- Portfolio return: `r_p,t = Σ_i N_i · r_i,t / Σ_i |N_i|`
- VaR in EUR = `−q_α(r_p) × gross notional`

**UKA leg and FX.**
- The UKA leg is GBP-denominated.
- Its EUR P&L is the combination of the UKA return and the EUR/GBP return. Treat EUR/GBP as a separate risk factor.
- Report the FX contribution separately (JD: "FX exposure impacts").

**Snapshot view for the daily report.** Convert today's notionals into lots and tonnes using today's prices. Assume 1 lot = 1,000 t CO2 for EUA and UKA, and verify this with the ICE contract specs. This is used for open-position limits.

**P&L definition.** All backtests use hypothetical (static-position) P&L, the standard for clean backtesting. Actual P&L (including intraday trading, fees and new deals) is out of scope; explain the distinction in the README (JD: "different PnL perspectives").

---

## 4. Models

All models forecast the next-day portfolio return quantile at `α ∈ {1%, 2.5%}` and 97.5% ES, using information up to and including day t only.

### 4.1 Historical simulation (champion 1)
- Rolling 250-day window of portfolio returns under the current book.
- VaR = empirical quantile, using `np.quantile(..., method="linear")`. Document this convention, because interviewers ask about it.
- ES = mean of returns at or below the 2.5% quantile.

### 4.2 Filtered HS (champion 2)
- **Model:** GJR-GARCH(1,1) with Student-t innovations on portfolio returns (`arch` package).
- **Fitting:** refit parameters every 21 trading days on an expanding window (minimum 750 obs). Update σ daily with fixed parameters between refits.
- **Standardised residuals:** `z_t = r_t / σ_t`.
- **VaR:** `σ_{t+1} × q_α(z)` over the last 750 residuals.
- **ES:** `σ_{t+1} × mean(z | z ≤ q_α)`.

### 4.3 EWMA parametric (champion 3)
- Variance recursion: `σ²_t = λσ²_{t−1} + (1−λ) r²_{t−1}` with λ = 0.94.
- VaR = `−z_α σ_t` under normal quantiles.
- Also report a Student-t variant with ν fixed from the FHS fit. This separates the effect of fat tails from the effect of volatility clustering.

### 4.4 QRNN (challenger)

**Target.** Next-day portfolio return. Train one model per book-level portfolio (desk and group). The QRNN forecasts quantiles of the portfolio directly; it does not aggregate factors.

**Features** (all known at close of day t):
- Lagged returns r_{t}, r_{t−1}, …, r_{t−4} of the portfolio and of EUA
- Realised volatility over 5 and 22 days (portfolio and EUA)
- TTF return and 22-day TTF volatility
- 60-day rolling EUA–TTF correlation
- Trading days to December expiry
- **MSR feature:** days since the most recent MSR event (capped at 60) and a dummy for "MSR event within the last 5 days". Use **lagged information only**; unscheduled announcements must never leak forward.

**Architecture**
- MLP with 2 hidden layers of 16–32 units, ReLU, dropout 0.1–0.2 and weight decay.
- Output is a vector of quantiles at `τ ∈ {0.0025, 0.005, …, 0.025}` (10 levels).
- **Non-crossing by construction:** output the lowest quantile, then add cumulative `softplus` increments.

**Loss.** Mean pinball (tick) loss across all τ levels.

**Training**
- Walk-forward: train on an expanding window (minimum 750 obs), refit every 21 trading days.
- Early stopping on the last 20% of the training window, kept **in time order**. Never shuffle across that split.
- Standardise features using training-window statistics only.

**Ensembling.** Train 10 seeds and average the quantiles.

**Outputs**
- VaR_99 = −q(0.01), interpolated from the grid.
- ES_97.5 = mean of the quantiles at τ ≤ 0.025.

**Known limitation to state explicitly.** With 750 training days, the 1% quantile rests on roughly 7–8 tail observations. Joint multi-quantile training, a small network and regularisation are the mitigations. This is the core reason the QRNN may not beat FHS.

Framework: PyTorch (CPU is sufficient).

---

## 5. Backtesting and evaluation

Run on each portfolio (Compliance desk, Trading desk, Group) and each model.

| Test | Implementation |
|---|---|
| Exceedance rate | `x/T` vs α |
| Kupiec POF | LR = −2 ln[(1−p)^{T−x} p^x] + 2 ln[(1−x/T)^{T−x}(x/T)^x] ~ χ²(1) |
| Christoffersen independence | Transition counts n00, n01, n10, n11 → LR_ind ~ χ²(1); conditional coverage = POF + IND ~ χ²(2) |
| Basel traffic light | 99% VaR, rolling 250 days: green 0–4, yellow 5–9, red ≥10 exceptions. Plot zone over time |
| Tick loss | Mean pinball loss per model at α = 1% and 2.5% |
| Diebold–Mariano | Loss differential vs each champion; HAC (Newey–West) variance, lag = ⌊T^{1/3}⌋ |
| ES backtest | Acerbi–Székely Z2 with losses L_t > 0: Z2 = Σ L_t·I_t / (T·α·ES_t) − 1. Expected value under H0 is 0; Z2 > 0 means ES is underestimated. p-values by simulation from each model's predictive distribution |
| VaR stability | Mean absolute daily % change in VaR; number of limit-status flips (below limit ↔ above limit) at a fixed limit |

**Required output:** a single results table (models × metrics × portfolio), plus three charts:
- VaR vs realised P&L with exceptions marked, for the full evaluation period
- Traffic-light zone over time
- Rolling 250-day tick loss per model

---

## 6. Liquidity risk

**Liquidity-adjusted VaR** (Bangia et al., 1999, exogenous spread):

`LVaR = VaR + ½ × Σ_i |N_i| × (s̄_i + k·σ_{s,i})`

- `s̄_i` and `σ_{s,i}` are the relative bid–ask spread mean and volatility.
- Free data will almost certainly not include bid–ask. Use **documented assumed spreads** per instrument in `config/liquidity.yaml` with k = 3, and label them as assumptions.
- Expect UKA to be the least liquid leg. Verify this with volume data where available; do not assert it.

**Days to liquidate.**
- `DTL_i = |position_tonnes_i| / (participation_rate × ADV_i)`
- Participation rate 10%; ADV is 20-day average volume.
- **Liquidity-horizon VaR:** scale each leg's 1-day VaR by √max(1, DTL_i).
- Flag this as a simplification (√T assumes i.i.d. returns).

**Limits.** Maximum DTL per book, set in `config/limits.yaml`.

---

## 7. Stress testing

Apply scenarios to **today's snapshot positions**. Report the P&L impact at book, desk and group level, alongside each scenario's ratio to 99% VaR.

**Historical scenarios.** Windows are identified from the data, not hard-coded. For each, find the worst cumulative EUA move of up to 10 trading days inside the search window, then apply all factor moves over those same dates.

| Scenario | Search window |
|---|---|
| COVID sell-off | 2020-02-15 → 2020-04-15 |
| Invasion shock | 2022-02-15 → 2022-03-31 |
| MSR-era rally (upside) | 2018-01-01 → 2018-12-31 |
| Energy-crisis peak volatility | 2022-07-01 → 2022-09-30 |

The rally scenario matters because the Trading desk is short UKA and TTF, so upside moves hurt it.

**Hypothetical scenarios.** Parameters live in `config/scenarios.yaml` and are labelled expert judgement.
1. **MSR reform shock.** EUA move equal to the worst 5-day cumulative EUA return in a [−1, +3] window around any of the 27 MSR events. UKA moves with its regression beta to EUA; TTF is unchanged.
2. **Gas–carbon decoupling.** TTF +50%, EUA −20%.
3. **EU–UK linkage convergence.** UKA +25%, EUA unchanged (spread compression).
4. **GBP shock.** EUR/GBP ±5%.

**Reverse stress test.** Solve for the EUA move (with other factors at their beta-implied moves) that produces a group loss equal to 3 × the group 99% VaR limit.

---

## 8. Data layer (DuckDB)

`sql/schema.sql`:

```sql
CREATE TABLE instruments (instrument VARCHAR PRIMARY KEY, currency VARCHAR, lot_size_t DOUBLE, description VARCHAR);
CREATE TABLE prices      (date DATE, instrument VARCHAR, settle DOUBLE, volume DOUBLE, source VARCHAR, PRIMARY KEY (date, instrument));
CREATE TABLE fx_rates    (date DATE, pair VARCHAR, rate DOUBLE, PRIMARY KEY (date, pair));
CREATE TABLE hierarchy   (book VARCHAR PRIMARY KEY, desk VARCHAR, entity VARCHAR);
CREATE TABLE positions   (date DATE, book VARCHAR, instrument VARCHAR, notional_eur DOUBLE, PRIMARY KEY (date, book, instrument));
CREATE TABLE limits      (level VARCHAR, node VARCHAR, metric VARCHAR, limit_value DOUBLE, warning_pct DOUBLE);
CREATE TABLE pnl         (date DATE, level VARCHAR, node VARCHAR, hypothetical_pnl_eur DOUBLE);
CREATE TABLE risk_results(date DATE, level VARCHAR, node VARCHAR, model VARCHAR, metric VARCHAR, confidence DOUBLE, value_eur DOUBLE);
CREATE TABLE stress_results(date DATE, scenario VARCHAR, level VARCHAR, node VARCHAR, pnl_eur DOUBLE);
```

**Views** in `sql/views/`:
- `v_limit_utilisation`: metric / limit, with status `OK` / `WARNING` (≥ warning_pct) / `BREACH` (≥ 100%)
- `v_exceptions`: days where loss > VaR, by model
- `v_daily_report`: everything the report needs for one date

The report must be generated from these SQL views, not recomputed in pandas.

---

## 9. Limits and escalation

**Limit types:**
- 99% VaR per desk and group
- Open position in net tonnes per book
- DTL per book

**Calibration.** Set VaR limits so that average HS utilisation over the evaluation period is around 65%. Record the calibration rule, not arbitrary numbers.

**Status logic:**
- `WARNING` at ≥ 80% utilisation
- `BREACH` at ≥ 100%. A breach generates a one-line escalation note in the report: what, where, how much, the driver (largest component), and who is notified according to a mock policy.

**Aggregation.**
- VaR is not additive. Show the sum of desk VaRs vs group VaR, and the difference as diversification benefit.
- Compute incremental VaR per book as VaR(group) − VaR(group excluding book).

---

## 10. Daily risk report (one page, A4 PDF)

Generated by `report.py` from `v_daily_report` using a matplotlib multi-panel figure.

1. **Header.** As-of date, group 99% VaR and 97.5% ES (champion), utilisation, status.
2. **Desk / book table.** VaR, ES, LVaR, open position (t), DTL, utilisation, status.
3. **Champion vs challenger.** Today's VaR per model and the running 250-day exception count per model.
4. **Stress panel.** Top 5 scenario losses, with each as a multiple of VaR.
5. **Chart.** 60-day VaR vs hypothetical P&L at group level.
6. **Breaches and escalations.** Auto-generated notes, or "No limit breaches".

Save one example report for a stressed date (e.g. early March 2022) and one for a calm date. Link both in the README.

---

## 11. Repository structure

```
euets_var_challenger/
├── README.md
├── BUILD_SPEC.md
├── pyproject.toml
├── config/
│   ├── book.yaml
│   ├── limits.yaml
│   ├── liquidity.yaml
│   └── scenarios.yaml
├── data/
│   ├── raw/            # + SOURCES.md
│   └── euets.duckdb    # gitignored if licence requires
├── sql/
│   ├── schema.sql
│   └── views/
├── src/euets_var/
│   ├── data.py         # load, clean, returns, calendar
│   ├── book.py         # hierarchy, P&L vectors, FX split
│   ├── features.py     # QRNN features, no-look-ahead checks
│   ├── models/
│   │   ├── hs.py
│   │   ├── fhs.py
│   │   ├── ewma.py
│   │   └── qrnn.py
│   ├── backtest.py
│   ├── liquidity.py
│   ├── stress.py
│   ├── limits.py
│   ├── report.py
│   └── pipeline.py     # end-to-end run
├── notebooks/
│   └── results.ipynb   # figures + tables only; logic lives in src/
├── reports/            # example PDFs, figures
└── tests/
```

**Stack:** Python 3.11, pandas, numpy, scipy, statsmodels, arch, torch, duckdb, pyyaml, matplotlib, pytest.

**Tests (minimum)**
- Kupiec and Christoffersen reproduce hand-computed values on a toy series.
- Pinball loss is correct on a toy example.
- QRNN output quantiles are non-decreasing in τ.
- **No look-ahead:** features for day t use data ≤ t only. Test this by perturbing future data and asserting that features are unchanged.
- Portfolio P&L equals the sum of book P&Ls, and FX decomposition sums to total.

---

## 12. Decision log

Keep a dated table in the README (or `DECISIONS.md`) with columns: date, decision, alternatives considered, reason.

Pre-seeded entries:
- constant-notional backtest book
- HS quantile convention
- roll handling
- assumed spreads
- full-book evaluation start date

---

## 13. Build plan (~3 hrs per session)

| Session | Build | Done when |
|---|---|---|
| 1 | Data download, `SOURCES.md`, DuckDB schema, load, returns, calendar alignment, book hierarchy, hypothetical P&L vectors, FX split | P&L table populated at book, desk and group level; FX decomposition test passes |
| 2 | HS, FHS, EWMA (normal + t); desk and group aggregation; diversification and incremental VaR | `risk_results` populated for all champions and all levels |
| 3 | Features, QRNN, walk-forward training, seed ensemble | QRNN results in `risk_results`; non-crossing and no-look-ahead tests pass |
| 4 | Backtesting suite, stability metric, results table and charts | One comparison table across all models and portfolios |
| 5 | LVaR, DTL, limits, stress and reverse stress, SQL views, PDF report, README results | Two example reports saved; README results section filled from actual output |

**Stretch goals** (only after Session 5):
- Multivariate FHS with per-factor GARCH and joint residual resampling
- Partial-dependence plots for QRNN features
- Monte Carlo VaR
- A margin/variation-margin forecast module

---

## 14. Honesty rules for the write-up

- Report only numbers produced by the code. There are no placeholder results in public files.
- If the QRNN does not beat FHS, say so plainly and explain why (tail-data scarcity, and GARCH already capturing volatility clustering).
- Label every assumption: spreads, scenario parameters, limit calibration, data proxies.
- State that the book is hypothetical and built from public data.

---

## 15. Likely interview probes (prepare answers)

1. Why HS as the main champion, and what are its weaknesses? (Ghost effects, slow reaction, window-length choice.)
2. How does a 250-day window with 1% VaR behave? Roughly 2–3 tail points; the quantile interpolation convention matters.
3. Why can VaR not simply be added across desks, and what does the diversification number mean?
4. Hypothetical vs actual P&L: why backtest on hypothetical?
5. What does a Christoffersen rejection tell you that Kupiec does not?
6. Why ES, and why is ES harder to backtest than VaR?
7. How would you explain the QRNN to the Risk & Audit Committee, and would you put it into production?
8. What drives EUA prices, and how did the MSR change the market? (Link to thesis.)
9. How does √T scaling for liquidity horizons fail?
10. What would you change with access to real position data and bid–ask spreads?
