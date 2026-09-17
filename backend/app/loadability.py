"""T3 -- can the part actually be PUT INTO the lattice the engine just counted?

`nesting.lattice_check` proves the parts do not interpenetrate once they are
all in place. That is a statement about the assembled box, and the debate
(PLANNING §10, T3) found the failure happens earlier, on the dock: a lattice
can be collision-free and still have no way in. Loadability is its own gate.

    pitch_sil(raster, order)   closest spacing of the PLAN SILHOUETTES
    delta_proj(p_sil, pitch)   p_sil - p_vol, per floor axis
    load_path(...)             straight | oblique | tilt | none

The discriminator is one number. `p_vol` is measured from the 3-D grid, so it
can be tighter than `p_sil` only by using the third dimension: the parts
overhang each other in plan and the count exists because their z-profiles
differ across the overlap. So

    delta_proj == 0  ->  the shadows are disjoint, so no neighbour with a
                         non-zero IN-PLANE offset can be touched by a vertical
                         descent, whatever the z-profiles do, and an insert
                         element may be installed BEFORE loading.
    delta_proj  > 0  ->  the shadows overlap; descent may still be clear, but
                         it is no longer free, so all 40 directions and all
                         four start corners are searched.

Fable's R5 §1.1 amendment, which is the version that shipped: delta_proj is a
PRUNE, not a verdict. It shrinks the straight test to one neighbour; it never
skips it. The projection argument says nothing at all about the part DIRECTLY
BELOW -- `min_pitch` proved the multiples of the stacking pitch clear, never
`pitch + one voxel`, which is exactly where an undercut fouls -- so that one
is tested on every layout, delta_proj zero or not.

MEASURED, 2026-09-17, so the numbers are here and not in anyone's head. The
ticket expects delta_proj == 0 on both floor axes for the Mubea/YXA bar and it
is NOT: the bar's plan shadows overlap 12mm on B at the 4mm raster, 14mm at
2mm and 16mm at 1mm. It GROWS as the raster refines, so it is the geometry,
not the voxel rounding outward. The bent bars really do overhang in plan and
really do only fit because their z-profiles differ. Its load path is still
`straight` -- but only from one end of the row (see `placed_neighbours`).

WHICH NEIGHBOURS. A part is lowered into a part-built box, not into a finished
one: the layer below is complete, the previous B rows are complete, and in its
own row everything up to i-1 is in. Checking BOTH in-plane sides at once
refuses every interleaved design there is -- including the two that shipped --
because you cannot insert anything into a closed pocket. That row-major
already-placed set is Fable's dock argument made computable.

ponytail: reuses `Raster.cells(order, off)` from `nesting`, so the sweep is
dictionary lookups over the overlap counts the solve already paid for. Nothing
here re-voxelises the mesh (the tilt rung rotates the EXISTING grid).
"""

from __future__ import annotations

import logging
import math

import numpy as np

from .nesting import (DIAG_FATAL_RATIO, FINE_MM, _shift_voxels, _slices,
                      _to_pose_axes, min_pitch)

logger = logging.getLogger(__name__)

# Directions on the upper hemisphere, straight up included. 40 is the ticket's
# number; the spiral below is deterministic, so a verdict is reproducible.
SWEEP_DIRECTIONS = 40

# Hard stop on the march along one direction. A direction that has neither hit
# something nor escaped every neighbour's bounding box by here is reported as
# blocked -- conservative, and only reachable for a near-horizontal direction
# on a part longer than this many voxels.
MAX_STEPS = 600

# Tilt is the last rung and the debate put its ceiling at about 6 degrees on
# the top third of a stack for want of headroom; searching past 20 is spending
# scipy rotations on angles no one loads at.
MAX_TILT_DEG = 20.0
TILT_STEPS = 3


def _memo(raster) -> dict:
    """Scratch dict on the `Raster`. Same lifetime as the pose.

    A plain attribute rather than a method on `nesting.Raster`: the pitch and
    the sweep are properties of the POSE, so the ~15 assets `rank_catalogue`
    scores share one answer, and T3 was told to add nothing to `Raster`.
    """
    return raster.__dict__.setdefault("_loadability", {})


