"""Celery worker: runs the CPU-heavy STEP extraction off the request cycle.

Run: celery -A app.worker worker --loglevel=info --concurrency=2
Concurrency note: each extraction is CPU-bound; set concurrency ~= vCPUs.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import numpy as np
from celery import Celery
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from . import dunnage, engine as engine_mod, synthesis
from .catalogue import containers, containers_named, excluded_drafts
from .config import settings
from .geometry import OrientationCandidate, extract_part, load_unified_mesh
from .insert_drawing import build_gif, explode_png, pose_voxels
from .models import (ExtractionJob, PartProfile, Project, Proposal, SolveJob,
                    Vehicle)
from .proposal import build_pdf
from .reasons import reasons_for
from .runs import _run_out

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


def _render_drawings(job_id: str, mesh, candidates, assets, result,
                     clearance_lbh=engine_mod.DEFAULT_CLEARANCE_LBH):
    """Exploded insert drawings AND packing-sequence GIFs for every ranked
    layout plus the custom design. Voxelises once per distinct pose --
    `pose_voxels` is the expensive call, and several ranked layouts commonly
    share a pose.

    A render failure -- including `explode_png`/`build_gif`'s own
    drawn-count-vs-BOM AssertionError -- must never fail the solve: it is
    logged and that layout's picture is left absent. The count is the
    valuable output; the picture is not worth losing it over. The PNG and the
    GIF are guarded SEPARATELY -- a GIF failure must not cost the PNG that
    already rendered fine, and vice versa.

    Returns ({catalogue index: (png_url, gif_url)}, (custom png_url, gif_url)).
    """
    rotation_by_label = {c.label: c.rotation_matrix for c in candidates}
    inner_by_name = {a.name: a.inner for a in assets}
    voxel_cache: dict = {}

    def voxels_for(pose_label: str):
        if pose_label not in voxel_cache:
            rotation = rotation_by_label.get(pose_label)
            try:
                voxel_cache[pose_label] = (
                    pose_voxels(mesh, rotation) if rotation is not None
                    else None
                )
            except Exception:
                logger.exception("pose_voxels failed for %s pose %r",
                                 job_id, pose_label)
                voxel_cache[pose_label] = None
        return voxel_cache[pose_label]

    def _write(data: bytes, file_name: str) -> str:
        path = Path(settings.local_storage_dir) / file_name
        # A missing storage directory would otherwise make EVERY drawing
        # vanish with nothing in `warnings` and nothing wrong on the
        # caller's side. Upload happens to create the directory first in
        # production, so this only ever showed up as a silent no-op under
        # test.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"/api/files/{file_name}"

    def render(*, pose_label, extent_lbh, pitch_lbh, grid, inner_lbh,
               asset_name, count, file_stem) -> tuple[str | None, str | None]:
        voxels = voxels_for(pose_label)
        if voxels is None:
            return None, None
        try:
            # Same pure expression engine.solve used to stamp `layout.dunnage`
            # -- recomputed, not a second opinion, so the drawing cannot
            # disagree with the numbers already returned.
            bom = dunnage.bom(extent_lbh, pitch_lbh, grid, inner_lbh,
                              clearance_lbh)
        except Exception:
            logger.exception("dunnage.bom failed for %s (%s)",
                             job_id, file_stem)
            return None, None

        png_url = None
        try:
            png = explode_png(voxels=voxels, extent_lbh=extent_lbh,
                              pitch_lbh=pitch_lbh, grid=grid,
                              inner_lbh=inner_lbh, bom=bom,
                              asset_name=asset_name, count=count)
            png_url = _write(png, f"{file_stem}.png")
        except Exception:
            logger.exception("insert drawing failed for %s (%s)",
                             job_id, file_stem)

        gif_url = None
        try:
            gif = build_gif(voxels=voxels, extent_lbh=extent_lbh,
                            pitch_lbh=pitch_lbh, grid=grid,
                            inner_lbh=inner_lbh, bom=bom,
                            asset_name=asset_name, count=count)
            gif_url = _write(gif, f"{file_stem}.gif")
        except Exception:
            logger.exception("build sequence gif failed for %s (%s)",
                             job_id, file_stem)

        return png_url, gif_url

    drawing_urls: dict = {}
    gif_urls: dict = {}
    for i, layout in enumerate(result.catalogue):
        inner_lbh = inner_by_name.get(layout.asset_name)
        if inner_lbh is None:
            continue
        png_url, gif_url = render(
            pose_label=layout.pose_label, extent_lbh=layout.extent_lbh,
            pitch_lbh=layout.pitch_lbh, grid=layout.grid,
            inner_lbh=inner_lbh, asset_name=layout.asset_name,
            count=layout.count, file_stem=f"drawing_{job_id}_{i}")
        if png_url is not None:
            drawing_urls[i] = png_url
        if gif_url is not None:
            gif_urls[i] = gif_url

    custom_drawing_url = custom_gif_url = None
    if result.custom is not None:
        custom_drawing_url, custom_gif_url = render(
            pose_label=result.custom.pose_label,
            extent_lbh=result.custom.extent_lbh,
            pitch_lbh=result.custom.pitch_lbh, grid=result.custom.grid,
            inner_lbh=result.custom.inner, asset_name="custom design",
            count=result.custom.count,
            file_stem=f"drawing_{job_id}_custom")

    return drawing_urls, gif_urls, custom_drawing_url, custom_gif_url


@celery_app.task(name="solve_part", time_limit=600, soft_time_limit=570)
def solve_part(job_id: str, params: dict | None = None) -> None:
    run_solve(job_id, params)


def run_solve(job_id: str, params: dict | None = None) -> None:
    """Plain function so the API can run it in a thread when the Celery
    broker is unreachable (dev without redis) — see main.solve_part_endpoint.

    Time limit is 600 not 300: solve() is ~11s for 15 containers today, and
    voxelising is the floor cost as the other 34 catalogue assets land.

    `params` (tare_kg, vehicle, top_n) arrives as a TASK ARGUMENT and is
    deliberately not read back out of the SolveJob row. It used to be stashed
    in `result_json` and then overwritten by the result, so a redelivered task
    -- and `task_acks_late = True` above means a worker lost mid-flight IS
    redelivered -- silently answered a different question: no tare (plus a
    bogus "no tare" warning), top_n back to 2, vehicle None.
    """
    params = params or {}
    with Session(_engine) as db:
        job = db.get(SolveJob, job_id)
        if job is None:
            logger.error("Solve job %s not found", job_id)
            return
        job.status = "processing"
        db.commit()

        try:
            part = db.get(PartProfile, job.part_id)
            if part is None:
                raise ValueError(f"Part {job.part_id} not found")

            mesh, _solid_count = load_unified_mesh(part.glb_path)

            extraction = (db.get(ExtractionJob, part.job_id)
                          if part.job_id else None)
            if extraction is None or not extraction.result_json:
                raise ValueError(f"Part {part.id} has no finished extraction "
                                 "to nest from")
            candidate_dicts = extraction.result_json["candidates"]
            # nesting.measure_poses only touches .label and .rotation_matrix,
            # but rebuild real objects rather than pass dicts around.
            candidates = [OrientationCandidate(**d) for d in candidate_dicts]

            # Which pose the engineer signed off in the viewer, by matrix.
            confirmed = (part.confirmed_orientation or {}).get("matrix")
            confirmed_label = None
            if confirmed is not None:
                want = np.asarray(confirmed, dtype=float)
                confirmed_label = next(
                    (c.label for c in candidates
                     if np.allclose(np.asarray(c.rotation_matrix, dtype=float),
                                    want)),
                    None,
                )
            warnings: list[str] = []
            # Pose lock. All four Tata decks pack a part the engine would lay
            # flat "in vertical orientation" (the customer's logo faces out),
            # and the engine's flat count beat the deck's on three of them.
            # A count in a pose the customer will not accept is not a
            # recommendation, so the engineer can restrict the search to the
            # pose they confirmed. Default is still every pose -- the
            # difference between the two is itself the number worth seeing.
            if params.get("confirmed_pose_only"):
                if confirmed_label is None:
                    warnings.append(
                        "Confirmed-pose-only was requested but this part has "
                        "no confirmed resting pose that matches a candidate; "
                        "all poses were searched."
                    )
                else:
                    candidates = [c for c in candidates
                                  if c.label == confirmed_label]

            # One catalogue source for the whole request. solve() defaults to
            # containers() with NO db, which reads the seed constants -- so it
            # cannot see a box the team added and verified, and it can rank a
            # winner that the truck lookup below then fails to find.
            requested = params.get("assets")
            missing_codes: list[str] = []
            if requested:
                assets, missing_codes = containers_named(db, requested)
            else:
                assets = containers(db)

            clearance_mm = params.get("clearance_mm")
            if clearance_mm is None:
                clearance_mm = engine_mod.DEFAULT_CLEARANCE_MM
            clearance_lbh = (clearance_mm, clearance_mm,
                             engine_mod.DEFAULT_STACK_CLEARANCE_MM)
            result = engine_mod.solve(
                mesh, candidates, part_kg=part.weight_kg, assets=assets,
                top_n=params.get("top_n", 2), clearance_mm=clearance_mm,
            )

            # The exploded insert drawing (G-DRAW), rendered here and only
            # here: the worker already holds the loaded mesh and the numbers
            # this job just solved, so the picture and the counts come from
            # one expression and cannot drift (hard rule 7: heavy geometry
            # stays in the worker, never on request).
            drawing_urls, gif_urls, custom_drawing_url, custom_gif_url = (
                _render_drawings(job_id, mesh, candidates, assets, result,
                                 clearance_lbh)
            )

            # C-TARE: this is now an OVERRIDE, not the tare -- default is each
            # option's own Packaging.tare_kg, resolved per option below.
            override_tare = params.get("tare_kg")
            # Carry the reader's own caveats through. An INCH-declared STEP or
            # a non-watertight shell is exactly the case where a confident
            # parts-per-box number needs a label attached to it.
            warnings += list(extraction.result_json.get("warnings") or [])

            # Dunnage the inner has no room for. In plane, `lattice_count`
            # charges no wall clearance and the side separators stand beside
            # the parts (the shipped Mubea design carries the same -5mm on L).
            # On H the count already pays the insert's dead height, so an
            # overflow there means a BOM the engine did not solve for -- say so
            # rather than leave it to a flag in one panel.
            for label, opt in ([(l.asset_name, l) for l in result.catalogue]
                               + ([("custom", result.custom)]
                                  if result.custom is not None else [])):
                over = [(ax, round(-s, 1)) for ax, s
                        in zip("LBH", (opt.dunnage or {}).get("slack_lbh", []))
                        if s < -1e-6]
                if over:
                    warnings.append(
                        f"{label}: the insert BOM overflows the inner by "
                        + ", ".join(f"{mm}mm on {ax}" for ax, mm in over)
                        + ". In plane the parts fill the inner with no wall "
                        "clearance and the side separators stand beside them "
                        "(the shipped Mubea design has the same tension; check "
                        "how the part ends meet the separators)."
                    )
            if requested:
                # Caller named specific boxes: the "N unverified box(es) not
                # ranked" warning below is about the *other* drafts they never
                # asked for, which would be noise here -- skip it entirely and
                # warn only about what they actually named instead.
                if missing_codes:
                    warnings.append(
                        f"{len(missing_codes)} requested box code(s) not "
                        f"found: {', '.join(sorted(missing_codes))}."
                    )
                named_drafts = sorted(
                    set(a.name for a in assets) & set(excluded_drafts(db))
                )
                if named_drafts:
                    warnings.append(
                        f"{len(named_drafts)} requested box(es) ranked with "
                        f"unconfirmed dims: {', '.join(named_drafts)} "
                        "(status 'draft'); mark them checked to confirm."
                    )
            else:
                # A box added through the UI saves as status="draft" and
                # `containers()` only ranks "checked" rows. Say so, or the
                # engineer who just added the customer's real box sees nothing
                # change and no reason why.
                drafts = excluded_drafts(db)
                if drafts:
                    warnings.append(
                        f"{len(drafts)} unverified box(es) not ranked: "
                        f"{', '.join(sorted(drafts))}. Dimensions are unconfirmed "
                        f"(status 'draft'); mark them checked to include them."
                    )
            truck = None
            vehicle_row = db.scalars(
                select(Vehicle).where(Vehicle.name == params.get("vehicle"))
            ).first()
            if vehicle_row is None:
                raise ValueError(f"Vehicle '{params.get('vehicle')}' not found")
            vehicle_tuple = (vehicle_row.name, vehicle_row.cargo_l_mm,
                             vehicle_row.cargo_b_mm, vehicle_row.cargo_h_mm,
                             vehicle_row.payload_kg)
            # Score EVERY ranked option to the truck, not just
            # catalogue[0]. rank_catalogue sorts on -count and is stable,
            # so a tie on parts-per-box was being broken by catalogue
            # insertion order -- and two boxes tying at 48/box give 2304
            # vs 1728 parts/truck, a 25% swing on the headline number from
            # reordering seed_data. This module's own header says reporting
            # the box number alone re-tools an insert for nothing; picking
            # the truck winner by box order is the same mistake.
            by_name = {c.name: c for c in assets}
            options = [
                (by_name[l.asset_name].outer, l.count, l.asset_name)
                for l in result.catalogue if l.asset_name in by_name
            ]
            if result.custom is not None:
                options.append((result.custom.outer, result.custom.count,
                                "custom"))

            def _tare_for(name: str) -> float | None:
                # C-TARE: the asset's OWN tare wins whenever it has one --
                # a single request-level value cannot rank two assets against
                # each other (that's exactly how PLS12103's real, heavier
                # tare used to get masked). The override only fills in for
                # options with none on file: an unlisted/draft box, or the
                # synthesised custom design, which is never in Packaging.
                known = by_name[name].tare_kg if name in by_name else None
                return known if known is not None else override_tare

            fits = []
            untared: list[str] = []
            for outer, count, name in options:
                t = _tare_for(name)
                if t is None:
                    untared.append(name)
                    continue
                fits.append((engine_mod.parts_per_truck(
                    outer, count, part.weight_kg, t, vehicle_tuple), name))

            if fits:
                truck, truck_winner = max(fits, key=lambda f: f[0].parts)
                truck = dataclasses.replace(truck, asset_name=truck_winner)
                # `options` is [*catalogue, custom], so options[0] is
                # catalogue[0] -- NOT the per-box winner whenever the
                # custom design beats it, which is the exact case the UI
                # badges "beats catalogue". Pick the winner by count.
                box_winner = max(options, key=lambda o: o[1])[2]
                box_fit = next((f[0] for f in fits if f[1] == box_winner), None)
                if box_fit is not None and truck_winner != box_winner:
                    warnings.append(
                        f"Best per box is {box_winner}, but best per truck "
                        f"is {truck_winner} ({truck.parts} parts vs "
                        f"{box_fit.parts}). Box-level gain does not "
                        f"always survive to the truck."
                    )
            if untared:
                # C-TARE: fires only for option(s) genuinely missing BOTH a
                # catalogue tare and an override -- never a blanket "no truck
                # at all" now that most catalogue boxes carry their own.
                warnings.append(
                    f"No tare weight for {', '.join(sorted(untared))}, so "
                    "parts-per-truck is not reported for "
                    f"{'it' if len(untared) == 1 else 'them'}. Pass tare_kg "
                    "to override."
                )

            # CLAUDE.md hard rule 2: nothing reaches the engineer unseen.
            # They confirmed a resting pose in the viewer; the winning layout
            # may be in a different one, and pose_label alone does not tell
            # them that. Surface it; `confirmed_pose_only` above is how they
            # restrict it.
            if (confirmed_label is not None and result.catalogue
                    and confirmed_label != result.catalogue[0].pose_label):
                warnings.append(
                    f"Best layout rests {result.catalogue[0].pose_label!r}, "
                    f"but this part was confirmed resting {confirmed_label!r}. "
                    "Check the recommendation is manufacturable in the pose "
                    "you signed off, or re-solve with confirmed pose only."
                )

            if not result.catalogue and result.custom is None:
                warnings.append(
                    f"No layout found. The part fits none of the {len(assets)} "
                    "loaded containers in any resting pose, or exceeds every "
                    "container's weight limit at "
                    f"{part.weight_kg}kg per part. This is not 'zero parts fit "
                    "a box' -- it means no box was found at all."
                )

            # F4: "why this design" (app/reasons.py). Composed here, last,
            # because this is where clearance_mm, poses_searched (candidates,
            # already pose-locked above), confirmed_label and warnings are ALL
            # in scope together -- everything a reason cites is a field that
            # already exists on the layout/design or was just computed above.
            poses_searched_labels = [c.label for c in candidates]
            assets_by_name = {a.name: a for a in assets}
            result = dataclasses.replace(
                result,
                catalogue=[
                    dataclasses.replace(l, reasons=reasons_for(
                        l, part_lbh=l.extent_lbh, part_kg=part.weight_kg,
                        clearance_mm=clearance_mm,
                        poses_searched=poses_searched_labels,
                        confirmed_label=confirmed_label,
                        inner_lbh=assets_by_name[l.asset_name].inner,
                        max_weight_kg=assets_by_name[l.asset_name].max_weight_kg,
                    )) if l.asset_name in assets_by_name else l
                    for l in result.catalogue
                ],
                custom=(dataclasses.replace(result.custom, reasons=reasons_for(
                    result.custom, part_lbh=result.custom.extent_lbh,
                    part_kg=part.weight_kg, clearance_mm=clearance_mm,
                    poses_searched=poses_searched_labels,
                    confirmed_label=confirmed_label,
                    inner_lbh=result.custom.inner,
                    max_weight_kg=synthesis.DEFAULT_MAX_WEIGHT_KG,
                )) if result.custom is not None else None),
            )

            job.result_json = {
                "catalogue": [
                    {**dataclasses.asdict(l), "interleave": l.interleave,
                     "drawing_url": drawing_urls.get(i),
                     "gif_url": gif_urls.get(i)}
                    for i, l in enumerate(result.catalogue)
                ],
                "poses_searched": [c.label for c in candidates],
                "clearance_mm": clearance_mm,
                "custom": (
                    {**dataclasses.asdict(result.custom),
                     "layers": result.custom.layers,
                     "drawing_url": custom_drawing_url,
                     "gif_url": custom_gif_url}
                    if result.custom is not None else None
                ),
                "custom_beats_catalogue": result.custom_beats_catalogue(),
                "best_count": result.best_count,
                "truck": dataclasses.asdict(truck) if truck is not None else None,
                "warnings": warnings,
            }
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            logger.exception("Solve failed for job %s", job_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        db.commit()


def run_proposal(proposal_id: int) -> None:
    """F7: render one proposal's PDF. Plain function, same reason as
    `run_solve`/`run_extraction` -- the API's dev fallback (no Celery broker)
    runs this in a thread, and tests drive it directly instead of a worker.
    """
    with Session(_engine) as db:
        proposal = db.get(Proposal, proposal_id)
        if proposal is None:
            logger.error("Proposal %s not found", proposal_id)
            return
        proposal.status = "processing"
        db.commit()

        try:
            project = db.get(Project, proposal.project_id)
            run = db.get(SolveJob, proposal.run_id)
            if project is None or run is None:
                raise ValueError(
                    f"Project {proposal.project_id} or run {proposal.run_id} "
                    "not found for this proposal"
                )
            part = db.scalars(
                select(PartProfile)
                .where(PartProfile.project_id == project.id)
                .order_by(PartProfile.created_at.desc())
            ).first()
            if part is None:
                raise ValueError(
                    f"Project {project.id} has no part to build a proposal from"
                )

            run_out = _run_out(run, project)
            out_dir = Path(settings.local_storage_dir)
            # Same landmine `_render_drawings._write` guards against: a
            # missing storage dir on a fresh checkout would otherwise fail
            # this render with nothing on-disk to show for it.
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"proposal_{proposal_id}.pdf"
            build_pdf(project, part, run, run_out, out_path, db=db)

            proposal.pdf_path = str(out_path)
            proposal.status = "done"
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            logger.exception("Proposal render failed for %s", proposal_id)
            proposal.status = "failed"
            proposal.error = f"{type(exc).__name__}: {exc}"
        db.commit()


@celery_app.task(name="render_proposal", time_limit=300)
def render_proposal(proposal_id: int) -> None:
    run_proposal(proposal_id)
