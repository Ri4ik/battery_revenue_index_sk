-- Battery Revenue Index SK - normalized database (schema "bri").
--
-- Principles:
--   * every input value keeps its provenance: source_artifact (immutable raw content)
--     -> import_batch (one parse attempt) -> price/afrr rows;
--   * input rows are immutable revisions: a new value for the same interval is a new row
--     with a newer batch_id, the current value is the newest successful revision;
--   * times are timestamptz with native interval length (interval_start, interval_end);
--     delivery days are Europe/Bratislava local days;
--   * results are versioned by calc_run, the dashboard reads only result_publication.

CREATE SCHEMA IF NOT EXISTS bri;
SET search_path = bri;

CREATE TABLE IF NOT EXISTS schema_migration (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- reference data

CREATE TABLE market (
    market_id   smallint PRIMARY KEY,
    code        text NOT NULL UNIQUE,          -- DA, IDM15, IDM60, IMB, AFRR
    name        text NOT NULL
);

-- What an input or result row of a market is about.
CREATE TABLE market_component (
    market_id   smallint NOT NULL REFERENCES market,
    component   text NOT NULL,                 -- prices | capacity | energy | total
    PRIMARY KEY (market_id, component)
);

CREATE TABLE data_source (
    source_id   smallint PRIMARY KEY,
    code        text NOT NULL UNIQUE,          -- OKTE, ENTSOE, SEPS_DAMAS
    name        text NOT NULL
);

-- Battery configurations are immutable: changed parameters = new row.
CREATE TABLE battery_config (
    config_id           smallint PRIMARY KEY,
    code                text NOT NULL UNIQUE,  -- e.g. 1h_1c
    energy_mwh          numeric NOT NULL CHECK (energy_mwh > 0),
    power_mw            numeric NOT NULL CHECK (power_mw > 0),
    cycle_limit         numeric NOT NULL CHECK (cycle_limit > 0),
    efficiency          numeric NOT NULL CHECK (efficiency > 0 AND efficiency <= 1),
    soc_min             numeric NOT NULL,
    soc_max             numeric NOT NULL,
    soc_start           numeric NOT NULL,
    dod                 numeric NOT NULL,
    capex_eur_kwh       numeric NOT NULL,
    service_life_years  numeric NOT NULL,
    CHECK (soc_min <= soc_start AND soc_start <= soc_max)
);

-- Market parameters of one battery configuration (also immutable).
-- Interval length is NOT stored here: it comes from the input intervals.
CREATE TABLE market_param (
    config_id       smallint NOT NULL REFERENCES battery_config,
    market_id       smallint NOT NULL,
    component       text NOT NULL,
    power_share     numeric NOT NULL,
    capture_rate    numeric NOT NULL,
    capacity_share  numeric NOT NULL,
    cycle_share     numeric,
    init_position   numeric,
    PRIMARY KEY (config_id, market_id, component),
    FOREIGN KEY (market_id, component) REFERENCES market_component
);

CREATE FUNCTION forbid_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable; insert a new row instead', TG_TABLE_NAME;
END $$;
CREATE TRIGGER battery_config_immutable BEFORE UPDATE ON battery_config
    FOR EACH ROW EXECUTE FUNCTION forbid_update();
CREATE TRIGGER market_param_immutable BEFORE UPDATE ON market_param
    FOR EACH ROW EXECUTE FUNCTION forbid_update();

-- ---------------------------------------------------------------- provenance

-- Immutable raw content of one file or one API response.
CREATE TABLE source_artifact (
    artifact_id      bigserial PRIMARY KEY,
    source_id        smallint NOT NULL REFERENCES data_source,
    transport        text NOT NULL CHECK (transport IN ('api', 'onedrive', 'local', 'legacy')),
    content_sha256   char(64) NOT NULL,
    original_name    text,                     -- file name; NULL for API responses
    raw_archive_path text NOT NULL,            -- raw/<sha256>.<ext> in the server archive
    received_at      timestamptz NOT NULL DEFAULT now(),
    source_metadata  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- endpoint + params, or drive item/ETag
    UNIQUE (source_id, content_sha256)
);

-- One attempt to parse an artifact and write its rows.
CREATE TABLE import_batch (
    batch_id        bigserial PRIMARY KEY,
    artifact_id     bigint NOT NULL REFERENCES source_artifact,
    parser_version  text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_written    integer NOT NULL DEFAULT 0,
    error_summary   text
);
CREATE INDEX import_batch_artifact ON import_batch (artifact_id, parser_version, status);
-- At most one successful import per artifact and parser version.
CREATE UNIQUE INDEX import_batch_one_success ON import_batch (artifact_id, parser_version)
    WHERE status = 'success';

-- ---------------------------------------------------------------- inputs (one table per market)

CREATE TABLE price_da (
    interval_start  timestamptz NOT NULL,
    interval_end    timestamptz NOT NULL,
    price_eur_mwh   numeric(12, 4) NOT NULL,
    batch_id        bigint NOT NULL REFERENCES import_batch,
    PRIMARY KEY (interval_start, batch_id),
    CHECK (interval_end > interval_start)
);

CREATE TABLE price_idm15 (LIKE price_da INCLUDING ALL);
ALTER TABLE price_idm15 ADD FOREIGN KEY (batch_id) REFERENCES import_batch;
CREATE TABLE price_idm60 (LIKE price_da INCLUDING ALL);
ALTER TABLE price_idm60 ADD FOREIGN KEY (batch_id) REFERENCES import_batch;

CREATE TABLE imb_evaluation (
    evaluation  text PRIMARY KEY,              -- proxy < daily < monthly < final
    rank        smallint NOT NULL UNIQUE
);

CREATE TABLE price_imb (
    interval_start  timestamptz NOT NULL,
    interval_end    timestamptz NOT NULL,
    evaluation      text NOT NULL REFERENCES imb_evaluation,
    price_eur_mwh   numeric(12, 4) NOT NULL,
    batch_id        bigint NOT NULL REFERENCES import_batch,
    PRIMARY KEY (interval_start, batch_id),
    CHECK (interval_end > interval_start)
);

-- aFRR capacity is procured per local delivery day in 4-hour blocks and direction.
CREATE TABLE afrr_capacity (
    delivery_day     date NOT NULL,
    direction        text NOT NULL CHECK (direction IN ('POS', 'NEG')),
    block_start_h    smallint NOT NULL CHECK (block_start_h BETWEEN 0 AND 23),
    block_end_h      smallint NOT NULL CHECK (block_end_h BETWEEN 1 AND 24),
    price_eur_mw_h   numeric(12, 4) NOT NULL,
    procured_mw      numeric(12, 4) NOT NULL,
    batch_id         bigint NOT NULL REFERENCES import_batch,
    PRIMARY KEY (delivery_day, direction, block_start_h, batch_id),
    CHECK (block_end_h > block_start_h)
);

-- aFRR activated energy as delivered by SEPS/Damas (raw values, no outlier cleaning).
CREATE TABLE afrr_energy (
    interval_start      timestamptz NOT NULL,
    interval_end        timestamptz NOT NULL,
    up_volume_mwh       numeric(14, 6) NOT NULL,
    up_price_eur_mwh    numeric(12, 4) NOT NULL,
    down_volume_mwh     numeric(14, 6) NOT NULL,
    down_price_eur_mwh  numeric(12, 4) NOT NULL,
    batch_id            bigint NOT NULL REFERENCES import_batch,
    PRIMARY KEY (interval_start, batch_id),
    CHECK (interval_end > interval_start)
);

-- ---------------------------------------------------------------- input readiness

CREATE TABLE market_day (
    market_id           smallint NOT NULL,
    delivery_day        date NOT NULL,
    component           text NOT NULL,
    status              text NOT NULL CHECK (status IN ('complete', 'incomplete', 'missing', 'invalid')),
    detail              text,
    input_max_batch_id  bigint,                -- newest revision among the current values
    input_sha256        char(64),              -- hash of the current values of the day
    checked_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (market_id, delivery_day, component),
    FOREIGN KEY (market_id, component) REFERENCES market_component
);

-- ---------------------------------------------------------------- calculation and results

CREATE TABLE calc_run (
    run_id          bigserial PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    trigger         text NOT NULL CHECK (trigger IN ('cron', 'manual', 'migration', 'backfill', 'test')),
    code_version    text NOT NULL,
    model_version   text NOT NULL,
    error_summary   text
);

-- Exact input snapshot used by a run: the revisions up to input_max_batch_id
-- reproduce the values whose hash is input_sha256.
CREATE TABLE calc_input (
    run_id              bigint NOT NULL REFERENCES calc_run,
    market_id           smallint NOT NULL,
    delivery_day        date NOT NULL,
    component           text NOT NULL,
    input_max_batch_id  bigint,
    input_sha256        char(64) NOT NULL,
    PRIMARY KEY (run_id, market_id, delivery_day, component),
    FOREIGN KEY (market_id, component) REFERENCES market_component
);

CREATE TABLE result_daily (
    run_id        bigint NOT NULL REFERENCES calc_run,
    config_id     smallint NOT NULL REFERENCES battery_config,
    market_id     smallint NOT NULL,
    delivery_day  date NOT NULL,
    component     text NOT NULL,
    revenue_eur   double precision NOT NULL,
    PRIMARY KEY (run_id, config_id, market_id, delivery_day, component),
    FOREIGN KEY (market_id, component) REFERENCES market_component
);

CREATE TABLE result_interval (
    run_id          bigint NOT NULL REFERENCES calc_run,
    config_id       smallint NOT NULL REFERENCES battery_config,
    market_id       smallint NOT NULL,
    component       text NOT NULL,
    interval_start  timestamptz NOT NULL,
    interval_end    timestamptz NOT NULL,
    revenue_eur     double precision NOT NULL,
    soc             double precision,
    PRIMARY KEY (run_id, config_id, market_id, component, interval_start),
    FOREIGN KEY (market_id, component) REFERENCES market_component
);

-- The one result version shown on the website per configuration, market and day.
CREATE TABLE result_publication (
    config_id     smallint NOT NULL REFERENCES battery_config,
    market_id     smallint NOT NULL REFERENCES market,
    delivery_day  date NOT NULL,
    run_id        bigint NOT NULL REFERENCES calc_run,
    published_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (config_id, market_id, delivery_day)
);

-- ---------------------------------------------------------------- operations

-- One invocation of the daily pipeline (independent of whether anything was computed).
CREATE TABLE pipeline_run (
    pipeline_run_id  bigserial PRIMARY KEY,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    trigger          text NOT NULL,
    status           text NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    messages         text
);

-- Files picked up from the OneDrive inbox and what happened to them.
CREATE TABLE inbox_file (
    inbox_file_id   bigserial PRIMARY KEY,
    remote_path     text NOT NULL,             -- path in 1_Nahrat at discovery time
    remote_version  text NOT NULL,             -- size + modification time (+ hash if provided)
    artifact_id     bigint REFERENCES source_artifact,
    state           text NOT NULL CHECK (state IN ('imported', 'duplicate', 'archive_pending', 'archived', 'rejected')),
    target_path     text,                      -- where the file was / must be moved
    last_error      text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (remote_path, remote_version)
);

-- ---------------------------------------------------------------- views

-- Current value = newest successful revision.
CREATE VIEW price_da_current AS
    SELECT DISTINCT ON (p.interval_start) p.*
    FROM price_da p JOIN import_batch b USING (batch_id)
    WHERE b.status = 'success'
    ORDER BY p.interval_start, p.batch_id DESC;
CREATE VIEW price_idm15_current AS
    SELECT DISTINCT ON (p.interval_start) p.*
    FROM price_idm15 p JOIN import_batch b USING (batch_id)
    WHERE b.status = 'success'
    ORDER BY p.interval_start, p.batch_id DESC;
CREATE VIEW price_idm60_current AS
    SELECT DISTINCT ON (p.interval_start) p.*
    FROM price_idm60 p JOIN import_batch b USING (batch_id)
    WHERE b.status = 'success'
    ORDER BY p.interval_start, p.batch_id DESC;
-- IMB: best evaluation first, then newest revision.
CREATE VIEW price_imb_current AS
    SELECT DISTINCT ON (p.interval_start) p.*
    FROM price_imb p JOIN import_batch b USING (batch_id) JOIN imb_evaluation e USING (evaluation)
    WHERE b.status = 'success'
    ORDER BY p.interval_start, e.rank DESC, p.batch_id DESC;
CREATE VIEW afrr_capacity_current AS
    SELECT DISTINCT ON (c.delivery_day, c.direction, c.block_start_h) c.*
    FROM afrr_capacity c JOIN import_batch b USING (batch_id)
    WHERE b.status = 'success'
    ORDER BY c.delivery_day, c.direction, c.block_start_h, c.batch_id DESC;
CREATE VIEW afrr_energy_current AS
    SELECT DISTINCT ON (e.interval_start) e.*
    FROM afrr_energy e JOIN import_batch b USING (batch_id)
    WHERE b.status = 'success'
    ORDER BY e.interval_start, e.batch_id DESC;

-- What the dashboard shows: the published total per configuration, market and day.
CREATE VIEW published_daily_total AS
    SELECT p.config_id, c.code AS config_code, c.energy_mwh, c.cycle_limit,
           m.code AS market, p.delivery_day, r.revenue_eur, p.run_id, p.published_at
    FROM result_publication p
    JOIN result_daily r ON r.run_id = p.run_id AND r.config_id = p.config_id
                       AND r.market_id = p.market_id AND r.delivery_day = p.delivery_day
                       AND r.component = 'total'
    JOIN battery_config c ON c.config_id = p.config_id
    JOIN market m ON m.market_id = p.market_id;

-- ---------------------------------------------------------------- seed

INSERT INTO market (market_id, code, name) VALUES
    (1, 'DA',    'Day-ahead market (OKTE DAM)'),
    (2, 'IDM15', 'Intraday market, 15-minute products (OKTE IDM)'),
    (3, 'IDM60', 'Intraday market, 60-minute products (OKTE IDM)'),
    (4, 'IMB',   'Imbalance settlement price (OKTE)'),
    (5, 'AFRR',  'aFRR capacity + energy');

INSERT INTO market_component (market_id, component) VALUES
    (1, 'prices'), (1, 'total'),
    (2, 'prices'), (2, 'total'),
    (3, 'prices'), (3, 'total'),
    (4, 'prices'), (4, 'total'),
    (5, 'capacity'), (5, 'energy'), (5, 'total');

INSERT INTO data_source (source_id, code, name) VALUES
    (1, 'OKTE',       'OKTE, a.s. public API (isot.okte.sk, iszo.okte.sk)'),
    (2, 'ENTSOE',     'ENTSO-E Transparency Platform API'),
    (3, 'SEPS_DAMAS', 'SEPS Damas export "Regulacna elektrina (denna)"');

INSERT INTO imb_evaluation (evaluation, rank) VALUES
    ('proxy', 0), ('daily', 1), ('monthly', 2), ('final', 3);

INSERT INTO schema_migration (version) VALUES ('001_schema');
