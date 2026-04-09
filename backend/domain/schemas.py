from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    root_path: str | None = None
    root_paths: list[str] | None = None


class AnalysisEnqueueRequest(BaseModel):
    track_ids: list[UUID] | None = None


class PlaylistGenerateRequest(BaseModel):
    name: str = "Generated Playlist"
    limit: int = Field(default=25, ge=1, le=200)
    seed_track_id: UUID | None = None
    profile: str = Field(default="balanced", pattern="^(balanced|focus|workout|chill|drive)$")
    min_bpm: float | None = None
    max_bpm: float | None = None
    target_energy_curve: str = Field(default="flat", pattern="^(warmup|peak|cooldown|flat)$")
    diversity: float = Field(default=0.3, ge=0.0, le=1.0)
    artist_cooldown: int = Field(default=2, ge=0, le=20)
    album_cooldown: int = Field(default=1, ge=0, le=20)
    max_bpm_jump: float = Field(default=18.0, ge=2.0, le=80.0)
    min_transition_score: float = Field(default=0.55, ge=0.0, le=1.0)
    temperature: float = Field(default=0.0, ge=0.0, le=0.2)
    variation_seed: int | None = None
    history_window: int = Field(default=3, ge=0, le=50)
    history_penalty: float = Field(default=0.12, ge=0.0, le=1.0)
    strict_constraints: bool = False
    genre_mode: str = Field(default="balanced", pattern="^(strict|balanced|open)$")
    track_feedback: dict[str, float] | None = None
    artist_feedback: dict[str, float] | None = None


class PlaylistManualRequest(BaseModel):
    name: str = "Manual Playlist"
    track_ids: list[UUID] = Field(default_factory=list, min_length=1, max_length=2000)


class NavidromeImportRequest(BaseModel):
    playlist_id: UUID
    navidrome_url: str
    username: str
    password: str
    playlist_format: str = Field(default="m3u8", pattern="^(m3u|m3u8)$")


class TrackOut(BaseModel):
    id: UUID
    file_path: str
    title: str | None
    artist: str | None
    album: str | None
    genre: str | None
    duration_seconds: float | None
    created_at: datetime
