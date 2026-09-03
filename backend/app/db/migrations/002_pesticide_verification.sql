-- Chemical treatment rows must be explicitly verified against a national
-- pesticide register before they can ever be shown to a farmer. A fresh node
-- has no verified rows and therefore gives cultural advice only.
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS verified boolean NOT NULL DEFAULT false;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS verified_by text;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS register_source text;

CREATE INDEX IF NOT EXISTS pesticides_lookup_idx
    ON pesticides (country, crop_code, disease_code) WHERE verified;

-- Record of what the on-device model actually returned, so gate thresholds can
-- be tuned against real field photos rather than benchmark numbers.
ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS gate jsonb;
ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS crop_code text;
