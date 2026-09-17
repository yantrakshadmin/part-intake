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
from dataclasses import dataclass, field, replace
from itertools import permutations

from . import dunnage, synthesis
from .catalogue import containers
from .config import settings
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


def cuboid_layout(part_lbh, inner_lbh, part_kg: float = 0.0,
                  max_weight_kg: float | None = None) -> tuple:
    """Best parts-per-asset treating the part as its own bounding box, plus
    the winning permutation's own extent and grid.

    Best of the 6 axis permutations of `part_lbh` in `inner_lbh` at pitch ==
    extent (touching, no clearance; same convention as
    `dunnage.NO_CLEARANCE_LBH`) -- this IS `tests/ground_truth.
    cuboid_baseline`'s count (CLAUDE.md: "a Mubea stabiliser bar fits 40 ...
    the best cuboid answer over all six orientations is 8" -- the baseline is
    never a fixed orientation), plus the grid T5's landed-cost fallback needs
    to build a real `dunnage.bom` (a count alone is not enough to draw one).

    Reuses `lattice_count` for the weight cap too: a raw `int(max_weight_kg
    // part_kg)` is not an achievable grid (100mm part, 350mm inner, 1kg, a
    25kg cap: that arithmetic says 25, but no product of 3 per-axis integers
    reaches higher than 18, 3x3x2 -- `lattice_count`'s own weight search
    finds the real one). -> (count, extent_lbh, grid).
    """
    best = (0, part_lbh, (0, 0, 0))
    for perm in set(permutations(part_lbh)):
        n, grid, _ = lattice_count(perm, perm, inner_lbh,
                                   part_kg, max_weight_kg or 0.0)
        if n > best[0]:
            best = (n, perm, grid)
    return best


def cuboid_count(part_lbh, inner_lbh, part_kg: float = 0.0,
                 max_weight_kg: float | None = None) -> int:
    """`cuboid_layout`'s count alone -- one expression, so a caller who only
    wants the number can never disagree with the grid `cuboid_layout` hands
    the landed-cost fallback (hard rule 9 spirit: a count and the layout that
    would draw it come from the same source).
    """
    return cuboid_layout(part_lbh, inner_lbh, part_kg, max_weight_kg)[0]


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


# ---------------------------------------------------------------------------
# T5 -- gates and landed cost (PLANNING §10, debate `final_verdict.md` S5).
# ---------------------------------------------------------------------------

# Noise gate: reuses the raster's own quantisation unit, not a new constant --
# a pitch loosened by less than one voxel is not a claim the raster can back.
NOISE_BAND_MM = VOXEL_MM


def noise_gate(extent_lbh, pitch_lbh, inner_lbh, cuboid_count_val: int,
              part_kg: float = 0.0, max_weight_kg: float = 0.0) -> tuple:
    """Recount at a pitch loosened by one raster voxel on every axis.

    PLANNING §10 T5: "recompute the grid at pitch + 4mm and refuse if the
    count falls to or below the cuboid." Reuses `lattice_count` -- the same
    fit already measured, not a re-solve -- so this is arithmetic on numbers
    that already exist. -> (refuse: bool, n_at_band: int).
    """
    widened = tuple(p + NOISE_BAND_MM for p in pitch_lbh)
    n, _, _ = lattice_count(extent_lbh, widened, inner_lbh,
                            part_kg, max_weight_kg)
    return n <= cuboid_count_val, n


# Loading time per part, seconds. ponytail: no per-design loading-time model
# exists (that's a debate open claim, C-adjacent, never ticketed), so this is
# a flat estimate off the debate's own worked example (810 parts ~= 40 min/
# box = 2.96s/part -- transcript §"loading time is a first-class output").
# Constant across candidates, so it never decides a REFUSE by itself; kept so
# the reported landed cost has a labour line at all.
SECONDS_PER_PART_LOAD = 3.0

