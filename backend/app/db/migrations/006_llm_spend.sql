-- Every Claude call this node makes, priced at the time it was made.
--
-- The Copernicus budget already has pu_ledger; this is the same idea for the
-- one dependency that bills in real money. Without it the only way to find out
-- what a pilot costs is to read the invoice after it arrives, and the only way
-- to stop a runaway loop is to notice it.
--
-- Rates are stored per row rather than looked up at read time, so a price
-- change never rewrites history: what a call cost is what it cost.

CREATE TABLE IF NOT EXISTS llm_ledger (
  id                 BIGSERIAL PRIMARY KEY,
  month              DATE NOT NULL,
  purpose            TEXT NOT NULL,          -- 'diagnosis' | 'narration'
  model              TEXT NOT NULL,
  user_id            UUID REFERENCES users(id) ON DELETE SET NULL,
  input_tokens       INTEGER NOT NULL DEFAULT 0,
  output_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  usd                NUMERIC(10, 6) NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_ledger_month_idx ON llm_ledger (month);
CREATE INDEX IF NOT EXISTS llm_ledger_user_day_idx ON llm_ledger (user_id, created_at DESC);
