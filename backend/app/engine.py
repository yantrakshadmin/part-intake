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
from dataclasses import dataclass, replace

from . import dunnage, synthesis
from .catalogue import containers
from .nesting import (DEFAULT_CLEARANCE_MM, DEFAULT_STACK_CLEARANCE_MM,
                      VOXEL_MM, measure_poses, rank_catalogue)

logger = logging.getLogger(__name__)

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


def parts_per_truck(box_outer_lbh, parts_per_box: int, part_kg: float,
                    tare_kg: float, vehicle) -> TruckFit:
    """Boxes per vehicle by floor-fit and stack, capped by payload.

    `vehicle` is a `(name, cargo_l, cargo_b, cargo_h, payload_kg)` tuple as in
    `seed_data.VEHICLES`. Boxes are axis-aligned in one of two floor
    orientations -- they are cuboids, so unlike parts there is no nesting gain
    to find here and no reason to be cleverer.
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
    by_volume = int(per_floor * (vh // oh))

    kg_per_box = parts_per_box * part_kg + tare_kg
    by_weight = int(payload // kg_per_box) if kg_per_box > 0 else by_volume

    boxes = min(by_volume, by_weight)
    # A tie is its own answer, not "volume": a load simultaneously at payload
    # and at cube tells the engineer that buying height buys nothing. The
    # frontend prints this verbatim after "bound".
    if by_weight == by_volume:
        limited_by = "weight and volume"
    else:
        limited_by = "weight" if by_weight < by_volume else "volume"
    return TruckFit(name, boxes, boxes * parts_per_box, kg_per_box, limited_by,
                    floor_grid=(int(floor[0]), int(floor[1])),
                    floor_rotated=floor_rotated, tare_kg=tare_kg)


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
    # (~2.4s per pose) and neither half needs its own copy.
    poses = measure_poses(mesh, candidates, voxel_mm, clearance_mm,
                          stack_clearance_mm)

    ranked = rank_catalogue(mesh, candidates, assets, part_kg, top_n,
                            poses=poses)

    # The insert BOM (PLANNING §7). Stamped here and not in `nesting` because
    # this is where the asset's inner dims and the layout meet, and only the
    # options we actually return need one. Pure arithmetic -- no mesh, no DB.
    inner_by_name = {a.name: a.inner for a in assets}
    ranked = [
        replace(l, dunnage=dunnage.bom(l.extent_lbh, l.pitch_lbh, l.grid,
                                       inner_by_name[l.asset_name]).as_dict())
        if l.asset_name in inner_by_name else l
        for l in ranked
    ]

    # `synthesise` tries both in-plane orders itself, so one call per pose.
    best_custom = None
    for pose in poses:
        design = synthesis.synthesise(pose.extent, pose.pitch, part_kg,
                                      silhouettes=pose.silhouettes)
        if design is not None and (best_custom is None
                                   or design.count > best_custom.count):
            best_custom = replace(design, pose_label=pose.label)
    if best_custom is not None:
        best_custom = replace(best_custom, dunnage=dunnage.bom(
            best_custom.extent_lbh, best_custom.pitch_lbh, best_custom.grid,
            best_custom.inner).as_dict())

    logger.info("solve: catalogue best %s, custom %s",
                ranked[0].count if ranked else 0,
                best_custom.count if best_custom else 0)
    return ResultSet(catalogue=ranked, custom=best_custom)
