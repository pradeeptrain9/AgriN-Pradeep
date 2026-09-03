-- AgriN node schema. Idempotent: safe to re-run.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------- identity
CREATE TABLE IF NOT EXISTS users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone       text UNIQUE NOT NULL,
    name        text,
    lang        text NOT NULL DEFAULT 'en',
    country     text NOT NULL DEFAULT 'IN',
    role        text NOT NULL DEFAULT 'farmer',   -- farmer | extension | admin
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS otp_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone       text NOT NULL,
    code_hash   text NOT NULL,
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz,
    attempts    int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS otp_phone_idx ON otp_codes (phone, created_at DESC);

-- ------------------------------------------------------------------ fields
CREATE TABLE IF NOT EXISTS fields (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    geom        geography(Polygon, 4326) NOT NULL,
    centroid    geography(Point, 4326) NOT NULL,
    area_ha     double precision NOT NULL,
    country     text,
    admin1      text,
    admin2      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);
CREATE INDEX IF NOT EXISTS fields_user_idx ON fields (user_id);
CREATE INDEX IF NOT EXISTS fields_geom_idx ON fields USING GIST (geom);
CREATE INDEX IF NOT EXISTS fields_centroid_idx ON fields USING GIST (centroid);

CREATE TABLE IF NOT EXISTS crop_cycles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    field_id        uuid NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    crop_code       text NOT NULL,          -- AGROVOC-aligned, see engine/crops.py
    variety         text,
    sowing_date     date NOT NULL,
    harvest_date    date,
    status          text NOT NULL DEFAULT 'active',   -- active | harvested | abandoned
    previous_crop   text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crop_cycles_field_idx ON crop_cycles (field_id, status);

-- Soil is effectively static, so this cache is permanent. SoilGrids is a beta
-- service with a 5 req/min fair-use limit and no uptime guarantee.
CREATE TABLE IF NOT EXISTS soil_profiles (
    field_id    uuid PRIMARY KEY REFERENCES fields(id) ON DELETE CASCADE,
    source      text NOT NULL DEFAULT 'soilgrids-v2',
    props       jsonb NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

-- --------------------------------------------------------- time series data
CREATE TABLE IF NOT EXISTS observations (
    time            timestamptz NOT NULL,
    field_id        uuid NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    index_name      text NOT NULL,          -- ndvi | ndmi | ndre
    value           double precision NOT NULL,
    valid_fraction  double precision NOT NULL,   -- share of pixels left after SCL mask
    source          text NOT NULL DEFAULT 'sentinel2-l2a',
    PRIMARY KEY (field_id, index_name, time)
);
SELECT create_hypertable('observations', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS weather_daily (
    time        timestamptz NOT NULL,
    field_id    uuid NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    tmax_c      double precision,
    tmin_c      double precision,
    tmean_c     double precision,
    rh_mean     double precision,
    wind2_ms    double precision,
    rad_mj      double precision,
    precip_mm   double precision,
    et0_mm      double precision,
    soil_moist  double precision,
    source      text NOT NULL DEFAULT 'open-meteo',
    PRIMARY KEY (field_id, time)
);
SELECT create_hypertable('weather_daily', 'time', if_not_exists => TRUE);

-- ------------------------------------------------------------- advisory/AI
CREATE TABLE IF NOT EXISTS advisories (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    field_id        uuid NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    crop_cycle_id   uuid REFERENCES crop_cycles(id) ON DELETE SET NULL,
    generated_at    timestamptz NOT NULL DEFAULT now(),
    engine_version  text NOT NULL,
    payload         jsonb NOT NULL,     -- deterministic engine output
    narration       jsonb,              -- Claude output, per language
    lang            text NOT NULL DEFAULT 'en'
);
CREATE INDEX IF NOT EXISTS advisories_field_idx ON advisories (field_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS diagnoses (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    field_id          uuid REFERENCES fields(id) ON DELETE SET NULL,
    image_path        text,
    on_device_label   text,
    on_device_conf    double precision,
    on_device_entropy double precision,
    server_label      text,
    server_conf       double precision,
    resolved_by       text NOT NULL,    -- on_device | claude_vision | inconclusive
    treatment         jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS diagnoses_user_idx ON diagnoses (user_id, created_at DESC);

-- Country-scoped registered-product allowlist. Claude may only name products
-- that appear here; anything else is stripped before the advice is returned.
CREATE TABLE IF NOT EXISTS pesticides (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    country           text NOT NULL,
    crop_code         text NOT NULL,
    disease_code      text NOT NULL,
    active_ingredient text NOT NULL,
    product_name      text,
    dose              text NOT NULL,
    phi_days          int NOT NULL,
    notes             text,
    UNIQUE (country, crop_code, disease_code, active_ingredient)
);

-- --------------------------------------------------- Copernicus PU accounting
CREATE TABLE IF NOT EXISTS pu_ledger (
    id          bigserial PRIMARY KEY,
    month       date NOT NULL,
    units       double precision NOT NULL,
    endpoint    text NOT NULL,
    field_id    uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pu_ledger_month_idx ON pu_ledger (month);
