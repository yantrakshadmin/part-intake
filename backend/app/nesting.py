"""Phase 2 nesting engine: solve for the lattice pitch, not the bounding box.

Both shipped proposals are regular lattices of one part (Mubea 4x10, TRW 3x2x8),
so the whole problem is: how close can two copies of this part sit on each axis?
Not "how many boxes fit in a box".

    occupancy(mesh, pose)  ->  3D boolean grid
    min_pitch(grid, axis)  ->  closest collision-free spacing on that axis, mm
    lattice_count(...)     ->  parts per asset

Why this and not jagua-rs / NFP + bottom-left-fill (PLANNING §4 named both):

  * The gain is mostly VERTICAL, and a 2D nester cannot see it. Mubea ships 10
    layers of a 285mm part inside 790mm -- an implied 56mm pitch, 5.1x. The
    part-per-layer gain is only 190 -> 187mm. "Nest 2D per layer, then stack"
    divides the height by the layer height and throws away 8 of the 10 layers.
    TRW is the same story: a 135mm wheel on a 120mm pitch, sitting into its tray.
  * A raster does not need a clean outline. These are open surface models; the
    IBJ splits into 48,960 disconnected faces. Polygonising that for an NFP is a
    project. Voxel occupancy does not care.
  * jagua-rs is a Rust crate and there is no cargo on this machine; NFP needs
    shapely. This needs numpy and trimesh, both already installed.

Measured on the real YXA stabiliser bar (1092 x 298 x 143): width pitch 140mm
(2.1x), height pitch 68mm (2.2x) -> exactly 40 in a PLS12801.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

logger = logging.getLogger(__name__)

# Count comparisons are float divisions of millimetre dimensions, and parts that
# fit their asset *exactly* are the normal case, not the edge case (4 x 186.67 in
# 750mm is Mubea). Without this an exact fit silently loses a whole row.
EPS = 1e-6

# Voxel edge for occupancy. 4mm keeps the bar grid at 273x75x37 and voxelises in
# ~2s; surface voxels round the part outward, so measured pitch errs generous
# (safe) rather than tight.
VOXEL_MM = 4.0

# ponytail: default clearance from PLANNING §6 (5-10mm/side, 3mm separator
# sheet). Real numbers belong in DOMAIN.md, which the packaging engineers still
# owe us -- so it stays a parameter on every call, never a baked constant.
#
# IN-PLANE only. This is the air a part needs beside its neighbour so it can be
# lowered into place without scraping it.
DEFAULT_CLEARANCE_MM = 5.0

# Stacking clearance, axis 2. Zero, and that is not the same value as
# DEFAULT_CLEARANCE_MM being loose -- the two are physically different
# quantities and one constant for both is wrong by 4 parts on the headline case:
#
#   Mubea bar, pose "Largest face down", extent_h 148.0, raw pitch_h 68.0
#     +5mm:  148 + 9 x 73 = 805 > 790  ->  9 layers  ->  36 parts
#     +0mm:  148 + 9 x 68 = 760 <= 790 ->  10 layers ->  40 parts  (shipped)
#
# There is no air gap to reserve between layers: a layer rests on the layer
# below it, or on the dunnage the BOM already counts (the Mubea deck's 11 Top
# Center Bars, 66mm, sit inside the nest depth). Reserving a gap on top of that
# double-counts the bar.
#
# ponytail: 4mm voxel + 0 stack clearance reproduces both decks; a
# clearance/quantisation model that is right for a REASON needs DOMAIN.md.
# At VOXEL_MM = 2.0 the same bar measures raw pitch_h 62mm and this predicts 11
# layers, one more than shipped -- so 4mm landing on the deck is luck, not
# accuracy. Leave VOXEL_MM alone until that model exists.
DEFAULT_STACK_CLEARANCE_MM = 0.0


@dataclass(frozen=True)
class Layout:
    """One scored way to fill one asset."""

    asset_name: str
    pose_label: str
    count: int
    grid: tuple                  # (n_along_L, n_along_B, n_layers)
    extent_lbh: tuple            # part bounding extent in this pose, mm
    pitch_lbh: tuple             # lattice spacing actually used, mm
    limited_by: str              # "geometry" or "weight"
    # Which of `Pose.footprint_orders`' two in-plane assignments this won in:
    # False = (0, 1, 2) as measured, True = (1, 0, 2), the quarter turn.
    # The worker composes `IN_PLANE_TURN` into the rotation it poses the
    # voxels AND the 3D animation with; without it the drawing stamped
    # 876mm-long parts into a 332mm lattice and clipped every one of them
    # while every number on screen stayed correct (F7). Not in the API
    # response model -- the frontend only ever sees the composed pose_matrix.
    turned: bool = False
    # Plan silhouette of ONE part in this footprint order -- `silhouette_runs`
    # wire format, or None when the pose carries no measured grid. The
    # interleaved insert drawing needs the real footprint; the engine used to
    # compute it inside `occupancy` and throw it away, leaving the frontend
    # nothing to draw but the bounding rectangle.
    silhouette: dict | None = None
    # `dunnage.bom(...).as_dict()`. Stamped by `engine.solve`, which is where
    # the asset's inner dims are known. None until then.
    dunnage: dict | None = None
    # The raster's quantisation band. Surface voxels round the part OUTWARD
    # (up to a voxel per end on the extent, up to one on the touching pitch),
    # so `count` is the floor of what the geometry allows and this is an
    # estimate of its ceiling: `lattice_count` at one voxel less on every
    # extent and pitch. Not a proven bound -- the extent can be over by two
    # -- but measured on the YXA bar it lands: 40 at 4mm with a ceiling of
    # 44, and 3mm and 2mm rasters both return 44 outright. Equal to `count`
    # when the pose carries no voxel size (bare-number poses).
    count_upper: int = 0
    # `engine.cuboid_count` for this SAME asset -- the ground-truth baseline
    # (CLAUDE.md), stamped by `engine.solve` where the asset's inner and the
    # part's weight are both known. 0 until then.
    cuboid_count: int = 0
    # `app.reasons.reasons_for(...)`, composed in the worker once clearance,
    # poses_searched and warnings are all in scope. Empty until then.
    reasons: list[str] = field(default_factory=list)

    @property
    def interleave(self) -> tuple:
        """Pitch / extent per axis. 1.0 = no nesting gain on that axis."""
        return tuple(round(p / e, 3) if e else 1.0
                     for p, e in zip(self.pitch_lbh, self.extent_lbh))


def occupancy(mesh: trimesh.Trimesh, transform=None,
              voxel_mm: float = VOXEL_MM) -> np.ndarray:
    """Boolean occupancy grid of the part's surface in a given resting pose.

    `transform` is a 4x4 from `geometry.OrientationCandidate.rotation_matrix`,
    so axis 2 of the returned grid is always up.

    Uses trimesh's subdivide voxeliser: it splits triangles until they are
    smaller than a voxel and marks the cells they land in. That works on open
    shells, which matters -- every customer file so far is one, and a
    fill/ray-based voxeliser needs watertight geometry.
    """
    m = mesh.copy()
    if transform is not None:
        m.apply_transform(np.asarray(transform, dtype=float))
    grid = m.voxelized(pitch=voxel_mm, method="subdivide").matrix
    return np.asarray(grid, dtype=bool)


def silhouette_runs(mask: np.ndarray, cell_mm: float = VOXEL_MM) -> dict:
    """Row run-lengths of a 2D boolean plan mask. The insert drawing's input.

    `mask` is `occupancy(...).any(axis=2)` -- the part's plan view in its
    resting pose, exact at `cell_mm`. Runs are `[row, col_start, col_end]`
    with **inclusive** ends, so a solid row of N cells is one run [r, 0, N-1].

    Deliberately raw: no smoothing, no contour fitting, no invented outline. A
    fitted outline would be a second geometry engine to keep honest, and the
    raster is what the pitch was actually measured from -- the drawing and the
    number stay the same expression (CLAUDE.md hard rule 9).
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError(f"silhouette needs a 2D mask, got shape {mask.shape}")
    runs: list[list[int]] = []
    for r, row in enumerate(mask):
        # Edges of each True run: diff of the row padded with False on both
        # sides gives +1 at every start and -1 at every end.
        edges = np.flatnonzero(np.diff(np.concatenate(
            ([False], row, [False])).astype(np.int8)))
        for start, stop in zip(edges[::2], edges[1::2]):
            runs.append([r, int(start), int(stop) - 1])
    return {"cell_mm": float(cell_mm), "rows": int(mask.shape[0]),
            "cols": int(mask.shape[1]), "runs": runs}


