"""FastAPI app — Phase 1: Part Intake.

Endpoints:
  POST /api/parts/upload-step   multipart .stp/.igs upload -> job_id (enqueues worker)
  GET  /api/jobs/{job_id}       poll status; returns dims + candidates when done
  GET  /api/files/{name}        serves GLB to the Three.js viewer (dev/local)
  POST /api/parts               create Part Profile (manual or confirmed STP)
  GET  /api/parts               list profiles
  POST /api/parts/{id}/solve    enqueue a nesting solve (worker) -> solve_job_id
  GET  /api/solve-jobs/{id}     poll solve status; returns catalogue/custom/truck
  GET  /api/packaging           packaging master list (seeded + custom)
  POST /api/packaging           add a custom box (status=draft)
  GET  /api/vehicles            vehicle master list

Production notes (see PLANNING.md):
  - swap direct upload for S3 presigned PUT; pass the S3 key to the job
  - serve GLB via CloudFront, not this API
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base, ExtractionJob, Packaging, PartProfile, SolveJob, Vehicle
from .schemas import (
    JobStatusOut, PackagingIn, PackagingOut, PartProfileIn, PartProfileOut,
    SolveIn, SolveJobStatusOut, VehicleOut,
)
from .seed_data import seed_master_data
from .geometry import SUPPORTED_SUFFIXES
from .worker import extract_step, run_extraction, run_solve, solve_part

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base.metadata.create_all(engine)  # v1; switch to Alembic when schema stabilizes


def _ensure_added_columns(eng) -> None:
    """Add columns that `create_all` cannot, because there is no Alembic.

    `create_all` creates missing TABLES but never ALTERs an existing one, and
    `seed_master_data` below runs at import time and selects every column. So
    a column added to a shipped table takes down the whole app on any existing
    dev.db or docker-compose volume, with a raw OperationalError from an
    `import app.main`. One narrow shim beats that; delete it when Alembic lands.
    """
    added = {"packaging": {"kind": "VARCHAR(16) DEFAULT 'container'",
                          "tare_kg": "FLOAT", "material": "VARCHAR(16)"}}
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    for table, columns in added.items():
        if table not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in columns.items():
            if name in have:
                continue
            logger.warning("Adding missing column %s.%s (no Alembic)", table, name)
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


_ensure_added_columns(engine)
with SessionLocal() as _db:
    seed_master_data(_db)

app = FastAPI(title="Part Intake", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/api/parts/upload-step", response_model=JobStatusOut)
async def upload_step(file: UploadFile, db: Session = Depends(get_db)):
    name = (file.filename or "").lower()
    suffix = next((s for s in SUPPORTED_SUFFIXES if name.endswith(s)), None)
    if suffix is None:
        raise HTTPException(400, "Only .stp / .step / .igs / .iges files are accepted.")

    job_id = str(uuid.uuid4())
    dest_dir = Path(settings.local_storage_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{job_id}{suffix}"

    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_mb * (1 << 20):
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB.")
            out.write(chunk)

    job = ExtractionJob(id=job_id, status="pending", step_path=str(dest))
    db.add(job)
    db.commit()

    # Prefer the Celery worker; if the broker is unreachable (dev without
    # redis, or a stale Celery client after a redis restart), fall back to a
    # background thread so uploads never hard-fail. Extraction is CPU-bound
    # (2–30 s) but releases the GIL inside OpenCascade/numpy, so the dev
    # server stays responsive enough.
    try:
        extract_step.delay(job_id)
    except Exception:
        logger.warning(
            "Celery enqueue failed for job %s — running extraction "
            "in-process (dev fallback)", job_id, exc_info=True,
        )
        threading.Thread(
            target=run_extraction, args=(job_id,), daemon=True,
        ).start()
    return JobStatusOut(job_id=job_id, status="pending")


@app.get("/api/jobs/{job_id}", response_model=JobStatusOut)
def job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    result = None
    if job.status == "done" and job.result_json:
        r = dict(job.result_json)
        r["glb_url"] = f"/api/files/{Path(job.glb_path).name}"
        r.pop("glb_path", None)
        result = r
    return JobStatusOut(
        job_id=job.id, status=job.status, error=job.error, result=result
    )


@app.get("/api/files/{name}")
def serve_file(name: str):
    path = Path(settings.local_storage_dir) / Path(name).name  # no traversal
    if not path.exists():
        raise HTTPException(404, "File not found.")
    # Was hardcoded to the GLB type for every file this endpoint serves; the
    # insert drawing (PNG) needs its own. Guessed from the suffix, falling
    # back to GLB so nothing that works today breaks.
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "model/gltf-binary")


@app.post("/api/parts", response_model=PartProfileOut)
def create_part(payload: PartProfileIn, db: Session = Depends(get_db)):
    L, B, H = payload.canonical_dims()
    glb_path = None
    if payload.source == "stp":
        if not payload.job_id:
            raise HTTPException(400, "job_id required for STP-sourced parts.")
        job = db.get(ExtractionJob, payload.job_id)
        if job is None or job.status != "done":
            raise HTTPException(400, "Extraction job not found or not finished.")
        glb_path = job.glb_path

    part = PartProfile(
        part_number=payload.part_number,
        part_name=payload.part_name,
        length_mm=L, breadth_mm=B, height_mm=H,
        weight_kg=payload.weight_kg,
        source=payload.source,
        job_id=payload.job_id,
        glb_path=glb_path,
        confirmed_orientation=(
            {"matrix": payload.confirmed_orientation}
            if payload.confirmed_orientation else None
        ),
    )
    db.add(part)
    db.commit()
    db.refresh(part)
    return _to_out(part)


@app.post("/api/parts/{part_id}/solve", response_model=SolveJobStatusOut,
         status_code=202)
def solve_part_endpoint(part_id: int, payload: SolveIn,
                        db: Session = Depends(get_db)):
    part = db.get(PartProfile, part_id)
    if part is None:
        raise HTTPException(404, "Part not found.")
    if part.source == "manual" or not part.glb_path:
        raise HTTPException(
            422,
            "Nesting needs the CAD file. This part was entered by hand, and "
            "bounding dimensions alone cannot beat the cuboid answer — "
            "upload the .stp/.igs to get a real fit.",
        )
    if part.job_id:
        job = db.get(ExtractionJob, part.job_id)
        if job is None or job.status != "done":
            raise HTTPException(
                422, "Extraction job for this part is missing or not finished."
            )
    vehicle = db.scalars(select(Vehicle).where(Vehicle.name == payload.vehicle)).first()
    if vehicle is None:
        raise HTTPException(404, f"Vehicle '{payload.vehicle}' not found.")

    # ponytail: no result cache. A cache key would need tare_kg + vehicle +
    # top_n or it risks serving a wrong truck number for a different request,
    # and that subtlety isn't worth it before anyone has used this endpoint.
    solve_job_id = str(uuid.uuid4())
    db.add(SolveJob(id=solve_job_id, part_id=part_id, status="pending"))
    db.commit()

    # Params ride with the task, not in SolveJob.result_json -- run_solve
    # overwrites that column with the result, so a redelivered task (acks_late
    # is on) would have answered a different question.
    params = {"tare_kg": payload.tare_kg, "vehicle": payload.vehicle,
              "top_n": payload.top_n, "assets": payload.assets}
    try:
        solve_part.delay(solve_job_id, params)
    except Exception:
        logger.warning(
            "Celery enqueue failed for solve job %s — running solve "
            "in-process (dev fallback)", solve_job_id, exc_info=True,
        )
        threading.Thread(
            target=run_solve, args=(solve_job_id, params), daemon=True,
        ).start()
    return SolveJobStatusOut(solve_job_id=solve_job_id, status="pending")


@app.get("/api/solve-jobs/{job_id}", response_model=SolveJobStatusOut)
def solve_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(SolveJob, job_id)
    if job is None:
        raise HTTPException(404, "Solve job not found.")
    result = job.result_json if job.status == "done" else None
    return SolveJobStatusOut(
        solve_job_id=job.id, status=job.status, error=job.error, result=result
    )


@app.get("/api/packaging", response_model=list[PackagingOut])
def list_packaging(db: Session = Depends(get_db)):
    return db.scalars(select(Packaging).order_by(Packaging.id)).all()


@app.post("/api/packaging", response_model=PackagingOut)
def create_packaging(payload: PackagingIn, db: Session = Depends(get_db)):
    exists = db.scalars(
        select(Packaging).where(Packaging.item_code == payload.item_code)
    ).first()
    if exists:
        raise HTTPException(409, f"Item code '{payload.item_code}' already exists.")
    if (payload.inner_l_mm > payload.outer_l_mm
            or payload.inner_b_mm > payload.outer_b_mm
            or payload.inner_h_mm > payload.outer_h_mm):
        raise HTTPException(400, "Inner dimensions cannot exceed outer dimensions.")
    box = Packaging(**payload.model_dump(), status="draft")
    db.add(box)
    db.commit()
    db.refresh(box)
    return box


@app.get("/api/vehicles", response_model=list[VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    return db.scalars(select(Vehicle).order_by(Vehicle.id)).all()


@app.get("/api/parts", response_model=list[PartProfileOut])
def list_parts(db: Session = Depends(get_db)):
    parts = db.scalars(
        select(PartProfile).order_by(PartProfile.created_at.desc())
    ).all()
    return [_to_out(p) for p in parts]


def _to_out(p: PartProfile) -> PartProfileOut:
    return PartProfileOut(
        id=p.id, part_number=p.part_number, part_name=p.part_name,
        length_mm=p.length_mm, breadth_mm=p.breadth_mm, height_mm=p.height_mm,
        weight_kg=p.weight_kg, source=p.source,
        glb_url=f"/api/files/{Path(p.glb_path).name}" if p.glb_path else None,
        created_at=p.created_at,
    )
