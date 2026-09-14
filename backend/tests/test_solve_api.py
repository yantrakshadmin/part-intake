"""B3-1 acceptance: POST /api/parts/{id}/solve and GET /api/solve-jobs/{id}.

Run:  ./venv/bin/python tests/test_solve_api.py

Skips cleanly when tests/fixtures/customer/ is empty (NDA material, not in
git) — same pattern as test_cad_import.py.

No `fastapi.testclient` here: this venv has fastapi/starlette but not httpx,
so TestClient raises `RuntimeError` on import (checked, not assumed). Instead
this drives the route functions directly — they are plain Python functions
once you supply the `db` session FastAPI would normally inject via Depends()
— and drives `run_solve` directly to stand in for the Celery worker, since no
worker process is running in this test environment. Both are exactly what the
ticket asks for.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Hermetic DB/storage for this run, set BEFORE importing anything under app/
# (app.config.Settings reads env at import time). Never touch a real dev.db.
_TMP = Path(tempfile.mkdtemp(prefix="solve_api_test_"))
os.environ["INTAKE_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["INTAKE_LOCAL_STORAGE_DIR"] = str(_TMP / "files")
os.environ.setdefault("INTAKE_REDIS_URL", "redis://localhost:6379/0")

import dataclasses  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import main as app_main  # noqa: E402
from app.geometry import extract_part  # noqa: E402
from app.catalogue import containers  # noqa: E402
from app.engine import parts_per_truck  # noqa: E402
from app.models import (ExtractionJob, Packaging, PartProfile,  # noqa: E402
                        SolveJob, Vehicle)
from app.config import settings  # noqa: E402
from app.schemas import SolveIn  # noqa: E402
from app.worker import run_solve  # noqa: E402

_ENQUEUED: list[tuple] = []


def _capture_delay(*args, **kwargs):
    """Stand in for Celery's .delay(), recording what the endpoint sent."""
    _ENQUEUED.append((args, kwargs))


app_main.solve_part.delay = _capture_delay


def _run_enqueued() -> None:
    """Run the queued solve with the args the ENDPOINT chose.

    Replaying the real call is the point: params now travel as task arguments
    (they used to be stashed in SolveJob.result_json and destroyed by the
    result), so this is also the only check that the endpoint -> task contract
    carries tare_kg / vehicle / top_n at all.
    """
    args, kwargs = _ENQUEUED.pop()
    run_solve(*args, **kwargs)


FIXTURES = Path(__file__).parent / "fixtures" / "customer"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
WHEEL_KG = 2.5  # matches tests/test_engine.py test_wheel_reproduces_trw_proposal_from_cad


def _make_wheel_part(db) -> PartProfile:
    """Real STEP extraction -> a done ExtractionJob -> a stp-sourced PartProfile.

    Mirrors what upload_step + create_part do, without going over HTTP.
    """
    glb_path = str(_TMP / "wheel.glb")
    result = extract_part(str(WHEEL), glb_path)
    job = ExtractionJob(
        id="wheel-job", status="done", step_path=str(WHEEL),
        glb_path=result.glb_path,
        result_json={
            **dataclasses.asdict(result),
            "candidates": [dataclasses.asdict(c) for c in result.candidates],
        },
    )
    db.add(job)
    part = PartProfile(
        part_number="TRW-WHEEL", part_name="Steering wheel",
        length_mm=370, breadth_mm=360, height_mm=135, weight_kg=WHEEL_KG,
        source="stp", job_id=job.id, glb_path=result.glb_path,
    )
    db.add(part)
    db.commit()
    db.refresh(part)
    return part


