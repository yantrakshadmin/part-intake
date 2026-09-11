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

import dataclasses
import datetime as dt
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
from .models import (
    Base, ExtractionJob, Packaging, PartProfile, Project, Proposal, SolveJob,
    Vehicle,
)
from .schemas import (
    JobStatusOut, PackagingIn, PackagingOut, PartProfileIn, PartProfileOut,
    ProjectIn, ProjectOut, ProjectPatch, ProjectSummaryOut, ProposalOut,
    RunOut, SolveIn, SolveJobStatusOut, TruckFitIn, TruckFitOut, VehicleOut,
)
from .engine import parts_per_truck
from .runs import _run_has_content, _run_out, proposal_run_for
from .seed_data import seed_master_data
from .geometry import SUPPORTED_SUFFIXES
from .worker import (extract_step, render_proposal, run_extraction, run_proposal,
                     run_solve, solve_part)

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
                          "tare_kg": "FLOAT", "material": "VARCHAR(16)"},
             "part_profiles": {"project_id": "INTEGER"},
             "solve_jobs": {"project_id": "INTEGER", "inputs_json": "JSON"}}
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


@app.api_route("/api/files/{name}", methods=["GET", "HEAD"])
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

    project = None
    if payload.project_id is not None:
        project = db.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(404, f"Project {payload.project_id} not found.")
        existing = db.scalars(
            select(PartProfile).where(PartProfile.project_id == payload.project_id)
        ).first()
        if existing is not None:
            raise HTTPException(
                409,
                f"Project already has a part (#{existing.id}). Re-uploading a "
                "corrected CAD is not supported yet.",
            )

    part = PartProfile(
        part_number=payload.part_number,
        part_name=payload.part_name,
        length_mm=L, breadth_mm=B, height_mm=H,
        weight_kg=payload.weight_kg,
        source=payload.source,
        job_id=payload.job_id,
        glb_path=glb_path,
        project_id=payload.project_id,
        confirmed_orientation=(
            {"matrix": payload.confirmed_orientation}
            if payload.confirmed_orientation else None
        ),
    )
    db.add(part)
    if project is not None:
        project.updated_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(part)
    return _to_out(part)


def _enqueue_solve(part: PartProfile, payload: SolveIn,
                   db: Session) -> SolveJobStatusOut:
    """Validate a part, create the SolveJob row and enqueue the worker.

    Shared by /api/parts/{id}/solve and /api/projects/{id}/solve so the
    validation, the SolveJob row, the params dict, the .delay and the
    threading fallback exist exactly once. `project_id` is taken from
    `part.project_id`, not a parameter: the part IS the project's part (the
    project route looks it up by project_id already), so deriving it here
    means a solve run under /api/parts/{id}/solve for a project's part also
    lands under that project, instead of only the ones enqueued through
    /api/projects/{id}/solve.
    """
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

    # Params ride with the task, not in SolveJob.result_json -- run_solve
    # overwrites that column with the result, so a redelivered task (acks_late
    # is on) would have answered a different question. inputs_json is a
    # separate column recorded here for the same reason, so a run's inputs
    # survive over HTTP without touching result_json.
    params = {"tare_kg": payload.tare_kg, "vehicle": payload.vehicle,
              "top_n": payload.top_n, "assets": payload.assets,
              "confirmed_pose_only": payload.confirmed_pose_only,
              "clearance_mm": payload.clearance_mm}

    # ponytail: no result cache. A cache key would need tare_kg + vehicle +
    # top_n or it risks serving a wrong truck number for a different request,
    # and that subtlety isn't worth it before anyone has used this endpoint.
    solve_job_id = str(uuid.uuid4())
    db.add(SolveJob(id=solve_job_id, part_id=part.id, project_id=part.project_id,
                    status="pending", inputs_json=params))
    if part.project_id is not None:
        project = db.get(Project, part.project_id)
        if project is not None:
            project.updated_at = dt.datetime.utcnow()
    db.commit()

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


@app.post("/api/parts/{part_id}/solve", response_model=SolveJobStatusOut,
         status_code=202)
def solve_part_endpoint(part_id: int, payload: SolveIn,
                        db: Session = Depends(get_db)):
    part = db.get(PartProfile, part_id)
    if part is None:
        raise HTTPException(404, "Part not found.")
    return _enqueue_solve(part, payload, db)


