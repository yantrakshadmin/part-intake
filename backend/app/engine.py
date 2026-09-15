"""Phase 2 assembly: the result set the engineer actually compares.

PLANNING §6 — every run returns the **top 2 catalogue solutions plus one custom
design**, side by side, because the engineer is making that comparison anyway.
This module is only assembly; the parts it calls do the work:

    geometry.py   resting poses from CAD
    nesting.py    lattice pitch + count  (one counting formula, lives there)
    catalogue.py  the stocked containers
    synthesis.py  solve for a custom box over the §6 bounded design space

It also carries the box -> truck check, and that is not decoration. Worked
example, Mubea, measured 2026-09-09, updated 2026-09-10 (C-TARE) once each
box carried its own tare instead of a flat assumed 30kg for both:

    PLS12801 (what shipped)   40/box, 30kg tare  ->  39 boxes/32ft  =  1560 parts
    PLS12103 (best on count)  65/box, 39kg tare  ->  24 boxes/32ft  =  1560 parts

A **+62% gain per box is +0% per truck**: PLS12103 is both bulkier and
heavier empty, at 5kg a part both boxes run out of payload before they run
out of space, and its real tare eats back exactly the margin its extra count
won. (At the old flat 30kg guess for both this looked like a +4% truck win --
still real evidence to be skeptical of a box number in isolation, just not
the actual number.) Reporting the box number alone would tell the team to
re-tool an insert for nothing. PLANNING §7 calls arithmetic consistency a
feature; this is that.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from itertools import permutations

from . import dunnage, synthesis
from .catalogue import containers
from .nesting import (DEFAULT_CLEARANCE_MM, DEFAULT_STACK_CLEARANCE_MM,
                      VOXEL_MM, lattice_count, measure_poses, rank_catalogue)

logger = logging.getLogger(__name__)

# What `nesting.measure_poses` bakes into a pitch, in asset L/B/H order. The
# dunnage archetype has to subtract it to see whether the PARTS nest (see
# `dunnage.archetype_of`), so every `dunnage.bom` call on an engine-measured
# layout passes it -- `solve` from its own parameters, `worker._render_drawings`
# from here, because it recomputes the same BOM for the drawing.
DEFAULT_CLEARANCE_LBH = (DEFAULT_CLEARANCE_MM, DEFAULT_CLEARANCE_MM,
                         DEFAULT_STACK_CLEARANCE_MM)

# ponytail: box -> truck directly, skipping the pallet tier. PLANNING §4 wants
# part -> box -> pallet -> truck and 30 of 49 assets carry a pallet spec, but
# only 15 assets are loaded and none of them has one. Add the pallet tier when
# the catalogue does.
#
# Tare is the sharp edge here: at tare=0 the answer above INVERTS (PLS12801
# ships 1800, PLS12103 1755), so tare is required, not optional, and callers
# must pass a real one. `Packaging.tare_kg` (C-TARE) is per asset, nullable,
# no zero-for-unknown; `parts_per_truck` still just takes a plain float --
# resolving "this asset's own tare, else the caller's override, else this
# option is not scored" is worker.run_solve's job, not this module's.


def cuboid_count(part_lbh, inner_lbh, part_kg: float = 0.0,
                 max_weight_kg: float | None = None) -> int:
    """Best parts-per-asset treating the part as its own bounding box.

    Best of the 6 axis permutations of `part_lbh` in `inner_lbh` -- this IS
    `tests/ground_truth.cuboid_baseline`'s count, moved here so a solve can
    stamp it on the result it's shipping (CLAUDE.md: "a Mubea stabiliser bar
    fits 40 ... the best cuboid answer over all six orientations is 8" --
    the baseline is never a fixed orientation). Capped by the weight limit
    only when both `part_kg` and `max_weight_kg` are given.
    """
    best = 0
    for perm in set(permutations(part_lbh)):
        n = 1
        for p, cap in zip(perm, inner_lbh):
            n *= int(cap // p)
        best = max(best, n)
    if part_kg and max_weight_kg:
        best = min(best, int(max_weight_kg // part_kg))
    return best


def gain_ratio(count: int | None, baseline: int | None) -> float | None:
    """`count / baseline`, rounded 2dp; None when there is no honest baseline."""
    if not count or not baseline:
        return None
    return round(count / baseline, 2)


def trips_per_year(annual_volume: int | None,
                   parts_per_truck: int | None) -> int | None:
    """Truck trips to move `annual_volume` parts; None when either is missing."""
    if not annual_volume or not parts_per_truck:
        return None
    return math.ceil(annual_volume / parts_per_truck)


@dataclass(frozen=True)
class TruckFit:
    vehicle: str
    boxes: int
    parts: int
    kg_per_box: float
    limited_by: str          # "weight" or "volume"
    # Which asset these numbers are for. `parts_per_truck` takes bare outer
    # dims and cannot know, so the caller stamps it: worker.run_solve scores
    # every ranked option and keeps the best, which is NOT always
    # catalogue[0]. Without this the truck block is a correct number under an
    # incorrect label -- and the load drawing gets built from the wrong outer.
    asset_name: str = ""
    # The winning floor arrangement: boxes along cargo length x along cargo
    # breadth, and whether that orientation lays the box outer L across the
    # trailer. The load drawing NEEDS this. Without it the frontend re-derives
    # a floor fit with its own algorithm (`bestFill`, which allows mixed
    # orientation split strips) and draws a pattern and stack height the
    # parts/truck figure was never computed from -- 21/floor drawn against 18
    # assumed. One fit, computed once, shipped.
    floor_grid: tuple[int, int] = (0, 0)
    floor_rotated: bool = False
    # The tare actually used for this option (CLAUDE.md hard rule 9): now
    # per-asset (C-TARE), the frontend must not re-derive it by subtracting
    # part_kg * parts_per_box back out of kg_per_box.
    tare_kg: float = 0.0
    # The two bounds `boxes` is the minimum of, and the stack height used.
    # Shipped so the load calculator shows them instead of re-deriving them
    # with its own floor-fit (the frontend's `loadPlan` was a second truck
    # calculator that disagreed with this one on the floor pattern).
    by_volume: int = 0
    by_weight: int = 0
    stack: int = 0


def parts_per_truck(box_outer_lbh, parts_per_box: int, part_kg: float,
                    tare_kg: float, vehicle, max_stack: int = 0) -> TruckFit:
    """Boxes per vehicle by floor-fit and stack, capped by payload.

    `vehicle` is a `(name, cargo_l, cargo_b, cargo_h, payload_kg)` tuple as in
    `seed_data.VEHICLES`. Boxes are axis-aligned in one of two floor
    orientations -- they are cuboids, so unlike parts there is no nesting gain
    to find here and no reason to be cleverer. `max_stack` > 0 caps the
    boxes-high (a crush or handling limit); 0 stacks to the cargo height.
    """
    name, vl, vb, vh, payload = vehicle
    ol, ob, oh = box_outer_lbh
    if min(ol, ob) <= 0 or oh <= 0:
        raise ValueError(f"degenerate box outer dims: {box_outer_lbh}")

    straight = ((vl // ol), (vb // ob))
    rotated = ((vl // ob), (vb // ol))
    floor_rotated = rotated[0] * rotated[1] > straight[0] * straight[1]
    floor = rotated if floor_rotated else straight
    per_floor = floor[0] * floor[1]
    stack = int(vh // oh)
    if max_stack > 0:
        stack = min(stack, max_stack)
    by_volume = int(per_floor * stack)

    kg_per_box = parts_per_box * part_kg + tare_kg
    by_weight = int(payload // kg_per_box) if kg_per_box > 0 else by_volume

    boxes = min(by_volume, by_weight)
    # A tie is its own answer, not "volume": a load simultaneously at payload
    # and at cube tells the engineer that buying height buys nothing. The
    # frontend prints this verbatim after "bound". No weight given is NOT a
    # tie -- `by_weight` above is only a stand-in there -- so it reads
    # "volume", or the load calculator prints "volume and payload balanced"
    # next to "no box weight given".
    if kg_per_box <= 0:
        limited_by = "volume"
    elif by_weight == by_volume:
        limited_by = "weight and volume"
    else:
        limited_by = "weight" if by_weight < by_volume else "volume"
    return TruckFit(name, boxes, boxes * parts_per_box, kg_per_box, limited_by,
                    floor_grid=(int(floor[0]), int(floor[1])),
                    floor_rotated=floor_rotated, tare_kg=tare_kg,
                    by_volume=by_volume, by_weight=by_weight, stack=stack)


@dataclass(frozen=True)
class ResultSet:
    """What one run returns. `catalogue` is best-first; `custom` may be None."""

    catalogue: list          # list[Layout], length <= top_n
    custom: object           # synthesis.BoxDesign | None

    @property
    def best_count(self) -> int:
        counts = [l.count for l in self.catalogue]
        if self.custom is not None:
            counts.append(self.custom.count)
        return max(counts, default=0)

    def custom_beats_catalogue(self) -> bool:
        """True when the custom design is worth the tooling conversation."""
        if self.custom is None:
            return False
        if not self.catalogue:
            # Nothing in stock fits at all, so the custom box is not merely
            # better -- it is the only answer. Reporting False would hide it.
            return True
        return self.custom.count > self.catalogue[0].count


def measure_distinct_poses(mesh, candidates,
                           voxel_mm: float = VOXEL_MM,
                           clearance_mm: float = DEFAULT_CLEARANCE_MM,
                           stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM
                           ) -> list:
    """One `nesting.Pose` per candidate, but voxelise once per distinct box.

    R6 put flip twins back in the candidate list: the same box resting on its
    other face, kept because the stability differs and the UI must show both.
    They nest identically -- same extent, same pitch, same count on both
    ground-truth fixtures (40/40, 48/48) -- and voxelising is the entire cost
    of a solve (~20s per pose on the 50MB inverter), so measuring a twin again
    buys nothing and pushes a big part past the worker's soft time limit.

    ponytail: exact reuse, not a tolerance on the measurement. Twins differ by
    up to one voxel on the raster (`nesting.occupancy` snaps the grid origin
    per transform) -- that band is what `Layout.count_upper` already reports.
    Upgrade path if a twin ever needs its own raster: drop this and lift the
    worker's time limit.
    """
    from .geometry import dims_match
    poses, measured = [], []       # measured: [(dims_lbh, Pose)]
    for cand in candidates:
        twin = next((pose for dims, pose in measured
                     if dims_match(dims, cand.dims_lbh)), None)
        if twin is None:
            pose = measure_poses(mesh, [cand], voxel_mm, clearance_mm,
                                 stack_clearance_mm)[0]
            measured.append((tuple(cand.dims_lbh), pose))
        else:
            pose = replace(twin, label=cand.label)
        poses.append(pose)
    logger.info("measured %d distinct footprints for %d candidates",
                len(measured), len(candidates))
    return poses


def solve(mesh, candidates, part_kg: float = 0.0, assets=None, top_n: int = 2,
          voxel_mm: float = VOXEL_MM,
          clearance_mm: float = DEFAULT_CLEARANCE_MM,
          stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM) -> ResultSet:
    """The PLANNING §6 result set for one part: top-N catalogue + one custom.

    `mesh` and `candidates` come from `geometry.extract_part`. Poses are
    measured once and reused for both halves -- see `nesting.measure_poses`.
    """
    if assets is None:
        assets = containers()

    # Measured once, used by both halves. Voxelising is the expensive step
    # (~2.4s per pose, ~20s on a 50MB assembly) and neither half needs its own
    # copy -- nor does a flip twin need its own measurement.
    poses = measure_distinct_poses(mesh, candidates, voxel_mm, clearance_mm,
                                   stack_clearance_mm)

    ranked = rank_catalogue(mesh, candidates, assets, part_kg, top_n,
                            poses=poses)

    # The insert BOM (PLANNING §7). Stamped here and not in `nesting` because
    # this is where the asset's inner dims and the layout meet, and only the
    # options we actually return need one. Pure arithmetic -- no mesh, no DB.
    inner_by_name = {a.name: a.inner for a in assets}
    max_weight_by_name = {a.name: getattr(a, "max_weight_kg", 0.0) for a in assets}
    clearance_lbh = (clearance_mm, clearance_mm, stack_clearance_mm)
    # F3 cuboid baseline: the part's own L/B/H, taken as the first (or the
    # sole, confirmed) pose's measured extent. Every resting pose's extent is
    # the same 3 physical dimensions in a different order, and `cuboid_count`
    # already tries all 6 permutations, so any pose gives the same answer --
    # this is the confirmed pose alone whenever the caller restricted
    # `candidates` to it (worker.run_solve's confirmed_pose_only).
    part_lbh = poses[0].extent if poses else (0.0, 0.0, 0.0)
    ranked = [
        replace(l, dunnage=dunnage.bom(l.extent_lbh, l.pitch_lbh, l.grid,
                                       inner_by_name[l.asset_name],
                                       clearance_lbh).as_dict(),
               cuboid_count=cuboid_count(part_lbh, inner_by_name[l.asset_name],
                                         part_kg, max_weight_by_name[l.asset_name]))
        if l.asset_name in inner_by_name else l
        for l in ranked
    ]

    # `synthesise` tries both in-plane orders itself, so one call per pose.
    best_custom = None
    for pose in poses:
        design = synthesis.synthesise(pose.extent, pose.pitch, part_kg,
                                      silhouettes=pose.silhouettes,
                                      clearance_lbh=clearance_lbh)
        if design is not None and (best_custom is None
                                   or design.count > best_custom.count):
            upper = design.count
            if pose.voxel_mm > 0:
                v = pose.voxel_mm
                dead = dunnage.dead_height_mm(design.extent_lbh, design.pitch_lbh,
                                              clearance_lbh, grid=design.grid)
                step = dunnage.layer_step_mm(design.extent_lbh, design.pitch_lbh,
                                             clearance_lbh, grid=design.grid)
                usable = (design.inner[0], design.inner[1], design.inner[2] - dead)
                upper = max(upper, lattice_count(
                    tuple(e - v for e in design.extent_lbh),
                    (design.pitch_lbh[0] - v, design.pitch_lbh[1] - v, step - v),
                    usable)[0])
            best_custom = replace(design, pose_label=pose.label, count_upper=upper)
    if best_custom is not None:
        # Baseline against this box's OWN inner -- the synthesised box is not
        # any catalogue asset, so `max_weight_by_name` has nothing for it;
        # `synthesis.DEFAULT_MAX_WEIGHT_KG` is the same PLS-family cap
        # `synthesise` itself solves the design's height against.
        best_custom = replace(best_custom, dunnage=dunnage.bom(
            best_custom.extent_lbh, best_custom.pitch_lbh, best_custom.grid,
            best_custom.inner, clearance_lbh).as_dict(),
            cuboid_count=cuboid_count(part_lbh, best_custom.inner, part_kg,
                                      synthesis.DEFAULT_MAX_WEIGHT_KG))

    logger.info("solve: catalogue best %s, custom %s",
                ranked[0].count if ranked else 0,
                best_custom.count if best_custom else 0)
    return ResultSet(catalogue=ranked, custom=best_custom)
