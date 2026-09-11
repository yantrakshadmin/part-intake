"""F1 acceptance: Project entity + API.

Run:  ./venv/bin/python tests/test_projects_api.py

Skips cleanly when tests/fixtures/customer/ is empty (NDA material, not in
git) — same pattern as test_solve_api.py, which this file mirrors exactly:
hermetic tmp sqlite + storage set before importing app, `app_main.solve_part
.delay` captured, `_run_enqueued` replays `run_solve` in place of a worker.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Hermetic DB/storage for this run, set BEFORE importing anything under app/
# (app.config.Settings reads env at import time). Never touch a real dev.db.
_TMP = Path(tempfile.mkdtemp(prefix="projects_api_test_"))
os.environ["INTAKE_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["INTAKE_LOCAL_STORAGE_DIR"] = str(_TMP / "files")
os.environ.setdefault("INTAKE_REDIS_URL", "redis://localhost:6379/0")

import dataclasses  # noqa: E402
import datetime as dt  # noqa: E402
import math  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

from app import main as app_main  # noqa: E402
from app.geometry import extract_part  # noqa: E402
from app.models import ExtractionJob, PartProfile, SolveJob  # noqa: E402
from app.schemas import PartProfileIn, ProjectIn, ProjectPatch, SolveIn  # noqa: E402
from app.worker import run_solve  # noqa: E402

_ENQUEUED: list[tuple] = []


def _capture_delay(*args, **kwargs):
    _ENQUEUED.append((args, kwargs))


app_main.solve_part.delay = _capture_delay


def _run_enqueued() -> None:
    args, kwargs = _ENQUEUED.pop()
    run_solve(*args, **kwargs)


FIXTURES = Path(__file__).parent / "fixtures" / "customer"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
WHEEL_KG = 2.5


def _make_wheel_part(db, project_id: int) -> PartProfile:
    """Mirrors test_solve_api._make_wheel_part, but attaches project_id like
    POST /api/parts would."""
    glb_path = str(_TMP / "wheel.glb")
    result = extract_part(str(WHEEL), glb_path)
    job = ExtractionJob(
        id=f"wheel-job-{project_id}", status="done", step_path=str(WHEEL),
        glb_path=result.glb_path,
        result_json={
            **dataclasses.asdict(result),
            "candidates": [dataclasses.asdict(c) for c in result.candidates],
        },
    )
    db.add(job)
    db.commit()
    payload = PartProfileIn(
        part_number="TRW-WHEEL", part_name="Steering wheel",
        length_mm=370, breadth_mm=360, height_mm=135, weight_kg=WHEEL_KG,
        source="stp", job_id=job.id, project_id=project_id,
    )
    return app_main.create_part(payload, db=db)  # goes through the real route


def main() -> int:
    if not WHEEL.exists():
        print("SKIP: no customer fixtures present (NDA material, not in git)")
        return 0

    failures = []

    def check(ok, label, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
        if not ok:
            failures.append(f"{label}{detail}")

    db = app_main.SessionLocal()
    try:
        # --- 1. create a project -----------------------------------------
        project = app_main.create_project(
            ProjectIn(customer="Mubea", part_number="STAB-1",
                     part_name="Stabiliser bar"), db=db
        )
        check(project.status == "draft" and project.runs == [],
              "new project is draft with no runs", f": {project}")

        # --- 1b. (code review #1) create_project validates vehicle_id too,
        # not only patch_project.
        try:
            app_main.create_project(
                ProjectIn(customer="Dangling", part_number="X", part_name="Y",
                         vehicle_id=999999), db=db
            )
            check(False, "create_project rejects a dangling vehicle_id",
                  ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 404,
                  "create_project rejects a dangling vehicle_id",
                  f": got {e.status_code}")

        # --- 2. attach the wheel part via project_id ----------------------
        part_out = _make_wheel_part(db, project.id)
        got = app_main.get_project(project.id, db=db)
        check(got.part is not None and got.part.id == part_out.id,
              "GET project shows the attached part",
              f": part={got.part}")

        # --- 2b. (code review #3a) a second part for the same project is
        # rejected -- one PartProfile per project.
        try:
            app_main.create_part(
                PartProfileIn(
                    part_number="STAB-1", part_name="Stabiliser bar",
                    length_mm=100, breadth_mm=50, height_mm=25, weight_kg=1.0,
                    source="manual", project_id=project.id,
                ),
                db=db,
            )
            check(False, "second create_part for the same project rejected",
                  ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 409,
                  "second create_part for the same project rejected with 409",
                  f": got {e.status_code}: {e.detail}")

        # --- 3. two solves, different clearance ---------------------------
        posted_a = app_main.solve_project_endpoint(
            project.id, SolveIn(clearance_mm=5.0, assets=["PLS12803"]), db=db
        )
        _run_enqueued()
        posted_b = app_main.solve_project_endpoint(
            project.id, SolveIn(clearance_mm=10.0, assets=["PLS12803"]), db=db
        )
        _run_enqueued()

        got = app_main.get_project(project.id, db=db)
        check(len(got.runs) == 2, "project detail lists 2 runs",
              f": {len(got.runs)}")
        newest, oldest = got.runs[0], got.runs[1]
        check(newest.solve_job_id == posted_b.solve_job_id
              and oldest.solve_job_id == posted_a.solve_job_id,
              "runs are newest first",
              f": newest={newest.solve_job_id} oldest={oldest.solve_job_id}")
        check(oldest.inputs.get("clearance_mm") == 5.0
              and newest.inputs.get("clearance_mm") == 10.0,
              "each run's inputs['clearance_mm'] matches what was asked",
              f": oldest={oldest.inputs} newest={newest.inputs}")
        check(all(r.best_count and r.best_count > 0 for r in got.runs),
              "both runs report best_count > 0", f": {[r.best_count for r in got.runs]}")
        check(oldest.clearance_mm == 5.0 and newest.clearance_mm == 10.0,
              "RunOut.clearance_mm equals what was asked",
              f": oldest={oldest.clearance_mm} newest={newest.clearance_mm}")

        # --- 3a. best_count/best_asset must describe the SAME option --------
        # (tester finding 2): pair best_asset off custom_beats_catalogue, not
        # off "catalogue happened to be non-empty" -- and catalogue_count /
        # catalogue_asset must both come from catalogue[0].
        status_a = app_main.solve_job_status(posted_a.solve_job_id, db=db)
        res_a = status_a.result
        check((oldest.best_asset == "custom") == res_a.custom_beats_catalogue,
              "RunOut.best_asset is 'custom' iff custom_beats_catalogue",
              f": best_asset={oldest.best_asset!r} "
              f"custom_beats_catalogue={res_a.custom_beats_catalogue}")
        check(res_a.catalogue and oldest.catalogue_count == res_a.catalogue[0].count
              and oldest.catalogue_asset == res_a.catalogue[0].asset_name,
              "RunOut.catalogue_count/catalogue_asset both come from catalogue[0]",
              f": count={oldest.catalogue_count} asset={oldest.catalogue_asset} "
              f"vs catalogue[0]={res_a.catalogue[0] if res_a.catalogue else None}")
        check(oldest.best_count == res_a.best_count,
              "RunOut.best_count is the engine's own best_count",
              f": {oldest.best_count} vs {res_a.best_count}")

        # --- 3b. (tester finding 1) a solve via the OLD /api/parts/{id}/solve
        # route, on a part that belongs to a project, must still land under
        # that project -- _enqueue_solve derives project_id from the part.
        posted_old_route = app_main.solve_part_endpoint(
            part_out.id, SolveIn(clearance_mm=7.0, assets=["PLS12803"]), db=db
        )
        _run_enqueued()
        got = app_main.get_project(project.id, db=db)
        check(len(got.runs) == 3
              and any(r.solve_job_id == posted_old_route.solve_job_id
                      for r in got.runs),
              "solve via the OLD route on a project's part shows up in "
              "the project's runs",
              f": run_count={len(got.runs)} "
              f"ids={[r.solve_job_id for r in got.runs]}")
        newest = got.runs[0]

        # --- 3c. (code review #3b) every RunOut carries part_id -------------
        check(all(r.part_id == part_out.id for r in got.runs),
              "every RunOut.part_id equals the project's part.id",
              f": {[(r.solve_job_id, r.part_id) for r in got.runs]}")

        summary = app_main.list_projects(db=db)
        row = next(p for p in summary if p.id == project.id)
        check(row.run_count == 3, "list row run_count is 3", f": {row.run_count}")
        check(row.best_count == newest.best_count
              and row.best_asset == newest.best_asset
              and row.catalogue_count == newest.catalogue_count
              and row.catalogue_asset == newest.catalogue_asset,
              "list best_count/best_asset/catalogue_count/catalogue_asset "
              "equal the newest done run's",
              f": row=({row.best_count}, {row.best_asset}, "
              f"{row.catalogue_count}, {row.catalogue_asset}) "
              f"newest=({newest.best_count}, {newest.best_asset}, "
              f"{newest.catalogue_count}, {newest.catalogue_asset})")

        # --- 3d. F3/F4/F6 (ticket): baseline/gain, reasons, demand -> trips --
        patched_counts = app_main.patch_project(
            project.id,
            ProjectPatch(customer_count=32, annual_volume=100000),
            db=db,
        )
        check(patched_counts.customer_count == 32
              and patched_counts.annual_volume == 100000,
              "PATCH customer_count/annual_volume sticks",
              f": {patched_counts.customer_count} {patched_counts.annual_volume}")

        got = app_main.get_project(project.id, db=db)
        run = got.runs[0]
        check(run.cuboid_count is not None and run.cuboid_count > 0
              and run.cuboid_count < run.best_count,
              "F3: cuboid_count > 0 and nesting beats cuboid",
              f": cuboid_count={run.cuboid_count} best_count={run.best_count}")
        check(run.gain_vs_cuboid == round(run.best_count / run.cuboid_count, 2),
              "F3: gain_vs_cuboid == best_count / cuboid_count",
              f": {run.gain_vs_cuboid} vs {run.best_count}/{run.cuboid_count}")
        check(run.gain_vs_customer_pct == round((run.best_count / 32 - 1) * 100, 1),
              "F3: gain_vs_customer_pct == (best_count/32 - 1)*100",
              f": {run.gain_vs_customer_pct}")
        check(len(run.reasons) >= 3,
              "F4: RunOut.reasons has >= 3 sentences", f": {run.reasons}")

        status_run = app_main.solve_job_status(run.solve_job_id, db=db)
        truck_parts = status_run.result.truck.parts if status_run.result.truck else None
        check(run.parts_per_truck == truck_parts,
              "F6: RunOut.parts_per_truck == result.truck.parts",
              f": {run.parts_per_truck} vs {truck_parts}")
        check(run.trips_per_year == math.ceil(100000 / truck_parts),
              "F6: trips_per_year == ceil(annual_volume / parts_per_truck)",
              f": {run.trips_per_year}")

        app_main.patch_project(project.id, ProjectPatch(annual_volume=None), db=db)
        got_no_vol = app_main.get_project(project.id, db=db)
        run_no_vol = next(r for r in got_no_vol.runs
                          if r.solve_job_id == run.solve_job_id)
        check(run_no_vol.trips_per_year is None,
              "F6: annual_volume cleared -> trips_per_year None",
              f": {run_no_vol.trips_per_year}")

        # An older stored result_json (no cuboid_count/reasons keys) must not
        # crash GET -- fields read None/[], never an exception.
        job_row = db.get(SolveJob, run.solve_job_id)
        stripped = dict(job_row.result_json)
        stripped["catalogue"] = [
            {k: v for k, v in l.items() if k not in ("cuboid_count", "reasons")}
            for l in stripped.get("catalogue", [])
        ]
        if stripped.get("custom"):
            stripped["custom"] = {
                k: v for k, v in stripped["custom"].items()
                if k not in ("cuboid_count", "reasons")
            }
        job_row.result_json = stripped
        db.commit()
        got_stripped = app_main.get_project(project.id, db=db)
        run_stripped = next(r for r in got_stripped.runs
                            if r.solve_job_id == run.solve_job_id)
        check(run_stripped.cuboid_count is None and run_stripped.reasons == []
              and run_stripped.gain_vs_cuboid is None,
              "older result_json without the new keys -> None/[] fields, no crash",
              f": cuboid_count={run_stripped.cuboid_count} "
              f"reasons={run_stripped.reasons} "
              f"gain_vs_cuboid={run_stripped.gain_vs_cuboid}")

        # --- 4. PATCH status + recommended_run_id --------------------------
        patched = app_main.patch_project(
            project.id,
            ProjectPatch(status="solved", recommended_run_id=oldest.solve_job_id),
            db=db,
        )
        check(patched.status == "solved"
              and patched.recommended_run_id == oldest.solve_job_id,
              "PATCH sets status and recommended_run_id",
              f": {patched.status} {patched.recommended_run_id}")

        try:
            app_main.patch_project(
                project.id,
                ProjectPatch(recommended_run_id=str(uuid.uuid4())),
                db=db,
            )
            check(False, "bogus recommended_run_id rejected", ": no exception")
        except HTTPException as e:
            check(e.status_code == 422, "bogus recommended_run_id -> 422",
                  f": got {e.status_code}")

        try:
            ProjectPatch(status="bogus")
            check(False, "invalid status rejected", ": no exception")
        except ValidationError:
            check(True, "invalid status -> pydantic ValidationError")

        # --- 5. search ------------------------------------------------------
        hits = app_main.list_projects(q="mub", db=db)
        check(len(hits) == 1, "?q=mub matches 1 project", f": {len(hits)}")
        misses = app_main.list_projects(q="zzz", db=db)
        check(len(misses) == 0, "?q=zzz matches 0 projects", f": {len(misses)}")

        # --- 5b. (code review #2) _project_runs is deterministic on a
        # created_at tie -- two rows stamped with the SAME created_at must
        # come back in the same order every time, not dialect luck.
        tie_project = app_main.create_project(
            ProjectIn(customer="Tiebreak Co", part_number="TB-1",
                     part_name="Tiebreak part"), db=db
        )
        same_ts = dt.datetime(2026, 1, 1, 12, 0, 0)
        job_lo = SolveJob(id="00000000-0000-0000-0000-000000000001",
                          part_id=1, project_id=tie_project.id,
                          status="pending", created_at=same_ts)
        job_hi = SolveJob(id="ffffffff-ffff-ffff-ffff-ffffffffffff",
                          part_id=1, project_id=tie_project.id,
                          status="pending", created_at=same_ts)
        db.add(job_lo)
        db.add(job_hi)
        db.commit()
        order1 = [j.id for j in app_main._project_runs(tie_project.id, db)]
        order2 = [j.id for j in app_main._project_runs(tie_project.id, db)]
        check(order1 == order2 == [job_hi.id, job_lo.id],
              "_project_runs breaks a created_at tie deterministically "
              "(id desc)", f": {order1} / {order2}")

        # --- 6. landmine 5 proof: old schema -> _ensure_added_columns -------
        old_url = f"sqlite:///{_TMP / 'old_schema.db'}"
        old_engine = create_engine(old_url)
        meta = MetaData()
        Table(
            "part_profiles", meta,
            Column("id", Integer, primary_key=True),
            Column("part_number", String(64)),
        )
        Table(
            "solve_jobs", meta,
            Column("id", String(36), primary_key=True),
            Column("part_id", Integer),
        )
        meta.create_all(old_engine)
        insp_before = inspect(old_engine)
        before = {
            "part_profiles": {c["name"] for c in insp_before.get_columns("part_profiles")},
            "solve_jobs": {c["name"] for c in insp_before.get_columns("solve_jobs")},
        }
        check("project_id" not in before["part_profiles"]
              and "project_id" not in before["solve_jobs"]
              and "inputs_json" not in before["solve_jobs"],
              "old schema really is missing the new columns", f": {before}")

        app_main._ensure_added_columns(old_engine)
        insp_after = inspect(old_engine)
        after_pp = {c["name"] for c in insp_after.get_columns("part_profiles")}
        after_sj = {c["name"] for c in insp_after.get_columns("solve_jobs")}
        check("project_id" in after_pp,
              "_ensure_added_columns adds part_profiles.project_id",
              f": {after_pp}")
        check("project_id" in after_sj and "inputs_json" in after_sj,
              "_ensure_added_columns adds solve_jobs.project_id/inputs_json",
              f": {after_sj}")

    finally:
        db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