def pitch_sil(raster, order, clearance_lbh=(0.0, 0.0, 0.0)) -> tuple:
    """Closest collision-free spacing of the PLAN SILHOUETTE. -> (p_L, p_B) mm

    `grid.any(axis=2)` is the part's shadow. Two copies that far apart cast
    disjoint shadows, so either can be lowered straight past the other however
    their z-profiles run. Same slide, same all-multiples rule and the same
    clearance `min_pitch` used for the volume pitch -- so the clearance cancels
    in `delta_proj` and what is left is pure geometry.
    """
    key = ("sil", tuple(order), tuple(round(c, 3) for c in clearance_lbh[:2]))
    m = _memo(raster)
    if key not in m:
        mask = raster.coarse(order).any(axis=2)
        m[key] = tuple(min_pitch(mask, ax, raster.voxel_mm, clearance_lbh[ax])
                       for ax in (0, 1))
    return m[key]


def delta_proj(p_sil, pitch_lbh, bounds=(2, 2)) -> tuple:
    """`p_sil - p_vol` per floor axis, floored at zero. -> (d_L, d_B) mm

    Floored because a REPAIRED lattice (`nesting.lattice_check`) ships a pitch
    WIDER than the one the part was measured at; a negative difference there
    means the shadows separated with room to spare, which is the same answer
    as zero and must not read as "more than clear".

    Zero on an axis the layout puts ONE part on: there is no neighbour there
    to overhang, so a positive number would buy a sweep for a pair that does
    not exist. The YXA bar is 1 x 4 x 10 and its L is exactly that axis.
    """
    return tuple(round(max(0.0, s - v), 2) if b > 1 else 0.0
                 for s, v, b in zip(p_sil, pitch_lbh, bounds))


def directions(n: int = SWEEP_DIRECTIONS) -> list:
    """`n` unit vectors on the upper hemisphere, STRAIGHT UP FIRST.

    Golden-angle spiral in z, so the set is spread rather than clustered at a
    pole and does not depend on any part. z never reaches 0: a purely
    horizontal path is not a load path, it is a second part already in the way.
    """
    ga = math.pi * (3.0 - math.sqrt(5.0))
    out = [(0.0, 0.0, 1.0)]
    for i in range(n - 1):
        z = (i + 0.5) / (n - 1)
        r = math.sqrt(max(0.0, 1.0 - z * z))
        out.append((r * math.cos(ga * i), r * math.sin(ga * i), z))
    return out


