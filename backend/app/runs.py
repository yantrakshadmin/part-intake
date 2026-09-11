"""Result_json -> RunOut summarisation, shared by `app.main` (HTTP) and
`app.worker` (F7 proposal rendering).

Split out of `app.main` rather than left there: `worker.run_proposal` needs
`_run_out` to build the same `RunOut` the API would return, and `main.py`
already imports `run_solve`/`render_proposal` etc. from `worker` -- importing
`_run_out` back the other way (worker -> main) would be a straight import
cycle. This module imports neither `main` nor `worker`, so both can import it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import gain_ratio, trips_per_year
from .models import Project, SolveJob
from .schemas import RunOut


def _run_has_content(run: SolveJob) -> bool:
    """A done run whose result_json found nothing to pack -- the worker's own
    "No layout found" case (still `status="done"`: something ran, it just
    came back empty) -- is not something a proposal can be written from:
    no comparison table, no BOM, no truck fit. `main.create_proposal` uses
    this to 422 with an honest reason instead of handing `build_pdf` a run
    it can only fail on."""
    r = run.result_json or {}
    return bool(r.get("catalogue")) or r.get("custom") is not None


def proposal_run_for(project: Project, db: Session) -> SolveJob | None:
    """The run an F7 proposal is written from: `recommended_run_id` if the
    project has one and it is still a done run of this project, else the
    newest done run that actually found something (see `_run_has_content`).
    One helper -- `main.create_proposal` (the route) and `app.proposal`'s CLI
    both call this, so "which run" can never drift between the two the way
    it did when the CLI picked newest-done while the route preferred
    recommended_run_id.

    An explicitly recommended run is returned AS IS even if it turns out
    empty -- `create_proposal` is what turns that into an honest 422;
    silently substituting a different run for the one an engineer pinned
    would be worse. The "newest done" fallback (no recommendation set) skips
    empty ones and falls back to the newest empty one only if every done run
    is empty, so create_proposal's message still distinguishes "no done run
    at all" from "a done run exists but found nothing".
    """
    if project.recommended_run_id:
        candidate = db.get(SolveJob, project.recommended_run_id)
        if (candidate is not None and candidate.project_id == project.id
                and candidate.status == "done"):
            return candidate
    rows = db.scalars(
        select(SolveJob).where(SolveJob.project_id == project.id,
                               SolveJob.status == "done")
        .order_by(SolveJob.created_at.desc(), SolveJob.id.desc())
    ).all()
    for row in rows:
        if _run_has_content(row):
            return row
    return rows[0] if rows else None


def _best_and_catalogue(r: dict) -> tuple:
    """From a done solve's result_json: (best_count, best_asset,
    catalogue_count, catalogue_asset, custom_count).

    best_count/best_asset must describe the SAME option (hard rule 9):
    result["best_count"] is the engine's overall winner, catalogue or
    custom, so best_asset is "custom" when custom_beats_catalogue, else
    catalogue[0]'s own name -- never catalogue[0]'s name paired with a count
    that belongs to a different box. catalogue_count/catalogue_asset are
    catalogue[0] specifically, for a reader who wants the best STOCKED
    option regardless of which one the engine picked overall.
    """
    best_count = r.get("best_count")
    catalogue = r.get("catalogue") or []
    custom = r.get("custom")
    catalogue_count = catalogue[0]["count"] if catalogue else None
    catalogue_asset = catalogue[0]["asset_name"] if catalogue else None
    if r.get("custom_beats_catalogue"):
        best_asset = "custom" if custom else None
    else:
        best_asset = catalogue_asset
    custom_count = custom["count"] if custom else None
    return best_count, best_asset, catalogue_count, catalogue_asset, custom_count


def _best_object(r: dict) -> Optional[dict]:
    """The result_json dict for the SAME option `best_count`/`best_asset`
    describe -- custom's when custom wins, else catalogue[0]'s. Source for
    fields that must agree with best_count (cuboid_count, reasons) rather
    than always reading catalogue[0], which is a different box whenever
    `custom_beats_catalogue` (hard rule 9)."""
    catalogue = r.get("catalogue") or []
    custom = r.get("custom")
    if r.get("custom_beats_catalogue"):
        return custom
    return catalogue[0] if catalogue else None


def _run_out(job: SolveJob, project: Project) -> RunOut:
    """Summarise one SolveJob for the API — hard rule 9: the frontend reads
    this, never result_json, for these fields.

    F3/F6 fields are computed READ-TIME off `project`, not stored on the job,
    so patching `customer_count` or `annual_volume` after the solve updates
    the gain/trips here without a re-run. `app.proposal.build_pdf` reads the
    same `RunOut` this returns, so the PDF and the API can never disagree.
    """
    inputs = job.inputs_json or {}
    best_count = best_asset = catalogue_count = catalogue_asset = None
    custom_count = truck_boxes = truck_vehicle = clearance_mm = None
    poses_searched: list[str] = []
    cuboid_count = None
    reasons: list[str] = []
    truck_parts = None
    if job.status == "done" and job.result_json:
        r = job.result_json
        (best_count, best_asset, catalogue_count, catalogue_asset,
         custom_count) = _best_and_catalogue(r)
        truck = r.get("truck")
        if truck is not None:
            truck_boxes = truck.get("boxes")
            truck_vehicle = truck.get("vehicle")
            truck_parts = truck.get("parts")
        clearance_mm = r.get("clearance_mm")
        poses_searched = r.get("poses_searched") or []
        best_obj = _best_object(r)
        if best_obj is not None:
            cuboid_count = best_obj.get("cuboid_count")
            reasons = best_obj.get("reasons") or []

    customer_count = project.customer_count
    gain_vs_cuboid = gain_ratio(best_count, cuboid_count)
    gain_vs_customer_pct = (
        round((best_count / customer_count - 1) * 100, 1)
        if best_count is not None and customer_count else None
    )
    return RunOut(
        solve_job_id=job.id, part_id=job.part_id, status=job.status,
        created_at=job.created_at,
        error=job.error, inputs=inputs, best_count=best_count,
        best_asset=best_asset, catalogue_count=catalogue_count,
        catalogue_asset=catalogue_asset, custom_count=custom_count,
        truck_boxes=truck_boxes, truck_vehicle=truck_vehicle,
        clearance_mm=clearance_mm, poses_searched=poses_searched,
        cuboid_count=cuboid_count, customer_count=customer_count,
        gain_vs_cuboid=gain_vs_cuboid, gain_vs_customer_pct=gain_vs_customer_pct,
        reasons=reasons, parts_per_truck=truck_parts,
        trips_per_year=trips_per_year(project.annual_volume, truck_parts),
    )
