-- Reuse a narration until the advice it describes actually changes.
--
-- The narration is a rephrasing of the advisory payload and nothing else -- the
-- guard in ai/guard.py enforces that it introduces no figure the payload does
-- not contain. So for an unchanged payload the narration is unchanged too, and
-- regenerating it buys nothing.
--
-- The payload changes when weather ingests (daily), a satellite pass lands
-- (every few days), or the farmer edits the crop or soil card. Screen opens are
-- far more frequent than any of those, and every one of them was paying for the
-- same sentences.

ALTER TABLE advisories ADD COLUMN IF NOT EXISTS payload_hash TEXT;

-- One row per field per language per distinct advice, looked up by hash.
CREATE INDEX IF NOT EXISTS advisories_narration_cache_idx
  ON advisories (field_id, lang, payload_hash);
