import json
import logging
import time
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from rq import Retry
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.domain.db import get_db
from backend.domain.models import AnalysisJob, Playlist, PlaylistTrack, Track, TrackFeaturesNorm, TrackFeaturesRaw
from backend.domain.playlist import compute_playlist_quality, generate_playlist
from backend.domain.queueing import get_queue
from backend.domain.scanner import scan_library
from backend.domain.schemas import (
    AnalysisEnqueueRequest,
    NavidromeImportRequest,
    PlaylistGenerateRequest,
    PlaylistManualRequest,
    ScanRequest,
    TrackOut,
)
from backend.domain.settings import get_default_music_root, get_scan_presets, settings
from backend.worker.jobs import analyze_track_job

app = FastAPI(title="Playlista API", version="0.1.0")
logger = logging.getLogger("playlista.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log_middleware(request, call_next):
    started = time.time()
    response = await call_next(request)
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": int((time.time() - started) * 1000),
            }
        )
    )
    return response


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    total_tracks = db.execute(select(func.count(Track.id))).scalar_one()
    total_jobs = db.execute(select(func.count(AnalysisJob.id))).scalar_one()
    pending_jobs = db.execute(select(func.count(AnalysisJob.id)).where(AnalysisJob.status == "queued")).scalar_one()
    failed_jobs = db.execute(select(func.count(AnalysisJob.id)).where(AnalysisJob.status == "failed")).scalar_one()
    return {
        "tracks_total": total_tracks,
        "analysis_jobs_total": total_jobs,
        "analysis_jobs_queued": pending_jobs,
        "analysis_jobs_failed": failed_jobs,
    }