# Element material rates and lives: order-of-magnitude Indian rates and trip
# lives off the debate's own costed survey (A3 table, `final_verdict.md`
# §1.9/§1.12 provenance), never a supplier quote -- same status as dunnage.py's
# own CAVEAT ("starting design, not a final BOM"). Matched against an
# element's `name`/`spec` text by substring; the first matching family wins.
# Refine when procurement prices a real BOM.
_ROD_RATE_PER_M = {"steel": 40.0}                       # MS rod, any diameter
_SHEET_RATE_PER_M2 = {"pp": 200.0, "hdpe": 1000.0}       # flat panel goods
_FOAM_RATE_PER_KG = {"eva": 300.0, "epe": 300.0, "pu": 400.0}
_FOAM_DEFAULT_DENSITY_KG_M3 = {"eva": 150.0, "epe": 30.0, "pu": 70.0}

# T_material per family, trips. PU is the one PM-ruled number (debate C13:
# compression set inside 15-20 trips); the rest are the debate's own
# order-of-magnitude family lives (§1.2/§1.8 D2a/D5/E3/E4), unmeasured on our
# own catalogue. Unknown material: no better number than the pool life.
_MATERIAL_LIFE_TRIPS = {"pu": 20.0, "eva": 80.0, "epe": 80.0, "pp": 150.0,
                        "hdpe": 500.0, "steel": 1000.0}

# Rigid elements keep their own thickness on the return leg; sheets/inserts
# fold flat with the box. dunnage.Element carries no such flag, so this is a
# name-keyword classification, not a fact off the BOM -- refine if dunnage.py
# ever gains a real one.
_RIGID_ELEMENT_KEYWORDS = ("bar", "rod", "tray")


def _element_text(e: dict) -> str:
    return f"{e.get('name', '')} {e.get('spec') or ''}".lower()


def _element_cost_inr(e: dict) -> float:
    """One BOM element's material cost, Rs, off its own dims/qty/spec.

    Never guesses: an element with an unrecognised material, a missing
    dimension or zero qty prices at 0 rather than inventing a number
    (dunnage.py's own rule for anything it holds no basis for).
    """
    text = _element_text(e)
    labels = list(e.get("labels") or [])
    dims = list(e.get("dims_mm") or [])
    qty = e.get("qty") or 0
    if not qty or not dims or any(d is None for d in dims):
        return 0.0
    for key, rate in _ROD_RATE_PER_M.items():
        if key in text and "L" in labels:
            length_m = dims[labels.index("L")] / 1000.0
            return rate * length_m * qty
    for key, rate in _SHEET_RATE_PER_M2.items():
        if key in text and "L" in labels and "B" in labels:
            area_m2 = (dims[labels.index("L")] / 1000.0
                      * dims[labels.index("B")] / 1000.0)
            return rate * area_m2 * qty
    for key, rate in _FOAM_RATE_PER_KG.items():
        if key in text:
            density = _FOAM_DEFAULT_DENSITY_KG_M3.get(key, 100.0)
            volume_m3 = 1.0
            for d in dims:
                volume_m3 *= d / 1000.0
            return rate * density * volume_m3 * qty
    return 0.0


def _element_life_trips(e: dict) -> float:
    text = _element_text(e)
    for key, trips in _MATERIAL_LIFE_TRIPS.items():
        if key in text:
            return trips
    # Unknown material: no better number than the pool life -- read live off
    # `settings`, not a module-level constant captured at import.
    return settings.T_pool


def _return_height_mm(elements: list) -> float:
    """Sum of the H dimension of every non-collapsible element in the BOM."""
    total = 0.0
    for e in elements:
        if not any(k in _element_text(e) for k in _RIGID_ELEMENT_KEYWORDS):
            continue
        labels = list(e.get("labels") or [])
        dims = list(e.get("dims_mm") or [])
        if "H" in labels and dims[labels.index("H")] is not None:
            total += float(dims[labels.index("H")])
    return total


