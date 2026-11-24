-- Battery Calculator (investor scenarios). Kept apart from the official index:
-- scenarios never write battery_config, calc_run, result_* or result_publication.

SET search_path = bri;

-- One investor scenario: parameters, state of the asynchronous calculation and KPIs.
CREATE TABLE calc_scenario (
    scenario_id     bigserial PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'success', 'failed')),
    progress        real NOT NULL DEFAULT 0,          -- 0..1
    message         text,
    params          jsonb NOT NULL,                   -- validated user input (bri.calculator.params)
    tech_hash       char(64) NOT NULL,                -- battery dispatch parameters, key of the revenue cache
    model_version   text NOT NULL,
    period_start    date NOT NULL,
    period_end      date NOT NULL,                    -- inclusive
    days_used       integer,
    summary         jsonb                             -- KPIs, revenue by market, sensitivity
);
CREATE INDEX calc_scenario_status_idx ON calc_scenario (status);

-- Revenue of the normalized battery (1 MW, energy = duration) per market and day.
-- Valid while the day's inputs (input_sha256 from market_day) and the model are unchanged.
CREATE TABLE scenario_revenue_cache (
    tech_hash       char(64) NOT NULL,
    market          text NOT NULL,                    -- DA, IDM15, IDM60, IMB, AFRR
    delivery_day    date NOT NULL,
    model_version   text NOT NULL,
    input_sha256    text NOT NULL,
    revenue_eur_mw  double precision NOT NULL,
    efc             double precision NOT NULL,        -- equivalent full cycles of the day
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tech_hash, market, delivery_day)
);

-- Historical daily revenue used by a scenario (strategy already applied, scaled to its power).
CREATE TABLE scenario_daily (
    scenario_id     bigint NOT NULL REFERENCES calc_scenario ON DELETE CASCADE,
    delivery_day    date NOT NULL,
    market          text NOT NULL,                    -- market chosen for the day
    revenue_eur     double precision NOT NULL,
    efc             double precision NOT NULL,
    PRIMARY KEY (scenario_id, delivery_day)
);

-- Projection year by year (year 0 = investment).
CREATE TABLE scenario_year (
    scenario_id         bigint NOT NULL REFERENCES calc_scenario ON DELETE CASCADE,
    year                smallint NOT NULL,
    soh_start           double precision,
    soh_end             double precision,
    efc                 double precision,
    discharged_mwh      double precision,
    revenue_gross       double precision,
    aggregator_fee      double precision,
    grid_fees           double precision,
    opex                double precision,
    ebitda              double precision,
    capex               double precision,            -- initial investment, replacements, minus residual value
    depreciation        double precision,
    interest            double precision,
    principal           double precision,
    tax                 double precision,
    cf_project          double precision,
    cf_equity           double precision,
    dscr                double precision,
    PRIMARY KEY (scenario_id, year)
);

INSERT INTO schema_migration (version) VALUES ('002_calculator');
