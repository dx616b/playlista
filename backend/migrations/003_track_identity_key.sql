ALTER TABLE tracks
  ADD COLUMN IF NOT EXISTS identity_key TEXT,
  ADD COLUMN IF NOT EXISTS file_size BIGINT;

UPDATE tracks
SET identity_key = file_hash
WHERE identity_key IS NULL;

UPDATE tracks
SET file_size = 0
WHERE file_size IS NULL;

ALTER TABLE tracks
  ALTER COLUMN identity_key SET NOT NULL,
  ALTER COLUMN file_size SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE indexname = 'tracks_identity_key_key'
  ) THEN
    CREATE UNIQUE INDEX tracks_identity_key_key ON tracks(identity_key);
  END IF;
END $$;
