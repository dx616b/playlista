ALTER TABLE track_features_raw
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE track_features_raw
SET updated_at = created_at
WHERE updated_at IS NULL;

ALTER TABLE track_features_raw
  ALTER COLUMN updated_at SET DEFAULT now(),
  ALTER COLUMN updated_at SET NOT NULL;
