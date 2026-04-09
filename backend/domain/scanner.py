import hashlib
from pathlib import Path

import mutagen
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    info = {
        "title": None,
        "artist": None,
        "album": None,
        "genre": None,
        "duration_seconds": None,
        "sample_rate": None,
        "channels": None,
    }
    audio = mutagen.File(str(file_path), easy=True)
    if audio:
        info["title"] = (audio.get("title") or [None])[0]
        info["artist"] = (audio.get("artist") or [None])[0]
        info["album"] = (audio.get("album") or [None])[0]
        info["genre"] = (audio.get("genre") or [None])[0]
    audio_full = mutagen.File(str(file_path))
    if info["genre"] is None and audio_full:
        # Format-specific fallbacks for genre tags when easy mode misses.
        for key in ("TCON", "\xa9gen", "GENRE", "genre"):
            try:
                value = audio_full.get(key)
            except Exception:
                value = None
            if value:
                if isinstance(value, (list, tuple)):
                    info["genre"] = str(value[0]) if value[0] is not None else None
                else:
                    info["genre"] = str(value)
                break
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
    pending_by_identity: dict[str, Track] = {}
    pending_by_path: dict[str, Track] = {}

    for file_path in root.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        discovered += 1

        try:
            # Savepoint per file so one uniqueness conflict doesn't abort whole scan.
            with db.begin_nested():
                stat = file_path.stat()
                rel_path = str(file_path.resolve())
                existing_by_path = pending_by_path.get(rel_path) or db.execute(
                    select(Track).where(Track.file_path == rel_path)
                ).scalar_one_or_none()
                if (
                    existing_by_path
                    and abs(existing_by_path.file_mtime - stat.st_mtime) < 0.0001
                    and int(existing_by_path.file_size) == int(stat.st_size)
                ):
                    skipped += 1
                    continue

                file_hash = compute_sha256(file_path)
                identity_key = file_hash
                existing_by_identity = pending_by_identity.get(identity_key) or db.execute(
                    select(Track).where(Track.identity_key == identity_key)
                ).scalar_one_or_none()
                existing = existing_by_path or existing_by_identity

                # Duplicate content at a different path: keep canonical record and skip duplicate copy.
                if existing_by_identity and not existing_by_path and existing_by_identity.file_path != rel_path:
                    skipped += 1
                    continue

                tags = extract_tags(file_path)
                if existing:
                    old_path = existing.file_path
                    old_identity = existing.identity_key
                    existing.file_path = rel_path
                    existing.identity_key = identity_key
                    existing.file_size = stat.st_size
                    existing.file_hash = file_hash
                    existing.file_mtime = stat.st_mtime
                    existing.title = tags["title"]
                    existing.artist = tags["artist"]
                    existing.album = tags["album"]
                    existing.genre = tags["genre"]
                    existing.duration_seconds = tags["duration_seconds"]
                    existing.sample_rate = tags["sample_rate"]
                    existing.channels = tags["channels"]
                    if old_path and pending_by_path.get(old_path) is existing:
                        pending_by_path.pop(old_path, None)
                    if old_identity and pending_by_identity.get(old_identity) is existing:
                        pending_by_identity.pop(old_identity, None)
                    pending_by_path[rel_path] = existing
                    pending_by_identity[identity_key] = existing
                    updated += 1
                else:
                    track = Track(
                        file_path=rel_path,
                        identity_key=identity_key,
                        file_size=stat.st_size,
                        file_hash=file_hash,
                        file_mtime=stat.st_mtime,
                        title=tags["title"],
                        artist=tags["artist"],
                        album=tags["album"],
                        genre=tags["genre"],
                        duration_seconds=tags["duration_seconds"],
                        sample_rate=tags["sample_rate"],
                        channels=tags["channels"],
                    )
                    db.add(track)
                    pending_by_path[rel_path] = track
                    pending_by_identity[identity_key] = track
                    created += 1
        except IntegrityError:
            skipped += 1
            continue

    db.commit()
    return {"discovered": discovered, "created": created, "updated": updated, "skipped": skipped}
