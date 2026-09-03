-- Never pay twice to identify the same photograph.
--
-- The same image bytes reaching the node again is not a rare edge case:
--
--   * A farmer who gets "inconclusive" photographs the same leaf again, or
--     taps submit twice on a slow connection.
--   * The offline outbox replays a queued diagnosis; a partial failure after
--     the call but before the response landed replays it again.
--   * Two people on a shared handset check the same plant.
--
-- The hash is taken over the *prepared* bytes -- after downscaling and EXIF
-- stripping -- so it is deterministic for a given input and does not change
-- when a phone writes different metadata.
--
-- This caches the identification, not the diagnosis record. Each submission is
-- still its own row: who asked, about which field, when. Only the paid call is
-- skipped.

ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS image_sha256 TEXT;

CREATE INDEX IF NOT EXISTS diagnoses_image_lookup_idx
  ON diagnoses (image_sha256, crop_code, created_at DESC);
