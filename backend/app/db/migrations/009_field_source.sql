-- How a boundary was obtained, because the two are not the same evidence.
--
-- Until now every field was walked: a farmer stood on each corner and the GPS
-- recorded where they were. That is a survey. Tapping corners on a satellite
-- basemap is an estimate -- it inherits the basemap's own registration error,
-- and a farmer drawing from memory may include a neighbour's strip or miss a
-- bund entirely.
--
-- Both are useful. A drawn field gets genuine NDVI and genuine weather for
-- those coordinates, which is the whole point. But the advisory computes
-- fertiliser and water in kilograms and millimetres *per hectare*, so the area
-- is a multiplier on every number a farmer acts on. If a drawn boundary is
-- later mistaken for a surveyed one -- in a dispute, in an insurance claim, in
-- a district-level aggregate -- nothing in the data says otherwise. This
-- column says otherwise.
--
-- Existing rows default to 'walked' because at the time this ships that is
-- what every one of them is.

ALTER TABLE fields ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'walked';

DO $$
BEGIN
    ALTER TABLE fields ADD CONSTRAINT fields_source_check
        CHECK (source IN ('walked', 'drawn'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