# The in-plane quarter turn of footprint order (1, 0, 2), as a 4x4 the worker
# composes into the resting rotation (`IN_PLANE_TURN @ candidate.rotation_matrix`)
# so `pose_voxels` and the 3D animation's `pose_matrix` are the same expression.
# +90 degrees about z pairs EXACTLY with `np.rot90(mask, 1)` below -- that
# pairing is the whole point of the constant and `test_nesting` asserts it on a
# voxelised L-shape. A transpose would be a MIRROR: same bounding size, same
# pitch, wrong part.
IN_PLANE_TURN = np.array([[0.0, -1.0, 0.0, 0.0],
                          [1.0, 0.0, 0.0, 0.0],
                          [0.0, 0.0, 1.0, 0.0],
                          [0.0, 0.0, 0.0, 1.0]])


def plan_silhouettes(mask: np.ndarray, cell_mm: float = VOXEL_MM) -> tuple:
    """The plan silhouette for each of `Pose.footprint_orders`' two orders.

    Order (0, 1, 2) is the mask as measured; order (1, 0, 2) swaps the two
    floor axes -- a quarter TURN, `np.rot90(mask, 1)`, matching `IN_PLANE_TURN`
    cell for cell. Not the transpose: extents and pitch permute identically
    either way, so no number on screen moves, but a transpose is a mirror and
    the posed part would not match its own drawing. Encoded once per pose here
    rather than per (pose x asset) inside `footprint_orders`, because ranking
    the catalogue calls that ~120 times for the same handful of masks.
    """
    mask = np.asarray(mask, dtype=bool)
    return (silhouette_runs(mask, cell_mm),
            silhouette_runs(np.rot90(mask, 1), cell_mm))


