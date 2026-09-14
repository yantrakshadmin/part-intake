"""F7 acceptance: proposal PDF (POST /api/projects/{id}/proposal, GET
.../proposals, GET /api/proposals/{id}, app.proposal.build_pdf).

Run:  ./venv/bin/python tests/test_proposal.py

Skips cleanly when tests/fixtures/customer/ is empty (NDA material, not in
git) — same hermetic pattern as test_projects_api.py: tmp sqlite + storage
set before importing app, `app_main.solve_part.delay` captured, `_run_enqueued`
replays `run_solve` in place of a worker, and `run_proposal` (the plain
function `render_proposal` delegates to) stands in for a proposal worker.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Hermetic DB/storage for this run, set BEFORE importing anything under app/
# (app.config.Settings reads env at import time). Never touch a real dev.db.
_TMP = Path(tempfile.mkdtemp(prefix="proposal_test_"))
os.environ["INTAKE_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["INTAKE_LOCAL_STORAGE_DIR"] = str(_TMP / "files")
os.environ.setdefault("INTAKE_REDIS_URL", "redis://localhost:6379/0")

import copy  # noqa: E402
import dataclasses  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app import main as app_main  # noqa: E402
from app.geometry import extract_part  # noqa: E402
from app.models import (ExtractionJob, Packaging, PartProfile, Project,  # noqa: E402
                        SolveJob)
from app.proposal import build_pdf  # noqa: E402
from app.runs import _best_object, _run_out, proposal_run_for  # noqa: E402
from app.schemas import PartProfileIn, ProjectIn, ProjectPatch, SolveIn  # noqa: E402
from app.worker import render_run, run_proposal, run_render, run_solve  # noqa: E402

_ENQUEUED: list[tuple] = []


def _capture_delay(*args, **kwargs):
    _ENQUEUED.append((args, kwargs))


app_main.solve_part.delay = _capture_delay
# Ticket 2: run_solve() itself enqueues render_run (drawing_url/gif_url/
# packed_url, and flips render_status pending -> done) once it lands. A
# render_status="pending" run now 409s the proposal route (ticket #1), so a
# test run must actually go through render_run, not just run_solve -- same
# inline-synchronous stand-in test_solve_api.py already uses.
render_run.delay = lambda *a, **kw: run_render(*a, **kw)


def _run_enqueued() -> None:
    args, kwargs = _ENQUEUED.pop()
    run_solve(*args, **kwargs)


FIXTURES = Path(__file__).parent / "fixtures" / "customer"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
WHEEL_KG = 2.5


def _make_wheel_part(db, project_id: int) -> PartProfile:
    """Mirrors test_projects_api._make_wheel_part."""
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
    return app_main.create_part(payload, db=db)


def _count_pages(pdf_bytes: bytes) -> int:
    """Page count from the raw PDF bytes -- no pypdf in this venv. Every leaf
    page object declares `/Type /Page`; the page-tree root declares
    `/Type /Pages` (plural) exactly once and that string is a superset match
    of the singular one, so subtracting its count leaves just the leaves."""
    return pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages")


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
        project = app_main.create_project(
            ProjectIn(customer="TRW", part_number="WHEEL-1",
                     part_name="Steering wheel"), db=db
        )
        _make_wheel_part(db, project.id)

        posted = app_main.solve_project_endpoint(
            project.id, SolveIn(assets=["PLS12803"]), db=db
        )
        _run_enqueued()
        status = app_main.solve_job_status(posted.solve_job_id, db=db)
        check(status.status == "done", "solve reaches done",
              f": status={status.status} error={status.error}")

        # So the gain-vs-customer / trips-per-year pages have something to
        # print, same as the F3/F6 check in test_projects_api.py.
        app_main.patch_project(
            project.id, ProjectPatch(customer_count=32, annual_volume=100000),
            db=db,
        )

        # --- POST with no done run -> 422 ------------------------------------
        empty_project = app_main.create_project(
            ProjectIn(customer="Nobody", part_number="X", part_name="Y"), db=db
        )
        try:
            app_main.create_proposal(empty_project.id, db=db)
            check(False, "POST proposal with no done run -> 422",
                  ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 422, "POST proposal with no done run -> 422",
                  f": got {e.status_code}: {e.detail}")

        # --- POST proposal -> 202 pending, run the worker function directly --
        proposed = app_main.create_proposal(project.id, db=db)
        check(proposed.status == "pending" and proposed.pdf_url is None,
              "POST proposal returns pending, no pdf_url yet", f": {proposed}")

        run_proposal(proposed.id)

        done = app_main.get_proposal(proposed.id, db=db)
        check(done.status == "done" and done.error is None,
              "proposal render reaches done",
              f": status={done.status} error={done.error}")

        pdf_path = (Path(app_main.settings.local_storage_dir)
                   / f"proposal_{proposed.id}.pdf")
        check(pdf_path.is_file(), "PDF file exists on disk", f": {pdf_path}")
        size = pdf_path.stat().st_size if pdf_path.is_file() else 0
        check(size > 20_000, "PDF is more than 20 kB", f": {size} bytes")

        raw = pdf_path.read_bytes() if pdf_path.is_file() else b""
        pages = _count_pages(raw)
        check(pages >= 6, "PDF has at least 6 pages", f": {pages}")

        check(done.pdf_url == f"/api/files/{pdf_path.name}",
              "ProposalOut.pdf_url matches the file written", f": {done.pdf_url}")

        listed = app_main.list_proposals(project.id, db=db)
        check(len(listed) == 1 and listed[0].id == proposed.id,
              "GET .../proposals lists the one proposal",
              f": {[p.id for p in listed]}")

        got_by_id = app_main.get_proposal(proposed.id, db=db)
        check(got_by_id.id == proposed.id,
              "GET /api/proposals/{id} returns the same row", f": {got_by_id}")

        # --- every headline number build_pdf printed matches RunOut / -------
        # result_json (the test reads both) -- calling build_pdf again off
        # the SAME rows run_proposal just used is how the numbers dict (never
        # stored -- ticket says so) reaches the test at all.
        project_row = db.get(Project, project.id)
        run_row = db.get(SolveJob, posted.solve_job_id)
        run_out = _run_out(run_row, project_row)
        part_row = db.scalars(
            app_main.select(PartProfile)
            .where(PartProfile.project_id == project.id)
        ).first()
        result = build_pdf(project_row, part_row, run_row, run_out,
                           _TMP / "verify.pdf")
        check(result["pages"] >= 6,
              "build_pdf's own page count is >= 6", f": {result['pages']}")

        numbers = result["numbers"]
        r = run_row.result_json or {}
        truck = r.get("truck") or {}
        dunnage = (_best_object(r) or {}).get("dunnage") or {}
        comparison_rows_expect = [
            (l.get("asset_name"), l.get("pose_label"), l.get("count"),
             l.get("cuboid_count"), tuple(l.get("grid") or ()),
             l.get("limited_by"), l.get("count_upper"))
            for l in r.get("catalogue") or []
        ]
        custom = r.get("custom")
        if custom is not None:
            comparison_rows_expect.append(
                (custom.get("asset_name", "custom"), custom.get("pose_label"),
                 custom.get("count"), custom.get("cuboid_count"),
                 tuple(custom.get("grid") or ()), "custom",
                 custom.get("count_upper"))
            )
        # `truck_outer`/`max_weight_kg` are catalogue reference data, not
        # engine output -- no result_json/RunOut field to check them against
        # here (that is what the DRAFT-BOX-1 check below is for, against a
        # value this test itself knows independently). Just carry the actual
        # value through so the strict equality below still catches a
        # regression that zeroes them out silently.
        expect = {
            "best_count": run_out.best_count,
            "best_asset": run_out.best_asset,
            "cuboid_count": run_out.cuboid_count,
            "customer_count": run_out.customer_count,
            "gain_vs_cuboid": run_out.gain_vs_cuboid,
            "gain_vs_customer_pct": run_out.gain_vs_customer_pct,
            "clearance_mm": run_out.clearance_mm,
            "poses_searched": len(run_out.poses_searched),
            "comparison_rows": comparison_rows_expect,
            "truck_boxes": run_out.truck_boxes,
            "parts_per_truck": run_out.parts_per_truck,
            "trips_per_year": run_out.trips_per_year,
            "truck_by_volume": truck.get("by_volume"),
            "truck_by_weight": truck.get("by_weight"),
            "truck_limited_by": truck.get("limited_by"),
            "truck_tare_kg": truck.get("tare_kg"),
            "truck_floor_grid": tuple(truck.get("floor_grid") or ()) or None,
            "truck_stack": truck.get("stack"),
            "truck_outer": numbers["truck_outer"],
            "max_weight_kg": numbers["max_weight_kg"],
            "bom_build_height_mm": dunnage.get("build_height_mm"),
            "bom_inner_h_mm": dunnage.get("inner_h_mm"),
            "bom_fits": dunnage.get("fits"),
            "bom_elements": [(e.get("name"), e.get("qty"))
                            for e in dunnage.get("elements") or []],
        }
        check(numbers == expect,
              "every numbers[...] entry matches RunOut / result_json",
              f": got={numbers} want={expect}")
        check(numbers["truck_outer"] is not None
              and len(numbers["truck_outer"]) == 3
              and numbers["max_weight_kg"] is not None,
              "numbers['truck_outer']/['max_weight_kg'] resolve for a real "
              "catalogue asset (DRAFT-BOX-1 below checks the DB-only case "
              "against a known value)",
              f": {numbers['truck_outer']} / {numbers['max_weight_kg']}")
        # Non-vacuity: this run must actually HAVE these numbers, or the
        # equality above would pass just as well against a broken build_pdf
        # that printed None everywhere.
        check(run_out.best_count and run_out.cuboid_count
              and run_out.gain_vs_cuboid and run_out.gain_vs_customer_pct
              and run_out.trips_per_year and numbers["comparison_rows"]
              and numbers["bom_elements"],
              "fixture really does exercise every field (non-vacuity)",
              f": {expect}")

        # --- negative proof: numbers is not a fixed dict that ignores what's
        # actually in result_json ---------------------------------------
        corrupted_json = copy.deepcopy(run_row.result_json)
        corrupted_json["truck"]["by_volume"] = 999999
        corrupted_run = SolveJob(
            id="00000000-0000-0000-0000-0000000000cc",
            part_id=run_row.part_id, project_id=project.id, status="done",
            result_json=corrupted_json,
        )
        db.add(corrupted_run)
        db.commit()
        corrupted_out = build_pdf(project_row, part_row, corrupted_run,
                                  _run_out(corrupted_run, project_row),
                                  _TMP / "corrupted.pdf")
        check(corrupted_out["numbers"] != numbers
              and corrupted_out["numbers"]["truck_by_volume"] == 999999,
              "corrupting result_json.truck.by_volume changes numbers",
              f": corrupted={corrupted_out['numbers']['truck_by_volume']} "
              f"clean={numbers['truck_by_volume']}")

        # --- run selection: one helper, shared by the route and the CLI ----
        # A second, NEWER done run, then recommended_run_id pinned to the
        # OLDER one -- proposal_run_for and create_proposal must both still
        # pick the recommended run, not newest-done (this is the bug the
        # standalone CLI had: it always took newest-done).
        posted_b = app_main.solve_project_endpoint(
            project.id, SolveIn(assets=["PLS1280"]), db=db
        )
        _run_enqueued()
        status_b = app_main.solve_job_status(posted_b.solve_job_id, db=db)
        check(status_b.status == "done", "second solve reaches done",
              f": status={status_b.status} error={status_b.error}")

        app_main.patch_project(
            project.id, ProjectPatch(recommended_run_id=posted.solve_job_id),
            db=db,
        )
        project_row2 = db.get(Project, project.id)
        helper_run = proposal_run_for(project_row2, db)
        check(helper_run is not None and helper_run.id == posted.solve_job_id,
              "proposal_run_for returns the recommended (older) run, not "
              "newest-done",
              f": got={getattr(helper_run, 'id', None)} "
              f"want={posted.solve_job_id} (newer={posted_b.solve_job_id})")

        proposed_b = app_main.create_proposal(project.id, db=db)
        check(proposed_b.run_id == posted.solve_job_id,
              "POST proposal picks the SAME run the helper does",
              f": got={proposed_b.run_id} want={posted.solve_job_id}")

        # --- ticket #1 (reconciled audit): explicit run_id -----------------
        # (a) run_id belonging to a DIFFERENT project -> 404.
        other_project = app_main.create_project(
            ProjectIn(customer="Other", part_number="Z", part_name="W"), db=db
        )
        other_run = SolveJob(
            id="00000000-0000-0000-0000-0000000000aa",
            part_id=run_row.part_id, project_id=other_project.id,
            status="done", result_json=run_row.result_json,
        )
        db.add(other_run)
        db.commit()
        try:
            app_main.create_proposal(project.id, run_id=other_run.id, db=db)
            check(False, "run_id from another project -> 404",
                  ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 404, "run_id from another project -> 404",
                  f": got {e.status_code}: {e.detail}")

        # (c) explicit OLDER done run wins over newest-done -- proven by
        # first showing what IGNORING run_id would have picked (newest),
        # then showing the explicit call picked the older one instead.
        app_main.patch_project(project.id, ProjectPatch(recommended_run_id=None),
                               db=db)
        project_row3 = db.get(Project, project.id)
        ignored_choice = proposal_run_for(project_row3, db)
        check(ignored_choice is not None
              and ignored_choice.id == posted_b.solve_job_id,
              "sanity: ignoring run_id would pick the NEWEST run",
              f": got={getattr(ignored_choice, 'id', None)} "
              f"want={posted_b.solve_job_id}")

        proposed_c = app_main.create_proposal(project.id,
                                              run_id=posted.solve_job_id, db=db)
        check(proposed_c.run_id == posted.solve_job_id != posted_b.solve_job_id,
              "explicit run_id=<older done run> -> response carries THAT "
              "run's id, not the newest (proves the parameter -- not "
              "proposal_run_for -- decided)",
              f": got={proposed_c.run_id} older={posted.solve_job_id} "
              f"newer(would-be-ignored-default)={posted_b.solve_job_id}")

        run_proposal(proposed_c.id)
        done_c = app_main.get_proposal(proposed_c.id, db=db)
        check(done_c.status == "done" and done_c.run_id == posted.solve_job_id,
              "rendered proposal (actual PDF built) still carries the "
              "explicit older run's id", f": status={done_c.status} "
              f"run_id={done_c.run_id}")

        # (d) default (no run_id, no recommendation) -> response carries the
        # run it actually used (newest-done, per proposal_run_for above).
        proposed_d = app_main.create_proposal(project.id, db=db)
        check(proposed_d.run_id == posted_b.solve_job_id,
              "default (no run_id) -> response carries the newest-done "
              "run's id it used",
              f": got={proposed_d.run_id} want={posted_b.solve_job_id}")

        # (b) run_id whose render_status is still "pending" -> 409, not a
        # PDF with a blank exploded-drawing page. Inserted AFTER (c)/(d):
        # it is itself a "done" row and would otherwise become the newest
        # one, changing what those two checks expect "ignoring run_id"
        # to resolve to.
        pending_run = SolveJob(
            id="00000000-0000-0000-0000-0000000000bb",
            part_id=run_row.part_id, project_id=project.id, status="done",
            result_json={**run_row.result_json, "render_status": "pending"},
        )
        db.add(pending_run)
        db.commit()
        try:
            app_main.create_proposal(project.id, run_id=pending_run.id, db=db)
            check(False, "render_status=pending run -> 409",
                  ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 409
                  and e.detail == "Drawings still rendering; try again in a minute",
                  "render_status=pending run -> 409",
                  f": got {e.status_code}: {e.detail}")

        # render_status="failed" -> 409 naming the error, never a "done"
        # proposal with a blank exploded page (review finding on ticket #1).
        failed_run = SolveJob(
            id="00000000-0000-0000-0000-0000000000f1",
            part_id=run_row.part_id, project_id=project.id, status="done",
            result_json={**run_row.result_json, "render_status": "failed",
                         "render_error": "boom"},
        )
        db.add(failed_run)
        db.commit()
        try:
            app_main.create_proposal(project.id, run_id=failed_run.id, db=db)
            check(False, "render_status=failed run -> 409", ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 409 and "boom" in e.detail
                  and "re-solve" in e.detail,
                  "render_status=failed run -> 409 naming the error",
                  f": got {e.status_code}: {e.detail}")

        # --- review fix 1: an empty-result done run is not proposable ------
        # Worker's own "No layout found" case: still status="done", just
        # nothing in it. Hand-inserted because reproducing it from real CAD
        # would need a part that fits nothing on file.
        empty_run = SolveJob(
            id="00000000-0000-0000-0000-0000000000ee",
            part_id=run_row.part_id, project_id=project.id, status="done",
            result_json={"catalogue": [], "custom": None,
                        "custom_beats_catalogue": False, "best_count": 0,
                        "truck": None, "warnings": [], "poses_searched": [],
                        "clearance_mm": 5.0},
        )
        db.add(empty_run)
        db.commit()
        app_main.patch_project(
            project.id, ProjectPatch(recommended_run_id=empty_run.id), db=db,
        )
        try:
            app_main.create_proposal(project.id, db=db)
            check(False, "empty-result run -> 422", ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 422 and "nothing to propose" in e.detail,
                  "empty-result run -> 422", f": got {e.status_code}: {e.detail}")

        # build_pdf itself must not raise on an empty-result run either (an
        # older proposal row, or a direct call, could still hand it one).
        empty_run_out = _run_out(empty_run, project_row)
        empty_result = build_pdf(project_row, part_row, empty_run, empty_run_out,
                                 _TMP / "empty.pdf")
        check(empty_result["pages"] >= 6,
              "build_pdf does not raise on an empty-result run",
              f": {empty_result['pages']} pages")

        app_main.patch_project(project.id, ProjectPatch(recommended_run_id=None),
                               db=db)

        # --- review fix 2: a box that exists only in the DB draws to its ----
        # real size, not a 1x1x1 placeholder (containers_named ignores
        # status, unlike containers() -- a draft box counts).
        db.add(Packaging(
            item_code="DRAFT-BOX-1", inner_l_mm=1310, inner_b_mm=910,
            inner_h_mm=900, outer_l_mm=1360, outer_b_mm=960, outer_h_mm=950,
            max_weight_kg=500, tare_kg=30.0, status="draft", kind="container",
        ))
        db.commit()
        posted_draft = app_main.solve_project_endpoint(
            project.id, SolveIn(assets=["DRAFT-BOX-1"]), db=db
        )
        _run_enqueued()
        status_draft = app_main.solve_job_status(posted_draft.solve_job_id, db=db)
        check(status_draft.status == "done",
              "solve with a DB-only draft box reaches done",
              f": status={status_draft.status} error={status_draft.error}")

        draft_run = db.get(SolveJob, posted_draft.solve_job_id)
        draft_run_out = _run_out(draft_run, project_row)
        draft_result = build_pdf(project_row, part_row, draft_run, draft_run_out,
                                 _TMP / "draft.pdf", db=db)
        check(draft_result["numbers"]["truck_outer"] == (1360.0, 960.0, 950.0),
              "numbers['truck_outer'] draws the DB-only box's real size",
              f": {draft_result['numbers']['truck_outer']}")
        check(draft_result["numbers"]["max_weight_kg"] == 500.0,
              "numbers['max_weight_kg'] reads the DB-only box's real cap",
              f": {draft_result['numbers']['max_weight_kg']}")
        # Without `db`, the same box is invisible to `containers()`'s seed
        # fallback -- proving `db=` is what fixed it, not a lucky default.
        no_db_result = build_pdf(project_row, part_row, draft_run, draft_run_out,
                                 _TMP / "draft_no_db.pdf")
        check(no_db_result["numbers"]["truck_outer"] is None,
              "…and is None without a db (the bug this fix closes)",
              f": {no_db_result['numbers']['truck_outer']}")

    finally:
        db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    print(f"proposal PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
