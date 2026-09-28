-- Database schema for euets_var_challenger (DuckDB).
-- Re-runnable: every table is created only if it does not exist yet.
--
-- Conventions
--   level  : 'book' | 'desk' | 'group'
--   node   : the name at that level, e.g. 'EUA_OUTRIGHT', 'TRADING', 'GROUP'
--   All monetary values are in EUR unless the column name says otherwise.

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

-- One row per tradable instrument (EUA, UKA, TTF).
CREATE TABLE IF NOT EXISTS instruments (
    instrument   VARCHAR PRIMARY KEY,
    currency     VARCHAR,          -- quote currency: EUR or GBP
    lot_size_t   DOUBLE,           -- tonnes CO2 per lot (carbon only; NULL for TTF)
    description  VARCHAR
);

-- Book -> desk -> group hierarchy.
CREATE TABLE IF NOT EXISTS hierarchy (
    book    VARCHAR PRIMARY KEY,
    desk    VARCHAR,
    entity  VARCHAR                -- the group
);

-- Limit definitions; utilisation and status are computed in views.
CREATE TABLE IF NOT EXISTS limits (
    level        VARCHAR,
    node         VARCHAR,
    metric       VARCHAR,          -- e.g. 'VAR_99', 'OPEN_POSITION_T', 'DTL_DAYS'
    limit_value  DOUBLE,
    warning_pct  DOUBLE            -- e.g. 0.80
);

-- ---------------------------------------------------------------------------
-- Market data (stored raw, before any transformation)
-- ---------------------------------------------------------------------------

-- Daily settlement prices in the instrument's own currency.
CREATE TABLE IF NOT EXISTS prices (
    date        DATE,
    instrument  VARCHAR,
    settle      DOUBLE,
    volume      DOUBLE,            -- NULL if the source has no volume
    source      VARCHAR,
    PRIMARY KEY (date, instrument)
);

-- Daily FX reference rates, e.g. pair 'EURGBP' = GBP per 1 EUR (ECB convention).
CREATE TABLE IF NOT EXISTS fx_rates (
    date  DATE,
    pair  VARCHAR,
    rate  DOUBLE,
    PRIMARY KEY (date, pair)
);

-- MSR announcement dates (from msr_carbon_repricing). Not in the original spec; see decision log.
CREATE TABLE IF NOT EXISTS msr_events (
    date         DATE PRIMARY KEY,
    description  VARCHAR
);

-- ---------------------------------------------------------------------------
-- Positions
-- ---------------------------------------------------------------------------

-- Constant EUR notional per book and instrument, one row per date.
CREATE TABLE IF NOT EXISTS positions (
    date          DATE,
    book          VARCHAR,
    instrument    VARCHAR,
    notional_eur  DOUBLE,          -- signed: long > 0, short < 0
    PRIMARY KEY (date, book, instrument)
);

-- ---------------------------------------------------------------------------
-- Outputs (long format: one row per value)
-- ---------------------------------------------------------------------------

-- Hypothetical (static-position) P&L at book, desk and group level.
CREATE TABLE IF NOT EXISTS pnl (
    date                  DATE,
    level                 VARCHAR,
    node                  VARCHAR,
    hypothetical_pnl_eur  DOUBLE
);

-- Every risk number from every model, e.g. model 'HS', metric 'VAR', confidence 0.99.
CREATE TABLE IF NOT EXISTS risk_results (
    date        DATE,
    level       VARCHAR,
    node        VARCHAR,
    model       VARCHAR,
    metric      VARCHAR,
    confidence  DOUBLE,
    value_eur   DOUBLE
);

-- Scenario P&L on the day's snapshot positions.
CREATE TABLE IF NOT EXISTS stress_results (
    date      DATE,
    scenario  VARCHAR,
    level     VARCHAR,
    node      VARCHAR,
    pnl_eur   DOUBLE
);
