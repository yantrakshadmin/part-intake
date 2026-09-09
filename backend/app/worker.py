"""Celery worker: runs the CPU-heavy STEP extraction off the request cycle.

Run: celery -A app.worker worker --loglevel=info --concurrency=2
Concurrency note: each extraction is CPU-bound; set concurrency ~= vCPUs.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .config import settings
from .geometry import extract_part
from .models import ExtractionJob

logger = logging.getLogger(__name__)

celery_app = Celery("intake", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1  # fair dispatch for long tasks

_engine = create_engine(settings.database_url, pool_pre_ping=True)


@celery_app.task(name="extract_step", time_limit=300, soft_time_limit=270)
def extract_step(job_id: str) -> None:
    run_extraction(job_id)


def run_extraction(job_id: str) -> None:
    """Plain function so the API can run it in a thread when the Celery
    broker is unreachable (dev without redis) — see main.upload_step."""
    with Session(_engine) as db:
        job = db.get(ExtractionJob, job_id)
        if job is None:
            logger.error("Job %s not found", job_id)
            return
        job.status = "processing"
        db.commit()

        try:
            glb_path = str(Path(job.step_path).with_suffix(".glb"))
            result = extract_part(job.step_path, glb_path)
            job.glb_path = result.glb_path
            job.result_json = {
                **dataclasses.asdict(result),
                "candidates": [dataclasses.asdict(c) for c in result.candidates],
            }
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            logger.exception("Extraction failed for job %s", job_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        db.commit()
