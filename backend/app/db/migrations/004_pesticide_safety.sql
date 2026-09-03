-- Safety constraints on the pesticide allowlist.
--
-- This table is the most dangerous thing in the system: a wrong dose or a wrong
-- pre-harvest interval sends a farmer to spray the wrong amount, or to harvest
-- while residue is still above the legal limit. The constraints below make
-- classes of bad row impossible to store, rather than relying on whoever writes
-- the INSERT to be careful.

-- Registrations get revoked and labels get amended. A row verified three years
-- ago against a register that has since changed must stop serving on its own
-- rather than quietly remaining authoritative.
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS review_by date;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS revoked_reason text;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS max_applications_per_season int;
ALTER TABLE pesticides ADD COLUMN IF NOT EXISTS notes_for_farmer text;

-- A verified row must carry who verified it, when, and against what. Provenance
-- is not optional metadata: without it nobody can re-check the row later, and an
-- unattributable dose is indistinguishable from an invented one.
ALTER TABLE pesticides DROP CONSTRAINT IF EXISTS pesticides_verified_needs_provenance;
ALTER TABLE pesticides ADD CONSTRAINT pesticides_verified_needs_provenance CHECK (
    NOT verified OR (
        verified_by IS NOT NULL AND length(trim(verified_by)) > 0
        AND verified_at IS NOT NULL
        AND register_source IS NOT NULL AND length(trim(register_source)) > 0
        AND review_by IS NOT NULL
    )
);

-- A pre-harvest interval of zero is almost always a data-entry error rather than
-- a real label value, and it is the error that puts residue on food.
ALTER TABLE pesticides DROP CONSTRAINT IF EXISTS pesticides_phi_plausible;
ALTER TABLE pesticides ADD CONSTRAINT pesticides_phi_plausible
    CHECK (phi_days >= 1 AND phi_days <= 365);

ALTER TABLE pesticides DROP CONSTRAINT IF EXISTS pesticides_dose_present;
ALTER TABLE pesticides ADD CONSTRAINT pesticides_dose_present
    CHECK (length(trim(dose)) > 0);

-- Actives banned or restricted in a country. This overrides everything: a
-- denied ingredient is never served even if some row claims it is verified.
-- Separate table so a national ban can be applied in one statement without
-- hunting through per-crop rows.
CREATE TABLE IF NOT EXISTS pesticide_denylist (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    country           text NOT NULL,
    active_ingredient text NOT NULL,
    reason            text NOT NULL,
    source            text NOT NULL,
    added_by          text NOT NULL,
    added_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (country, active_ingredient)
);

-- Serving index: only rows that are verified, unrevoked and unexpired.
DROP INDEX IF EXISTS pesticides_lookup_idx;
CREATE INDEX IF NOT EXISTS pesticides_serving_idx
    ON pesticides (country, crop_code, disease_code)
    WHERE verified AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS pesticide_denylist_idx
    ON pesticide_denylist (country, active_ingredient);
