# Data sources

Raw files are **not** committed to this repository (Investing.com terms of use prohibit redistribution).
This file documents every series so the dataset can be rebuilt. Fill in one row per downloaded file.

| File | Series | Source | Contract definition | Download date | Notes |
|---|---|---|---|---|---|
| `eua_dec_investing.csv` | EUA | [Investing.com, Carbon Emissions Futures](https://www.investing.com/commodities/carbon-emissions-historical-data) | ICE EUA front-December, continuous; 1 lot = 1,000 t | | Check mid-December roll jumps |
| `uka_investing.csv` | UKA | Investing.com (UK site), UK Emissions Allowances Energy c1 | Appears to be front-month (UKAFMc1), not December | | Check zero-volume / stale-price days |
| `ttf_front_investing.csv` | TTF | Investing.com, Dutch TTF Natural Gas Futures | Front-month, continuous; monthly roll | | |
| `eurgbp_ecb.csv` | EUR/GBP | ECB Data Portal, series `EXR.D.GBP.EUR.SP00.A` | Daily reference rate, GBP per 1 EUR | | FX is the only series that may be forward-filled |
| `msr_events.csv` | MSR events | [msr_carbon_repricing](https://github.com/AndradaDuta/msr_carbon_repricing) | 27 announcement dates | | Own thesis dataset |

## How to rebuild

1. Download each series from the source above as daily CSV over the full available history.
2. Save it in this folder under the file name in the table.
3. Record the download date in the table.
4. Run `python -m euets_var.pipeline --config config/book.yaml`.
