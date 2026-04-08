from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from backend.domain.analysis import run_track_analysis
from backend.domain.db import SessionLocal
from backend.domain.models import AnalysisJob
from backend.domain.settings import settings


def analyze_track_job(track_id: str, job_id: str) -> dict:
    db = SessionLocal()
    try:
        record = db.execute(select(AnalysisJob).where(AnalysisJob.id == UUID(job_id))).scalar_one()
        record.status = "running"
        record.started_at = datetime.now(timezone.utc)
        db.commit()
        features = run_track_analysis(db, UUID(track_id), settings.analysis_version)
        record.status = "completed"
        record.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"track_id": track_id, "features": features}
    except Exception as exc:
        db.rollback()
        record = db.execute(select(AnalysisJob).where(AnalysisJob.id == UUID(job_id))).scalar_one_or_none()
        if record:
            record.status = "failed"
            record.error_message = str(exc)
            record.retry_count = (record.retry_count or 0) + 1
            record.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise
    finally:
        db.close()
