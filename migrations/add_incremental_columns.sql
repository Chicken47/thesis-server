-- Incremental reanalysis support
-- Run once against Neon DB

-- 1. Add columns to analyses table
ALTER TABLE analyses
  ADD COLUMN IF NOT EXISTS is_incremental       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS based_on_analysis_id UUID REFERENCES analyses(id),
  ADD COLUMN IF NOT EXISTS changes_made         JSONB;

-- 2. Conviction timeline table (denormalised for fast reads / charting)
CREATE TABLE IF NOT EXISTS conviction_timeline (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  stock_symbol  TEXT        NOT NULL,
  analysis_id   UUID        REFERENCES analyses(id),
  conviction    NUMERIC,
  verdict       TEXT,
  analysis_type TEXT        DEFAULT 'full',   -- 'full' | 'incremental'
  changes_made  JSONB,
  analysed_at   TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conviction_timeline_symbol_date
  ON conviction_timeline (stock_symbol, analysed_at DESC);
