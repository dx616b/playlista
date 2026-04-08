import hashlib
from pathlib import Path

import mutagen
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import Track

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aiff", ".aac"}


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_identity_key(file_path: Path, file_size: int) -> str:
    token = f"{file_path.name.lower()}:{file_size}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_tags(file_path: Path) -> dict:
    info = {"title": None, "artist": None, "album": None, "duration_seconds": None, "sample_rate": None, "channels": None}
    audio = mutagen.File(str(file_path), easy=True)
    if audio:
        info["title"] = (audio.get("title") or [None])[0]
        info["artist"] = (audio.get("artist") or [None])[0]
        info["album"] = (audio.get("album") or [None])[0]
    audio_full = mutagen.File(str(file_path))
    if audio_full and getattr(audio_full, "info", None):
        info["duration_seconds"] = float(getattr(audio_full.info, "length", 0.0) or 0.0)
        info["sample_rate"] = getattr(audio_full.info, "sample_rate", None)
        info["channels"] = getattr(audio_full.info, "channels", None)
    return info


def scan_library(db: Session, root_path: str) -> dict:
    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Invalid root path: {root_path}")

    discovered = 0
    created = 0
    updated = 0
    skipped = 0

    for file_path in root.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        discovered += 1

        stat = file_path.stat()
        rel_path = str(file_path.resolve())
        identity_key = compute_identity_key(file_path, stat.st_size)

        existing_by_identity = db.execute(select(Track).where(Track.identity_key == identity_key)).scalar_one_or_none()
        existing_by_path = db.execute(select(Track).where(Track.file_path == rel_path)).scalar_one_or_none()
        existing = existing_by_identity or existing_by_path
        if (
            existing
            and abs(existing.file_mtime - stat.st_mtime) < 0.0001
            and int(existing.file_size) == int(stat.st_size)
        ):
            skipped += 1
            continue

        file_hash = compute_sha256(file_path)
        tags = extract_tags(file_path)
        if existing:
            existing.file_path = rel_path
            existing.identity_key = identity_key
            existing.file_size = stat.st_size
            existing.file_hash = file_hash
            existing.file_mtime = stat.st_mtime
            existing.title = tags["title"]
            existing.artist = tags["artist"]
            existing.album = tags["album"]
            existing.duration_seconds = tags["duration_seconds"]
            existing.sample_rate = tags["sample_rate"]
            existing.channels = tags["channels"]
            updated += 1
        else:
            db.add(
                Track(
                    file_path=rel_path,
                    identity_key=identity_key,
                    file_size=stat.st_size,
                    file_hash=file_hash,
                    file_mtime=stat.st_mtime,
                    title=tags["title"],
                    artist=tags["artist"],
                    album=tags["album"],
                    duration_seconds=tags["duration_seconds"],
                    sample_rate=tags["sample_rate"],
                    channels=tags["channels"],
                )
            )
            created += 1

    db.commit()
    return {"discovered": discovered, "created": created, "updated": updated, "skipped": skipped}
