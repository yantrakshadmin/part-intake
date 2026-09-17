"""Phase 2 nesting engine: solve for the lattice pitch, not the bounding box.

Both shipped proposals are regular lattices of one part (Mubea 4x10, TRW 3x2x8),
so the whole problem is: how close can two copies of this part sit on each axis?
Not "how many boxes fit in a box".

    occupancy(mesh, pose)  ->  3D boolean grid
    min_pitch(grid, axis)  ->  closest collision-free spacing on that axis, mm
    lattice_count(...)     ->  parts per asset
    lattice_check(...)     ->  is that lattice clear on its MIXED vectors too?

`min_pitch` answers one axis at a time and the lattice contains every
(i.pL, j.pB, k.pH); three 1-D clearances are not a clear 3-D lattice. On a
24-file sweep 8 parts collide on an offset like (0,1,1) or (1,1,0): FIVE are
real crashes (rear shroud, front fairing, headstock cover, visor, ZB 3000),
one is ambiguous line contact (motor cover, 2.35), one is the deck-proven YXA
bar grazing at 1.04 -- and the eighth, Y2V_YK9_Rack, collides only on an
offset its winning lattice does not contain, so it is a phantom and is never
enumerated. Hence a contact tolerance, not a strict rule -- see
`DIAG_FATAL_RATIO`.

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
    # `nesting.lattice_check` on THIS layout's own grid and pitch: the 3-D
    # lattice vectors `min_pitch` never looks at (it slides one axis at a
    # time). None for a pose measured from bare numbers, which carries no
    # raster. `count`, `grid` and `pitch_lbh` above are already the REPAIRED
    # ones when the verdict is "repaired" -- the crash is not shipped and then
    # annotated, it is fixed, and the drawing reads the same fields it always
    # did. Declared on `schemas.LayoutOut` in the same edit or pydantic drops
    # it silently (hard rule 9).
    lattice_check: dict | None = None
    # `retention.retention` on this layout: free stand, 8-direction drift,
    # the friction freeze per layer and the `compact()` jam test (T4). None
    # for a bare-number pose, same as `lattice_check`. Declared on
    # `schemas.LayoutOut` in the same edit or pydantic drops it silently
    # (hard rule 9).
    retention: dict | None = None
    # T3 loadability. `pitch_sil` is the closest spacing of the PLAN
    # SILHOUETTES per floor axis and `delta_proj` is `pitch_sil - pitch_lbh`
    # floored at zero: zero means the shadows are disjoint and a straight
    # descent is provably clear, above zero means the parts overhang and the
    # count exists only because their z-profiles differ across the overlap.
    # `load_path` is `loadability.load_path` -- kind straight|oblique|tilt and
    # the direction it clears on. A layout with kind "none" is REFUSED in
    # `layouts_for` and never reaches here. None for a bare-number pose, same
    # as `lattice_check`. Declared on `schemas.LayoutOut` in the same edit or
    # pydantic drops them silently (hard rule 9).
    pitch_sil: tuple | None = None
    delta_proj: tuple | None = None
    load_path: dict | None = None

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
    return _voxelise(mesh, transform, voxel_mm)[0]


def _voxelise(mesh: trimesh.Trimesh, transform, voxel_mm: float) -> tuple:
    """`occupancy`, plus the grid ORIGIN in posed-mesh mm. -> (grid, origin)

    trimesh snaps the grid origin to a multiple of the pitch, so cell (i, j, k)
    spans `origin + (i, j, k) * voxel_mm` to one voxel further. `Raster` needs
    that to turn an overlapping cell back into a millimetre box it can
    re-voxelise finer; `occupancy` itself never did, which is why this is a
    separate function and not a second return value on the public one.
    """
    m = mesh.copy()
    if transform is not None:
        m.apply_transform(np.asarray(transform, dtype=float))
    vg = m.voxelized(pitch=voxel_mm, method="subdivide")
    return (np.asarray(vg.matrix, dtype=bool),
            tuple(float(v) for v in np.asarray(vg.transform)[:3, 3]))


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


# Voxel edge for the collision re-check. Halving the raster multiplies the
# shared-cell count by ~8 for a solid interpenetration, ~4 for a surface patch,
# ~2 for a line contact and ~1 for a tangency that is only there because the
# coarse raster rounded outward.
FINE_MM = 2.0

# Above this ratio a multi-axis lattice collision is a real crash, below it the
# two copies are only grazing and the design ships.
#
# CALIBRATION -- these are open SURFACE shells (CLAUDE.md: no solids), so a real
# interpenetration shows up as a surface patch and scales ~4x, not the 8x a
# filled solid would give. Measured 2026-09-16 over 24 customer files, cells at
# 2mm / cells at 4mm on the offset that collides:
#
#   rear shroud     3.16   front fairing 3.43   headstock cover 3.06
#   visor           3.12   ZB 3000 M2    2.70      -> real crashes
#   motor cover     2.35                           -> line contact, ambiguous
#   YXA stabiliser  1.04                           -> tangency, and this design
#                                                     SHIPPED 40/PLS12801
#
# 2.0 is the gap between the bar and everything else, with the motor cover on
# the crash side of it. A strict "any shared cell fails" rule takes the bar to
# 30 and breaks CLAUDE.md's one non-negotiable fact.
DIAG_FATAL_RATIO = 2.0


def _slices(shape, off):
    """The two basic-slicing index tuples that align a grid with itself at `off`.

    -> (a, b) such that `grid[a] & grid[b]` is the overlap of the part with a
    copy translated by `off` voxels, signed, on all three axes at once. Views
    only -- a range index would copy the grid (see `min_pitch.clear_at`).
    `grid[a]`'s local index l is global index `l + a[axis].start`.
    None when the translated copy is clear of the bounding box outright.
    """
    a, b = [], []
    for n, d in zip(shape, off):
        if abs(d) >= n:
            return None
        a.append(slice(d, n) if d >= 0 else slice(0, n + d))
        b.append(slice(0, n - d) if d >= 0 else slice(-d, n))
    return tuple(a), tuple(b)


def _overlap_cells(grid: np.ndarray, off) -> int:
    """Cells shared by `grid` and a copy of itself translated by `off` voxels."""
    sl = _slices(grid.shape, off)
    if sl is None:
        return 0
    return int(np.count_nonzero(grid[sl[0]] & grid[sl[1]]))


class Raster:
    """The occupancy grid a pose was measured from, and the mesh behind it.

    `min_pitch` answers one axis at a time; a 3D lattice also contains every
    mixed vector (i.pL, j.pB, k.pH), and three 1-D clearances do not imply a
    clear lattice (`lattice_check`). Answering the mixed ones needs the grid
    itself, so `measure_poses` hands it on instead of throwing it away.

    Mutable on purpose: the memos below are what keep the check off the solve's
    critical path. `rank_catalogue` scores one pose against ~15 assets, and the
    overlap at a given offset is a property of the POSE, not of the asset.

    ponytail: holds `mesh` + `transform`, not the posed copy -- posing is cheap
    next to voxelising and most poses never need the fine re-check at all, so a
    50MB assembly does not get duplicated once per pose for nothing.
    """

    def __init__(self, grid: np.ndarray, origin, voxel_mm: float,
                 mesh: trimesh.Trimesh, transform):
        self.grid = grid
        self.origin = tuple(float(v) for v in origin)
        self.voxel_mm = float(voxel_mm)
        self._mesh = mesh
        self._transform = transform
        self._tri = None
        self._coarse: dict = {}
        self._fine: dict = {}

    def coarse(self, order) -> np.ndarray:
        """The grid in a footprint order's (L, B, H) axes. A view, no copy."""
        return np.transpose(self.grid, order)

    def cells(self, order, off) -> int:
        """`_overlap_cells` on that view, memoised per (order, offset)."""
        key = (tuple(order), tuple(off))
        got = self._coarse.get(key)
        if got is None:
            got = _overlap_cells(self.coarse(order), off)
            self._coarse[key] = got
        return got

    def triangles(self) -> np.ndarray:
        """The posed mesh as (F, 3, 3) corner points. Built once, on demand.

        The posed mesh itself is not kept -- the fine re-check only ever wants
        triangle corners, and holding one copy per pose of a 50MB assembly to
        re-derive the same array is the cost this class exists to avoid.
        """
        if self._tri is None:
            m = self._mesh.copy()
            if self._transform is not None:
                m.apply_transform(np.asarray(self._transform, dtype=float))
            self._tri = np.asarray(m.triangles, dtype=float)
        return self._tri

    def fine_cells(self, off) -> int:
        """Cells the two copies share at `FINE_MM`, over the 4mm overlap AABB.

        `off` is in POSE axes (x, y, z), voxels of `self.voxel_mm`.

        Only the overlap box is re-voxelised, not the part: the whole mesh at
        2mm is ~8x the cells and ~4x the time of the raster the solve already
        paid for, once per colliding pose -- which is most of the solve again.
        Faces are selected by triangle AABB against the box and its
        counter-translate, so every surface that can land inside the box is
        kept and nothing else is; cells outside the box are then dropped, so a
        face that pokes out cannot inflate the count.
        """
        key = tuple(off)
        if key not in self._fine:
            self._fine[key] = self._compute_fine(off)
        return self._fine[key]

    def _compute_fine(self, off) -> int:
        # Every `return 0` below except the last is a path that should not be
        # reachable from a collision, and each one silently downgrades a crash
        # to "grazing" (ratio 0). They are guards, so they log. The LAST one --
        # no shared cell at 2mm -- is the real answer, not a guard: it is what
        # a pure raster artefact looks like (SX4 headstock cover, (1,0,-2),
        # 4 cells at 4mm and 0 at 2mm).
        sl = _slices(self.grid.shape, off)
        if sl is None:
            logger.warning("fine re-check: offset %s is clear of the bounding "
                           "box, so there was nothing to re-check", off)
            return 0
        idx = np.nonzero(self.grid[sl[0]] & self.grid[sl[1]])
        if not len(idx[0]):
            logger.warning("fine re-check: offset %s shares no coarse cell -- "
                           "called for a collision that is not there", off)
            return 0
        v = self.voxel_mm
        lo = np.array([self.origin[a] + (int(idx[a].min()) + sl[0][a].start) * v
                       for a in range(3)])
        hi = np.array([self.origin[a] + (int(idx[a].max()) + sl[0][a].start + 1) * v
                       for a in range(3)])
        delta = np.asarray(off, dtype=float) * v          # the offset in mm

        tri = self.triangles()
        if not len(tri):
            logger.warning("fine re-check: posed mesh has no triangles; "
                           "offset %s reported as grazing by default", off)
            return 0
        t_lo, t_hi = tri.min(axis=1), tri.max(axis=1)
        keep = np.zeros(len(tri), dtype=bool)
        for box_lo, box_hi in ((lo, hi), (lo - delta, hi - delta)):
            sel = np.ones(len(tri), dtype=bool)
            for a in range(3):
                sel &= (t_hi[:, a] >= box_lo[a]) & (t_lo[:, a] <= box_hi[a])
            keep |= sel
        if not keep.any():
            logger.warning("fine re-check: no triangle reaches the overlap box "
                           "%s..%s at offset %s; reported as grazing", lo, hi, off)
            return 0
        verts = tri[keep].reshape(-1, 3)
        crop = trimesh.Trimesh(vertices=verts,
                               faces=np.arange(len(verts)).reshape(-1, 3),
                               process=False)
        fvg = crop.voxelized(pitch=FINE_MM, method="subdivide")
        fine = np.asarray(fvg.matrix, dtype=bool)
        f_origin = np.asarray(fvg.transform)[:3, 3]
        f_off = tuple(int(round(c * v / FINE_MM)) for c in off)
        f_sl = _slices(fine.shape, f_off)
        if f_sl is None:
            logger.warning("fine re-check: the %gmm crop is smaller than the "
                           "offset %s, so nothing could overlap in it",
                           FINE_MM, off)
            return 0
        f_idx = np.nonzero(fine[f_sl[0]] & fine[f_sl[1]])
        if not len(f_idx[0]):
            return 0                    # the real answer: a raster artefact
        inside = np.ones(len(f_idx[0]), dtype=bool)
        for a in range(3):
            g = f_idx[a] + f_sl[0][a].start
            inside &= (f_origin[a] + (g + 1) * FINE_MM > lo[a]) \
                & (f_origin[a] + g * FINE_MM < hi[a])
        return int(inside.sum())