def _h_fold_mm(fold_type: str, folded_h_mm: float | None,
              outer_h_mm: float) -> float:
    """Height the box itself contributes to a return stack.

    Hard rule 2: never guess a folded height nobody measured. A box that is
    "collapsible" but has no measured `folded_h_mm`, or whose `fold_type` is
    "rigid"/"unknown", keeps its full erected height on the return leg.
    """
    if fold_type == "collapsible" and folded_h_mm:
        return folded_h_mm
    return outer_h_mm


def landed_cost_per_part(count: int, part_kg: float, limited_by: str,
                        max_weight_kg: float, elements: list,
                        outer_h_mm: float, fold_type: str,
                        folded_h_mm: float | None,
                        k_programs: int | None = None) -> float:
    """PLANNING §10 T5's landed-cost formula for one solved layout.

    forward freight/n + return freight/n + per-element material amortised
    over min(material life, pool life) + per-element tooling amortised over
    K programs (0 today -- no BOM element carries a tooling cost until T7's
    die/knife-cut elements land) + labour/n.

    Weight gate (S5): `count` is always the forward-freight denominator, never
    a second, re-derived cap. When this layout's own count is weight-limited
    (`limited_by == "weight"`), `count` is ALREADY the achievable weight-capped
    grid `lattice_count` searched for -- and `cuboid_layout` runs the exact
    same search against the same `max_weight_kg`, so an interleaved layout and
    its cuboid fallback naturally land on the same forward-freight/part
    whenever the cap actually binds both of them (the gain is zero) without
    this function re-deriving anything. `limited_by`/`max_weight_kg` are
    accepted for the caller's own bookkeeping and future gates, not branched
    on here: a raw `int(max_weight_kg // part_kg)` is not an achievable grid
    (see `cuboid_layout`'s docstring) and pricing forward freight off it
    understates the real cost.

    `T_program` (a per-project assumed program life) is NOT modelled: no
    field on `Project`/`PartProfile` carries one yet, and the debate's own
    evidence (§1.12) is that a 120-trip program-life assumption never binds
    once `T_pool` (66) and material life are applied -- so it is treated as
    unbounded until a real field exists to fill it.

    `part_kg` is accepted for interface parity (every other T5 gate takes it)
    but no term below reads it directly any more -- weight-capping already
    happened upstream, in `count`.
    """
    if count <= 0:
        return 0.0
    forward_per_part = settings.f_fwd_per_trip / count

    h_return = _return_height_mm(elements)
    h_fold = _h_fold_mm(fold_type, folded_h_mm, outer_h_mm)
    h_eff = h_fold + h_return
    stack_count = max(1, int(settings.stack_H_max_mm // h_eff)) if h_eff > 0 else 1
    boxes_per_return_trip = max(1, settings.n_footprints * stack_count)
    return_per_part = (settings.f_ret_per_trip / boxes_per_return_trip) / count

    k = k_programs if k_programs and k_programs > 0 else settings.K_programs
    element_per_part = 0.0
    for e in elements:
        c_e = _element_cost_inr(e)
        if c_e <= 0:
            continue
        t_e = min(_element_life_trips(e), settings.T_pool)
        element_per_part += c_e / (t_e * count)
        # Σ C_tool,e/(K·T_e·n): C_tool,e is 0 for every element this BOM
        # emits today (bar_and_rod/pocket_tray/layer_sheets carry no tooling
        # cost field) -- the term is a no-op until T7's die/knife-cut/
        # thermoform elements exist to price.
        _ = k

    labour_per_part = settings.wage_per_hour * SECONDS_PER_PART_LOAD / 3600.0

    return forward_per_part + return_per_part + element_per_part + labour_per_part


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
    # T5: one sentence per fired gate (noise gate drop, cost-fallback
    # refusal), Pack Studio style -- no jargon, no field names. The worker
    # folds these into `warnings`; declared here, not on `Layout`, because a
    # noise-gate-refused candidate is not IN `catalogue` to carry its own.
    gate_notes: list[str] = field(default_factory=list)
    # T5: landed Rs/part for the winning option (`custom` when
    # `custom_beats_catalogue()`, else `catalogue[0]`) -- None when nothing
    # was solved. `landed_cost_per_part_class_a` is the same figure under the
    # "Class A" branch of the missing-input dual-branch (T5/T6 §1.13):
    # identical to `landed_cost_per_part` today because this cost formula has
    # no surface-class-dependent term yet (that arrives with a future damage-
    # cost ticket) -- both are still reported so `ranking_unreliable` is never
    # silently backed by only one number.
    landed_cost_per_part: float | None = None
    landed_cost_per_part_class_a: float | None = None
    # True when mass, surface_class, annual_volume or the winning asset's
    # fold_type was not given -- the landed cost above used a default
    # assumption for at least one of them.
    ranking_unreliable: bool = False

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

    KNOWN LIMITATION, T1: the twin also inherits the ORIGINAL pose's
    `Pose.raster`, so `nesting.lattice_check` answers the mixed lattice
    vectors for a flipped pose on the un-flipped grid -- on the YXA bar the
    twin reports the offset's sign and about 7% of its shared cells wrong
    (same verdict, and both ground-truth counts are unmoved). Fixing it means
    giving a twin its own raster, which is the whole voxelisation this
    function exists to skip; do that, and lift the worker's soft time limit,
    when a twin's verdict actually differs from its original's.
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
          stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM,
          surface_class: str | None = None,
          annual_volume: int | None = None,
          k_programs: int | None = None) -> ResultSet:
    """The PLANNING §6 result set for one part: top-N catalogue + one custom.

    `mesh` and `candidates` come from `geometry.extract_part`. Poses are
    measured once and reused for both halves -- see `nesting.measure_poses`.

    `surface_class` / `annual_volume` are T5/T6 inputs this module did not
    read before: neither changes a count, but a missing one (with a missing
    `part_kg` or the winning asset's `fold_type`) makes the landed cost below
    `ranking_unreliable` (PLANNING §10 T5, debate §1.13). `k_programs`
    overrides `settings.K_programs` (tooling amortisation) per call.
    """
    if assets is None:
        assets = containers()

    # Measured once, used by both halves. Voxelising is the expensive step
    # (~2.4s per pose, ~20s on a 50MB assembly) and neither half needs its own
    # copy -- nor does a flip twin need its own measurement.
    poses = measure_distinct_poses(mesh, candidates, voxel_mm, clearance_mm,
                                   stack_clearance_mm)

    ranked = rank_catalogue(mesh, candidates, assets, part_kg, top_n,
                            poses=poses, surface_class=surface_class)

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

    # T5 noise gate: a candidate whose count does not survive a pitch
    # loosened by one raster voxel is not shipped -- its gain is not provably
    # real (PLANNING §10). Dropped from `ranked` entirely, not flagged in
    # place: `Layout.reasons` is composed later (worker.reasons_for) and only
    # for candidates that ARE returned, so the one sentence a refused
    # candidate gets lives in `gate_notes` instead.
    gate_notes: list[str] = []
    kept = []
    for l in ranked:
        # Nothing to scrutinise when this layout is not even CLAIMING a gain
        # over its own cuboid baseline (a single-part fit is the same box
        # either way, count == cuboid_count) -- the gate exists to catch an
        # overstated nesting gain, not to hide a plain fit.
        if l.asset_name not in inner_by_name or l.count <= l.cuboid_count:
            kept.append(l)
            continue
        refuse, n_band = noise_gate(l.extent_lbh, l.pitch_lbh,
                                    inner_by_name[l.asset_name], l.cuboid_count,
                                    part_kg, max_weight_by_name[l.asset_name])
        if refuse:
            gate_notes.append(
                f"{l.asset_name}: count reduced -- a pitch loosened by "
                f"{NOISE_BAND_MM:g}mm (one raster voxel) drops it to "
                f"{n_band}, at or below the {l.cuboid_count}-part cuboid "
                "fallback, so the nesting gain is not provably real."
            )
        else:
            kept.append(l)
    ranked = kept

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
        refuse, n_band = (False, 0)
        if best_custom.count > best_custom.cuboid_count:
            refuse, n_band = noise_gate(
                best_custom.extent_lbh, best_custom.pitch_lbh,
                best_custom.inner, best_custom.cuboid_count, part_kg,
                synthesis.DEFAULT_MAX_WEIGHT_KG)
        if refuse:
            gate_notes.append(
                f"custom: count reduced -- a pitch loosened by "
                f"{NOISE_BAND_MM:g}mm (one raster voxel) drops it to "
                f"{n_band}, at or below the {best_custom.cuboid_count}-part "
                "cuboid fallback, so the nesting gain is not provably real."
            )
            best_custom = None

    logger.info("solve: catalogue best %s, custom %s",
                ranked[0].count if ranked else 0,
                best_custom.count if best_custom else 0)

    result = ResultSet(catalogue=ranked, custom=best_custom, gate_notes=gate_notes)

    # T5 landed cost, for the winning option only (same one `best_count`/
    # `custom_beats_catalogue` describe -- never a different candidate,
    # CLAUDE.md hard rule 9).
    winner_is_custom = result.custom_beats_catalogue()
    winner = result.custom if winner_is_custom else (
        result.catalogue[0] if result.catalogue else None)
    if winner is not None:
        assets_by_name = {a.name: a for a in assets}
        if winner_is_custom:
            outer_h_mm = winner.outer[2]
            fold_type, folded_h_mm = "unknown", None
            inner_w = winner.inner
            max_weight_w = synthesis.DEFAULT_MAX_WEIGHT_KG
        else:
            asset = assets_by_name.get(winner.asset_name)
            outer_h_mm = asset.outer[2] if asset else winner.extent_lbh[2]
            fold_type = asset.fold_type if asset else "unknown"
            folded_h_mm = asset.folded_h_mm if asset else None
            inner_w = inner_by_name.get(winner.asset_name, winner.extent_lbh)
            max_weight_w = max_weight_by_name.get(winner.asset_name, 0.0)
        elements = (winner.dunnage or {}).get("elements", [])
        limited_by = getattr(winner, "limited_by", "geometry") or "geometry"
        part_kg_eff = part_kg if part_kg else 1.0

        missing = []
        if not part_kg:
            missing.append("mass")
        if surface_class is None:
            missing.append("surface_class")
        if annual_volume is None:
            missing.append("annual_volume")
        if fold_type == "unknown":
            missing.append("fold_type")
        ranking_unreliable = bool(missing)

        cost = landed_cost_per_part(winner.count, part_kg_eff, limited_by,
                                    max_weight_w, elements, outer_h_mm,
                                    fold_type, folded_h_mm, k_programs)
        # See `ResultSet.landed_cost_per_part_class_a`'s docstring: identical
        # to `cost` today (no surface-class-dependent term exists yet) -- so
        # this is the same number, not a second byte-identical call.
        cost_class_a = cost

        cb_count, cb_extent, cb_grid = cuboid_layout(
            winner.extent_lbh, inner_w, part_kg_eff, max_weight_w)
        if cb_count > 0:
            fallback_elements = dunnage.bom(
                cb_extent, cb_extent, cb_grid, inner_w,
                dunnage.NO_CLEARANCE_LBH).as_dict()["elements"]
            fallback_cost = landed_cost_per_part(
                cb_count, part_kg_eff, "geometry", max_weight_w,
                fallback_elements, outer_h_mm, fold_type, folded_h_mm,
                k_programs)
            if fallback_cost < cost:
                delta = round(cost - fallback_cost, 2)
                gate_notes.append(
                    f"Landed cost favours the plain cuboid fallback by "
                    f"Rs {delta:g}/part (Rs {fallback_cost:.2f} vs "
                    f"Rs {cost:.2f}): once freight, the return leg and insert "
                    "life are amortised, the nested design costs more per "
                    "part, not less."
                )

        result = replace(result, gate_notes=gate_notes,
                         landed_cost_per_part=round(cost, 2),
                         landed_cost_per_part_class_a=round(cost_class_a, 2),
                         ranking_unreliable=ranking_unreliable)

    return result