@app.get("/api/solve-jobs/{job_id}", response_model=SolveJobStatusOut)
def solve_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(SolveJob, job_id)
    if job is None:
        raise HTTPException(404, "Solve job not found.")
    result = job.result_json if job.status == "done" else None
    return SolveJobStatusOut(
        solve_job_id=job.id, status=job.status, error=job.error, result=result
    )


@app.post("/api/truck-fit", response_model=TruckFitOut)
def truck_fit(payload: TruckFitIn, db: Session = Depends(get_db)):
    """Boxes per vehicle for the standalone load calculator.

    Same `engine.parts_per_truck` the solve uses -- the frontend used to carry
    its own floor-fit (`loadPlan`/`bestFill`) and the two disagreed on the
    floor pattern (CLAUDE.md hard rule 9). Pure arithmetic, so it runs on the
    request. `parts_per_box`/`part_kg` are 1/0 here: the calculator asks about
    boxes, so `parts` == `boxes` and `kg_per_box` is what the caller typed.
    """
    v = db.scalars(select(Vehicle).where(Vehicle.name == payload.vehicle)).first()
    if v is None:
        raise HTTPException(404, f"Vehicle '{payload.vehicle}' not found.")
    fit = parts_per_truck(
        (payload.outer_l_mm, payload.outer_b_mm, payload.outer_h_mm), 1, 0.0,
        payload.kg_per_box,
        (v.name, v.cargo_l_mm, v.cargo_b_mm, v.cargo_h_mm, v.payload_kg),
        max_stack=payload.max_stack,
    )
    return TruckFitOut(**dataclasses.asdict(fit))


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
        project_id=p.project_id,
        created_at=p.created_at,
    )


# --- F1: Project entity + API -----------------------------------------------

# _best_and_catalogue / _best_object / _run_out live in app.runs now (F7):
# app.worker.run_proposal needs `_run_out` too, and worker -> main would be
# an import cycle since main already imports run_solve/render_proposal etc.
# from worker.


def _project_runs(project_id: int, db: Session) -> list[SolveJob]:
    return db.scalars(
        select(SolveJob).where(SolveJob.project_id == project_id)
        # id is a uuid4 (SolveJob.id), not chronological -- this is a
        # deterministic tiebreaker for created_at ties, not a second sort key
        # with any meaning of its own.
        .order_by(SolveJob.created_at.desc(), SolveJob.id.desc())
    ).all()


def _check_vehicle_exists(vehicle_id: int | None, db: Session) -> None:
    if vehicle_id is not None and db.get(Vehicle, vehicle_id) is None:
        raise HTTPException(404, f"Vehicle {vehicle_id} not found.")


def _project_out(project: Project, db: Session) -> ProjectOut:
    part = db.scalars(
        select(PartProfile).where(PartProfile.project_id == project.id)
        .order_by(PartProfile.created_at.desc())
    ).first()
    runs = _project_runs(project.id, db)
    return ProjectOut(
        id=project.id, customer=project.customer, part_number=project.part_number,
        part_name=project.part_name, status=project.status, owner=project.owner,
        notes=project.notes, annual_volume=project.annual_volume,
        route_km=project.route_km, vehicle_id=project.vehicle_id,
        customer_count=project.customer_count, customer_box=project.customer_box,
        cost_per_trip=project.cost_per_trip,
        emission_factor_kg_per_km=project.emission_factor_kg_per_km,
        recommended_run_id=project.recommended_run_id,
        created_at=project.created_at, updated_at=project.updated_at,
        part=_to_out(part) if part is not None else None,
        runs=[_run_out(j, project) for j in runs],
    )