@app.post("/library/scan")
def library_scan(payload: ScanRequest, db: Session = Depends(get_db)):
    roots: list[str] = []
    if payload.root_paths:
        roots.extend([p.strip() for p in payload.root_paths if p and p.strip()])
    if payload.root_path and payload.root_path.strip():
        roots.append(payload.root_path.strip())
    if not roots:
        roots = get_scan_presets()
    if not roots:
        roots = [get_default_music_root()]
    roots = list(dict.fromkeys(roots))

    try:
        if len(roots) == 1:
            result = scan_library(db, roots[0])
            result["roots"] = roots
            return result

        per_root: list[dict] = []
        root_errors: list[dict] = []
        totals = {"discovered": 0, "created": 0, "updated": 0, "skipped": 0}
        for root in roots:
            try:
                result = scan_library(db, root)
                per_root.append({"root_path": root, **result})
                for key in totals:
                    totals[key] += int(result.get(key, 0))
            except ValueError as exc:
                root_errors.append({"root_path": root, "detail": str(exc)})

        if not per_root and root_errors:
            raise HTTPException(status_code=400, detail=f"All scan roots invalid: {[e['root_path'] for e in root_errors]}")

        return {
            **totals,
            "roots": roots,
            "per_root": per_root,
            "root_errors": root_errors,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/library/presets")
def library_presets():
    return {"presets": get_scan_presets()}


@app.get("/tracks", response_model=list[TrackOut])
def list_tracks(db: Session = Depends(get_db)):
    rows = db.execute(select(Track).order_by(Track.created_at.desc())).scalars().all()
    return rows


@app.get("/tracks/status")
def list_tracks_status(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    q: str | None = None,
    genre: str | None = None,
    analyzed: str = Query(default="all", pattern="^(all|yes|no)$"),
    sort_by: str = Query(
        default="artist",
        pattern="^(id|artist|title|album|genre|status|bpm|energy|key_strength|spectral_flux|dynamic_complexity|created_at)$",
    ),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    join_cond = (TrackFeaturesNorm.track_id == Track.id) & (TrackFeaturesNorm.analysis_version == settings.analysis_version)
    raw_join_cond = (TrackFeaturesRaw.track_id == Track.id) & (TrackFeaturesRaw.analysis_version == settings.analysis_version)
    base = (
        select(Track, TrackFeaturesNorm, TrackFeaturesRaw)
        .outerjoin(TrackFeaturesNorm, join_cond)
        .outerjoin(TrackFeaturesRaw, raw_join_cond)
    )
    count_base = (
        select(func.count(Track.id))
        .select_from(Track)
        .outerjoin(TrackFeaturesNorm, join_cond)
        .outerjoin(TrackFeaturesRaw, raw_join_cond)
    )

    if q:
        like_q = f"%{q}%"
        text_filter = (
            Track.title.ilike(like_q)
            | Track.artist.ilike(like_q)
            | Track.album.ilike(like_q)
            | Track.file_path.ilike(like_q)
        )
        base = base.where(text_filter)
        count_base = count_base.where(text_filter)
    if genre:
        like_genre = f"%{genre}%"
        genre_filter = Track.genre.ilike(like_genre)
        base = base.where(genre_filter)
        count_base = count_base.where(genre_filter)

    if analyzed == "yes":
        base = base.where(TrackFeaturesNorm.track_id.is_not(None))
        count_base = count_base.where(TrackFeaturesNorm.track_id.is_not(None))
    elif analyzed == "no":
        base = base.where(TrackFeaturesNorm.track_id.is_(None))
        count_base = count_base.where(TrackFeaturesNorm.track_id.is_(None))

    sort_col_map = {
        "id": Track.id,
        "artist": func.lower(Track.artist),
        "title": func.lower(Track.title),
        "album": func.lower(Track.album),
        "genre": func.lower(Track.genre),
        "status": TrackFeaturesNorm.track_id,
        "bpm": TrackFeaturesNorm.bpm,
        "energy": TrackFeaturesNorm.energy,
        "key_strength": TrackFeaturesNorm.key_strength,
        "spectral_flux": TrackFeaturesNorm.spectral_flux,
        "dynamic_complexity": TrackFeaturesNorm.dynamic_complexity,
        "created_at": Track.created_at,
    }
    sort_col = sort_col_map.get(sort_by, func.lower(Track.artist))
    if sort_dir == "desc":
        base = base.order_by(sort_col.desc().nullslast(), Track.created_at.desc())
    else:
        base = base.order_by(sort_col.asc().nullslast(), Track.created_at.desc())

    filtered_total = db.execute(count_base).scalar_one()
    offset = (page - 1) * page_size
    rows = db.execute(base.offset(offset).limit(page_size)).all()

    total_tracks = db.execute(select(func.count(Track.id))).scalar_one()
    analyzed_tracks = db.execute(
        select(func.count(Track.id))
        .select_from(Track)
        .join(
            TrackFeaturesNorm,
            (TrackFeaturesNorm.track_id == Track.id) & (TrackFeaturesNorm.analysis_version == settings.analysis_version),
        )
    ).scalar_one()

    tracks = []
    for track, norm, raw in rows:
        raw_features = raw.features_json if raw and raw.features_json else {}
        tracks.append(
            {
                "id": str(track.id),
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "genre": track.genre,
                "file_path": track.file_path,
                "duration_seconds": track.duration_seconds,
                "analyzed": norm is not None,
                "metrics": {
                    "bpm": norm.bpm if norm else None,
                    "energy": norm.energy if norm else None,
                    "danceability": norm.danceability if norm else None,
                    "key_strength": norm.key_strength if norm else None,
                    "spectral_flux": norm.spectral_flux if norm else None,
                    "dynamic_complexity": norm.dynamic_complexity if norm else None,
                },
                "raw_metrics": {
                    "bpm": raw_features.get("bpm"),
                    "energy": raw_features.get("energy"),
                    "loudness": raw_features.get("loudness"),
                    "key_strength": raw_features.get("key_strength"),
                    "spectral_flux": raw_features.get("spectral_flux"),
                    "dynamic_complexity": raw_features.get("dynamic_complexity"),
                },
            }
        )
    return {
        "counts": {
            "total": total_tracks,
            "analyzed": analyzed_tracks,
            "not_analyzed": max(0, total_tracks - analyzed_tracks),
            "filtered_total": filtered_total,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (filtered_total + page_size - 1) // page_size),
        },
        "sort": {"sort_by": sort_by, "sort_dir": sort_dir},
        "tracks": tracks,
    }


@app.post("/analysis/enqueue")
def enqueue_analysis(payload: AnalysisEnqueueRequest, db: Session = Depends(get_db)):
    queue = get_queue("analysis")
    track_ids = payload.track_ids
    if not track_ids:
        # Incremental enqueue: new tracks or tracks updated after last analysis for this version.
        rows = db.execute(
            select(Track.id)
            .outerjoin(
                TrackFeaturesRaw,
                (TrackFeaturesRaw.track_id == Track.id)
                & (TrackFeaturesRaw.analysis_version == settings.analysis_version),
            )
            .where((TrackFeaturesRaw.id.is_(None)) | (Track.updated_at > TrackFeaturesRaw.updated_at))
        ).all()
        track_ids = [row[0] for row in rows]
    enqueued = 0
    for track_id in track_ids:
        job_record = AnalysisJob(track_id=track_id, analysis_version=settings.analysis_version, status="queued")
        db.add(job_record)
        db.flush()
        queue.enqueue(analyze_track_job, str(track_id), str(job_record.id), retry=Retry(max=2))
        enqueued += 1
    db.commit()
    return {"enqueued": enqueued, "analysis_version": settings.analysis_version}


@app.post("/analysis/retry-failed")
def retry_failed_jobs(db: Session = Depends(get_db)):
    queue = get_queue("analysis")
    failed = db.execute(
        select(AnalysisJob).where(AnalysisJob.status == "failed").order_by(AnalysisJob.queued_at.asc())
    ).scalars()
    count = 0
    for row in failed:
        row.status = "queued"
        row.error_message = None
        queue.enqueue(analyze_track_job, str(row.track_id), str(row.id), retry=Retry(max=2))
        count += 1
    db.commit()
    return {"requeued": count}


@app.get("/analysis/jobs")
def get_analysis_jobs(db: Session = Depends(get_db)):
    rows = db.execute(select(AnalysisJob).order_by(AnalysisJob.queued_at.desc()).limit(200)).scalars().all()
    return [
        {
            "id": str(r.id),
            "track_id": str(r.track_id),
            "analysis_version": r.analysis_version,
            "status": r.status,
            "retry_count": r.retry_count,
            "error_message": r.error_message,
            "queued_at": r.queued_at,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in rows
    ]


@app.get("/analysis/progress")
def get_analysis_progress(db: Session = Depends(get_db)):
    grouped = db.execute(
        select(AnalysisJob.status, func.count(AnalysisJob.id)).group_by(AnalysisJob.status)
    ).all()
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    for status, count in grouped:
        if status in counts:
            counts[status] = int(count or 0)
    total = sum(counts.values())
    remaining = counts["queued"] + counts["running"]
    avg_seconds = db.execute(
        select(func.avg(func.extract("epoch", AnalysisJob.finished_at - AnalysisJob.started_at))).where(
            AnalysisJob.status == "completed",
            AnalysisJob.started_at.is_not(None),
            AnalysisJob.finished_at.is_not(None),
        )
    ).scalar_one()
    avg_seconds_per_track = float(avg_seconds or 0.0)
    eta_seconds = float(avg_seconds_per_track * remaining) if avg_seconds_per_track > 0 else 0.0
    return {
        "counts": {"total": total, **counts},
        "remaining": remaining,
        "avg_seconds_per_track": round(avg_seconds_per_track, 3),
        "eta_seconds": round(eta_seconds, 3),
    }


@app.post("/playlists/generate")
def create_playlist(payload: PlaylistGenerateRequest, db: Session = Depends(get_db)):
    profile_defaults = {
        "balanced": {"target_energy_curve": "flat", "diversity": 0.3, "artist_cooldown": 2, "album_cooldown": 1, "max_bpm_jump": 18.0, "min_transition_score": 0.55, "temperature": 0.03, "history_window": 3, "history_penalty": 0.12},
        "focus": {"target_energy_curve": "flat", "diversity": 0.2, "artist_cooldown": 3, "album_cooldown": 2, "max_bpm_jump": 12.0, "min_transition_score": 0.62, "temperature": 0.02, "history_window": 4, "history_penalty": 0.14},
        "workout": {"target_energy_curve": "warmup", "diversity": 0.4, "artist_cooldown": 2, "album_cooldown": 1, "max_bpm_jump": 22.0, "min_transition_score": 0.50, "temperature": 0.05, "history_window": 3, "history_penalty": 0.10},
        "chill": {"target_energy_curve": "cooldown", "diversity": 0.25, "artist_cooldown": 3, "album_cooldown": 2, "max_bpm_jump": 10.0, "min_transition_score": 0.65, "temperature": 0.02, "history_window": 5, "history_penalty": 0.16},
        "drive": {"target_energy_curve": "peak", "diversity": 0.35, "artist_cooldown": 2, "album_cooldown": 1, "max_bpm_jump": 16.0, "min_transition_score": 0.58, "temperature": 0.04, "history_window": 3, "history_penalty": 0.12},
    }
    defaults = profile_defaults.get(payload.profile, profile_defaults["balanced"])
    target_curve = payload.target_energy_curve or defaults["target_energy_curve"]
    diversity = payload.diversity if payload.diversity is not None else defaults["diversity"]
    artist_cooldown = payload.artist_cooldown if payload.artist_cooldown is not None else defaults["artist_cooldown"]
    album_cooldown = payload.album_cooldown if payload.album_cooldown is not None else defaults["album_cooldown"]
    max_bpm_jump = payload.max_bpm_jump if payload.max_bpm_jump is not None else defaults["max_bpm_jump"]
    min_transition_score = (
        payload.min_transition_score if payload.min_transition_score is not None else defaults["min_transition_score"]
    )
    temperature = payload.temperature if payload.temperature is not None else defaults["temperature"]
    history_window = payload.history_window if payload.history_window is not None else defaults["history_window"]
    history_penalty = payload.history_penalty if payload.history_penalty is not None else defaults["history_penalty"]

    try:
        result = generate_playlist(
            db=db,
            name=payload.name,
            limit=payload.limit,
            seed_track_id=payload.seed_track_id,
            min_bpm=payload.min_bpm,
            max_bpm=payload.max_bpm,
            target_energy_curve=target_curve,
            diversity=diversity,
            artist_cooldown=artist_cooldown,
            album_cooldown=album_cooldown,
            max_bpm_jump=max_bpm_jump,
            min_transition_score=min_transition_score,
            temperature=temperature,
            variation_seed=payload.variation_seed,
            history_window=history_window,
            history_penalty=history_penalty,
            strict_constraints=payload.strict_constraints,
            genre_mode=payload.genre_mode,
            candidate_track_ids=None,
            track_feedback=payload.track_feedback,
            artist_feedback=payload.artist_feedback,
        )
        result_diag = result.setdefault("generation_diagnostics", {})
        result_diag["cache_used"] = False
        result_diag["cache_candidate_count"] = 0
        result_diag["cache_fallback_applied"] = False
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _save_manual_tracks(db: Session, playlist_id: UUID, track_ids: list[UUID]) -> None:
    db.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
    for idx, track_id in enumerate(track_ids):
        db.add(
            PlaylistTrack(
                playlist_id=playlist_id,
                track_id=track_id,
                position=idx,
                score=1.0,
                reason_json={"manual": True, "snapshot_path": None},
            )
        )


@app.post("/playlists/manual")
def create_manual_playlist(payload: PlaylistManualRequest, db: Session = Depends(get_db)):
    existing_ids = {
        r[0]
        for r in db.execute(select(Track.id).where(Track.id.in_(payload.track_ids))).all()
    }
    missing = [str(tid) for tid in payload.track_ids if tid not in existing_ids]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown track ids: {missing[:10]}")
    playlist = Playlist(
        name=payload.name,
        constraints_json={"manual": True},
        explanation_json={"source": "manual_builder"},
    )
    db.add(playlist)
    db.flush()
    _save_manual_tracks(db, playlist.id, payload.track_ids)
    db.commit()
    return {"playlist_id": str(playlist.id), "name": playlist.name, "track_count": len(payload.track_ids), "manual": True}


@app.put("/playlists/{playlist_id}/manual")
def update_manual_playlist(playlist_id: UUID, payload: PlaylistManualRequest, db: Session = Depends(get_db)):
    playlist = db.execute(select(Playlist).where(Playlist.id == playlist_id)).scalar_one_or_none()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    existing_ids = {
        r[0]
        for r in db.execute(select(Track.id).where(Track.id.in_(payload.track_ids))).all()
    }
    missing = [str(tid) for tid in payload.track_ids if tid not in existing_ids]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown track ids: {missing[:10]}")
    playlist.name = payload.name
    playlist.constraints_json = {"manual": True}
    playlist.explanation_json = {"source": "manual_builder"}
    _save_manual_tracks(db, playlist.id, payload.track_ids)
    db.commit()
    return {"playlist_id": str(playlist.id), "name": playlist.name, "track_count": len(payload.track_ids), "manual": True}


@app.get("/playlists/{playlist_id}")
def get_playlist(playlist_id: UUID, db: Session = Depends(get_db)):
    playlist = db.execute(select(Playlist).where(Playlist.id == playlist_id)).scalar_one_or_none()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    rows = db.execute(
        select(PlaylistTrack, Track, TrackFeaturesNorm)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .join(
            TrackFeaturesNorm,
            (TrackFeaturesNorm.track_id == PlaylistTrack.track_id)
            & (TrackFeaturesNorm.analysis_version == settings.analysis_version),
        )
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ).all()
    return {
        "id": str(playlist.id),
        "name": playlist.name,
        "constraints": playlist.constraints_json,
        "explanation": playlist.explanation_json,
        "tracks": [
            {
                "position": pt.position,
                "score": pt.score,
                "reason": pt.reason_json,
                "track": {
                    "id": str(track.id),
                    "file_path": track.file_path,
                    "title": track.title,
                    "artist": track.artist,
                    "genre": track.genre,
                },
                "features": {
                    "energy": feat.energy,
                    "bpm": feat.bpm,
                    "key_strength": feat.key_strength,
                    "spectral_flux": feat.spectral_flux,
                    "dynamic_complexity": feat.dynamic_complexity,
                },
            }
            for pt, track, feat in rows
        ],
    }


@app.get("/playlists/{playlist_id}/export.m3u")
def export_playlist_m3u(playlist_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        select(PlaylistTrack, Track)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Playlist not found or empty")

    lines = ["#EXTM3U"]
    for playlist_track, track in rows:
        title = track.title or "Unknown Title"
        artist = track.artist or "Unknown Artist"
        duration = int(track.duration_seconds or 0)
        lines.append(f"#EXTINF:{duration},{artist} - {title}")
        snapshot_path = (playlist_track.reason_json or {}).get("snapshot_path")
        lines.append(snapshot_path or track.file_path)
    content = "\n".join(lines) + "\n"
    return Response(content=content, media_type="audio/x-mpegurl")


@app.get("/playlists/{playlist_id}/export.m3u8")
def export_playlist_m3u8(playlist_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        select(PlaylistTrack, Track)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Playlist not found or empty")

    lines = ["#EXTM3U"]
    for playlist_track, track in rows:
        title = track.title or "Unknown Title"
        artist = track.artist or "Unknown Artist"
        duration = int(track.duration_seconds or 0)
        lines.append(f"#EXTINF:{duration},{artist} - {title}")
        snapshot_path = (playlist_track.reason_json or {}).get("snapshot_path")
        lines.append(snapshot_path or track.file_path)
    content = "\n".join(lines) + "\n"
    return Response(content=content, media_type="application/vnd.apple.mpegurl; charset=utf-8")


@app.get("/playlists/{playlist_id}/export.json")
def export_playlist_json(playlist_id: UUID, db: Session = Depends(get_db)):
    data = get_playlist(playlist_id, db)
    return data


@app.get("/playlists/{playlist_id}/quality")
def get_playlist_quality(playlist_id: UUID, db: Session = Depends(get_db)):
    try:
        return compute_playlist_quality(db, playlist_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/integrations/navidrome/import")
def import_playlist_to_navidrome(payload: NavidromeImportRequest, db: Session = Depends(get_db)):
    rows = db.execute(
        select(PlaylistTrack, Track)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == payload.playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Playlist not found or empty")

    playlist = db.execute(select(Playlist).where(Playlist.id == payload.playlist_id)).scalar_one_or_none()
    playlist_name = playlist.name if playlist else "Playlista Export"
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist_name}"]
    for playlist_track, track in rows:
        title = track.title or "Unknown Title"
        artist = track.artist or "Unknown Artist"
        duration = int(track.duration_seconds or 0)
        lines.append(f"#EXTINF:{duration},{artist} - {title}")
        snapshot_path = (playlist_track.reason_json or {}).get("snapshot_path")
        lines.append(snapshot_path or track.file_path)
    content = "\n".join(lines) + "\n"

    base = payload.navidrome_url.rstrip("/")
    login_url = f"{base}/auth/login"
    import_url = f"{base}/api/playlist"
    try:
        login_req = urllib_request.Request(
            login_url,
            data=json.dumps({"username": payload.username, "password": payload.password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(login_req, timeout=20) as resp:
            login_data = json.loads(resp.read().decode("utf-8"))
        token = login_data.get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Navidrome login did not return token")

        ct = "audio/x-mpegurl" if payload.playlist_format == "m3u" else "application/vnd.apple.mpegurl"
        import_req = urllib_request.Request(
            import_url,
            data=content.encode("utf-8"),
            headers={
                "Content-Type": ct,
                "X-ND-Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib_request.urlopen(import_req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
        return {
            "ok": True,
            "navidrome_status": status,
            "playlist_id": str(payload.playlist_id),
            "playlist_name": playlist_name,
            "import_url": import_url,
            "response_excerpt": body[:500],
        }
    except HTTPException:
        raise
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise HTTPException(status_code=502, detail=f"Navidrome HTTP error {exc.code}: {detail[:500]}") from exc
    except urllib_error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Navidrome: {exc}") from exc