def min_pitch(grid: np.ndarray, axis: int, voxel_mm: float = VOXEL_MM,
              clearance_mm: float = DEFAULT_CLEARANCE_MM) -> float:
    """Closest collision-free spacing of two identical copies along `axis`, mm.

    Slides a copy of the grid along the axis one voxel at a time and returns the
    first offset where the two do not share a cell. Returns the full extent when
    the part cannot interleave at all (a cuboid), which is the correct answer.

    A lattice at pitch p puts copies at 0, p, 2p, 3p..., so EVERY multiple has
    to be clear, not just the neighbour. Disjoint at p does not imply disjoint
    at 2p once the part is non-convex -- and non-convex comb/bent-bar parts are
    the whole reason this engine exists. A part occupying cells {0,1,4} is
    pairwise-clear at pitch 2 but copies 0 and 2 both want cell 4.

    ponytail: clearance is added to the touching pitch rather than dilating the
    grid first. That guarantees the gap along the packing axis -- the axis the
    lattice actually spaces on -- not a true 3D minimum separation. Dilate with
    scipy.ndimage.binary_dilation if a part ever needs the stricter version.
    """
    n = grid.shape[axis]

    def clear_at(offset: int) -> bool:
        # Slices, not np.take(range(...)): a range index is fancy indexing and
        # COPIES the whole grid, twice per probe. min_pitch runs ~n.ln(n)
        # probes per axis against a ~760k-cell grid -- cheap next to the
        # ~2.4s/pose `occupancy` (trimesh's own voxeliser) actually costs,
        # which is why the endpoint had to go async. Basic slicing returns
        # views regardless.
        hi = [slice(None)] * grid.ndim
        lo = [slice(None)] * grid.ndim
        hi[axis] = slice(offset, n)
        lo[axis] = slice(0, n - offset)
        return not (grid[tuple(hi)] & grid[tuple(lo)]).any()

    # Stop at n - 1: at shift == n both slices are empty, which reads as
    # "no collision" and would mask the real no-interleave answer below.
    for shift in range(1, n):
        if all(clear_at(k) for k in range(shift, n, shift)):
            return shift * voxel_mm + clearance_mm
    return n * voxel_mm + clearance_mm


