import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.domain.db import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    identity_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    file_mtime: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    artist: Mapped[str | None] = mapped_column(Text)
    album: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"))
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    track = relationship("Track")


class TrackFeaturesRaw(Base):
    __tablename__ = "track_features_raw"
    __table_args__ = (UniqueConstraint("track_id", "analysis_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"))
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    features_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TrackFeaturesNorm(Base):
    __tablename__ = "track_features_norm"
    __table_args__ = (UniqueConstraint("track_id", "analysis_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"))
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    bpm: Mapped[float | None] = mapped_column(Float)
    loudness: Mapped[float | None] = mapped_column(Float)
    energy: Mapped[float | None] = mapped_column(Float)
    danceability: Mapped[float | None] = mapped_column(Float)
    spectral_centroid: Mapped[float | None] = mapped_column(Float)
    spectral_rolloff: Mapped[float | None] = mapped_column(Float)
    mfcc_mean: Mapped[float | None] = mapped_column(Float)
    mfcc_var: Mapped[float | None] = mapped_column(Float)
    key: Mapped[str | None] = mapped_column(String(8))
    scale: Mapped[str | None] = mapped_column(String(8))
    key_strength: Mapped[float | None] = mapped_column(Float)
    onset_rate: Mapped[float | None] = mapped_column(Float)
    spectral_flux: Mapped[float | None] = mapped_column(Float)
    dynamic_complexity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    constraints_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    explanation_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlists.id", ondelete="CASCADE"))
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
