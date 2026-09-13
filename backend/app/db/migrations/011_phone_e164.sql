-- Move existing accounts onto the same phone form the app now sends.
--
-- `app/phone.py` normalises a typed number to +<country><national> so that
-- 9876543210, 09876543210 and +919876543210 are one account rather than three.
-- Accounts created before that change hold whatever was typed, so without this
-- the fix would orphan them: the farmer signs in, the node finds no user for
-- the normalised number, creates a fresh one, and their fields are gone.
--
-- India only. This node's other supported dial codes have no accounts and no
-- national-length rule encoded here, and guessing at a length is how two
-- different people end up sharing one login.
--
-- A row is rewritten only when the target does not already exist. If both
-- forms are present they are two rows with two sets of fields, and merging
-- them is a judgement about whose data is whose -- not something a migration
-- should decide silently at deploy time. Those rows are left exactly as they
-- are, and the operator can find them with the query at the bottom.

DO $$
DECLARE
    moved int := 0;
    collided int := 0;
BEGIN
    -- Ten digits, no punctuation, Indian mobile range.
    WITH candidates AS (
        SELECT id, phone, '+91' || phone AS target
        FROM users
        WHERE country = 'IN'
          AND phone ~ '^[6-9][0-9]{9}$'
    ),
    safe AS (
        SELECT c.id, c.target
        FROM candidates c
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.phone = c.target)
    )
    UPDATE users u
    SET phone = s.target
    FROM safe s
    WHERE u.id = s.id;
    GET DIAGNOSTICS moved = ROW_COUNT;

    SELECT count(*) INTO collided
    FROM users c
    WHERE c.country = 'IN'
      AND c.phone ~ '^[6-9][0-9]{9}$'
      AND EXISTS (SELECT 1 FROM users u WHERE u.phone = '+91' || c.phone);

    RAISE NOTICE 'phone normalisation: % account(s) moved to E.164, % left alone '
                 'because both forms exist', moved, collided;
END $$;

-- Leading zero, same treatment.
DO $$
BEGIN
    WITH candidates AS (
        SELECT id, phone, '+91' || substring(phone from 2) AS target
        FROM users
        WHERE country = 'IN'
          AND phone ~ '^0[6-9][0-9]{9}$'
    ),
    safe AS (
        SELECT c.id, c.target
        FROM candidates c
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.phone = c.target)
    )
    UPDATE users u
    SET phone = s.target
    FROM safe s
    WHERE u.id = s.id;
END $$;

-- Pending OTPs are keyed on the phone too. They expire in five minutes, so
-- rewriting them is a courtesy rather than a correctness fix: without it,
-- anyone mid-sign-in at deploy time gets "Incorrect code" once and succeeds on
-- a second attempt. Cheap enough to do properly.
UPDATE otp_codes
SET phone = '+91' || phone
WHERE phone ~ '^[6-9][0-9]{9}$'
  AND consumed_at IS NULL
  AND expires_at > now();

-- Accounts an operator has to resolve by hand, if the notice above reported any:
--
--   SELECT id, phone, created_at FROM users
--   WHERE phone ~ '^[6-9][0-9]{9}$'
--      OR phone ~ '^\+91[6-9][0-9]{9}$'
--   ORDER BY regexp_replace(phone, '^\+91', ''), created_at;