def limits(shifts, bounds, shape=None) -> tuple:
    """How far the enumeration below reaches on each axis, in lattice steps.

    `bounds` caps it (a 3-wide lattice has no 4th neighbour) and the part's own
    bounding box caps it again (nothing further apart than the part is wide can
    be reached from the seat by any path).

    Public and separate because `load_path`'s memo key has to be keyed on THIS
    and not on `bounds`: two assets with the same inner height and a 2-wide and
    a 6-wide lattice produce different neighbour sets and must not share a
    cached verdict. Keying on `min(b, 2)` let a 6-wide lattice read a 2-wide
    one's answer -- measured flip, oblique reported as straight.
    """
    out = []
    for ax in range(3):
        n = max(int(bounds[ax]) - 1, 0)
        if shape is not None and shifts[ax]:
            n = min(n, (shape[ax] - 1) // shifts[ax])
        out.append(n)
    return tuple(out)


def placed_neighbours(shifts, bounds, signs=(1, 1), shape=None) -> list:
    """Lattice offsets in VOXELS of the parts already in the box. -> [(x,y,z)]

    Row-major loading: every layer below is COMPLETE, the previous B rows are
    complete, and in its own row everything up to i-1 is in. One-sided in
    plane on purpose -- see the module note; checking both sides at once
    refuses every interleaved design there is, the two shipped ones included.

    Not just the six touching neighbours: a part sliding out at a shallow
    angle passes over the whole layer beneath it, so the enumeration runs the
    lattice out to `bounds` on every axis and lets `shape` (the part's own
    bounding box, in voxels) cut it off. Anything further apart than the part
    is wide cannot be reached from the seat by any path.

    `signs` is WHICH END the packer starts from, (+1/-1 along L, along B). It
    is a free design variable and it is not symmetric: on the YXA bar, lifting
    a bar away from the neighbour on its -B side fouls from the first voxel
    (2, 5, 11, 19 ... shared cells) and lifting it away from the +B one is
    clear all the way out. Same pair of parts, opposite answer -- the overlap
    count is symmetric in the WHOLE offset vector, not in one component of it.
    Fixing the order at (+1, +1) refuses the deck-proven 40.
    """
    lim = limits(shifts, bounds, shape)
    out = []
    for k in range(-lim[2], 1):
        for j in range(-lim[1], lim[1] + 1):
            for i in range(-lim[0], lim[0] + 1):
                if (i, j, k) == (0, 0, 0):
                    continue
                if k == 0 and not (j * signs[1] < 0
                                   or (j == 0 and i * signs[0] < 0)):
                    continue          # not placed yet when this one goes in
                out.append((i * shifts[0], j * shifts[1], k * shifts[2]))
    return out


def fine_blocks(raster, order, off, cells4: int, fine_seat: int) -> bool:
    """Is one contact on a load path a REAL collision, or 4mm rounding?

    `nesting.lattice_check`'s rule, unchanged and with its own constant
    imported rather than copied: re-voxelise the overlap at `FINE_MM` and call
    it a crash when the shared-cell count scales like an interpenetration
    (`ratio >= DIAG_FATAL_RATIO`), or when the fine contact is worse than the
    fine contact the part already has at rest.

    The second half is the strict one and usually the one that fires: a
    neighbour the part does not touch at rest has `fine_seat == 0`, so any
    surviving 2mm cell blocks. Only a contact that is LITERALLY ZERO at 2mm is
    forgiven -- which is what a 4mm surface raster rounding outward looks like.

    Measured on ZB 3000 M2, the file this rung exists for (PLS12101,
    'Standing upright' turned, 32573 cells): the oblique path's contacts are
    2-7 cells at 4mm and 0 at 2mm, while the straight descent reaches
    cells4=45 -> cells2=111, ratio 2.47. One is rounding, the other is the
    part jamming into its neighbour's flank. Pass 1 could not tell them apart.
    """
    cells2 = raster.fine_cells(_to_pose_axes(off, order))
    if cells2 > fine_seat:
        return True
    return cells4 > 0 and cells2 / cells4 >= DIAG_FATAL_RATIO


def _clear_along(raster, order, d, neigh, seat, shape, fine_seat=None) -> bool:
    """Can the part travel from its seat along unit vector `d` and get out?

    One voxel per step on the dominant axis, and it stops the moment the part
    is clear of every neighbour's bounding box -- past that point nothing can
    come back, because `d` only ever moves it further away in z.

    A step is blocked when it makes the contact WORSE than it already is at
    rest (`seat`). The resting contact has already been judged, by
    `nesting.lattice_check` at `DIAG_FATAL_RATIO`, and on the YXA bar it is
    the deck-proven 1.04 tangency: 28 shared cells against the (0,-1,-1)
    neighbour that fade to 19, 8, 1 as the bar lifts. Counting those as a
    blocked path refuses the one design CLAUDE.md says is right.

    PASS 2 (`fine_seat` given, per-neighbour fine contact at rest): the 4mm
    contact is only a candidate, and `fine_blocks` gets the last word. Costly
    -- every candidate re-voxelises its overlap box -- so `load_path` pays it
    only for a layout pass 1 was about to throw away.

    Overlap comes from `Raster.cells`, memoised per (order, offset), so the
    same offset costs one dict lookup no matter how many directions, load
    orders, assets or lattice checks ask for it.
    """
    for t in range(1, MAX_STEPS + 1):
        o = (int(round(t * d[0])), int(round(t * d[1])), int(round(t * d[2])))
        gone = True
        for n in neigh:
            off = (o[0] - n[0], o[1] - n[1], o[2] - n[2])
            if _slices(shape, off) is None:
                continue              # translated clear of that neighbour
            gone = False
            cells4 = raster.cells(order, off)
            if cells4 <= seat[n]:
                continue
            if fine_seat is None:
                return False                          # pass 1, strict
            if fine_blocks(raster, order, off, cells4, fine_seat[n]):
                return False                          # pass 2, T1's rule
        if gone:
            return True
    return False


def _seat(raster, order, neigh, shape) -> dict:
    """Cells each placed neighbour already shares with the part at rest."""
    out = {}
    for n in neigh:
        off = (-n[0], -n[1], -n[2])
        out[n] = 0 if _slices(shape, off) is None else raster.cells(order, off)
    return out


def _seat_fine(raster, order, neigh, shape) -> dict:
    """`_seat` at `FINE_MM`. Pass 2 only -- it re-voxelises once per contact."""
    out = {}
    for n in neigh:
        off = (-n[0], -n[1], -n[2])
        out[n] = (0 if _slices(shape, off) is None
                  or not raster.cells(order, off)
                  else raster.fine_cells(_to_pose_axes(off, order)))
    return out


def _load_orders(shifts, bounds, shape=None) -> list:
    """The distinct already-placed sets over the four start corners.

    -> [(signs, neigh)]. Deduped: an axis with one part contributes no
    neighbour, so its sign changes nothing and is not searched twice.
    """
    seen, out = set(), []
    for signs in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        neigh = tuple(placed_neighbours(shifts, bounds, signs, shape))
        if neigh in seen:
            continue
        seen.add(neigh)
        out.append((signs, list(neigh)))
    return out


def sweep(raster, order, shifts, bounds, column_only: bool = False,
          tolerant: bool = False) -> tuple:
    """Translation sweep over directions x load orders. -> (kind, u, signs, n)

    `("straight", (0,0,1), signs, k)` when a vertical descent clears for some
    start corner, `("oblique", u, signs, k)` for any other direction, and
    `("none", None, (1,1), N)` when nothing clears. `k`/`N` count PROBES --
    direction x start corner -- not directions. Straight up is tried for
    EVERY load order before any oblique direction is tried for any of them:
    a design that can be laid down from one end is not an oblique design.

    `column_only` is `load_path`'s prune: with disjoint plan shadows the only
    neighbour a vertical lift can reach is the part directly below, so there
    is one neighbour and one direction to test instead of 40 x 4.

    `tolerant` is pass 2: a 4mm contact only blocks if `fine_blocks` agrees it
    is a real collision. Same walk, same order, same verdicts -- the only
    difference is who judges a contact.
    """
    shape = raster.coarse(order).shape
    full = _load_orders(shifts, bounds, shape)
    if not any(neigh for _, neigh in full):
        return "straight", (0.0, 0.0, 1.0), (1, 1), 1   # nothing to clear
    dirs = directions()

    def seats(pairs):
        for signs, neigh in pairs:
            yield (signs, neigh, _seat(raster, order, neigh, shape),
                   _seat_fine(raster, order, neigh, shape) if tolerant else None)

    # Phase 1, straight down, every start corner. The prune shrinks this to
    # the one neighbour a vertical lift can still reach; it never skips it.
    straight = ([((1, 1), [(0, 0, -shifts[2])])] if bounds[2] > 1 else [])  \
        if column_only else full
    tested = 0
    for signs, neigh, seat, fine in seats(straight):
        tested += 1
        if _clear_along(raster, order, dirs[0], neigh, seat, shape, fine):
            return "straight", dirs[0], signs, tested
    if column_only and not straight:
        return "straight", dirs[0], (1, 1), 1        # no neighbour at all

    # Phase 2, oblique. The prune does NOT survive here: disjoint shadows say
    # nothing about a path that moves sideways as it rises, so every placed
    # neighbour is back in.
    everything = list(seats(full))
    for d in dirs[1:]:
        for signs, neigh, seat, fine in everything:
            tested += 1
            if _clear_along(raster, order, d, neigh, seat, shape, fine):
                return "oblique", d, signs, tested
    return "none", None, (1, 1), tested


def max_tilt_deg(w_mm: float, h_mm: float, headroom_mm: float) -> float:
    """Biggest tilt the remaining head height allows, degrees.

    Tilting a `w x h` section by theta needs `w.sin(t) + h.cos(t)` of height.
    Zero when the stack already fills the box, which is the usual answer and
    the reason this rung is gated rather than searched.
    """
    if headroom_mm <= 0 or w_mm <= 0:
        return 0.0
    for deg in range(int(MAX_TILT_DEG), 0, -1):
        t = math.radians(deg)
        if w_mm * math.sin(t) + h_mm * math.cos(t) <= h_mm + headroom_mm:
            return float(deg)
    return 0.0


def tilted_copy(grid: np.ndarray, phi_deg: float, axis: int) -> np.ndarray:
    """The occupancy grid rotated `phi_deg` about a floor axis, then DILATED.

    Nearest-neighbour rotation drops cells out of a one-voxel-thick shell --
    measured on the real files, the YXA bar goes 16503 -> 16409 cells at 5deg
    and the TRW wheel loses 2.4% at 10deg. Those holes are punctures a lift
    can slide straight through, which would let the tilt rung INVENT a path
    that is not there; one binary dilation closes them and keeps the rung on
    the conservative side of its own docstring. It costs a voxel of slop, and
    slop here can only ever refuse.
    """
    from scipy.ndimage import binary_dilation, rotate as nd_rotate

    rot = np.asarray(nd_rotate(grid, phi_deg, axes=(1 - axis, 2), order=0,
                               reshape=True, prefilter=False), dtype=bool)
    return binary_dilation(rot)


def _overlap2(a: np.ndarray, b: np.ndarray, off) -> int:
    """Cells shared by grid `a` and grid `b` translated by `off` voxels.

    `nesting._overlap_cells` slides a grid against ITSELF; the tilt rung has a
    rotated copy on one side, so the two shapes differ.
    """
    sa, sb = [], []
    for na, nb, d in zip(a.shape, b.shape, off):
        lo, hi = max(0, d), min(na, nb + d)
        if hi <= lo:
            return 0
        sa.append(slice(lo, hi))
        sb.append(slice(lo - d, hi - d))
    return int(np.count_nonzero(a[tuple(sa)] & b[tuple(sb)]))


def tilt(raster, order, shifts, bounds, headroom_mm: float) -> tuple:
    """Headroom-gated tilt rung. -> (ok, phi_deg, axis, signs)

    Rotates the EXISTING occupancy grid (scipy nearest-neighbour, no
    re-voxelisation of the mesh) about each floor axis and asks whether the
    tilted part can be lifted straight out of the already-placed neighbours,
    over the same four start corners the translation sweep used.

    The rotated copy is dilated (`tilted_copy`) so a thinned shell cannot be
    lifted through solid geometry.

    ponytail: rotate in place, then lift, and no seat tolerance -- a tilted
    part has no resting contact to be tolerant of. The real manoeuvre is
    rotate WHILE translating, which is a motion planner; this is its
    conservative shadow, so it can refuse a path that exists but never invent
    one. Upgrade to a swept rotation if a real part is ever refused here alone.
    """
    grid = raster.coarse(order)
    orders = [(sg, n) for sg, n in _load_orders(shifts, bounds, grid.shape) if n]
    if not orders:
        return True, 0.0, 2, (1, 1)
    v = raster.voxel_mm
    for axis in (0, 1):
        cap = max_tilt_deg(grid.shape[1 - axis] * v, grid.shape[2] * v,
                           headroom_mm)
        if cap <= 0:
            continue
        for step in range(TILT_STEPS, 0, -1):
            phi = cap * step / TILT_STEPS
            for sign in (1, -1):
                rot = tilted_copy(grid, sign * phi, axis)
                for signs, neigh in orders:
                    if _lift_clear(rot, grid, neigh):
                        return True, round(sign * phi, 1), axis, signs
    return False, 0.0, 2, (1, 1)


def _lift_clear(rot: np.ndarray, grid: np.ndarray, neigh) -> bool:
    """Straight-up escape of a tilted copy `rot` from the placed `neigh`."""
    reach = max(rot.shape[2], grid.shape[2]) + max(abs(n[2]) for n in neigh) + 1
    for t in range(0, min(reach, MAX_STEPS) + 1):
        gone = True
        for n in neigh:
            off = (-n[0], -n[1], t - n[2])
            if off[2] >= rot.shape[2] or -off[2] >= grid.shape[2]:
                continue
            gone = False
            if _overlap2(rot, grid, off):
                return False
        if gone:
            return True
    return False


def load_path(raster, order, pitch_lbh, grid, inner,
              clearance_lbh=(0.0, 0.0, 0.0)) -> dict | None:
    """Is there a way to get one part into this lattice? -> `Layout.load_path`

    `order` is the footprint order the layout won in ((0,1,2) or (1,0,2)),
    `pitch_lbh`/`grid`/`inner` are the SHIPPED ones -- `layouts_for` passes
    what it counted, after any T1 repair, so the path and the number are one
    expression (CLAUDE.md hard rule 9).

    None for a pose measured from bare numbers (synthesis), which carries no
    raster to sweep. `kind == "none"` is a REFUSAL, not a warning: the caller
    drops the layout.

    Rungs, in order: delta_proj prune -> straight -> oblique -> tilt -> none.

    DEVIATION from the ticket, and it is load-bearing: the ticket expects
    `delta_proj == 0` on both floor axes for the Mubea bar. It is not; see the
    module docstring for the measurement. The sweep is what answers it.
    """
    if raster is None or getattr(raster, "voxel_mm", 0.0) <= 0:
        return None
    shifts = _shift_voxels(pitch_lbh, clearance_lbh, raster.voxel_mm)
    bounds = tuple(int(b) for b in grid)
    shape = raster.coarse(order).shape
    stack_h = shape[2] * raster.voxel_mm + max(0, bounds[2] - 1) * pitch_lbh[2]
    headroom = round(float(inner[2]) - stack_h, 2)

    # Keyed on the EFFECTIVE limits, never on `bounds`: that is what decides
    # the neighbour set, and `min(b, 2)` collapsed a 6-wide lattice onto a
    # 2-wide one's cached verdict.
    key = ("path", tuple(order), shifts, limits(shifts, bounds, shape),
           tuple(round(p, 2) for p in pitch_lbh), round(headroom, 1))
    m = _memo(raster)
    if key in m:
        return dict(m[key])

    p_sil = pitch_sil(raster, order, clearance_lbh)
    d_proj = delta_proj(p_sil, pitch_lbh, bounds)
    out = {"pitch_sil": [round(p, 2) for p in p_sil],
           "delta_proj": list(d_proj),
           "neighbours": len(placed_neighbours(shifts, bounds, (1, 1), shape)),
           "headroom_mm": headroom,
           # `probes`, not "directions": the sweep walks direction x start
           # corner, so 40 directions over 4 corners is 160 probes.
           "probes": 0, "load_order": [1, 1], "reason": None,
           # Which pass produced the verdict. 2 means pass 1 refused and the
           # 2mm re-check overturned it (or confirmed it) -- see below.
           "pass": 1}

    # The prune. Disjoint shadows mean no vertical lift can ever touch a
    # neighbour with a non-zero IN-PLANE lattice offset, so the only thing
    # left that a straight descent can foul is the part directly below --
    # and that one is not covered by the projection argument at all
    # (`min_pitch` proved the MULTIPLES of the stacking pitch clear, never
    # `pitch + one voxel`, which is what a part with an undercut fouls on).
    # So the prune shrinks the straight test to one neighbour; it does not
    # skip it.
    prune = not any(d_proj)
    kind, u, signs, tested = sweep(raster, order, shifts, bounds,
                                   column_only=prune)
    out["probes"] = tested

    # PASS 2. Pass 1 judges a contact by the 4mm raster alone, and that raster
    # rounds a surface shell OUTWARD -- the same reason `lattice_check` needs
    # a contact tolerance and not a strict rule. A refusal is the one verdict
    # expensive enough to be worth re-measuring, so it is the only one that
    # buys the fine re-check: ZB 3000 M2 was refused on contacts of 2-7 cells
    # out of 32573 that are ZERO cells at 2mm, and shipped 27 where 32 loads.
    # Everything that clears in pass 1 never gets here, so the common case
    # pays nothing.
    if kind == "none":
        out["pass"] = 2
        logger.info("no load path at 4mm -- re-measuring the %d contacts at "
                    "%gmm (pass 2)", tested, FINE_MM)
        kind, u, signs, tested2 = sweep(raster, order, shifts, bounds,
                                        column_only=prune, tolerant=True)
        out["probes"] = tested + tested2

    if kind != "none":
        out.update(kind=kind, load_order=list(signs),
                   dir={"u": [round(c, 4) for c in u], "phi_deg": 0.0})
    else:
        # The tilt rung is the last one, after BOTH passes -- a design that
        # needs no tilt must never be reported as needing one.
        ok, phi, axis, signs = tilt(raster, order, shifts, bounds, headroom)
        if ok:
            out.update(kind="tilt", load_order=list(signs),
                       dir={"u": [0.0, 0.0, 1.0], "phi_deg": phi,
                            "axis": axis})
        else:
            out.update(kind="none", dir=None, reason="no load path")
    m[key] = dict(out)
    return out
