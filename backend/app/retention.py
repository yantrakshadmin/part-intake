"""T4 — retention computed, not assumed (PLANNING §10, verdict §1.7/§1.8).

Three questions about a lattice that is already known to FIT, asked of the same
voxel raster the count came from (CLAUDE.md hard rule 9 -- no second geometry):

    free_stand(...)   does one part stand on its own foot, or does it need a jig?
    drift(...)        how far can a part slide before it touches a neighbour?
    frozen_layers(..) does friction hold each layer at 1 g lateral?
    compact(...)      does a chain squeezed by a follower compact, or jam?

`retention(...)` is the one entry point `nesting.layouts_for` calls; it returns
the `Layout.retention` payload.

What these are NOT: a mass property. `m_p` is the user-supplied
`PartProfile.weight_kg` and nothing here derives a mass, a volume or a density
from customer CAD (hard rule 4 -- the files are open surface shells). With no
weight given the payload says `mass_assumed` and the numbers are for 1 kg.

Calibration knobs, every one of them empirical and none of them a fact:
`FOOT_BAND_MM`, `R_MIN_MM`, `TIP_MIN_DEG`, `WALL_STEP_MM`, `MU`. The μ
table is the debate's default table, not a measurement; DOMAIN.md (owed by the
packaging engineers) replaces it.

Self-check: `python tests/test_retention.py`.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from scipy.spatial import ConvexHull, QhullError

logger = logging.getLogger(__name__)

G = 9.81                      # m/s^2

# --- free stand -------------------------------------------------------------
# The foot is every voxel within this much of the floor, not just the lowest
# layer: a part settles into the sheet or foam under it, and a one-voxel foot
# on a bent bar is a tessellation artefact, not a contact patch. Measured on
# the YXA bar (band 4/10/20mm -> support radius 128/136/140mm, tip 39/46/52
# deg), so the verdict is not sensitive to the exact band -- which is the only
# reason a guessed constant is acceptable here.
FOOT_BAND_MM = 10.0
# Verdict §1.7 (Opus R3 §5.1). Inscribed circle of the SUPPORT POLYGON -- the
# convex hull of the foot voxels, which is what a rigid body actually stands
# on. The hull matters: a bent bar touches along two curved lines whose hull is
# 136mm wide, while the mask itself inscribes 12mm.
R_MIN_MM = 20.0
TIP_MIN_DEG = 15.0

# --- drift / compact --------------------------------------------------------
# Displacement-controlled wall step (verdict §1.8). The jam criterion itself is
# not a length: it is the contact slope against the friction cone -- see
# `compact`. A rack allowance in millimetres was the first version and it
# measured "is this part a solid block", not "does this chain wedge": the first
# offset that FULLY separates two copies is 84mm on the stabiliser bar and 76mm
# on the TRW wheel, because that offset is the part leaving the row, so every
# non-cuboid in the catalogue reported a jam.
WALL_STEP_MM = 4.0
# Wall steps past the first contact that `compact` may walk looking for a
# contact that actually RESISTS the push. On an open surface shell (hard rule
# 4) two copies intersect in a curve, not a volume, so pushing deeper can
# SHRINK the shared cells -- the TRW wheel goes 111 -> 81 -- and the slope has
# no denominator. Three steps is 12mm; past that there is no contact normal to
# find and the answer is "unknown", never a magnitude out of a guard.
COMPACT_WALK_STEPS = 3

# --- friction ---------------------------------------------------------------
# Default μ table, verdict §1.7, keyed (surface_class, contact face). Four rows
# is all the debate agreed; anything else falls back to raw/PP and says so in
# `mu_basis`. `class_a` is read as `painted` -- both are coated steel.
MU = {
    ("raw", "PP"): 0.25,
    ("raw", "EPE"): 0.5,
    ("painted", "EPE"): 0.35,
    ("ecoat", "PP"): 0.2,
}
MU_FALLBACK = MU[("raw", "PP")]

# The face the part rests on, per `dunnage` archetype: every family in the §6
# stock vocabulary is PP Bubble Guard against the part (the bar system's EVA is
# inside the separator assembly, under the PP skin). An EPE-faced interleaf is
# a T7 element and is not in the catalogue yet, so the EPE rows of `MU` are
# only reachable through the explicit `mu=` override.
CONTACT_FACE = {"bar_and_rod": "PP", "pocket_tray": "PP", "layer_sheets": "PP"}

# The four distinct slide directions, and the eight lattice neighbours any of
# them can run into. Only four: on a lattice `+L` and `-L` test the same pairs
# (the neighbour set is symmetric and `cells(v) == cells(-v)`, one pair of
# copies either way), so eight keys were four numbers printed twice.
_DIRS = (("L", (1, 0)), ("B", (0, 1)), ("L+B", (1, 1)), ("L-B", (1, -1)))
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1),
               (1, 1), (1, -1), (-1, 1), (-1, -1))


def _hull_planes(mask: np.ndarray):
    """Half-planes of the convex hull of a 2D boolean mask's True cells.

    -> (n, 3) `a*i + b*j + c <= 0 inside`, unit normals, in CELL units. None
    when the foot is fewer than three cells or collinear (a line contact: it
    rocks, and zero support is the right answer -- same call `geometry.
    support_polygon` makes on a cylinder).
    """
    pts = np.argwhere(mask).astype(float)
    if len(pts) < 3:
        logger.warning("free stand: the foot is %d cell(s) -- a point contact, "
                       "not a stance; reported as no support", len(pts))
        return None
    try:
        return ConvexHull(pts).equations
    except QhullError:
        # A LINE contact (a bar or a cylinder on its side): no support polygon
        # at all, which is a different failure from a narrow one and has to be
        # readable as such in the log.
        logger.warning("free stand: %d foot cells spanning %s are collinear -- "
                       "a line contact; it rocks, reported as no support",
                       len(pts), tuple(int(x) for x in np.ptp(pts, axis=0)))
        return None


def free_stand(foot_cells: np.ndarray, voxel_mm: float, com_mm) -> dict:
    """Does one part stand on its own foot? -> {ok, r_min_mm, tip_deg}

    `foot_cells` is the plan mask of the voxels within `FOOT_BAND_MM` of the
    floor; `com_mm` is the posed part's voxel centroid (x, y, z) in mm.

    `r_min_mm` is the inscribed circle of the support polygon -- how wide the
    part's stance is. `tip_deg` is `atan(d / h)`: the angle the part has to be
    tipped through before its centroid passes outside the support polygon, with
    `d` the centroid's plan distance to the nearest hull edge and `h` its
    height. A centroid already outside the polygon is 0 degrees -- it falls.

    ponytail: the centroid of the SURFACE voxels, not of a solid. These files
    are open shells (hard rule 4) and there is no honest volume centroid to be
    had; a shell centroid is the same point for anything of even wall
    thickness, which every one of these pressings is.
    """
    eq = _hull_planes(foot_cells)
    if eq is None:
        return {"ok": False, "r_min_mm": 0.0, "tip_deg": 0.0}
    ii, jj = np.mgrid[0:foot_cells.shape[0], 0:foot_cells.shape[1]]
    inside = np.min(-(eq[:, 0, None, None] * ii + eq[:, 1, None, None] * jj
                      + eq[:, 2, None, None]), axis=0)
    # Evaluated at cell centres, so a stance narrower than one voxel reads
    # zero -- the error runs towards FAIL, which is the safe side.
    r_min = max(0.0, float(inside.max())) * voxel_mm
    c = np.asarray(com_mm, dtype=float) / voxel_mm - 0.5      # cell coords
    d_com = float(np.min(-(eq[:, 0] * c[0] + eq[:, 1] * c[1] + eq[:, 2]))) \
        * voxel_mm
    h_com = max(float(np.asarray(com_mm, dtype=float)[2]), 1e-9)
    tip = math.degrees(math.atan2(max(d_com, 0.0), h_com))
    return {"ok": bool(r_min >= R_MIN_MM and tip >= TIP_MIN_DEG),
            "r_min_mm": round(r_min, 1), "tip_deg": round(tip, 1)}


def _floor_shifts(pitch_lbh, voxel_mm) -> tuple:
    """Lattice pitch -> the in-plane spacing in voxels, as the parts SIT.

    Not `nesting._shift_voxels`: that one takes the clearance back out to test
    the touching pitch, and a part that is already touching cannot drift. The
    air the engine added is exactly what drift measures.
    """
    return tuple(max(1, int(round(p / voxel_mm))) for p in pitch_lbh[:2])


def drift(raster, order, pitch_lbh, grid) -> dict:
    """Free travel of one part along each floor direction, mm. Four keys.

    Slides the part one voxel at a time and, at every step, tests it against
    EVERY neighbour the lattice has -- not just the one it is heading for. A
    part sliding on the diagonal reaches the part beside it long before the
    diagonal one: the TRW wheel's shipped layout reports 5.7mm on `L+B`, and
    probing only the diagonal neighbour said 135.8mm, which is the distance to
    a part that is not in the way.

    `None` where the lattice has no neighbour on a moving axis (a single row or
    column) -- the part then drifts to the box wall, which is a slack this
    function is not given the inner dimensions to compute. Walls and inserts as
    obstacles (verdict §1.7) are therefore NOT modelled: part-to-part only.

    Four directions, not eight: `+L` and `-L` are the same measurement on a
    lattice (see `_DIRS`).
    """
    v = raster.voxel_mm
    s = _floor_shifts(pitch_lbh, v)
    live = [(ni, nj) for ni, nj in _NEIGHBOURS
            if (int(grid[0]) >= 2 or not ni) and (int(grid[1]) >= 2 or not nj)]
    out: dict = {}
    for name, (di, dj) in _DIRS:
        if (di and int(grid[0]) < 2) or (dj and int(grid[1]) < 2):
            out[name] = None
            continue
        step = math.hypot(di, dj) * v
        t_max = min(s[a] for a, d in ((0, di), (1, dj)) if d)
        travel = 0.0
        for t in range(1, t_max + 1):
            if any(raster.cells(order, (ni * s[0] - t * di,
                                        nj * s[1] - t * dj, 0))
                   for ni, nj in live):
                break
            travel = t * step
        out[name] = round(travel, 1)
    return out


def frozen_layers(grid, mu: float, mass_kg: float,
                  preload_n: float = 0.0, a_g: float = 1.0) -> dict:
    """Which layers friction holds at `a_g` g lateral. -> payload dict.

    One part is a free body with TWO friction faces -- the layer under it and
    the layer (or pad) over it -- so both normals count:

        N_bot(k) = P/n_xy + (n_z - k)     . m . g     (its own weight included)
        N_top(k) = P/n_xy + (n_z - 1 - k) . m . g
        layer k is frozen iff  mu . (N_bot + N_top) >= m . g . a

    The TOP layer is exempt by construction: nothing rests on it, so its top
    face carries only the preload and holding it is the lid pad's job, not
    friction's. The stack verdict is therefore the weakest layer BELOW the top
    -- `held` iff every one of them is frozen -- and `frozen` still reports all
    `n_z` so the top row is visible rather than quietly dropped.
    """
    n_z, n_xy = int(grid[2]), int(grid[0]) * int(grid[1])
    w = mass_kg * G * a_g
    p = preload_n / max(n_xy, 1)
    frozen = [bool(mu * (2 * p + (2 * (n_z - k) - 1) * mass_kg * G) >= w)
              for k in range(n_z)]
    judged = frozen[:max(n_z - 1, 0)]
    return {"layers": n_z, "frozen": frozen,
            "verdict": "held" if all(judged) else "slides",
            "weakest_layer": next((k for k, f in enumerate(judged) if not f),
                                  -1),
            "preload_n": round(float(preload_n), 1)}


def compact(raster, order, pitch_lbh, grid, mu: float,
            wall_step_mm: float = WALL_STEP_MM) -> dict:
    """Displacement-controlled follower on the chain. -> {verdict, ...}

    The wall steps `wall_step_mm` into the longest row until the pushed part
    touches its neighbour, and then asks ONE question about that contact: how
    steep is it? Three cell counts at the first wall position whose contact
    RESISTS the push (`n_push > n0`; see `COMPACT_WALK_STEPS`) --

        n0      shared cells there
        n_push  shared cells one wall step deeper
        n_lat   the fewest shared cells a one-voxel shift perpendicular to the
                push leaves (in-plane and vertical, four probes)

        tan_theta = (n0 - n_lat) / (n_push - n0)

    -- and a face square to the push gives about zero (sliding sideways barely
    changes the contact, pushing in doubles it) while a 45 degree ramp gives
    1.0. The chain compacts when that slope sits inside the friction cone,
    `tan_theta <= mu`, and wedges when it does not: the pushed part is being
    told to climb a ramp steeper than friction can hold it on, which is Gemini
    R4 §1's mechanism as a computation against a number rather than a rule
    about interleaving (Opus R5 §2).

    `mu` is the SAME coefficient the rest of the payload is judged at, so a
    surface class that changes the freeze also changes the jam.

    Verdict `unknown` with `tan_theta: null` when no wall position in the
    window resists: two open shells crossing have no contact normal to measure
    and a number invented out of a divide-by-zero guard would be worse than
    saying so. `wall_mm` is always the position the numbers came from.

    ponytail: one-voxel gradient as the contact normal; upgrade is a fitted
    plane over the contact cells.
    """
    axis = 0 if int(grid[0]) >= int(grid[1]) else 1
    out = {"axis": "LB"[axis], "wall_mm": 0.0, "contact_cells": 0,
           "tan_theta": 0.0, "mu": round(float(mu), 3), "note": ""}
    if int(grid[axis]) < 2:
        return dict(out, verdict="ok", note="one part on this axis: no chain")
    v = raster.voxel_mm
    s = _floor_shifts(pitch_lbh, v)
    d_step = max(1, int(round(wall_step_mm / v)))
    off, n0, step0 = None, 0, 0
    for step in range(1, s[axis] // d_step + 1):
        probe = [0, 0, 0]
        probe[axis] = s[axis] - step * d_step
        if probe[axis] <= 0:
            break
        n0 = raster.cells(order, tuple(probe))
        if n0:
            off, step0 = probe, step
            break
    if off is None:
        return dict(out, verdict="ok",
                    note="the row closes before the two ever touch")

    # Walk deeper until the contact RESISTS: `n_push > n_here`. On a solid that
    # is the first step; on a shell pair it may be a few, or never.
    found = None
    for extra in range(COMPACT_WALK_STEPS + 1):
        here = list(off)
        here[axis] -= extra * d_step
        if here[axis] <= 0:
            break
        n_here = n0 if not extra else raster.cells(order, tuple(here))
        if not n_here:
            continue                       # passed clean through at this step
        deeper = list(here)
        deeper[axis] -= d_step
        n_push = raster.cells(order, tuple(deeper))
        if n_push > n_here:
            found = (here, n_here, n_push, (step0 + extra) * wall_step_mm)
            break
    if found is None:
        return dict(out, verdict="unknown", tan_theta=None,
                    contact_cells=int(n0), wall_mm=step0 * wall_step_mm,
                    note="shells intersect in a curve, no contact normal")

    here, n_here, n_push, wall_mm = found
    n_lat = min(raster.cells(order, tuple(q))
                for q in _perpendicular(here, axis))
    tan = max(n_here - n_lat, 0) / (n_push - n_here)
    out.update(wall_mm=wall_mm, contact_cells=int(n_here),
               tan_theta=round(tan, 3))
    return dict(out, verdict="ok" if tan <= mu else "jam")


def _perpendicular(off, axis: int) -> list:
    """The four one-voxel probes across the push: in-plane lateral, and up."""
    return [tuple(c + sign if a == other else c for a, c in enumerate(off))
            for other in (1 - axis, 2) for sign in (1, -1)]


def mu_for(surface_class: str | None, family: str | None) -> tuple:
    """(mu, basis sentence) from the default table. Never raises."""
    surf = {"class_a": "painted"}.get(surface_class or "", surface_class) \
        or "raw"
    face = CONTACT_FACE.get(family or "")
    note = ""
    if face is None:
        face, note = "PP", f" (dunnage family {family!r} has no known contact " \
                           f"material, PP assumed)"
    if (surf, face) in MU:
        return MU[(surf, face)], f"{surf} on {face}{note}" + (
            "" if surface_class else " (surface_class not given, raw assumed)")
    return MU_FALLBACK, (f"{surf} on {face} is not in the default table, "
                         f"raw on PP assumed{note}")


def retention(raster, order, pitch_lbh, grid, family: str | None = None,
              surface_class: str | None = None, weight_kg: float | None = None,
              mu: float | None = None, preload_n: float = 0.0,
              foot_band_mm: float = FOOT_BAND_MM) -> dict | None:
    """The `Layout.retention` payload for one scored layout.

    None for a pose measured from bare numbers (synthesis), which carries no
    raster -- same discipline as `nesting.lattice_check`.
    """
    if raster is None or getattr(raster, "voxel_mm", 0.0) <= 0:
        return None
    g = raster.coarse(order)
    v = raster.voxel_mm
    nb = max(1, int(round(foot_band_mm / v)))
    total = int(g.sum())
    if not total:
        return None
    # Marginal sums, not argwhere: the centroid of a 750k-cell grid is on the
    # solve's critical path once per (pose x asset).
    com = tuple(float((np.arange(g.shape[a]) * g.sum(
        axis=tuple(x for x in range(3) if x != a))).sum()) / total * v + v / 2
        for a in range(3))
    mu_used, basis = (mu, "explicit override") if mu is not None \
        else mu_for(surface_class, family)
    mass = float(weight_kg) if weight_kg else 1.0
    return {
        "free_stand": free_stand(g[:, :, :nb].any(axis=2), v, com),
        "frozen_layers": frozen_layers(grid, mu_used, mass, preload_n),
        "drift_mm": drift(raster, order, pitch_lbh, grid),
        # The whole dict, not the one word: a jam verdict with no numbers
        # behind it is an alarm nobody can check (hard rule 9).
        "compact": compact(raster, order, pitch_lbh, grid, mu_used),
        "mu": mu_used,
        "mu_basis": basis,
        "mass_assumed": not bool(weight_kg),
    }