def _shift_voxels(pitch_lbh, clearance_lbh, voxel_mm) -> tuple:
    """Lattice pitch in mm -> the shift in voxels the two copies sit at.

    `min_pitch` returns `shift * voxel + clearance`, so the geometry the grid
    can speak about is at `(pitch - clearance) / voxel`. Clearance is air the
    engine ADDED, not something it measured, so taking it back out here tests
    the copies at their touching spacing -- and it is the same integer
    `min_pitch` proved clear on each axis on its own.

    That error runs one way, on purpose: the check runs at the TOUCHING pitch,
    up to `DEFAULT_CLEARANCE_MM` (5mm) tighter on each floor axis than the
    lattice the parts actually sit in, so it can over-report penetration and
    never under-report it. Rounding the real pitch instead is not the safer
    option -- it is a different answer: 145mm / 4mm rounds to a 36-voxel shift
    instead of 35, and the YXA bar's deck-proven (0,1,1) contact disappears
    entirely, verdict "clear", which is exactly the false negative this
    function exists to prevent.
    """
    return tuple(max(1, int(round((p - c) / voxel_mm)))
                 for p, c in zip(pitch_lbh, clearance_lbh))


def _lattice_offsets(shape, shifts, bounds, brick_s: int = 0) -> list:
    """Difference vectors of the lattice, half-space. -> [(voxels, (i, j, k))]

    A lattice of `bounds` = (n_L, n_B, n_H) copies contains the vector
    (i.pL, j.pB, k.pH) for |i| < n_L and so on; anything further apart than the
    part's own bounding box cannot collide, so the enumeration is bounded by
    BOTH. Offsets outside the winning lattice are phantom -- they belong to
    some other asset's grid, not this one -- and are not enumerated at all.

    v and -v test the same pair, so only half the space is walked.

    `brick_s`: a brick bond displaces odd B-rows by `brick_s` voxels along L,
    so an odd `j` carries +-brick_s and the plain (0, j, 0) column vectors stop
    being single-axis. That is why this returns single-axis vectors too -- the
    repair search has to re-test everything, only the base verdict is about the
    mixed ones.
    """
    lim = [min((n - 1) // s, max(b - 1, 0)) if s else 0
           for n, s, b in zip(shape, shifts, bounds)]
    if brick_s:
        lim[0] += 1
    out: dict = {}

    def add(v_l, i, j, k):
        v = (v_l, j * shifts[1], k * shifts[2])
        if v == (0, 0, 0):
            return
        if all(abs(c) < n for c, n in zip(v, shape)):
            out.setdefault(v, (i, j, k))

    for i in range(0, lim[0] + 1):                 # j == 0 plane, half-space
        for k in range(-lim[2] if i else 1, lim[2] + 1):
            add(i * shifts[0], i, 0, k)
    for j in range(1, lim[1] + 1):                 # j > 0: both signs on i, k
        for i in range(-lim[0], lim[0] + 1):
            for k in range(-lim[2], lim[2] + 1):
                if brick_s and j % 2:
                    add(i * shifts[0] + brick_s, i, j, k)
                    add(i * shifts[0] - brick_s, i, j, k)
                else:
                    add(i * shifts[0], i, j, k)
    return sorted(out.items())


def _to_pose_axes(off_lbh, order) -> tuple:
    """(L, B, H) voxel offset -> the pose's own (x, y, z) axes.

    `order` maps pose axis -> layout axis, so undoing it is its inverse
    permutation, which is what `argsort` of a permutation is.
    """
    return tuple(off_lbh[i] for i in np.argsort(order))


def _any_shared_cell(raster, order, shape, shifts, bounds, brick_s: int = 0) -> bool:
    """Strict: does ANY lattice vector put two copies in the same voxel?

    Used only to accept a REPAIR. The tolerance (`DIAG_FATAL_RATIO`) is for
    judging the design the engine measured; once a design is already known to
    crash, the thing that replaces it has to be clean, and a pitch the part
    never proved clear on (the repair grows past `min_pitch`'s answer, and a
    comb part is not monotonic in pitch) has to be re-tested on every vector,
    single-axis ones included.
    """
    for v, _ in _lattice_offsets(shape, shifts, bounds, brick_s):
        if raster.cells(order, v):
            return True
    return False


def lattice_check(raster, order, pitch_lbh, clearance_lbh, fit) -> dict | None:
    """Is the 3-D lattice this layout implies actually collision-free?

    `fit(pitch_lbh, shrink_l_mm)` -> `(count, grid, pitch_fit)` is the caller's
    OWN counting expression (`layouts_for`'s), so the grid this walks and the
    count the engine ships are one expression, never two (CLAUDE.md hard rule
    9). `shrink_l_mm` is the floor length a brick bond gives up.

    Returns the `Layout.lattice_check` payload, or None for a pose measured
    from bare numbers (synthesis), which carries no raster to check.

    Verdicts: "clear" (nothing shared), "grazing" (shared cells, every ratio
    under `DIAG_FATAL_RATIO` -- the YXA bar), "repaired" (a crash, and growing
    one floor pitch clears it), "fatal" (a crash nothing here clears -- see
    `layouts_for`, which drops the layout rather than ranking it).

    `brick_bond` is reported and never applied; see the comment on that branch.
    """
    if raster is None or raster.voxel_mm <= 0:
        return None
    grid = raster.coarse(order)
    _, bounds, pitch_fit = fit(pitch_lbh, 0.0)
    shifts = _shift_voxels(pitch_fit, clearance_lbh, raster.voxel_mm)
    mixed = [(v, ijk) for v, ijk in _lattice_offsets(grid.shape, shifts, bounds)
             if sum(1 for c in ijk if c) >= 2]

    collisions, fatal = [], False
    for v, ijk in mixed:
        cells4 = raster.cells(order, v)
        if not cells4:
            continue
        cells2 = raster.fine_cells(_to_pose_axes(v, order))
        ratio = cells2 / cells4
        is_fatal = ratio >= DIAG_FATAL_RATIO
        fatal = fatal or is_fatal
        collisions.append({"offset": list(ijk), "cells4": cells4,
                           "cells2": cells2, "ratio": round(ratio, 2),
                           "fatal": is_fatal})
    out = {"offsets_tested": len(mixed), "collisions": collisions,
           "verdict": "clear" if not collisions
                      else ("fatal" if fatal else "grazing"),
           "repair": None, "brick_bond": None}
    if not fatal:
        return out

    v_mm = raster.voxel_mm
    branches = []
    # Growing a pitch is also how a row or a column gets DELETED -- the count
    # `fit` returns at the wider pitch already has one fewer of them. There is
    # no separate deletion branch, and there should not be: deleting a row at
    # the old pitch leaves the surviving neighbours still colliding.
    for axis, name in ((1, "pitch_B"), (0, "pitch_L")):
        for step in range(1, grid.shape[axis] - shifts[axis] + 1):
            wider = list(pitch_lbh)
            wider[axis] += step * v_mm
            count, bounds2, p_fit2 = fit(tuple(wider), 0.0)
            if not count:
                break
            s2 = _shift_voxels(p_fit2, clearance_lbh, v_mm)
            if not _any_shared_cell(raster, order, grid.shape, s2, bounds2):
                branches.append({"branch": name, "count": count,
                                 "pitch_lbh": tuple(round(p, 2) for p in wider)})
                break
    # Brick bond: same pitches, odd rows slid along L, costing `s` of the floor
    # length. PARKED -- computed and reported, never shipped.
    #
    # It is the cheapest repair on paper and it is not a design this codebase
    # can draw: `Layout` has nowhere to put a row offset, so `insert_drawing`
    # and `dunnage.bom` would both build the ALIGNED grid the check just
    # proved crashes, and the BOM would report it as a fit (ZB 3000 M2: 74mm
    # of reported slack against a real 54mm overflow). Reporting the number
    # keeps the option visible for the packaging debate; shipping it would
    # ship a drawing that disagrees with its own count (hard rule 9).
    #
    # Unpark it in a drawing ticket that teaches `_place` and `bom` about
    # staggered rows -- then move this into `branches` and delete the comment.
    for s in range(1, shifts[0] + 1):
        if _any_shared_cell(raster, order, grid.shape, shifts, bounds, brick_s=s):
            continue
        count, _, _ = fit(pitch_lbh, s * v_mm)
        if count:
            out["brick_bond"] = {"s_mm": s * v_mm, "count": count}
        break

    if branches:
        branches.sort(key=lambda b: -b["count"])
        out["verdict"] = "repaired"
        out["repair"] = dict(branches[0],
                             runner_up=branches[1]["count"] if len(branches) > 1
                             else 0)
    return out


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
    # The `Raster` the extent and pitch were measured from, so `lattice_check`
    # can ask about the MIXED lattice vectors `min_pitch` cannot see. None for
    # bare-number poses. Out of `eq`/`repr`: it holds a numpy grid, and a
    # frozen dataclass would otherwise compare it elementwise and raise.
    raster: object | None = field(default=None, compare=False, repr=False)

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
        grid, origin = _voxelise(mesh, cand.rotation_matrix, voxel_mm)
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
            # Free -- the grid is already built, and `Raster` keeps the mesh by
            # reference, not by copy. `lattice_check` needs both.
            raster=Raster(grid, origin, voxel_mm, mesh, cand.rotation_matrix),
        ))
        logger.debug("pose %r extent=%s pitch=%s", cand.label,
                     poses[-1].extent, poses[-1].pitch)
    return poses