def lattice_count(extent_lbh, pitch_lbh, inner_lbh,
                  part_kg: float = 0.0, max_weight_kg: float = 0.0) -> tuple:
    """Parts per asset for a regular lattice. -> (count, (nx, ny, nz), limited_by)

    Per axis: the first part needs its full extent, every one after it needs
    only the pitch. n = floor((inner - extent) / pitch) + 1.

    Clearance lives in the PITCH, deliberately, and there is NO gap charged
    between the outermost part and the asset wall. That looks like a missing
    PLANNING §6 5-10mm/side, and it is not: Mubea ships 190 + 9 x 66.67 =
    790.0mm of parts inside a 790mm inner, i.e. exactly zero wall clearance on
    the stacking axis. Charging even 1mm/side takes that case from 40 to 27.
    Whatever the real datum is, the shipped proposals measure inner height as
    the height the lattice gets -- a DOMAIN.md question, not a bug here.

    This is the formula the proposals encode. It reproduces Mubea 40 and TRW 48
    from their own decks' pitches; `tests/test_nesting.py` asserts exactly that.
    """
    counts = []
    for extent, pitch, inner in zip(extent_lbh, pitch_lbh, inner_lbh):
        if extent > inner + EPS or pitch <= 0:
            return 0, (0, 0, 0), "geometry"
        counts.append(int((inner - extent) / pitch + EPS) + 1)

    nx, ny, nz = counts
    geometric = nx * ny * nz
    if part_kg > 0 and max_weight_kg > 0:
        by_weight = int(max_weight_kg / part_kg + EPS)
        if by_weight < geometric:
            # Shrink to a lattice that actually respects the cap rather than
            # capping the product. `count` must always equal the product of
            # `grid` -- returning 10 alongside a 3x3x3 grid is the PLANNING §7
            # "minimum of 48 and 46" defect, and a UI rendering both would
            # print two numbers that disagree.
            #
            # Search the grid; do NOT cascade layers -> rows -> columns. The
            # cascade held nx fixed and so under-counted badly: at a 10-part
            # cap a 7x2x1 geometry returned 7x1x1 = 7 where 5x2x1 = 10 fits at
            # exactly the cap. Both satisfy count == product(grid), so the §7
            # invariant cannot catch it. nx*ny*nz is a few dozen here.
            #
            # Ties go to the fullest footprint (largest i*j), because a full
            # bottom layer is what an insert actually is -- 3x3x1 beats 3x1x3
            # at the same 9 parts.
            best_total, best_grid = 0, (0, 1, 1)
            for k in range(1, nz + 1):
                for j in range(1, ny + 1):
                    i = min(nx, by_weight // (k * j))
                    if i < 1:
                        continue
                    total = i * j * k
                    if (total, i * j) > (best_total, best_grid[0] * best_grid[1]):
                        best_total, best_grid = total, (i, j, k)
            return best_total, best_grid, "weight"
    return geometric, tuple(counts), "geometry"


@dataclass(frozen=True)
class Pose:
    """A resting pose with its lattice pitch already measured.

    Pitch is a property of the part in a pose, not of the asset it goes into,
    so measuring it once and reusing it across the catalogue turns an
    O(poses x assets) voxelisation into O(poses). At ~2.4s per pose that is the
    difference between 10s and 2.5 minutes over 15 containers.
    """

    label: str
    extent: tuple                # (x, y, z) mm, z is up
    pitch: tuple                 # (x, y, z) mm
    # `plan_silhouettes(...)` output: one wire-format silhouette per footprint
    # order, taken out of the occupancy grid `measure_poses` already built.
    # (None, None) when the pose came from bare numbers -- synthesis does that
    # -- so a missing silhouette is null, never a fabricated rectangle.
    silhouettes: tuple = (None, None)
    # Voxel edge the extent and pitch were measured at; 0.0 for bare-number
    # poses. `layouts_for` turns it into `Layout.count_upper`.
    voxel_mm: float = 0.0
    # Clearance `measure_poses` baked into `pitch`, (x, y, z). Zeros for
    # bare-number poses (a deck's pitch is taken as given). The dunnage
    # archetype needs it back out -- see `dunnage.archetype_of`.
    clearance: tuple = (0.0, 0.0, 0.0)

    def footprint_orders(self):
        """The two 90-degree in-plane assignments, per the §4 angle ladder.

        Pitch travels with its own axis, so extent and pitch permute together
        -- and so does the plan silhouette. Order (1, 0, 2) swaps the two
        floor axes, so the mask is TURNED (`plan_silhouettes`, and the worker
        poses with the matching `IN_PLANE_TURN`). Getting that wrong leaves the
        insert drawing 90 degrees out while every number on screen stays
        correct; `tests/test_nesting.py` asserts the silhouette's bounding
        size against the extent it is yielded with, which is what catches it.

        Height (index 2) is fixed by the resting pose.
        Yields (extent, pitch, silhouette | None, clearance).
        """
        for which, order in enumerate(((0, 1, 2), (1, 0, 2))):
            yield (tuple(self.extent[i] for i in order),
                   tuple(self.pitch[i] for i in order),
                   self.silhouettes[which] if which < len(self.silhouettes)
                   else None,
                   tuple(self.clearance[i] for i in order))


def measure_poses(mesh: trimesh.Trimesh, candidates,
                  voxel_mm: float = VOXEL_MM,
                  clearance_mm: float = DEFAULT_CLEARANCE_MM,
                  stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM) -> list:
    """Measure extent and lattice pitch for each resting pose. Do this once.

    `candidates` are `geometry.OrientationCandidate`s.

    `clearance_mm` spaces the two FLOOR axes; `stack_clearance_mm` spaces the
    vertical one (axis 2 is always up -- see `occupancy`). They default
    differently because they are different physical quantities: in-plane is real
    air so a part clears its neighbour going in, vertical is nothing, because a
    layer rests on the layer (or the counted dunnage) below it. Charging the
    in-plane 5mm vertically as well costs the Mubea bar a whole layer, 36 parts
    against the 40 that shipped -- see `DEFAULT_STACK_CLEARANCE_MM`.
    """
    poses = []
    for cand in candidates:
        grid = occupancy(mesh, cand.rotation_matrix, voxel_mm)
        poses.append(Pose(
            label=cand.label,
            extent=tuple(s * voxel_mm for s in grid.shape),
            pitch=tuple(min_pitch(
                grid, ax, voxel_mm,
                stack_clearance_mm if ax == 2 else clearance_mm)
                for ax in range(3)),
            # Free -- taken out of the grid we just built. Voxelising again for
            # the drawing would double the ~2.4s/pose cost of the whole solve.
            #
            # ponytail: this leaves the same silhouette duplicated across every
            # asset that shares a pose (a few hundred runs each, so kilobytes).
            # Upgrade path when it matters: ship a pose table keyed by label and
            # have layouts reference it.
            silhouettes=plan_silhouettes(grid.any(axis=2), voxel_mm),
            voxel_mm=voxel_mm,
            clearance=(clearance_mm, clearance_mm, stack_clearance_mm),
        ))
        logger.debug("pose %r extent=%s pitch=%s", cand.label,
                     poses[-1].extent, poses[-1].pitch)
    return poses


def layouts_for(poses, asset, part_kg: float = 0.0) -> list:
    """Score already-measured poses against one asset. Best first.

    The inner height the lattice gets is the asset's minus the height its
    own insert adds above the stack (`dunnage.dead_height_mm`): a pocket
    tray's bottom sheet, a bar or top separator taller than the nest depth.
    That is the feedback the audit found missing -- the count used to fill
    the inner and the BOM then reported it did not fit. Mubea and TRW are
    unmoved: the bar's 68mm bars sit inside an 80mm nest depth (dead 0), the
    wheel's 3mm sheet leaves 975 + 3 <= 1000.

    The vertical PITCH the lattice steps by is `dunnage.layer_step_mm`, not
    the measured pitch: with layer separator sheets and no tray to share the
    pitch with (F11) the parts rest ON each sheet, so every layer costs one.
    Both numbers come from the insert, and `Layout.pitch_lbh` stays the
    measured pitch -- `dunnage.bom` derives the stack and the nest depth from
    it, and the drawing gets the step off the BOM (`Bom.layer_step_mm`).
    """
    from . import dunnage   # dunnage imports nothing from here; local to be safe
    out = []
    for pose in poses:
        # `footprint_orders` yields (0,1,2) then (1,0,2); index 1 IS the turn.
        for turned, (extent, pitch, silhouette, clearance) in enumerate(
                pose.footprint_orders()):
            # F11: the insert -- and so the height each layer costs -- turns
            # on whether the parts interleave IN PLAN, which needs the in-plane
            # grid. One flat layer in the real footprint is that grid, from
            # this same function rather than a second copy of the formula.
            flat, in_plane, _ = lattice_count(
                extent, pitch, (asset.inner[0], asset.inner[1], extent[2]))
            # Geometric grid on purpose: no `part_kg`, so the weight cap
            # cannot shrink it here. The cap only ever REMOVES parts from a
            # layer, and a smaller in-plane grid can only turn the interleave
            # off, so this over-reserves height at worst -- never under.
            in_plane = in_plane if flat else (1, 1, 1)
            dead = dunnage.dead_height_mm(extent, pitch, clearance,
                                          grid=in_plane)
            # Layer separators that the parts do NOT nest into are part of the
            # step, not of `dead`: 10 layers need 11 sheets, and charging them
            # once promised a layer the insert cannot carry.
            step = dunnage.layer_step_mm(extent, pitch, clearance,
                                         grid=in_plane)
            pitch_fit = (pitch[0], pitch[1], step)
            inner = (asset.inner[0], asset.inner[1], asset.inner[2] - dead)
            count, grid_counts, limited_by = lattice_count(
                extent, pitch_fit, inner, part_kg,
                getattr(asset, "max_weight_kg", 0.0),
            )
            if count:
                upper = count
                if pose.voxel_mm > 0:
                    v = pose.voxel_mm
                    upper = max(count, lattice_count(
                        tuple(e - v for e in extent),
                        tuple(p - v for p in pitch_fit),
                        inner, part_kg, getattr(asset, "max_weight_kg", 0.0),
                    )[0])
                out.append(Layout(asset.name, pose.label, count, grid_counts,
                                  tuple(round(v, 2) for v in extent),
                                  tuple(round(v, 2) for v in pitch), limited_by,
                                  turned=bool(turned),
                                  silhouette=silhouette, count_upper=upper))
    out.sort(key=lambda l: -l.count)
    return out


def rank_catalogue(mesh: trimesh.Trimesh, candidates, assets,
                   part_kg: float = 0.0, top_n: int = 2,
                   voxel_mm: float = VOXEL_MM,
                   clearance_mm: float = DEFAULT_CLEARANCE_MM,
                   stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM,
                   poses=None) -> list:
    """Best layout per asset across the whole catalogue, best first.

    One entry per asset -- the engineer compares containers, not the 8 poses of
    one container. `top_n` is the PLANNING §6 result set (top 2 catalogue,
    alongside one custom design from `synthesis`).

    `assets` are `catalogue.Container`s or anything with `.name`, `.inner` and
    `.max_weight_kg`. Pass `poses` from a previous `measure_poses` to skip
    re-voxelising -- `engine.solve` needs the same poses for the custom box.
    """
    if poses is None:
        poses = measure_poses(mesh, candidates, voxel_mm, clearance_mm,
                              stack_clearance_mm)
    best = []
    for asset in assets:
        layouts = layouts_for(poses, asset, part_kg)
        if layouts:
            best.append(layouts[0])
    # Ties on count go to the SMALLER box. Equal parts-per-box is NOT equal
    # value: the truck is volume-limited (`truck.limited_by` says so itself),
    # so a bigger crate holding the same count ships strictly fewer parts.
    # Measured on the TRW wheel over real HTTP -- PLS12103 ties PLS12803 at 48
    # per box and, on a stable sort, was ranked second by nothing but its
    # position in the catalogue: 1728 parts/truck against 2304, a 25% loss
    # presented as the runner-up recommendation. This only reorders equals; it
    # can never change which count wins. `outer` falls back to `inner` because
    # this function accepts anything with .name/.inner/.max_weight_kg.
    vol = {}
    for asset in assets:
        d = getattr(asset, "outer", None) or asset.inner
        vol[asset.name] = d[0] * d[1] * d[2]
    best.sort(key=lambda l: (-l.count, vol[l.asset_name]))
    return best[:top_n] if top_n else best


def nest(mesh: trimesh.Trimesh, candidates, asset,

         voxel_mm: float = VOXEL_MM,
         clearance_mm: float = DEFAULT_CLEARANCE_MM,
         stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM,
         part_kg: float = 0.0) -> list:
    """Rank every (resting pose x in-plane rotation) for one asset. Best first.

    `candidates` are `geometry.OrientationCandidate`s; `asset` needs `.name`,
    `.inner` and `.max_weight_kg` (`ground_truth.Asset` fits).

    In-plane rotation is the two 90-degree footprint assignments only, per the
    PLANNING §4 angle ladder. Mirrored/interleaved pairs are the next rung and
    are deliberately not here -- see the module note in tests/test_nesting.py.

    Single-asset convenience wrapper. Use `rank_catalogue` for more than one
    asset: it measures each pose once instead of once per asset.
    """
    return layouts_for(
        measure_poses(mesh, candidates, voxel_mm, clearance_mm,
                      stack_clearance_mm),
        asset, part_kg,
    )
