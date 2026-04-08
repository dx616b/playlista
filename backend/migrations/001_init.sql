CREATE TABLE IF NOT EXISTS tracks (
  id UUID PRIMARY KEY,
  file_path TEXT NOT NULL UNIQUE,
  identity_key TEXT NOT NULL UNIQUE,
  file_size BIGINT NOT NULL,
  file_hash TEXT NOT NULL,
  file_mtime DOUBLE PRECISION NOT NULL,
  title TEXT,
  artist TEXT,
  album TEXT,
  duration_seconds DOUBLE PRECISION,
  sample_rate INTEGER,
  channels INTEGER,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
  id UUID PRIMARY KEY,
  track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  analysis_version TEXT NOT NULL,
  status TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  queued_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  started_at TIMESTAMP WITH TIME ZONE,
  finished_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_track_id ON analysis_jobs(track_id);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs(status);

CREATE TABLE IF NOT EXISTS track_features_raw (
  id UUID PRIMARY KEY,
  track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  analysis_version TEXT NOT NULL,
  features_json JSONB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE (track_id, analysis_version)
);

CREATE TABLE IF NOT EXISTS track_features_norm (
  id UUID PRIMARY KEY,
  track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  analysis_version TEXT NOT NULL,
  bpm DOUBLE PRECISION,
  loudness DOUBLE PRECISION,
  energy DOUBLE PRECISION,
  danceability DOUBLE PRECISION,
  spectral_centroid DOUBLE PRECISION,
  spectral_rolloff DOUBLE PRECISION,
  mfcc_mean DOUBLE PRECISION,
  key TEXT,
  scale TEXT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE (track_id, analysis_version)
);

CREATE TABLE IF NOT EXISTS playlists (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  constraints_json JSONB NOT NULL,
  explanation_json JSONB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
  id UUID PRIMARY KEY,
  playlist_id UUID NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  score DOUBLE PRECISION NOT NULL,
  reason_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist_id ON playlist_tracks(playlist_id);
