# Data sources

Raw price files are **not** committed to this repository (Investing.com terms of use prohibit redistribution).
This file documents every series so the dataset can be rebuilt, and records what each series was checked to be.

The sample is frozen at **2026-09-25** for all series (`sample.data_end` in `config/book.yaml`). Rows after that date
are stored in the raw tables but never used.

| File | Series | Source | What the series is | Download date |
|---|---|---|---|---|
| `eua_investing.csv` | EUA | [Investing.com, Carbon Emissions Futures](https://www.investing.com/commodities/carbon-emissions-historical-data) | ICE EUA futures, nearest-contract continuous. **Not** a pure December series (see below). 1 lot = 1,000 t | _to fill in_ (file ends 2026-09-25) |
| `uka_investing.csv` | UKA | [Investing.com (UK site), UK Emissions Allowances c1](https://uk.investing.com/commodities/uk-emissions-allowances-energy-c1-futures-historical-data) | ICE UKA futures, front-month continuous, in GBP. 1 lot = 1,000 t | 2026-10-04 |
| `ttf_front_investing.csv` | TTF | Investing.com, Dutch TTF Natural Gas Futures | Front-month continuous, monthly roll. **Not yet downloaded or checked** | |
| `eurgbp_ecb.csv` | EUR/GBP | ECB Data Portal, series `EXR.D.GBP.EUR.SP00.A` | Daily reference rate, GBP per 1 EUR. **Not yet downloaded** | |
| `msr_events.csv` | MSR events | [msr_carbon_repricing](https://github.com/AndradaDuta/msr_carbon_repricing), `data/events/EU_MSR_Events_sign.xlsx`, sheet `events` | 27 announcement dates. Own thesis dataset, so this file **is** committed | 2026-10-11 |

In both Investing.com files the `Price` column is the closing price the site publishes. It is loaded into the
`settle` column, but it is not guaranteed to equal the official ICE settlement price.

## How to rebuild

1. Download each series from the source above as daily CSV over the full available history (EUA and TTF from 2015-01-01, UKA from 2021-05-01).
2. Save it in this folder under the file name in the table. Investing.com exports use month/day/year dates; the loader rejects any other order.
3. Record the download date in the table.
4. Run `python -m euets_var.pipeline --config config/book.yaml`.

## Checks on each series

Figures in this section were produced from the files on 2026-10-11, on the sample to 2026-09-25.

### EUA

- 3,016 rows, 2015-01-02 to 2026-09-25. No duplicate dates, no weekend rows. The `Change %` column is reproduced by the `Price` column to within rounding.
- **The series is not a front-December continuous.** After each December expiry it follows a thinly traded contract, and later switches to the December contract on a date that differs every year. Evidence:
  - *Volume.* Since December 2022 the median daily volume alternates between under 1,000 lots and 16,000 to 27,000 lots:

    | From | To | Days | Median volume (lots) |
    |---|---|---|---|
    | 2022-12-19 | 2023-03-24 | 69 | 750 |
    | 2023-03-27 | 2023-12-15 | 188 | 16,240 |
    | 2023-12-18 | 2024-01-12 | 17 | 800 |
    | 2024-01-15 | 2024-12-09 | 234 | 25,050 |
    | 2024-12-10 | 2025-06-20 | 134 | 965 |
    | 2025-06-23 | 2025-12-12 | 125 | 26,600 |
    | 2025-12-15 | 2026-06-19 | 130 | 930 |
    | 2026-06-22 | 2026-09-25 | 70 | 18,815 |

    The thin stretches are 36% of days since 19 December 2022.
  - *Price.* Against the December 2023 contract's daily closes as published by PPC Group
    ([January 2023](https://www.ppcgroup.com/en/ppc-group/publications/monthly-reports/apolog-stoix-eua-futures-xrhnmat-ice/january-2023-prices/),
    [March 2023](https://www.ppcgroup.com/en/ppc-group/publications/monthly-reports/apolog-stoix-eua-futures-xrhnmat-ice/march-2023-prices/)):

    | Date | This file | Dec-23 close | File vs Dec-23 |
    |---|---|---|---|
    | 2023-01-02 | 83.67 | 86.28 | −3.0% |
    | 2023-03-24 | 85.40 | 87.65 | −2.6% |
    | 2023-03-27 | 88.13 | 87.41 | +0.8% |
    | 2023-03-28 | 89.40 | 89.32 | +0.1% |
    | 2023-03-30 | 90.80 | 90.96 | −0.2% |

    On 27 March 2023 the file shows a log return of +3.1%, while the December 2023 contract itself moved −0.3%. Almost all of the file's move that day is the gap between two contracts.
- **Eight contract-switch days are excluded** (`data.excluded_returns` in `config/book.yaml`). File log returns on those days:
  2022-12-19 (+1.2%), 2023-03-27 (+3.1%), 2023-12-18 (+3.0%), 2024-01-15 (+4.7%), 2024-12-10 (+3.7%), 2025-06-23 (+1.5%), 2025-12-15 (+1.9%), 2026-06-22 (+2.5%).
  - Status of the evidence: the 27 March 2023 switch is **verified** against published December-contract closes. The other seven are **inferred** from the volume regime change alone, and the size of their gap has not been measured. The December dates are accurate to within one trading day.
- **Before December 2022** volume is thin in most years as well (annual median between 140 and 1,050 lots in 2015 to 2019), so the series probably follows a non-December contract for much of that period too. No days are excluded there. This rests on an **assumption, not a check**: with euro interest rates near zero, the price gap between contracts should have been small.
- **Four placeholder rows are dropped** (`data.stale_rows`): 2015-04-06, 2016-08-29, 2019-12-26 and 2021-04-05. Each is a UK bank holiday with open = high = low = close, no volume and an unchanged price.
- **Volume** is only meaningful for liquidity in the liquid stretches.

### UKA

- 1,358 rows, 2021-05-20 to 2026-10-02; 1,353 rows fall inside the sample. No duplicate dates, no weekend rows. `Change %` is reproduced by `Price` to within rounding.
- **Front-month, not December.** An Argus report of 23 February 2023 put the December 2023 contract at £82.75 with spot near £78.80 to £78.90; the file shows 78.96 that day. (Argus figure noted on 2026-10-04 and not re-checked.)
- **Not stale.** 61% of days carry no volume, but the price moves on them: the daily standard deviation is 2.6% on no-volume days and 2.7% on traded days. There are 7 zero-return days.
- **Roll drift.** The series should climb towards the December price in monthly steps. On the 64 days following an assumed last-Monday expiry the mean log return is +0.6%, against about zero on other days. The t-statistic is 1.4, so this is an imprecise estimate. It is documented, not corrected; it works against the short UKA leg.
- **Calendar.** EUA has a price on 24 days when UKA does not (UK bank holidays), never the reverse.
- **Co-movement.** Correlation with EUA log returns on common dates is 0.57 (0.50 to 0.72 by calendar year). Lag-1 autocorrelation is +0.18, against −0.06 for EUA over the same period, which hints at some smoothing in the prices.
- **Volume** resembles the December contract only from November to the December expiry. In the 1 November to 10 December windows of 2021 to 2025, median EUA volume is 8 to 13 times median UKA volume.

### MSR events

- 27 dates, 2014-01-22 to 2025-11-27. The extra columns (`event_id`, `event_type`, `date_precision`) are kept in the CSV; only `date` and `description` are loaded.
- Five dates have month-only precision in the thesis dataset and are placed on the 15th (the TNAC publications of 2017 to 2021). Six dates fall on a non-trading day (three on a weekend, three on 1 January). Both points must be resolved before the dates are used as model features, so that no event is known earlier than it was announced.