def wheel_job_candidates(db, part) -> list[dict]:
    return db.get(ExtractionJob, part.job_id).result_json["candidates"]


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
        wheel = _make_wheel_part(db)

        # --- 1. POST returns fast (does not block for the ~11s solve) -------
        t0 = time.monotonic()
        posted = app_main.solve_part_endpoint(
            wheel.id, SolveIn(tare_kg=None), db=db
        )
        elapsed = time.monotonic() - t0
        check(elapsed < 2.0, "POST /solve returns without blocking",
              f": {elapsed:.2f}s")
        check(posted.status == "pending" and bool(posted.solve_job_id),
              "POST /solve response shape", f": {posted}")

        # Nothing has run the enqueued job yet (no worker in this test env) --
        # drive the worker function directly, exactly like the Celery task
        # would, then poll through the real GET route.
        _run_enqueued()
        status = app_main.solve_job_status(posted.solve_job_id, db=db)
        check(status.status == "done", "solve job reaches done",
              f": status={status.status} error={status.error}")

        if status.status == "done":
            wheel_layout = next(
                (l for l in status.result.catalogue if l.asset_name == "PLS12803"),
                None,
            )
            check(wheel_layout is not None,
                  "PLS12803 present in top-N catalogue",
                  f": got {[l.asset_name for l in status.result.catalogue]}")
            if wheel_layout is not None:
                check(wheel_layout.count == 48 and wheel_layout.grid == (3, 2, 8),
                      "wheel reproduces TRW proposal through HTTP",
                      f": count={wheel_layout.count} grid={wheel_layout.grid}")
            # C-TARE: no override supplied, but PLS12803 carries its OWN
            # tare_kg (33, seeded from the SCS report) -- the truck tier now
            # defaults to that, it does not go null just because the request
            # didn't type a number in.
            check(status.result.truck is not None
                  and status.result.truck.asset_name == "PLS12803"
                  and status.result.truck.tare_kg == 33.0,
                  "no override -> truck defaults to the asset's own tare",
                  f": truck={status.result.truck}")
            # B-DRAW, hard rule 9: the drawing is rendered in the worker and
            # shipped as `drawing_url`. Nothing here checked that, and every
            # render in this file was in fact failing silently -- the worker
            # swallows render errors on purpose so a drawing cannot fail a
            # solve, so a missing storage directory made all of them vanish
            # with nothing in `warnings`. Assert the field arrives populated
            # AND that the file is really on disk behind it.
            drawn = [l for l in status.result.catalogue if l.drawing_url]
            check(len(drawn) == len(status.result.catalogue),
                  "every ranked layout ships a drawing_url",
                  f": {len(drawn)}/{len(status.result.catalogue)} "
                  f"{[l.drawing_url for l in status.result.catalogue]}")
            on_disk = [
                l.asset_name for l in drawn
                if not (Path(settings.local_storage_dir)
                        / l.drawing_url.rsplit("/", 1)[-1]).is_file()
            ]
            check(not on_disk, "the PNG behind each drawing_url exists",
                  f": missing for {on_disk}")

            # B-GIF, same hard rule 9 discipline, tightened for the VM cost
            # fix (worker.GIF_FOR_TOP_CATALOGUE_ONLY): the packing-sequence
            # GIF is now only rendered for catalogue[0] (top-ranked box) and
            # the custom design -- every other ranked layout still gets its
            # exploded PNG but ships gif_url=None on purpose.
            def _opens(url: str) -> bool:
                from PIL import Image
                path = Path(settings.local_storage_dir) / url.rsplit("/", 1)[-1]
                with Image.open(path) as im:
                    im.verify()
                return path.is_file()

            top = status.result.catalogue[0]
            check(bool(top.gif_url) and _opens(top.gif_url),
                  "catalogue[0] (top ranked) ships a gif_url that opens",
                  f": {top.gif_url}")
            others_have_gif = [
                l.asset_name for l in status.result.catalogue[1:]
                if l.gif_url is not None
            ]
            check(not others_have_gif,
                  "other ranked layouts ship gif_url=None",
                  f": unexpected gif on {others_have_gif}")
            check(status.result.custom is not None
                  and bool(status.result.custom.gif_url)
                  and _opens(status.result.custom.gif_url),
                  "custom.gif_url ships and opens with PIL",
                  f": {status.result.custom.gif_url if status.result.custom else None}")

            # Ticket 1a: packed_url is the GIF's own final frame -- present
            # exactly where gif_url is present (top-ranked + custom), null
            # everywhere else, opens with PIL, same size as the GIF frames,
            # and not a blank/solid image.
            def _packed_ok(layout) -> bool:
                if not (layout.gif_url and layout.packed_url):
                    return False
                from PIL import Image
                gpath = (Path(settings.local_storage_dir)
                        / layout.gif_url.rsplit("/", 1)[-1])
                ppath = (Path(settings.local_storage_dir)
                        / layout.packed_url.rsplit("/", 1)[-1])
                with Image.open(gpath) as gim, Image.open(ppath) as pim:
                    gim.seek(gim.n_frames - 1)
                    same_size = pim.size == gim.size
                    colours = pim.convert("RGB").getcolors(maxcolors=256 * 256)
                    non_blank = colours is None or len(colours) > 1
                return ppath.is_file() and same_size and non_blank

            check(_packed_ok(top),
                  "catalogue[0] ships a packed_url matching its GIF's final frame",
                  f": {top.packed_url}")
            others_have_packed = [
                l.asset_name for l in status.result.catalogue[1:]
                if l.packed_url is not None
            ]
            check(not others_have_packed,
                  "other ranked layouts ship packed_url=None (no GIF built)",
                  f": unexpected packed_url on {others_have_packed}")
            check(status.result.custom is not None
                  and _packed_ok(status.result.custom),
                  "custom.packed_url ships and matches its GIF's final frame",
                  f": {status.result.custom.packed_url if status.result.custom else None}")

            # The synthesised custom design has no catalogue tare and no
            # override was given, so IT (only) gets the "no tare" warning.
            check(any("no tare weight for custom" in w.lower()
                      for w in status.result.warnings),
                  "untared custom design still gets its own tare warning",
                  f": {status.result.warnings}")

        # --- 1a. AUDIT fields survive the response model (hard rule 9) -------
        if status.status == "done":
            res = status.result
            check(all(l.count_upper >= l.count for l in res.catalogue),
                  "count_upper >= count on every layout",
                  f": {[(l.count, l.count_upper) for l in res.catalogue]}")
            # F3/F4 over real HTTP (serialisation, not the route function's
            # return value straight off the dataclass -- pydantic drops
            # undeclared fields silently, CLAUDE.md hard rule 9).
            check(all(l.cuboid_count > 0 for l in res.catalogue)
                  and all(len(l.reasons) >= 3 for l in res.catalogue),
                  "every catalogue LayoutOut carries cuboid_count and reasons",
                  f": {[(l.asset_name, l.cuboid_count, len(l.reasons)) for l in res.catalogue]}")
            check(all(l.dunnage is not None and len(l.dunnage.slack_lbh) == 3
                      for l in res.catalogue),
                  "dunnage.slack_lbh ships on every layout",
                  f": {[getattr(l.dunnage, 'slack_lbh', None) for l in res.catalogue]}")
            check(res.custom is not None and res.custom.dunnage is not None
                  and len(res.custom.dunnage.slack_lbh) == 3,
                  "custom.dunnage.slack_lbh ships")
            check(res.custom is not None and res.custom.count_upper >= res.custom.count,
                  "custom.count_upper ships and is >= count",
                  f": {(res.custom.count, res.custom.count_upper) if res.custom else None}")
            check(res.custom is not None and res.custom.dunnage.fits,
                  "custom box fits its own BOM",
                  f": slack {res.custom.dunnage.slack_lbh if res.custom else None}")

            # POST /api/truck-fit through the route function and its models.
            from app.schemas import TruckFitIn
            tf = app_main.truck_fit(TruckFitIn(
                outer_l_mm=1200, outer_b_mm=800, outer_h_mm=986,
                kg_per_box=230, vehicle="32_ft_sxl"), db=db)
            check((tf.boxes, tf.limited_by, tf.by_volume, tf.by_weight, tf.stack)
                  == (39, "weight", 48, 39, 2),
                  "truck-fit: 230kg box in a 32ft -> 39 by weight", f": {tf}")
            tf0 = app_main.truck_fit(TruckFitIn(
                outer_l_mm=1200, outer_b_mm=800, outer_h_mm=986,
                vehicle="32_ft_sxl", max_stack=1), db=db)
            check((tf0.boxes, tf0.limited_by, tf0.stack) == (24, "volume", 1),
                  "truck-fit: no weight, max_stack=1 -> 24 by volume, not a 'tie'",
                  f": {tf0}")
            try:
                app_main.truck_fit(TruckFitIn(outer_l_mm=1, outer_b_mm=1,
                                              outer_h_mm=1, vehicle="nope"), db=db)
                check(False, "truck-fit: unknown vehicle -> 404")
            except HTTPException as exc:
                check(exc.status_code == 404, "truck-fit: unknown vehicle -> 404",
                      f": {exc.status_code}")
            check(len(res.poses_searched) == 3,
                  "unrestricted solve searched 3 poses (one per OBB axis)",
                  f": {res.poses_searched}")
            check(res.truck is not None and res.truck.by_volume >= res.truck.boxes
                  and res.truck.by_weight >= res.truck.boxes and res.truck.stack >= 1,
                  "truck by_volume/by_weight/stack ship",
                  f": {res.truck}")

            # confirmed_pose_only: confirm candidate 0 on the part, re-solve.
            cand0 = wheel_job_candidates(db, wheel)[0]
            wheel.confirmed_orientation = {"matrix": cand0["rotation_matrix"],
                                           "label": cand0["label"]}
            db.commit()
            posted_locked = app_main.solve_part_endpoint(
                wheel.id, SolveIn(confirmed_pose_only=True), db=db)
            _run_enqueued()
            locked = app_main.solve_job_status(posted_locked.solve_job_id, db=db)
            check(locked.status == "done"
                  and locked.result.poses_searched == [cand0["label"]],
                  "confirmed_pose_only searches exactly the confirmed pose",
                  f": {getattr(locked.result, 'poses_searched', None)} "
                  f"error={locked.error}")
            check(locked.status == "done" and locked.result.catalogue
                  and all(l.pose_label == cand0["label"]
                          for l in locked.result.catalogue),
                  "every locked layout is in the confirmed pose")
            wheel.confirmed_orientation = None
            db.commit()

            # clearance_mm reaches the engine: at 0 the wheel's in-plane pitch
            # is its extent (no interleave), at the default it is extent + 5.
            posted_c0 = app_main.solve_part_endpoint(
                wheel.id, SolveIn(clearance_mm=0.0, assets=["PLS12803"]), db=db)
            _run_enqueued()
            c0 = app_main.solve_job_status(posted_c0.solve_job_id, db=db)
            base = unrestricted_by_name = {l.asset_name: l for l in res.catalogue}
            check(c0.status == "done" and c0.result.clearance_mm == 0.0
                  and res.clearance_mm == 5.0,
                  "clearance_mm is echoed back", f": {getattr(c0.result, 'clearance_mm', None)}")
            l0, l5 = c0.result.catalogue[0], base["PLS12803"]
            check(l0.pitch_lbh[0] == l5.pitch_lbh[0] - 5.0
                  and l0.pitch_lbh[1] == l5.pitch_lbh[1] - 5.0
                  and l0.pitch_lbh[2] == l5.pitch_lbh[2],
                  "clearance_mm=0 takes 5mm off both in-plane pitches, not the stack",
                  f": {l0.pitch_lbh} vs {l5.pitch_lbh}")

        # --- 1b. UI-2: `assets` restricts the candidate set -------------------
        # Acceptance 1: naming PLS12803 ranks only it, at the SAME count as the
        # unrestricted run above -- proves the filter narrows the candidate
        # set without touching the nesting result itself.
        unrestricted_by_name = {l.asset_name: l for l in status.result.catalogue}
        posted_restricted = app_main.solve_part_endpoint(
            wheel.id, SolveIn(assets=["PLS12803"]), db=db
        )
        _run_enqueued()
        status_restricted = app_main.solve_job_status(
            posted_restricted.solve_job_id, db=db
        )
        restricted_names = [l.asset_name for l in status_restricted.result.catalogue]
        check(restricted_names == ["PLS12803"],
              "assets=[PLS12803] ranks exactly that asset", f": {restricted_names}")
        check(status_restricted.status == "done" and restricted_names
              and status_restricted.result.catalogue[0].count
                  == unrestricted_by_name["PLS12803"].count,
              "restricted count matches the unrestricted run for the same asset",
              f": restricted={status_restricted.result.catalogue[0].count if restricted_names else None} "
              f"unrestricted={unrestricted_by_name['PLS12803'].count}")

        # Acceptance 3: naming a DRAFT box ranks it anyway and warns its dims
        # are unconfirmed. Add a dedicated draft row so this really exercises
        # the draft path (PLS12803 above is still "checked" at this point).
        db.add(Packaging(
            item_code="ZZ-TEST-DRAFT", inner_l_mm=1150, inner_b_mm=950,
            inner_h_mm=200, outer_l_mm=1200, outer_b_mm=1000, outer_h_mm=250,
            max_weight_kg=200, status="draft", kind="container",
        ))
        db.commit()
        posted_draft = app_main.solve_part_endpoint(
            wheel.id, SolveIn(assets=["ZZ-TEST-DRAFT"]), db=db
        )
        _run_enqueued()
        status_draft = app_main.solve_job_status(posted_draft.solve_job_id, db=db)
        draft_names = [l.asset_name for l in status_draft.result.catalogue]
        check("ZZ-TEST-DRAFT" in draft_names,
              "naming a draft box ranks it anyway", f": {draft_names}")
        check(any("ZZ-TEST-DRAFT" in w and "unconfirmed" in w
                  for w in status_draft.result.warnings),
              "warns the named draft's dims are unconfirmed",
              f": {status_draft.result.warnings}")

        # Acceptance 4: naming only unknown codes -> empty catalogue, a
        # warning naming the misses, no 500.
        posted_unknown = app_main.solve_part_endpoint(
            wheel.id, SolveIn(assets=["NOPE-1", "NOPE-2"]), db=db
        )
        _run_enqueued()
        status_unknown = app_main.solve_job_status(posted_unknown.solve_job_id, db=db)
        check(status_unknown.status == "done",
              "unknown-only assets does not 500",
              f": status={status_unknown.status} error={status_unknown.error}")
        check(status_unknown.result.catalogue == [],
              "unknown-only assets -> empty catalogue",
              f": {status_unknown.result.catalogue}")
        check(any("NOPE-1" in w and "NOPE-2" in w
                  for w in status_unknown.result.warnings),
              "warning names the codes that were not found",
              f": {status_unknown.result.warnings}")

        # Acceptance 2: omitting `assets` (or explicit None) is the existing
        # unrestricted behaviour -- exercised implicitly by every SolveIn()
        # call elsewhere in this file; assert the default here too.
        check(SolveIn().assets is None,
              "assets omitted -> None -> unrestricted (existing behaviour)")

        # --- 2. tare_kg supplied -> a truck block appears --------------------
        posted2 = app_main.solve_part_endpoint(
            wheel.id, SolveIn(tare_kg=30.0, assets=["PLS12101", "PLS12801"]),
            db=db
        )
        _run_enqueued()
        status2 = app_main.solve_job_status(posted2.solve_job_id, db=db)
        check(status2.status == "done" and status2.result.truck is not None,
              "tare_kg=30 -> truck block present",
              f": status={status2.status} truck={getattr(status2.result, 'truck', None)}")

        # --- 2a. the truck number is the BEST ranked option, not the first --
        # Restricted (above) to a pair whose count order and truck order
        # DISAGREE: PLS12101 takes 42 of the wheel per box against PLS12801's
        # 36, but its 1000mm outer breadth costs a floor row, so it ships 1512
        # parts/truck against 1728. Ranking is by parts-per-box, so PLS12101
        # comes first and the truck answer must still come from PLS12801.
        #
        # This used to run unrestricted, where PLS12803 and PLS12103 tied at 48
        # per box and split 2304 vs 1728 on the truck. That divergence existed
        # only because a stable sort on -count left seed order to break the tie
        # -- reordering seed_data.PACKAGING moved the truck answer by 25%.
        # `rank_catalogue` now breaks ties by outer volume (equal count, smaller
        # box, so strictly more per truck), which is a fix but it also made the
        # non-vacuity guard below pass trivially: both ranked options came back
        # at 2304. A divergence from DIFFERENT counts is the case that
        # genuinely survives the tie-break, so that is what this pins now.
        veh_row = db.scalars(
            select(Vehicle).where(Vehicle.name == "32_ft_sxl")
        ).first()
        veh = (veh_row.name, veh_row.cargo_l_mm, veh_row.cargo_b_mm,
               veh_row.cargo_h_mm, veh_row.payload_kg)
        by_name = {c.name: c for c in containers(db)}
        # Mirror the worker's own precedence -- the asset's own tare, and the
        # request's only as a fallback. A flat 30.0 here recomputes a DIFFERENT
        # quantity than the one under test, and only agrees while every fixture
        # happens to be volume-limited (tare-insensitive). That is a coincidence,
        # not a guarantee.
        def _tare(c):
            return c.tare_kg if c.tare_kg is not None else 30.0
        alt = [
            parts_per_truck(by_name[l.asset_name].outer, l.count, WHEEL_KG,
                            _tare(by_name[l.asset_name]), veh).parts
            for l in status2.result.catalogue if l.asset_name in by_name
        ]
        ranked_names = [l.asset_name for l in status2.result.catalogue
                        if l.asset_name in by_name]
        # The engine scores [*catalogue, custom]. `assets` restricts the
        # CATALOGUE half only, so a restricted request can leave the synthesised
        # box the outright best -- and it is here, 2304 against 1728. Scoring
        # only the catalogue entries compares the engine's answer against a
        # smaller option set than the engine had, and then calls it wrong.
        cust = status2.result.custom
        if cust is not None:
            alt.append(parts_per_truck(tuple(cust.outer), cust.count,
                                       WHEEL_KG, 30.0, veh).parts)
            ranked_names.append("custom")

        # Guard against a vacuous assertion: if every option gave the same
        # parts/truck this check would pass regardless of which one was picked.
        check(len(set(alt)) > 1,
              "ranked options really do differ on parts/truck",
              f": {dict(zip(ranked_names, alt))}")
        check(status2.result.truck.parts == max(alt),
              "truck fit is the best ranked option, not catalogue[0]",
              f": reported={status2.result.truck.parts} best={max(alt)} all={alt}")

        # ...and it says WHICH option, so the reader (and the load drawing)
        # cannot attach the right number to the wrong box.
        want_name = ranked_names[alt.index(max(alt))]
        check(status2.result.truck.asset_name == want_name,
              "truck fit names the option it belongs to",
              f": got {status2.result.truck.asset_name!r} want {want_name!r}")

        # --- 2b. ONE catalogue source for the whole request ------------------
        # solve() defaults to containers() with no db, which reads the seed
        # constants, while the truck lookup reads containers(db). Those two can
        # diverge, and then the lookup raises StopIteration into an opaque
        # failed job. Everything ranked must exist in the DB list.
        db_names = {c.name for c in containers(db)}
        ranked = [l.asset_name for l in status2.result.catalogue]
        missing = [n for n in ranked if n not in db_names]
        check(not missing, "ranked assets all exist in the DB catalogue",
              f": ranked={ranked} missing={missing}")

        # --- 2c. a box the team added and verified must be rankable ----------
        # It exists only in the DB, so this fails if solve() is ever pointed
        # back at the seed constants. 1400mm inner takes 11 layers of the wheel
        # at its 120mm pitch, so it must also WIN, not merely appear.
        db.add(Packaging(
            item_code="ZZ-TEST-TALL", inner_l_mm=1150, inner_b_mm=950,
            inner_h_mm=1400, outer_l_mm=1200, outer_b_mm=1000, outer_h_mm=1600,
            max_weight_kg=900, status="checked", kind="container",
        ))
        db.commit()
        posted3 = app_main.solve_part_endpoint(wheel.id, SolveIn(), db=db)
        _run_enqueued()
        status3 = app_main.solve_job_status(posted3.solve_job_id, db=db)
        top = status3.result.catalogue[0] if status3.status == "done" else None
        check(top is not None and top.asset_name == "ZZ-TEST-TALL",
              "DB-only verified box reaches the ranking and wins",
              f": top={getattr(top, 'asset_name', None)} "
              f"count={getattr(top, 'count', None)}")

        # --- 2d. per-box winner != per-truck winner ---------------------------
        # ZZ-TEST-TALL wins per box (66 > 48) and loses per truck: its 1600mm
        # outer only stacks one high, so 18 boxes x 66 = 1188 against
        # PLS12803's 48 x 48 = 2304. That divergence is the whole point of
        # scoring every ranked option, and until now nothing tested it -- the
        # wheel's per-box and per-truck winners are the same box, so the
        # earlier checks pass even against a `catalogue[0]` hardcode.
        posted4 = app_main.solve_part_endpoint(
            wheel.id, SolveIn(tare_kg=30.0), db=db
        )
        _run_enqueued()
        st4 = app_main.solve_job_status(posted4.solve_job_id, db=db)
        by_name4 = {c.name: c for c in containers(db)}
        per_truck = {
            l.asset_name: parts_per_truck(
                by_name4[l.asset_name].outer, l.count, WHEEL_KG,
                (by_name4[l.asset_name].tare_kg
                 if by_name4[l.asset_name].tare_kg is not None else 30.0),
                veh).parts
            for l in st4.result.catalogue if l.asset_name in by_name4
        }
        box_winner = st4.result.catalogue[0].asset_name
        truck_winner = max(per_truck, key=per_truck.get)
        # Non-vacuity: this test is worthless unless the two really disagree.
        check(box_winner != truck_winner,
              "fixture really does split per-box and per-truck winners",
              f": box={box_winner} truck={truck_winner} {per_truck}")
        check(st4.result.truck.asset_name == truck_winner,
              "truck block names the per-truck winner, not catalogue[0]",
              f": got {st4.result.truck.asset_name!r} "
              f"want {truck_winner!r} (catalogue[0] is {box_winner!r})")
        check(st4.result.truck.parts == per_truck[truck_winner],
              "truck parts match the option it names",
              f": {st4.result.truck.parts} vs {per_truck[truck_winner]}")
        check(any(box_winner in w and truck_winner in w
                  for w in st4.result.warnings),
              "warning names both the per-box and per-truck winner",
              f": {st4.result.warnings}")

        # --- 2e. the per-box winner may be the CUSTOM box -------------------
        # `options` in run_solve is [*catalogue, custom], so reading the
        # per-box winner off options[0] always named catalogue[0] -- wrong in
        # exactly the case the UI badges "beats catalogue". Here every stocked
        # container is demoted to draft except one deliberately tiny box that
        # holds a single wheel, so the custom design (48) is the per-box winner
        # by a wide margin AND the per-truck winner. The old code would emit
        # "Best per box is ZZ-TEST-TINY, but best per truck is custom" -- a
        # warning about a split that does not exist. Correct behaviour: no
        # split warning at all.
        #
        # This block leaves the catalogue demoted; nothing after it ranks.
        for row in db.scalars(
            select(Packaging).where(Packaging.kind == "container")
        ).all():
            row.status = "draft"
        db.add(Packaging(
            item_code="ZZ-TEST-TINY", inner_l_mm=400, inner_b_mm=400,
            inner_h_mm=150, outer_l_mm=450, outer_b_mm=450, outer_h_mm=200,
            max_weight_kg=200, status="checked", kind="container",
        ))
        db.commit()
        posted5 = app_main.solve_part_endpoint(
            wheel.id, SolveIn(tare_kg=30.0), db=db
        )
        _run_enqueued()
        st5 = app_main.solve_job_status(posted5.solve_job_id, db=db)
        check(st5.status == "done", "demoted-catalogue solve completes",
              f": status={st5.status} error={st5.error}")

        ranked5 = [l.asset_name for l in st5.result.catalogue]
        check(ranked5 == ["ZZ-TEST-TINY"],
              "only the checked box is ranked", f": {ranked5}")

        # Non-vacuity: the custom box must really be the per-box winner here,
        # or the assertion below says nothing about which one gets named.
        cust = st5.result.custom
        check(cust is not None and ranked5
              and cust.count > st5.result.catalogue[0].count,
              "fixture really does make the custom box the per-box winner",
              f": custom={getattr(cust, 'count', None)} "
              f"catalogue[0]={st5.result.catalogue[0].count if ranked5 else None}")
        check(st5.result.truck is not None
              and st5.result.truck.asset_name == "custom",
              "custom is also the per-truck winner here",
              f": {getattr(st5.result.truck, 'asset_name', None)}")
        split = [w for w in st5.result.warnings if "Best per box is" in w]
        check(not split,
              "no split warning when per-box and per-truck winner agree",
              f": {split}")

        # --- 2f. demoted boxes are named, not silently dropped --------------
        # POST /api/packaging saves status="draft" -- which is what the UI's
        # "+ Custom box" button creates -- and containers() only ranks
        # "checked". An engineer who adds the box the customer actually stocks
        # and re-solves saw no card, no warning and no reason why.
        drafted = [w for w in st5.result.warnings if "not ranked" in w]
        check(len(drafted) == 1, "unranked draft boxes get exactly one warning",
              f": {drafted}")
        check(drafted and "PLS12803" in drafted[0] and "ZZ-TEST-TALL" in drafted[0],
              "the warning names the boxes it dropped",
              f": {drafted[0] if drafted else None}")

        # --- 3. manual-source part -> 422, not a cuboid answer ---------------
        manual = PartProfile(
            part_number="MAN-1", part_name="Manual part",
            length_mm=100, breadth_mm=50, height_mm=25, weight_kg=1.0,
            source="manual",
        )
        db.add(manual)
        db.commit()
        db.refresh(manual)
        try:
            app_main.solve_part_endpoint(manual.id, SolveIn(), db=db)
            check(False, "manual part rejected", ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 422, "manual part rejected with 422",
                  f": got {e.status_code}: {e.detail}")

        # --- 404 guards --------------------------------------------------
        try:
            app_main.solve_part_endpoint(999999, SolveIn(), db=db)
            check(False, "unknown part rejected", ": no exception raised")
        except HTTPException as e:
            check(e.status_code == 404, "unknown part -> 404", f": got {e.status_code}")

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