def _project_summary(project: Project, db: Session) -> ProjectSummaryOut:
    runs = _project_runs(project.id, db)
    done_runs = [j for j in runs if j.status == "done"]
    chosen = None
    if project.recommended_run_id:
        chosen = next((j for j in done_runs if j.id == project.recommended_run_id),
                     None)
    if chosen is None:
        chosen = done_runs[0] if done_runs else None
    best_count = best_asset = catalogue_count = catalogue_asset = None
    cuboid_count = gain_vs_cuboid = gain_vs_customer_pct = None
    if chosen is not None:
        run_out = _run_out(chosen, project)
        best_count, best_asset = run_out.best_count, run_out.best_asset
        catalogue_count = run_out.catalogue_count
        catalogue_asset = run_out.catalogue_asset
        cuboid_count = run_out.cuboid_count
        gain_vs_cuboid = run_out.gain_vs_cuboid
        gain_vs_customer_pct = run_out.gain_vs_customer_pct
    return ProjectSummaryOut(
        id=project.id, customer=project.customer, part_number=project.part_number,
        part_name=project.part_name, status=project.status, owner=project.owner,
        updated_at=project.updated_at, run_count=len(runs),
        best_count=best_count, best_asset=best_asset,
        catalogue_count=catalogue_count, catalogue_asset=catalogue_asset,
        cuboid_count=cuboid_count, customer_count=project.customer_count,
        gain_vs_cuboid=gain_vs_cuboid, gain_vs_customer_pct=gain_vs_customer_pct,
    )


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, db: Session = Depends(get_db)):
    _check_vehicle_exists(payload.vehicle_id, db)
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_out(project, db)


@app.get("/api/projects", response_model=list[ProjectSummaryOut])
def list_projects(q: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Project)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            (Project.customer.ilike(like))
            | (Project.part_number.ilike(like))
            | (Project.part_name.ilike(like))
        )
    stmt = stmt.order_by(Project.updated_at.desc())
    projects = db.scalars(stmt).all()
    return [_project_summary(p, db) for p in projects]


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    return _project_out(project, db)


@app.patch("/api/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, payload: ProjectPatch,
                  db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    updates = payload.model_dump(exclude_unset=True)
    if "vehicle_id" in updates:
        _check_vehicle_exists(updates["vehicle_id"], db)
    if "recommended_run_id" in updates and updates["recommended_run_id"] is not None:
        run = db.get(SolveJob, updates["recommended_run_id"])
        if (run is None or run.project_id != project_id
                or run.status != "done"):
            raise HTTPException(
                422,
                "recommended_run_id must be a done solve run belonging to "
                "this project.",
            )
    for field, value in updates.items():
        setattr(project, field, value)
    project.updated_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(project)
    return _project_out(project, db)


@app.post("/api/projects/{project_id}/solve", response_model=SolveJobStatusOut,
         status_code=202)
def solve_project_endpoint(project_id: int, payload: SolveIn,
                           db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    part = db.scalars(
        select(PartProfile).where(PartProfile.project_id == project_id)
        .order_by(PartProfile.created_at.desc())
    ).first()
    if part is None:
        raise HTTPException(422, "Project has no part yet — save one first.")
    return _enqueue_solve(part, payload, db)


# --- F7: proposal PDF ---------------------------------------------------

def _proposal_out(p: Proposal) -> ProposalOut:
    pdf_url = f"/api/files/{Path(p.pdf_path).name}" if p.pdf_path else None
    return ProposalOut(id=p.id, project_id=p.project_id, run_id=p.run_id,
                       status=p.status, pdf_url=pdf_url, error=p.error,
                       created_at=p.created_at)


@app.post("/api/projects/{project_id}/proposal", response_model=ProposalOut,
         status_code=202)
def create_proposal(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")

    # recommended_run_id if the project has one and it is still a done run
    # of this project, else the newest done run -- one helper (app.runs),
    # shared with `python -m app.proposal`'s CLI, so the two can never pick
    # different runs for the same project.
    run = proposal_run_for(project, db)
    if run is None:
        raise HTTPException(422, "No finished solve to write a proposal from.")
    if not _run_has_content(run):
        raise HTTPException(
            422, "The selected run found no box; nothing to propose."
        )

    proposal = Proposal(project_id=project_id, run_id=run.id, status="pending")
    db.add(proposal)
    project.updated_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(proposal)

    try:
        render_proposal.delay(proposal.id)
    except Exception:
        logger.warning(
            "Celery enqueue failed for proposal %s — rendering in-process "
            "(dev fallback)", proposal.id, exc_info=True,
        )
        threading.Thread(
            target=run_proposal, args=(proposal.id,), daemon=True,
        ).start()
    return _proposal_out(proposal)


@app.get("/api/projects/{project_id}/proposals", response_model=list[ProposalOut])
def list_proposals(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    rows = db.scalars(
        select(Proposal).where(Proposal.project_id == project_id)
        .order_by(Proposal.created_at.desc(), Proposal.id.desc())
    ).all()
    return [_proposal_out(p) for p in rows]


@app.get("/api/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Proposal not found.")
    return _proposal_out(proposal)
