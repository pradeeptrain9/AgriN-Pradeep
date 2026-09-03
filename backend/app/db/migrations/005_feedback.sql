-- Farmer feedback and grievances.
--
-- A system that gives agronomic advice to smallholders must have a way for the
-- farmer to say it was wrong. Without one, errors are invisible to the operator
-- and the farmer has no recourse -- which is the gap the DPG "do no harm"
-- indicator asks about.
--
-- It also produces the only labelled data this project can honestly obtain:
-- a farmer correcting a diagnosis is a ground-truth label from the field, which
-- is exactly what the disease model lacks.
CREATE TABLE IF NOT EXISTS feedback (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('advisory', 'diagnosis')),
    field_id      uuid REFERENCES fields(id) ON DELETE SET NULL,
    diagnosis_id  uuid REFERENCES diagnoses(id) ON DELETE SET NULL,
    verdict       text NOT NULL CHECK (
                      verdict IN ('helpful', 'unclear', 'wrong', 'harmful')),
    -- What the farmer says it actually was. For a diagnosis this is a ground
    -- truth label from a real field, which no dataset here provides.
    corrected_label text,
    comment       text,
    -- Anything marked harmful needs a human, not a dashboard.
    acknowledged_at timestamptz,
    acknowledged_by text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feedback_user_idx ON feedback (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_triage_idx ON feedback (verdict, created_at DESC)
    WHERE verdict IN ('wrong', 'harmful') AND acknowledged_at IS NULL;
