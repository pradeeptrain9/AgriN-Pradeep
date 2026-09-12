-- Rename the escalation route from claude_vision to cloud_vision.
--
-- The value names WHERE a photograph was decided, not WHO decided it. Baking a
-- vendor into it was a mistake that only became visible when the vendor changed:
-- a node now running Gemini was writing rows saying "claude_vision", which is
-- the kind of quiet untruth that makes a dataset useless for auditing later.
--
-- Provenance did not disappear with the rename -- the model that actually
-- answered is recorded on the diagnosis in its own column, which is where it
-- belongs and where it can differ per row.
--
-- Existing rows are migrated rather than left mixed. A column with two spellings
-- of the same meaning is a reporting bug waiting to happen.

UPDATE diagnoses SET resolved_by = 'cloud_vision' WHERE resolved_by = 'claude_vision';
