-- Add token tracking columns to analyses table
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS input_tokens  INTEGER;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS output_tokens INTEGER;
