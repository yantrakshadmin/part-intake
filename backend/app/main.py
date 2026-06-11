"""FastAPI app — Phase 1: Part Intake.

Endpoints:
  POST /api/parts/upload-step   multipart .stp upload -> job_id (enqueues worker)
  GET  /api/jobs/{job_id}       poll status; returns dims + candidates when done
  GET  /api/files/{name}        serves GLB to the Three.js viewer (dev/local)
  POST /api/parts               create Part Profile (manual or confirmed STP)
  GET  /api/parts               list profiles

Production notes (see PLANNING.md):
  - swap direct upload for S3 presigned PUT; pass the S3 key to the job
  - serve GLB via CloudFront, not this API
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base, ExtractionJob, PartProfile
from .schemas import JobStatusOut, PartProfileIn, PartProfileOut
from .worker import extract_step

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base.metadata.create_all(engine)  # v1; switch to Alembic when schema stabilizes

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
    if not name.endswith((".stp", ".step")):
        raise HTTPException(400, "Only .stp / .step files are accepted.")

    job_id = str(uuid.uuid4())
    dest_dir = Path(settings.local_storage_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{job_id}.step"

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

    extract_step.delay(job_id)
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
    return FileResponse(path, media_type="model/gltf-binary")


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