def layouts_for(poses, asset, part_kg: float = 0.0,
                surface_class: str | None = None) -> list:
    """Score already-measured poses against one asset. Best first.

    `surface_class` is `PartProfile.surface_class` (raw|painted|ecoat|class_a)
    and only feeds the T4 retention friction coefficient; None means the
    default table's raw row, and the payload says so. `part_kg` is the same
    user-supplied `PartProfile.weight_kg` the weight cap uses -- retention
    never derives a mass from the CAD (hard rule 4).

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
    from . import retention as retention_mod        # ditto (T4)
    from . import loadability                       # ditto (T3)
    out = []
    max_kg = getattr(asset, "max_weight_kg", 0.0)
    for pose in poses:
        # `footprint_orders` yields (0,1,2) then (1,0,2); index 1 IS the turn.
        for turned, (extent, pitch, silhouette, clearance) in enumerate(
                pose.footprint_orders()):

            def fit(try_pitch, shrink_l_mm=0.0, _e=extent, _c=clearance):
                """One candidate in-plane pitch, fully scored.

                -> (count, grid, pitch_fit, limited_by, inner)

                THE counting expression for this (pose x asset): the ranked
                number, the quantisation ceiling, and every pitch
                `lattice_check` tries for a repair all come through here, so
                the grid the check walks can never be a re-derivation of the
                grid the count came from (CLAUDE.md hard rule 9).

                `shrink_l_mm` is floor length given up along L -- what a brick
                bond costs, because its odd rows start that much further in.
                Only `lattice_check`'s PARKED brick branch passes it, to price
                an option it never ships; nothing here applies it.
                """
                inner_l = asset.inner[0] - shrink_l_mm
                # F11: the insert -- and so the height each layer costs --
                # turns on whether the parts interleave IN PLAN, which needs
                # the in-plane grid. One flat layer in the real footprint is
                # that grid, from this same function rather than a second copy
                # of the formula.
                #
                # Geometric grid on purpose: no `part_kg`, so the weight cap
                # cannot shrink it here. The cap only ever REMOVES parts from a
                # layer, and a smaller in-plane grid can only turn the
                # interleave off, so this over-reserves height at worst.
                flat, in_plane, _ = lattice_count(
                    _e, try_pitch, (inner_l, asset.inner[1], _e[2]))
                in_plane = in_plane if flat else (1, 1, 1)
                dead = dunnage.dead_height_mm(_e, try_pitch, _c, grid=in_plane)
                # Layer separators that the parts do NOT nest into are part of
                # the step, not of `dead`: 10 layers need 11 sheets, and
                # charging them once promised a layer the insert cannot carry.
                step = dunnage.layer_step_mm(_e, try_pitch, _c, grid=in_plane)
                p_fit = (try_pitch[0], try_pitch[1], step)
                inner = (inner_l, asset.inner[1], asset.inner[2] - dead)
                n, g, limited = lattice_count(_e, p_fit, inner, part_kg, max_kg)
                return n, g, p_fit, limited, inner

            count, grid_counts, pitch_fit, limited_by, inner = fit(pitch)
            if not count:
                continue
            # The mixed lattice vectors. `min_pitch` cleared each axis on its
            # own; three 1-D clearances are not a clear 3-D lattice.
            check = lattice_check(pose.raster, (1, 0, 2) if turned else (0, 1, 2),
                                  pitch, clearance,
                                  lambda try_p, shrink=0.0: fit(try_p, shrink)[:3])
            if check and check["verdict"] == "fatal":
                # Nothing clears it, so there is no design here to offer. A
                # crash MUST NOT be ranked: the review found this branch
                # shipping 70,400 interpenetrating parts as the top answer,
                # with a correct-looking count, a drawing, a BOM and no reason
                # attached. An empty result set is visibly a non-answer; a
                # confident wrong one is not.
                logger.warning(
                    "%s / %r%s: lattice collides on %s at pitch %s and no "
                    "repair clears it -- layout dropped",
                    asset.name, pose.label, " turned" if turned else "",
                    [c["offset"] for c in check["collisions"] if c["fatal"]],
                    tuple(round(v, 1) for v in pitch))
                continue
            repair = (check or {}).get("repair")
            if repair:
                # A crash is repaired, not annotated: every number below -- and
                # so the drawing, the BOM and the truck fit downstream -- is
                # the repaired lattice's. `lattice_check` says which branch.
                # Only a pitch branch ever gets here (the brick bond is parked),
                # so the lattice stays the aligned grid the drawing can build.
                pitch = tuple(repair["pitch_lbh"])
                count, grid_counts, pitch_fit, limited_by, inner = fit(pitch)
                if not count:
                    continue
            # T4: does the design HOLD the parts once it fits them? Same
            # raster, same pitch, same grid as the count above (hard rule 9);
            # the dunnage family decides the face the part rests on, so it
            # comes from `dunnage.archetype_for` rather than a second guess.
            held = retention_mod.retention(
                pose.raster, (1, 0, 2) if turned else (0, 1, 2),
                pitch_fit, grid_counts,
                family=dunnage.archetype_for(extent, pitch, grid_counts,
                                             clearance),
                surface_class=surface_class, weight_kg=part_kg or None)
            # T3: can one part be PUT INTO that lattice? Same raster, same
            # (repaired) pitch, same grid and the same `inner` the count came
            # out of -- hard rule 9. `kind == "none"` is a refusal, not a
            # footnote.
            path = loadability.load_path(
                pose.raster, (1, 0, 2) if turned else (0, 1, 2),
                pitch_fit, grid_counts, inner, clearance)
            if path and path["kind"] == "none":
                # Same discipline as a fatal lattice above: a design with no
                # way in is not a design. `Layout` is only built for layouts
                # that ship, so there are no `reasons` to append the string
                # to -- it goes to the log and into `load_path["reason"]`.
                logger.warning(
                    "%s / %r%s: no load path -- delta_proj %s at pitch %s, "
                    "%d probes and the tilt rung all blocked by the "
                    "already-placed neighbours -- layout dropped",
                    asset.name, pose.label, " turned" if turned else "",
                    path["delta_proj"], tuple(round(v, 1) for v in pitch_fit),
                    path["probes"])
                continue
            upper = count
            if pose.voxel_mm > 0:
                v = pose.voxel_mm
                upper = max(count, lattice_count(
                    tuple(e - v for e in extent),
                    tuple(p - v for p in pitch_fit),
                    inner, part_kg, max_kg,
                )[0])
            out.append(Layout(asset.name, pose.label, count, grid_counts,
                              tuple(round(v, 2) for v in extent),
                              tuple(round(v, 2) for v in pitch), limited_by,
                              turned=bool(turned),
                              silhouette=silhouette, count_upper=upper,
                              lattice_check=check, retention=held,
                              pitch_sil=tuple(path["pitch_sil"]) if path else None,
                              delta_proj=tuple(path["delta_proj"]) if path else None,
                              load_path=path))
    out.sort(key=lambda l: (-l.count, load_rank(l)))
    return out


_KIND_RANK = {"straight": 0, "oblique": 1, "tilt": 2}


def load_rank(layout) -> tuple:
    """Tie-break for equal counts: the easier load wins.

    Pass 1 (strict sweep) before pass 2 (fine-raster tolerance), then straight
    descent before oblique before tilt. Without this a stable sort let pose
    order decide, and the two-pass load path un-refused earlier poses on ZB
    3000 so FLC12102's 27 silently changed from 'Alternative 1' (straight,
    pass 1) to 'Largest face down' (oblique, pass 2) -- same count, different
    drawing, insert and BOM. A bare-number layout carries no load_path and
    ranks as a pass-1 straight drop.
    """
    path = layout.load_path or {}
    return (path.get("pass", 1), _KIND_RANK.get(path.get("kind", "straight"), 3))


def rank_catalogue(mesh: trimesh.Trimesh, candidates, assets,
                   part_kg: float = 0.0, top_n: int = 2,
                   voxel_mm: float = VOXEL_MM,
                   clearance_mm: float = DEFAULT_CLEARANCE_MM,
                   stack_clearance_mm: float = DEFAULT_STACK_CLEARANCE_MM,
                   poses=None, surface_class: str | None = None) -> list:
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
        layouts = layouts_for(poses, asset, part_kg, surface_class)
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
         part_kg: float = 0.0, surface_class: str | None = None) -> list:
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
        asset, part_kg, surface_class,
    )
