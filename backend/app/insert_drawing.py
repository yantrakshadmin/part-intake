"""The insert drawing: every dunnage component at its TRUE position in the
asset, the dunnage translucent so the parts inside it stay visible.

Drawn the way the customer decks draw it (TP_TRW slide 4): a translucent
pocket-tray stack on a base, inside the box, every layer countable and a part
visible in each one. An opaque stack is a striped block -- only the top layer
reads -- and that was the whole legibility problem.

Ported from the working prototype (`build_gif3d.py`), whose projection and
painter are kept unchanged in substance because the reasons they are right
were the expensive part:

1. Labelled `int16` voxel volumes painted far->near by x+y+z. A cuboid
   carrying a single depth key gets painted in front of a mass it is actually
   behind -- that is how the bottom separator once covered every part in the
   box. No z-buffer, one ordering.
   Parts and dunnage are TWO volumes because one `int16` cell holds one label
   and a part inside a pocket would erase, or be erased by, the tray. Both
   volumes go into ONE globally sorted quad list (`_paint`), so far->near
   ordering still holds across them -- which is exactly what back-to-front
   alpha compositing needs.
2. Camera-facing faces only (+x/+y/+z) as a flat 2D `PolyCollection`, shaded
   per face normal. Edges in the FACE colour inside a component (`"none"`
   leaves antialiasing seams that read as graph paper), a DARK edge only where
   a component meets air or another component, and no edge at all on a
   translucent face -- restroking a 26%-alpha face in its own colour
   composites twice and brings the graph paper back.
3. Labels are spread evenly down a column with dotted leaders back to real
   component instances. Anchoring text to geometry stacks it: the components
   share one stack now, so several would land on the same millimetre.
4. Component counts are asserted against the BOM's own `qty`. The prototype
   drew 8 centre bars where the BOM said 10 (one per layer BOUNDARY, so one
   above the top row) and the picture looked entirely plausible. The counts
   come off the LATTICE, never off `qty`, or the check compares the BOM with
   itself.

Nothing here is retyped: every dimension, qty, spec and basis in the drawing
comes off the `dunnage.Bom` for the same lattice, so the number and the
drawing that illustrates it are one expression (CLAUDE.md hard rule 9).
Anything invented to make a component visible -- the pocket wall, the base
slab, every alpha -- is DRAW-ONLY: it may move pixels, it never appears in
text. Bar width is NOT one of those: the BOM emits it, so the drawing reads
it off the element and the constants here are only a fallback.

Pure render over numbers. No DB, no HTTP, no CAD reading. Meshes arrive in mm
(`geometry.load_unified_mesh` already applied the metre->mm x1000), so nothing
here rescales.

Self-check (renders both archetypes off the ground-truth lattices):
    venv/bin/python -m app.insert_drawing [outdir]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Callable

import numpy as np
import trimesh
from scipy import ndimage

import matplotlib
matplotlib.use("Agg")               # Celery worker, no display. Before pyplot.
import matplotlib.pyplot as plt     # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection   # noqa: E402
from matplotlib.patches import Circle, Rectangle                    # noqa: E402
from matplotlib import font_manager                                # noqa: E402
from PIL import Image                                               # noqa: E402

from . import dunnage                               # noqa: E402
from .nesting import occupancy                      # noqa: E402

logger = logging.getLogger(__name__)

# Voxel edge for the drawing. Coarser than nesting.VOXEL_MM (4mm): this is a
# 14x13in figure, and at 4mm the 1150x750 footprint is 288x188 cells of
# invisible detail and ~10x the quads.
CELL_MM = 12.0

# ---------------------------------------------------------------------------
# DRAW-ONLY constants: the BOM holds no basis for them, so they set how a
# component LOOKS and appear in no label. The bar widths below are the one
# exception -- a FALLBACK for a dimension the BOM normally states itself.
# ---------------------------------------------------------------------------
# Bar width FALLBACKS only. `dunnage.bom` emits the width (dims_mm[1], and it
# takes `centre_bar_w_mm` / `side_bar_w_mm`), so the drawing reads it off the
# element -- a label saying 120 over a 60mm bar is exactly hard rule 9. These
# apply only to a BOM that carries no width for the element at all.
W_SIDE_BAR, W_CENTRE_BAR = 70.0, 60.0
BASE_MM = 70.0           # the black pallet base every deck image sits on
# Pocket wall. The pockets ARE the pitch (376.6 x 367.5 of a 1150 x 750 tray),
# so the real walls are ~10mm -- under one cell, and they vanish into the
# rounding, which draws the tray as a flat sheet. The drawn pocket is inset by
# half of this per side purely so the wall survives rasterising; the label
# states the BOM's own pocket size. The pocket is cut clean THROUGH the tray:
# a drawn floor puts a translucent surface between the camera and the part in
# every one of 8 layers, and 8 of those is an opaque grey wash.
POCKET_WALL_MM = 36.0

# Per-component alpha. Parts are opaque; dunnage is not, or it hides them.
# 0.26 is the prototype's DUNNAGE_ALPHA. Anything that spans the FULL
# footprint (separator sheets, the bottom assembly) is lower still: nine
# sheets at 0.26 leave 7% transmittance and the stack goes solid grey.
A_TRAY, A_BAR, A_SLAB, A_PANEL = 0.30, 0.42, 0.13, 0.16

COS30, SIN30 = np.cos(np.pi / 6), 0.5
C_INK, C_MUTE, C_BASE, C_ACCENT = "#0F172A", "#64748B", "#1E293B", "#D97706"
C_NAME, C_LEADER = "#1E40AF", "#94A3B8"    # component name text; leader lines
C_PART, PART_ALT = "#E8DFA0", "#CBBF77"   # warm part vs cool dunnage:
# the deck's own contrast. Blue parts inside a blue tray read as one mass
# however good the alpha is, which defeats drawing them translucent at all.
LABEL0 = 10              # first component label in the voxel volume

# Report-drawing typeface. The UI direction is Fira Sans; matplotlib only has
# what is installed on the host, so fall back rather than emit a warning per
# text call. ponytail: no font file is bundled -- ship one in the repo if the
# PDF must look identical on every machine.
SANS = next((n for n in ("Fira Sans", "Fira Sans Condensed", "DejaVu Sans")
             if n in {f.name for f in font_manager.fontManager.ttflist}),
            "sans-serif")

# BOM elements we DELIBERATELY do not draw, and the reason, which goes on the
# label. Narrow on purpose: any other element carrying a size and a qty that
# reaches no geometry is a GAP, not a decision, and `explode_png` warns --
# `_check_undrawn_warns` holds that line. The rod is loose stock with no
# position in the lattice, so there is nothing to place it against.
LABEL_ONLY = {"MS Rod": "not drawn: loose stock, no lattice position"}

FACES = {"z": np.array([(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float),
         "x": np.array([(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)], float),
         "y": np.array([(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)], float)}
SHADE = {"z": 1.0, "x": 0.80, "y": 0.62}    # brightness per face normal
TAG = {"derived": ("#166534", "DERIVED"), "measured": ("#1E40AF", "MEASURED"),
       "pattern": ("#B45309", "PATTERN"), "unknown": ("#B91C1C", "UNKNOWN")}

# ---------------------------------------------------------------------------
# Lift per layer in the exploded view: half a layer pitch, per the ticket --
# capped so the whole explosion never adds more than 60% of the box height.
# ponytail: at half a pitch an interleaving pose (the Mubea bar nests 123mm
# into the layer below) still overlaps; separating those fully needs
# gap >= nest depth, which is a 1900mm-tall figure. Raise EXPLODE_FRAC if a
# reviewer needs full separation more than a readable aspect ratio.
EXPLODE_FRAC, EXPLODE_CAP = 0.5, 0.6


# ---------------------------------------------------------------------------
# Voxels
# ---------------------------------------------------------------------------
def pose_voxels(mesh: trimesh.Trimesh, rotation_matrix,
                cell_mm: float = CELL_MM) -> np.ndarray:
    """Bool occupancy of `mesh` rotated into a resting pose, z up, reseated to
    the origin. Rotation ONLY, then reseat -- do not re-centre.

    `rotation_matrix` is a 3x3 or a 4x4 (`geometry.OrientationCandidate.
    rotation_matrix`); any translation in a 4x4 is irrelevant because the dense
    voxel matrix is seated at the occupied region's own lower corner, which is
    the reseat. Same subdivide voxeliser as the engine measured the pitch with
    -- it works on the open shells every customer file is.
    """
    return occupancy(mesh, _as_4x4(rotation_matrix), voxel_mm=cell_mm)


def _as_4x4(matrix) -> np.ndarray:
    """A 3x3 or 4x4 rotation as a 4x4. The sequence JSON ships the same
    matrix `pose_voxels` posed the part with, in one shape (F4)."""
    r = np.asarray(matrix, dtype=float)
    if r.shape == (3, 3):
        t = np.eye(4)
        t[:3, :3] = r
        r = t
    return r


def _fill(vol: np.ndarray, origin, size, label: int, cell_mm: float,
          over: bool = False, step_vol: np.ndarray | None = None,
          step: int = 0) -> None:
    """Rasterise a mm-space cuboid into the voxel volume.

    `over=False` paints only into empty cells, so a component listed EARLIER
    survives a later one drawn through it (a layer bar lying inside the bottom
    separator assembly is the real case -- the assembly is the nest depth the
    bottom row sits down into). `over=True` with `label=0` erases, which is how
    the tray's pockets are cut.

    `step_vol`/`step`, when given, get the SAME mask this call wrote into
    `vol` (not a fresh `== 0` test on `step_vol` itself, which would read a
    legitimate step index of 0 as "still empty" and let a later component
    overwrite it) -- build_gif's per-cell packing-sequence index.
    """
    lo = [max(0, int(round(a / cell_mm))) for a in origin]
    hi = [max(lo[i] + 1, int(round((origin[i] + size[i]) / cell_mm)))
          for i in range(3)]
    sub = vol[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    mask = np.ones_like(sub, dtype=bool) if over else sub == 0
    sub[mask] = label
    if step_vol is not None:
        step_vol[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]][mask] = step


# ---------------------------------------------------------------------------
# Projection and painter -- unchanged in substance from the prototype
# ---------------------------------------------------------------------------
def _proj(p: np.ndarray) -> np.ndarray:
    """Isometric, camera at (+1,+1,+1), z up. Depth is x+y+z."""
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    return np.stack([(x - y) * COS30, z - (x + y) * SIN30], axis=-1)


def _shift(a: np.ndarray, axis: int, step: int) -> np.ndarray:
    """`a` rolled by `step` along `axis`, with the wrapped face zeroed --
    outside the volume reads as empty, never as the far side of it."""
    out = np.roll(a, step, axis=axis)
    sl = [slice(None)] * 3
    sl[axis] = slice(0, 1) if step > 0 else slice(-1, None)
    out[tuple(sl)] = 0
    return out


def _exposed(vol: np.ndarray, cell_mm: float) -> tuple:
    """Camera-facing faces of a labelled volume.

    -> (quads, depth, label, shade, edge). `edge` marks a face where the
    surface STOPS: no face of the same label continues into the in-plane
    neighbour, give or take one cell along the face normal. That one-cell
    tolerance is the whole rule -- without it every step of a voxelised
    cylinder counts as a boundary and the part comes out knitted. What
    survives is the silhouette against air, the join with another component
    and real creases, which is the deck's line-work; per-voxel edges
    everywhere read as graph paper at these cell sizes.
    """
    quads, depth, label, shade, edge = [], [], [], [], []
    filled = vol > 0
    for axis, key in ((2, "z"), (0, "x"), (1, "y")):
        vis = filled & ~_shift(filled, axis, -1)
        idx = np.argwhere(vis)
        if not len(idx):
            continue
        face = np.where(vis, vol, 0)        # label of the drawn face, 0 = none
        rim = np.zeros(vol.shape, dtype=bool)
        for plane in (a for a in range(3) if a != axis):
            for step in (-1, 1):
                nb = _shift(face, plane, step)
                goes_on = np.zeros(vol.shape, dtype=bool)
                for off in (-1, 0, 1):      # the surface may step by one cell
                    goes_on |= (nb if off == 0 else _shift(nb, axis, off)) == face
                rim |= ~goes_on
        quads.append(_proj((idx[:, None, :] + FACES[key][None]) * cell_mm))
        depth.append(idx.sum(axis=1))
        label.append(vol[idx[:, 0], idx[:, 1], idx[:, 2]])
        shade.append(np.full(len(idx), SHADE[key]))
        edge.append(rim[idx[:, 0], idx[:, 1], idx[:, 2]])
    if not quads:
        return (np.empty((0, 4, 2)), np.empty(0), np.empty(0, int),
                np.empty(0), np.empty(0, bool))
    return tuple(np.concatenate(a) for a in (quads, depth, label, shade, edge))


def _paint(ax, vols: list, rgba_for, cell_mm: float, lw: float = 0.35) -> int:
    """ONE far->near ordering across ALL volumes. Returns the quad count.

    Alpha compositing needs strictly back-to-front, and the depth key is per
    CELL, so the volumes cannot be painted one after another -- they are
    concatenated and sorted together. A tie in depth resolves in favour of the
    LATER volume, so pass the opaque parts first and the translucent dunnage
    after it: on an equal-depth cell the tint lands over the part, which is
    what the deck shows.
    """
    got = [_exposed(v, cell_mm) for v in vols]
    got = [g for g in got if len(g[0])]
    if not got:
        return 0
    q, d, lab, sh, eg = (np.concatenate([g[i] for g in got]) for i in range(5))
    order = np.argsort(d, kind="stable")
    q, lab, sh, eg = q[order], lab[order], sh[order], eg[order]
    fc = rgba_for(lab)
    fc[:, :3] *= sh[:, None]                    # shade by face normal
    ec = fc.copy()
    # Inside a component: edge in the face colour, closing the antialiasing
    # seams between adjacent quads. On a translucent face that second stroke
    # composites over the first, so those get no edge -- at alpha 0.26 the
    # seam error is ~0.02 and invisible, a doubled stroke is not.
    ec[~eg & (fc[:, 3] < 1.0), 3] = 0.0
    # Silhouettes and creases: the deck's black line-work at every boundary.
    # A darkened face colour, which reads as line-work on the light card both
    # drawings sit on.
    ec[eg, :3] = fc[eg, :3] * 0.38
    ec[eg, 3] = np.clip(fc[eg, 3] * 2.4, 0.5, 1.0)
    ax.add_collection(PolyCollection(q, facecolors=fc, edgecolors=ec,
                                     linewidths=lw, zorder=2))
    return len(q)


def _draw_asset(ax, inner, mark_z: float | None) -> None:
    """The asset itself: a base slab plus the inner box as a wireframe.

    "Does the stack fit inside the box" is a question the drawing cannot
    answer with the box off-screen. `mark_z` draws a build-height dimension
    line just outside the box's left edge -- solid, ticked at the floor, the
    build height and the inner height -- so the headroom under the lid is the
    gap between the last two ticks.
    """
    l, b, h = inner
    slab = [[(0, 0, 0), (l, 0, 0), (l, b, 0), (0, b, 0)],               # +z
            [(l, 0, -BASE_MM), (l, b, -BASE_MM), (l, b, 0), (l, 0, 0)],  # +x
            [(0, b, -BASE_MM), (l, b, -BASE_MM), (l, b, 0), (0, b, 0)]]  # +y
    base = np.array([matplotlib.colors.to_rgba(C_BASE)] * 3)
    base[:, :3] *= np.array([SHADE["z"], SHADE["x"], SHADE["y"]])[:, None]
    ax.add_collection(PolyCollection([_proj(np.array(f, float)) for f in slab],
                                     facecolors=base, edgecolors=base,
                                     linewidths=0.6, zorder=0))

    # index = 4*(x==l) + 2*(y==b) + (z==h)
    p = _proj(np.array([(x, y, z) for x in (0.0, l) for y in (0.0, b)
                        for z in (0.0, h)], float))
    wire = [(0, 1), (2, 3), (4, 5), (6, 7),             # verticals
            (0, 2), (2, 6), (6, 4), (4, 0),             # at the base
            (1, 3), (3, 7), (7, 5), (5, 1)]             # at the lid
    ax.add_collection(LineCollection([p[list(e)] for e in wire],
                                     colors=C_INK, linewidths=0.9, alpha=0.5,
                                     zorder=3))
    if mark_z is not None:
        off = np.array([-40.0, 0.0])   # just outside the (0,0) corner edge
        p0, p1 = (_proj(np.array([0.0, 0.0, z])) + off for z in (0.0, h))
        ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=C_ACCENT, lw=1.0,
                zorder=4)
        for z in (0.0, mark_z, h):
            t = _proj(np.array([0.0, 0.0, z])) + off
            ax.plot([t[0], t[0] + 18], [t[1], t[1]], color=C_ACCENT, lw=1.0,
                    zorder=4)


# ---------------------------------------------------------------------------
# Rows: one per BOM element, plus one for the parts themselves
# ---------------------------------------------------------------------------
@dataclass
class _Row:
    name: str | None                # BOM element name; None = the parts row
    colour: str
    # geo() -> (solids, voids); each cuboid is (origin_mm, size_mm) at its TRUE
    # position in the asset. Voids are zeroed after the solids (the tray's
    # pockets). None = label only, or the parts row, which is rasterised from
    # the pose voxels instead.
    geo: Callable | None
    alpha: float = 1.0
    z0: float = 0.0                 # parts row: z of the bottom layer

    @property
    def is_parts(self) -> bool:
        return self.name is None

    @property
    def drawn(self) -> bool:
        return self.is_parts or self.geo is not None


def _bar_w(e, fallback: float) -> float:
    """Bar width off the BOM element (`dims_mm` is (L, width, H) for a bar).

    The drawn width and the labelled width must be one expression; the
    constant is the fallback for a BOM that states no width.
    """
    w = e.dims_mm[1] if len(e.dims_mm) > 1 else None
    return float(w) if w is not None else fallback


def _origins(extent, pitch, grid, inner) -> tuple:
    """Lattice origin so the occupied span is centred in the asset footprint."""
    return tuple((inner[a] - (extent[a] + (grid[a] - 1) * pitch[a])) / 2
                 for a in (0, 1))


def _bar_and_rod_rows(el: dict, extent, pitch, grid, inner, cell_mm) -> list:
    """Mubea archetype. Bars run ACROSS the breadth (their stated length is the
    asset inner breadth), so they are spaced along the length.

    Heights are the real ones. A bar sits in a layer BOUNDARY at k x pitch_H
    and the parts nest down into it -- that is why `dunnage` gives it zero net
    height -- so the bottom bar lies inside the bottom separator assembly and
    the parts start at z=0. The sum comes to `bom.stack_height_mm`.
    """
    inner_l, inner_b = inner[0], inner[1]
    ox, _oy = _origins(extent, pitch, grid, inner)
    layers = grid[2]
    stack_top = extent[2] + (layers - 1) * pitch[2]     # == stack_height_mm

    def top_sep() -> tuple:
        l, b, h = el["Top Separator"].dims_mm
        return [(((inner_l - l) / 2, (inner_b - b) / 2, stack_top - h),
                 (l, b, h))], []

    # How many of each we draw comes off the LATTICE, never off `element.qty`
    # -- otherwise the count check below compares the BOM with itself and the
    # prototype's 8-instead-of-10 centre bars would have passed it.
    def centre_bars() -> tuple:
        e = el["Top Center Bar"]
        length, h = e.dims_mm[0], e.dims_mm[2]
        w = _bar_w(e, W_CENTRE_BAR)                 # the BOM's width, not ours
        x = ox + extent[0] / 2 - w / 2
        # One per layer BOUNDARY, so one above the top row: layers + 1.
        return [((x, 0.0, k * pitch[2]), (w, length, h))
                for k in range(layers + 1)], []

    def side_bars() -> tuple:
        e = el["Top Side Bar"]
        length, h = e.dims_mm[0], e.dims_mm[2]
        w = _bar_w(e, W_SIDE_BAR)
        x0, x1 = ox, ox + extent[0] - w
        out = []
        for k in range(layers):                     # 2 per layer, one each side
            out.append(((x0, 0.0, k * pitch[2]), (w, length, h)))
            out.append(((x1, 0.0, k * pitch[2]), (w, length, h)))
        return out, []

    def bottom_sep() -> tuple:
        l, b, h = el["Bottom Separator Sheet Assy"].dims_mm
        return [((0.0, 0.0, 0.0), (l, b, h))], []

    def side_seps() -> tuple:
        # dims are (L=inner breadth, B=inner height, H=thickness): vertical
        # panels, not a layer in the stack.
        b_span, h_span, t = el["Side Separator"].dims_mm
        return [((0.0, 0.0, 0.0), (t, b_span, h_span)),
                ((inner_l - t, 0.0, 0.0), (t, b_span, h_span))], []

    return [
        _Row("Top Separator", "#7DD3FC", top_sep, A_SLAB),
        _Row("Top Center Bar", "#D97706", centre_bars, A_BAR),
        _Row("Top Side Bar", "#F59E0B", side_bars, A_BAR),
        _Row(None, C_PART, None),                        # the parts
        _Row("Bottom Separator Sheet Assy", "#94A3B8", bottom_sep, A_SLAB),
        # Last, so the full-height panels have nothing under them to cut
        # through: `_fill` paints only into empty cells.
        _Row("Side Separator", "#A3A3A3", side_seps, A_PANEL),
    ]


def _pocket_tray_rows(el: dict, extent, pitch, grid, inner, cell_mm) -> list:
    """TRW archetype. The tray is a slab with the pocket cells CUT out of it: a
    featureless slab would hide the only thing that makes it a tray.

    Real heights: pocket depth + layer sheet == the vertical pitch, so layer k
    is a sheet at k x pitch_H with its tray sitting straight on top. The part
    is 135 tall in a 117 pocket, so it pokes 18mm through the sheet above --
    which is what the deck's own picture shows (image9: the hub stands proud of
    the grey sheets capping the pockets).
    """
    # F11 landed a third archetype, "layer_sheets": an interleaved-in-plane
    # pose gets the tray's sheets and NO tray, because no wall fits between
    # parts that overlap in plan. `_place` routes everything that is not
    # bar_and_rod here, so without this guard `next(...)` raised StopIteration
    # and the worker swallowed it into `drawing_url: None` -- silently, on the
    # exact SX4-cover pose F9/F15 exist for. One guard here fixes the exploded
    # PNG, the GIF/sequence and the ortho view together, since all three place
    # through `_place`.
    tray = next((e for e in el.values() if e.matrix), None)
    sheet = next(e for e in el.values()
                 if e.matrix is None and e is not tray)
    ox, oy = _origins(extent, pitch, grid, inner)
    sheet_h = sheet.dims_mm[2]
    layers = grid[2]

    def trays() -> tuple:
        l, b, h = tray.dims_mm
        pl, pb, pd = tray.cell_mm
        cols, rows = tray.matrix
        solids, voids = [], []
        for k in range(layers):                     # one insert per layer
            z = k * pitch[2] + sheet_h
            solids.append(((0.0, 0.0, z), (l, b, h)))
            for i in range(cols):
                for j in range(rows):
                    # Pocket centred on where the part actually lands.
                    px = max(0.0, ox + i * pitch[0] - (pl - extent[0]) / 2)
                    py = max(0.0, oy + j * pitch[1] - (pb - extent[1]) / 2)
                    voids.append(((px + POCKET_WALL_MM / 2,
                                   py + POCKET_WALL_MM / 2, z),
                                  (pl - POCKET_WALL_MM, pb - POCKET_WALL_MM, pd)))
        return solids, voids

    def sheets() -> tuple:
        l, b, h = sheet.dims_mm
        # One per layer, plus one capping the top layer's pockets.
        return [((0.0, 0.0, k * pitch[2]), (l, b, h))
                for k in range(layers + 1)], []

    rows = [_Row(None, C_PART, None, 1.0, z0=sheet_h)]
    if tray is not None:
        rows.append(_Row(tray.name, "#38BDF8", trays, A_TRAY))
    rows.append(_Row(sheet.name, "#94A3B8", sheets, A_SLAB))
    return rows


@dataclass
class _Label:
    """One spec-column entry: short label, qty, ONE dimension line, and
    whatever the BOM element says about itself that is not a number.

    Every number in `qty`, `dim` and `extras` is formatted from a BOM element
    or a layout field -- `_check_spec_column` asserts exactly that, so nothing
    here may be built from a constant of this module.
    """
    title: str
    basis: str
    qty: str
    dim: str
    extras: list


def _labels(rows: list, el: dict, extent, pitch, grid, count: int) -> list:
    """One `_Label` per row, straight off the BOM. Nothing retyped."""
    out = []
    for row in rows:
        if row.name is None:
            out.append(_Label(
                title="Part",
                basis="measured",
                qty="qty %d" % count,
                dim="%g x %g x %g mm" % tuple(extent),
                extras=["%d x %d per layer, pitch %g / %g mm"
                        % (grid[0], grid[1], pitch[0], pitch[1]),
                        "%d layers at %g mm step" % (grid[2], pitch[2]),
                        "pitch %g < width %g: layers interleave"
                        % (pitch[1], extent[1]) if pitch[1] < extent[1] else
                        "pitch %g > width %g: clearance / pocket wall"
                        % (pitch[1], extent[1])]))
            continue
        e = el[row.name]
        extras = []
        if e.matrix:
            extras.append("%d x %d pockets, %g x %g x %g mm each"
                          % (*e.matrix, *e.cell_mm))
        if row.geo is None:
            extras.append(LABEL_ONLY.get(e.name, "not drawn: nothing to draw"))
        if e.spec:
            extras.append(e.spec)
        if e.unknown:
            extras.append("needs deck: " + ", ".join(e.unknown))
        out.append(_Label(
            title=e.name, basis=e.basis,
            qty="qty %s" % (e.qty if e.qty is not None else "?"),
            dim="%s mm" % e.size if e.size else "size: not derivable",
            extras=extras))
    return out


def _explode_gap(pitch_h: float, inner_h: float, layers: int) -> float:
    """How far each layer rises above the one below it in the exploded PNG."""
    return min(EXPLODE_FRAC * pitch_h, EXPLODE_CAP * inner_h / max(1, layers))


def _explode(vol: np.ndarray, step_vol: np.ndarray, gap_cells: int) -> np.ndarray:
    """`vol` with every cell lifted by `(step // 2) * gap_cells` along z.

    Grouped by the per-cell BUILD STEP `_place` already stamped, never by z
    index: a TRW wheel is 135mm tall in a 120mm pitch, so a z-slice rule cuts
    the gap through the middle of a part and shears it in half. One layer
    assembly (separator sheet, tray and the parts nested in it) shares one
    step pair, so it travels as one object -- which is what makes the layer,
    not the slab, the thing that reads as distinct.

    A pure translation along +z, so `_exposed`'s depth key (x+y+z, recomputed
    from the new indices) stays a correct far->near order for the alpha
    compositing. The groups only ever move further apart, so no cell lands on
    another.
    """
    if gap_cells <= 0:
        return vol
    steps = np.unique(step_vol[vol != 0])
    lift = (steps.astype(int) // 2) * gap_cells
    out = np.zeros(vol.shape[:2] + (vol.shape[2] + int(lift.max()) + 1,),
                   dtype=vol.dtype)
    for s, dz in zip(steps, lift):
        m = (step_vol == s) & (vol != 0)
        x, y, z = np.nonzero(m)
        out[x, y, z + int(dz)] = vol[m]
    return out


# ---------------------------------------------------------------------------
# Placement: the ONE part/dunnage placement expression (hard rule 9 applies to
# the picture too -- explode_png and build_gif both call this, neither one
# re-derives a position).
# ---------------------------------------------------------------------------
def _dun_step(z0: float, parts_z0: float, pitch_h: float, layers: int) -> int:
    """build_gif's step rule for a dunnage cuboid sitting at `z0`.

    `j` = how many parts layers sit fully below `z0`; the cuboid's step is
    `2*j`, one below the parts step (`2*j+1`) of the layer it precedes so it
    always animates in before the parts that land on or in it. `j == layers`
    (nothing below) is the "top" step, after the last layer of parts.
    """
    return 2 * sum(1 for k in range(layers)
                   if parts_z0 + k * pitch_h < z0 - 1e-6)


@dataclass
class _Placement:
    rows: list
    el: dict
    extent: tuple
    pitch: tuple
    inner: tuple
    grid: tuple
    dun: np.ndarray
    prt: np.ndarray
    dun_step: np.ndarray        # same shape as `dun`; per-cell build_gif step
    prt_step: np.ndarray        # same shape as `prt`
    alt: int                    # the parts' alternate label
    top: float
    layers: int
    counts: dict
    # {step index: {row index: instance count}}, dunnage only -- build_gif's
    # caption source. Parts steps are `2k+1`, computed from `layers` directly.
    dun_at_step: dict
    # The same placements in mm, for the JSON build sequence (F4): the voxel
    # volumes above are a raster OF these, so neither can disagree with the
    # other. {step: [(row index, origin_mm, size_mm)]} for the dunnage,
    # {step: [origin_mm]} for the part instances.
    dun_solids_at_step: dict
    parts_at_step: dict


def _place(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid, inner_lbh,
          bom: dunnage.Bom, count: int, cell_mm: float) -> _Placement:
    """Build the labelled part/dunnage volumes AND their per-cell packing-step
    volumes off ONE placement expression.

    Raises AssertionError if the number of cuboids drawn for any element does
    not equal that element's `qty`, or if the parts that actually RASTERISED
    into the volume do not equal the engine's count -- see the header, bug 4.
    Counting the placement loop's trips instead made that second one
    `product(grid) == product(grid)`, which passed on an empty picture.
    """
    extent = tuple(float(v) for v in extent_lbh)
    pitch = tuple(float(v) for v in pitch_lbh)
    # F11: layers step by the BOM's own vertical step, which is the measured
    # pitch PLUS any layer separator the parts do not nest into -- the number
    # `nesting.layouts_for` counted layers with (hard rule 9: one expression).
    # At the raw pitch every layer was drawn into the sheet above it and the
    # insert topped out below its own build height.
    pitch = (pitch[0], pitch[1], bom.layer_step_mm or pitch[2])
    inner = tuple(float(v) for v in inner_lbh)
    grid = tuple(int(v) for v in grid)
    el = {e.name: e for e in bom.elements}

    builder = (_bar_and_rod_rows if bom.archetype == "bar_and_rod"
               else _pocket_tray_rows)
    # An archetype builder offers a row per element it knows how to draw; the
    # BOM decides which of those exist. `bar_and_rod` omits the bottom
    # separator assembly when the pose has no vertical interleave (nest depth
    # 0: there is no depth to provide), and indexing it anyway was a KeyError
    # the worker swallowed into `drawing_url: None` with an empty `warnings`.
    rows = [r for r in builder(el, extent, pitch, grid, inner, cell_mm)
            if r.is_parts or r.name in el]
    drawn_names = {r.name for r in rows}
    for e in bom.elements:                  # e.g. the MS Rod: label only
        if e.name not in drawn_names:
            rows.append(_Row(e.name, "#CBD5E1", None))
            if (e.name not in LABEL_ONLY
                    and e.size is not None and e.qty is not None):
                logger.warning("insert drawing has no geometry for %r "
                               "(size %s, qty %s)", e.name, e.size, e.qty)

    # One volume for the parts, one for the dunnage: an int16 cell holds one
    # label, and a part in a pocket has to survive the tray drawn through it.
    # Two more, same shape, carry the build_gif step each cell was written at.
    top = max(inner[2], bom.build_height_mm)
    shape = (int(np.ceil(inner[0] / cell_mm)),
             int(np.ceil(inner[1] / cell_mm)),
             int(np.ceil(top / cell_mm)) + 2)
    dun, prt, dun_step, prt_step = (np.zeros(shape, dtype=np.int16)
                                     for _ in range(4))
    # Two labels for the parts, alternating on (column + row + layer): a
    # 48-part stack of one colour is a solid blue block. Touching parts (the
    # Mubea pose interleaves in plane, and the TRW parts nest 15mm into the
    # layer below) would also merge into one mass with no boundary between
    # them -- the parity flips between every pair of neighbours, in plane and
    # between layers, so each part keeps its own silhouette and the layers
    # stay countable.
    alt = LABEL0 + len(rows)
    layers = grid[2]
    per_part = int(np.count_nonzero(voxels))    # cells one part should occupy
    cells = 0                                   # cells the parts actually got
    counts: dict = {}
    parts_at_step: dict = {}
    cuboids: list = []          # (volume, row, solids, voids), filled smallest-first
    parts_z0 = next(r.z0 for r in rows if r.is_parts)
    for i, row in enumerate(rows):
        if row.is_parts:
            ox, oy = _origins(extent, pitch, grid, inner)
            n = 0
            for k in range(grid[2]):
                z0 = max(0, int(round((row.z0 + k * pitch[2]) / cell_mm)))
                pstep = 2 * k + 1
                for a in range(grid[0]):
                    for b in range(grid[1]):
                        xa = max(0, int(round((ox + a * pitch[0]) / cell_mm)))
                        y0 = max(0, int(round((oy + b * pitch[1]) / cell_mm)))
                        sub = prt[xa:xa + voxels.shape[0],
                                  y0:y0 + voxels.shape[1],
                                  z0:z0 + voxels.shape[2]]
                        ssub = prt_step[xa:xa + voxels.shape[0],
                                       y0:y0 + voxels.shape[1],
                                       z0:z0 + voxels.shape[2]]
                        src = voxels[:sub.shape[0], :sub.shape[1],
                                     :sub.shape[2]]
                        np.putmask(sub, src,
                                   LABEL0 + i if (a + b + k) % 2 == 0 else alt)
                        np.putmask(ssub, src, pstep)
                        # Read back OUT OF THE VOLUME. Counting loop trips
                        # counted the instances we tried to draw, and
                        # `np.putmask` on a slice that fell outside the volume
                        # writes nothing and says nothing: an all-empty
                        # `voxels` rendered 40 parts' worth of nothing and
                        # asserted clean.
                        got = int(np.count_nonzero(sub[src]))
                        cells += got
                        n += got > 0
                        # mm origin of this instance: the min corner of the
                        # posed part's AABB, the same lattice expression the
                        # cell indices above were rounded from.
                        parts_at_step.setdefault(pstep, []).append(
                            (ox + a * pitch[0], oy + b * pitch[1],
                             row.z0 + k * pitch[2]))
            counts[None] = n
            continue
        if row.geo is None:
            continue
        solids, voids = row.geo()
        counts[row.name] = len(solids)
        cuboids.append((sum(np.prod(sz) for _o, sz in solids), i, solids, voids))

    # Smallest component first. `_fill` paints only into empty cells, so the
    # first one to reach a cell keeps it, and at a 12mm cell the thin part is
    # the one that loses: a 3mm separator sheet and a 117mm tray share a 120mm
    # pitch, which is 10 cells for 11 cells of component. Filling in BOM order
    # left the nine sheets with cells only where the tray's pockets had been
    # cut away -- i.e. underneath the parts, where they cannot be seen -- and
    # every count still asserted, because the counts come off the lattice and
    # not off the pixels. Same for a layer bar lying inside the bottom
    # separator assembly.
    dun_at_step: dict = {}
    dun_solids_at_step: dict = {}
    for _v, i, solids, voids in sorted(cuboids, key=lambda c: c[0]):
        for o, sz in solids:
            dstep = _dun_step(o[2], parts_z0, pitch[2], layers)
            _fill(dun, o, sz, LABEL0 + i, cell_mm, step_vol=dun_step,
                  step=dstep)
            dun_at_step.setdefault(dstep, {})
            dun_at_step[dstep][i] = dun_at_step[dstep].get(i, 0) + 1
            dun_solids_at_step.setdefault(dstep, []).append(
                (i, tuple(float(v) for v in o), tuple(float(v) for v in sz)))
        for o, sz in voids:                 # the tray's pockets, cut out
            _fill(dun, o, sz, 0, cell_mm, over=True)
        if not (dun == LABEL0 + i).any():
            logger.warning("insert drawing: %r is in the BOM but no cell of "
                           "it survives at a %gmm cell", rows[i].name, cell_mm)

    # The check the prototype's plausible-looking picture needed: 8 centre bars
    # where the BOM said 10 (one per layer BOUNDARY) read as fine. Every count
    # here was produced by the lattice, not read off the BOM.
    assert per_part > 0, "the part occupies no voxel at a %gmm cell" % cell_mm
    assert counts.get(None) == grid[0] * grid[1] * grid[2], \
        "rasterised %s of %d part instances into the volume" \
        % (counts.get(None), np.prod(grid))
    if cells != per_part * grid[0] * grid[1] * grid[2]:
        # Every instance landed something, but not all of it: a part clipped
        # at the edge of the volume. Not fatal for the picture; still wrong.
        logger.warning("insert drawing: %d of %d part cells landed (%d "
                       "instances x %d) -- parts clipped by the volume",
                       cells, per_part * np.prod(grid), np.prod(grid),
                       per_part)
    assert grid[0] * grid[1] * grid[2] == count, \
        "grid %s = %d parts, engine says %d" % (grid, np.prod(grid), count)
    for name, n in counts.items():
        if name is None:
            continue
        assert n == el[name].qty, \
            "drew %d %r, BOM says qty %s" % (n, name, el[name].qty)

    return _Placement(rows=rows, el=el, extent=extent, pitch=pitch,
                      inner=inner, grid=grid, dun=dun, prt=prt,
                      dun_step=dun_step, prt_step=prt_step, alt=alt, top=top,
                      layers=layers, counts=counts, dun_at_step=dun_at_step,
                      dun_solids_at_step=dun_solids_at_step,
                      parts_at_step=parts_at_step)


# ---------------------------------------------------------------------------
# The drawing
# ---------------------------------------------------------------------------
def explode_png(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid, inner_lbh,
                bom: dunnage.Bom, asset_name: str, count: int,
                cell_mm: float = CELL_MM) -> bytes:
    """The insert component breakdown as PNG bytes: an EXPLODED view on the
    same light card as `build_gif`, leaders out to a right-hand spec column.

    `bom` is a `dunnage.Bom`; every dimension, qty, spec and basis in the
    drawing comes off it. Never retype a dimension the BOM already carries.
    The explosion is a pure draw-time translation of `_place`'s volumes
    (`_explode`) -- the placement itself, and so every number, is the one
    `build_gif` replays.

    Takes voxels, not a mesh, deliberately: several ranked layouts share one
    pose, so the caller voxelises once per distinct pose (`pose_voxels`).
    """
    p = _place(voxels=voxels, extent_lbh=extent_lbh, pitch_lbh=pitch_lbh,
              grid=grid, inner_lbh=inner_lbh, bom=bom, count=count,
              cell_mm=cell_mm)
    rows, el, extent, pitch, inner, grid = (p.rows, p.el, p.extent, p.pitch,
                                            p.inner, p.grid)
    layers, alt = p.layers, p.alt

    # Lift the stack apart along z. In cells, and the leaders use the SAME
    # rounded gap, so a label's dot lands on the instance where it actually
    # got drawn rather than where an unrounded gap would have put it.
    gap_cells = max(1, int(round(_explode_gap(pitch[2], inner[2], layers)
                                 / cell_mm)))
    gap = gap_cells * cell_mm
    dun = _explode(p.dun, p.dun_step, gap_cells)
    prt = _explode(p.prt, p.prt_step, gap_cells)
    z_top = max(dun.shape[2], prt.shape[2]) * cell_mm
    parts_z0 = next(r.z0 for r in rows if r.is_parts)

    def _lift(z0: float) -> float:
        """Rise of a dunnage cuboid sitting at `z0` -- `_explode`'s own rule
        (step // 2 gaps), read off the same `_dun_step`."""
        return (_dun_step(z0, parts_z0, pitch[2], layers) // 2) * gap

    # Leaders out to a label column, poster-style. Every component lives in
    # the same stack, so anchoring each label at its own component's
    # mid-height would pile them all at mid-box: each label points instead at
    # ONE REAL DRAWN instance -- the one nearest the camera (largest x+y+z,
    # `_proj`'s own depth convention), projected with `_proj` so the dot lands
    # where the component actually is, not at a fixed column edge with nothing
    # under it. A row with no drawn geometry (MS Rod, an undrawn BOM element)
    # gets no leader at all -- pointing at empty space is worse than not
    # pointing.
    def _row_anchor(row: _Row):
        if row.is_parts:
            ox, oy = _origins(extent, pitch, grid, inner)
            k = grid[2] - 1                          # the topmost layer
            centres = [(ox + a * pitch[0] + extent[0] / 2,
                       oy + b * pitch[1] + extent[1] / 2,
                       row.z0 + k * pitch[2] + extent[2] / 2 + k * gap)
                      for a in range(grid[0]) for b in range(grid[1])]
        elif row.geo is not None:
            solids, _voids = row.geo()
            centres = [(o[0] + s[0] / 2, o[1] + s[1] / 2,
                       o[2] + s[2] / 2 + _lift(o[2])) for o, s in solids]
        else:
            return None
        return _proj(np.array(max(centres, key=sum), float)) if centres else None

    labels = _labels(rows, el, extent, pitch, grid, count)
    anchors = [_row_anchor(r) for r in rows]
    y_top = z_top + 120
    y_bot = -(inner[0] + inner[1]) * SIN30 - BASE_MM - 100
    lx, tx = inner[0] + 10.0, inner[0] + 150.0
    x_lo, x_hi = -inner[1] * COS30 - 300, tx + 820
    y_lo, y_hi = y_bot - 300, y_top + 210      # band for the header block

    # Equal aspect, so let the figure follow the drawing. 14in at 110dpi is
    # the width the hero image (`.hero-image .modal-img`, 480px tall,
    # object-fit: contain) has always been scaled from -- unchanged here.
    fig_h_in = float(np.clip(14.0 * (y_hi - y_lo) / (x_hi - x_lo), 10.0, 22.0))
    fig, ax = plt.subplots(figsize=(14.0, fig_h_in), dpi=110)
    # Axis off, no title/colourbar, nothing outside the axes data area for
    # `fig.tight_layout()` (below) to pad against -- it shrinks this figure's
    # margins to ~0, confirmed against real renders (a 2470-unit y-range at
    # 11.27in/110dpi renders at 1239px against this formula's 1240px). That
    # makes the points<->data-unit ratio known WITHOUT a renderer round trip,
    # which is what lets every label block get its own line instead of
    # guessing at a fraction of the row's `step` budget (the qty/dims-line and
    # tag/name overlaps a design review caught).
    data_per_pt = (y_hi - y_lo) / (fig_h_in * 72.0)

    def _lineh(fontsize: float, n: int = 1, spacing: float = 1.2) -> float:
        return fontsize * spacing * n * data_per_pt

    palette = np.zeros((alt + 1, 4))
    for i, row in enumerate(rows):
        palette[LABEL0 + i] = matplotlib.colors.to_rgba(row.colour, row.alpha)
        if row.is_parts:
            palette[alt] = matplotlib.colors.to_rgba(PART_ALT, row.alpha)
    _draw_asset(ax, inner, bom.build_height_mm)
    # One faint guide up the middle of the stack, base to top: the lifted
    # layers otherwise float with nothing saying they are one assembly.
    g0, g1 = (_proj(np.array([inner[0] / 2, inner[1] / 2, z], float))
              for z in (-BASE_MM, z_top))
    ax.plot([g0[0], g1[0]], [g0[1], g1[1]], lw=0.8, color=C_LEADER, zorder=1)
    # Parts FIRST: on a depth tie the later volume wins, and the tint belongs
    # over the part, not the part over the tray it sits in.
    _paint(ax, [prt, dun], lambda lab: palette[lab], cell_mm, lw=0.3)

    # Spec column. Every block (tag, name, qty+dims, notes) gets its OWN line
    # -- stacked by its own measured height (`_lineh`), not a fraction of a
    # shared row budget guessed to be big enough. That guess is what put
    # "qty N" and the dims line on one baseline, and the basis tag on top of
    # the name. qty and dimension share one baseline at two FIXED x offsets,
    # so the numbers column up across rows (mono, ~0.6em per character).
    gap_y, row_gap = 5.0 * data_per_pt, 16.0 * data_per_pt
    dim_x = tx + 10 * 0.6 * 9.0 * data_per_pt   # 10 mono chars at 9pt
    row_y = y_top - 20.0
    for lb, a in zip(labels, anchors):
        y = row_y
        if a is not None:
            # Solid dog-leg, not a straight diagonal: a horizontal run off the
            # text block to a column just right of the box, then one straight
            # segment to the real instance the label is naming.
            ax_x, ax_y = a
            elbow = lx + 0.12 * (tx - lx)
            ax.plot([tx - 20, elbow], [y, y], lw=0.5, color=C_LEADER, zorder=4)
            ax.plot([elbow, ax_x], [y, ax_y], lw=0.5, color=C_LEADER, zorder=4)
            ax.plot([ax_x], [ax_y], marker="o", ms=2.6, color=C_LEADER,
                    zorder=4)
        ax.text(tx, y, TAG[lb.basis][1], fontsize=7.5, weight="bold",
                color=TAG[lb.basis][0], va="top")
        y -= _lineh(7.5) + gap_y
        ax.text(tx, y, lb.title.upper(), fontsize=10.5, weight="bold",
                va="top", color=C_NAME)
        y -= _lineh(10.5) + gap_y
        ax.text(tx, y, lb.qty, fontsize=9, va="top", weight="bold",
                color=C_ACCENT, family="DejaVu Sans Mono")
        ax.text(dim_x, y, lb.dim, fontsize=9, va="top", color=C_INK,
                family="DejaVu Sans Mono")
        y -= _lineh(9) + gap_y
        if lb.extras:
            ax.text(tx, y, "\n".join(lb.extras), fontsize=8.5, va="top",
                    color=C_MUTE, linespacing=1.5,
                    family="DejaVu Sans Mono")
            y -= _lineh(8.5, n=len(lb.extras), spacing=1.5)
        row_y = y - row_gap

    # The build height against the inner height, called out on the box itself:
    # the drawing exists to answer "does the stack fit". Measured on the
    # UNEXPLODED box, which is where those two numbers live. The two ticks are
    # only the headroom apart -- 22mm on the TRW box -- so they share one
    # text block rather than overprinting each other.
    ticks = [_proj(np.array([0.0, inner[1], z], float))
             for z in (inner[2], bom.build_height_mm)]
    for t in ticks:
        ax.plot([t[0] - 90, t[0]], [t[1], t[1]], lw=1.0, color=C_INK,
                alpha=0.8, zorder=4)
    ax.text(ticks[0][0] - 100, (ticks[0][1] + ticks[1][1]) / 2,
            "inner H %g mm\nbuild %g mm  %s"
            % (inner[2], round(bom.build_height_mm, 1),
               "FITS" if bom.fits else "DOES NOT FIT"),
            fontsize=9, ha="right", va="center", weight="bold",
            linespacing=1.5, family="DejaVu Sans Mono",
            color=C_ACCENT if bom.fits else "#B91C1C")

    # Header: box code, the count in large type, the pose. Offsets in POINTS
    # off the axes corner -- the figure's aspect follows the drawing, so a
    # transAxes fraction would slide the block around between cases.
    def head(dx: float, dy: float, txt: str, **kw) -> None:
        ax.annotate(txt, xy=(0.0, 1.0), xycoords=ax.transAxes,
                    xytext=(dx, dy), textcoords="offset points", va="top",
                    annotation_clip=False, **kw)

    head(0, -2, asset_name, fontsize=12, weight="bold", color=C_MUTE,
         family="DejaVu Sans Mono")
    head(0, -22, "%d" % count, fontsize=40, weight="bold", color=C_INK)
    head(86, -28, "parts per box", fontsize=11, color=C_MUTE)
    head(86, -46, "%d layers x %d per layer  -  %s insert"
         % (grid[2], grid[0] * grid[1], bom.archetype.replace("_", "-")),
         fontsize=9, color=C_MUTE, family="DejaVu Sans Mono")
    head(0, -82, "pose %g x %g x %g mm  -  pitch %g / %g / %g mm"
         % (*extent, *pitch), fontsize=9.5, color=C_ACCENT,
         family="DejaVu Sans Mono")

    # ponytail: the ticket's "Generated from layout <run id>" footer is left
    # out -- no run id reaches this renderer and threading one through
    # worker.py is out of scope. The fit/caveat line below is the footer.
    foot = ("build %g of %g mm inner  -  %s        nest depth %g mm "
            "(extent H %g - pitch H %g): dunnage inside that depth is free\n%s"
            % (round(bom.build_height_mm, 1), bom.inner_h_mm,
               "fits" if bom.fits else "DOES NOT FIT",
               round(bom.nest_depth_mm, 1), extent[2],
               float(pitch_lbh[2]), bom.caveat))
    ax.text(0.0, 0.0, foot, transform=ax.transAxes, fontsize=8.5, va="bottom",
            color=C_MUTE, wrap=True, linespacing=1.6)

    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)             # room under the last block for the caveat
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    logger.info("drew %s %s: %d components, build %g of %g mm inner, "
                "exploded %g mm per layer", asset_name, bom.archetype,
                len(rows), round(bom.build_height_mm, 1), bom.inner_h_mm, gap)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Orthographic Front / Side / Top -- the legible report drawing (F15)
# ---------------------------------------------------------------------------
# (name, axis projected away, horizontal axis, vertical axis). The two kept
# axes are always in ascending order, so `mask.any(axis=drop)` is already
# (horizontal, vertical) with no transpose to get wrong.
ORTHO_VIEWS = (("Front", 1, 0, 2), ("Side", 0, 1, 2), ("Top", 2, 0, 1))
C_SIL = "#1E40AF"        # the part silhouette; the deck's own primary blue
C_SIL_EDGE = "#14286B"   # its drawn outline, so a part reads as a shape
C_CARD = "#B45309"       # dunnage outlines: cardboard, thin, never filled
# The silhouette is a raster of a 12mm lattice and at report scale that
# staircase is what the eye sees first ("very low level of image generation").
# Upsample the mask x4 and threshold at 0.5, then stroke the same 0.5 contour:
# the shape is unchanged -- this is resampling of `_place`'s own mask, not a
# different mask -- but the edge reads as drawn instead of as pixels.
SMOOTH = 4


def _runs(flags: np.ndarray) -> list:
    """`[(start, stop), ...]` for every run of True in a 1-D bool array."""
    idx = np.flatnonzero(np.diff(np.r_[False, flags.astype(bool), False]))
    return list(zip(idx[0::2], idx[1::2]))


def _smooth_sil(ax, mask, cell_mm: float, x0: float = 0.0, y0: float = 0.0,
                *, alpha: float = 1.0, z: float = 2.0) -> None:
    """One part silhouette: `_place`'s OWN mask, upsampled x4 bilinear and
    thresholded at 0.5, stroked on that same 0.5 contour.

    The shape is unchanged -- this is a resample of the mask, not a different
    mask -- but the edge reads as drawn instead of as 12mm pixels. Shared by
    `ortho_png` and F17's SECTION A-A so the two drawings cannot drift apart
    in style; `_check_ortho_panels` measures the area this produces.
    """
    field = ndimage.zoom(mask.astype(float), SMOOTH, order=1,
                         grid_mode=True, mode="nearest")
    fill = field > 0.5
    rgba = np.zeros(fill.shape[::-1] + (4,))
    rgba[fill.T] = matplotlib.colors.to_rgba(C_SIL, alpha)
    ax.imshow(rgba, origin="lower", interpolation="nearest", zorder=z,
              extent=(x0, x0 + mask.shape[0] * cell_mm,
                      y0, y0 + mask.shape[1] * cell_mm))
    sub = cell_mm / SMOOTH
    ax.contour(x0 + (np.arange(field.shape[0]) + 0.5) * sub,
               y0 + (np.arange(field.shape[1]) + 0.5) * sub, field.T,
               levels=[0.5], colors=[C_SIL_EDGE], linewidths=0.6,
               zorder=z + 0.5, alpha=alpha)


def ortho_png(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid, inner_lbh,
              bom: dunnage.Bom, asset_name: str, count: int,
              cell_mm: float = CELL_MM) -> bytes:
    """The packed box as three flat views -- Front, Side, Top -- as PNG bytes.

    The competitor's pack report (PLANNING F15) draws parts as filled
    silhouettes inside a thin box outline with the dunnage as thin lines; on a
    13-layer stack that reads where `explode_png`'s isometric voxel mass does
    not (F9).

    Every silhouette is `_place`'s OWN parts volume flattened along one axis --
    the same lattice the count came off, never a second placement expression
    (hard rule 9). The dunnage rectangles are the same `row.geo()` cuboids
    `_place` rasterises.
    """
    p = _place(voxels=voxels, extent_lbh=extent_lbh, pitch_lbh=pitch_lbh,
              grid=grid, inner_lbh=inner_lbh, bom=bom, count=count,
              cell_mm=cell_mm)
    inner, extent = p.inner, p.extent
    # Each panel gets the same axes height, so the figure follows the tallest
    # one; widths follow the horizontal span so all three share one mm scale.
    widths = [inner[h] for _n, _d, h, _v in ORTHO_VIEWS]
    fig_w = 13.0
    fig_h = float(np.clip(fig_w * 0.94 * max(inner[1], inner[2]) / sum(widths)
                          + 1.1, 3.5, 16.0))
    fig, axes = plt.subplots(1, 3, figsize=(fig_w, fig_h), dpi=110,
                             gridspec_kw={"width_ratios": widths})
    fig.suptitle("%s  -  %d parts  -  grid %d x %d x %d"
                 % (asset_name, count, *p.grid),
                 fontsize=13, weight="bold", color=C_INK, family=SANS)

    for ax, (name, drop, h, v) in zip(axes, ORTHO_VIEWS):
        mask = (p.prt > 0).any(axis=drop)           # (horizontal, vertical)
        # `grid_mode=True` (inside `_smooth_sil`) resamples CELLS, not sample
        # points, so the smoothed field covers exactly the same mm extent as
        # `mask` -- endpoint-aligned zoom would stretch it by half a cell at
        # each end. `> 0.5` (strict) keeps a one-cell gap between two parts
        # open: bilinear reads exactly 0.5 across it.
        _smooth_sil(ax, mask, cell_mm)
        for row in p.rows:
            if row.geo is None:
                continue
            solids, voids = row.geo()
            if name == "Top":
                # Plan view: a layer sheet or a tray slab is the whole
                # footprint and would just restate the box outline. Only a
                # pocket that could actually HOLD the part earns a wall line
                # -- an interleaved pose whose in-plane pitch is under the
                # part extent has no pockets, whatever its BOM says (F11).
                e = p.el[row.name]
                if not (e.matrix and e.cell_mm
                        and e.cell_mm[0] >= extent[0]
                        and e.cell_mm[1] >= extent[1]):
                    continue
                boxes = voids
            else:
                # ponytail: EVERY dunnage solid, outlined, not just the layer
                # sheets -- a sheet is full footprint x 3mm and draws as the
                # thin line the ticket asks for, and a bar or tray drawing
                # itself costs one rectangle. Filter by element role here if a
                # busy bar_and_rod front view ever needs it.
                boxes = solids
            # Snap every dunnage rectangle to the SAME `cell_mm` lattice the
            # silhouette is a raster of, and draw each distinct one once.
            # Unsnapped, a component thinner than a drawn line contributed two
            # coincident edges: the 3mm separator sheet and the 69mm tray that
            # sits 3mm above it produced FOUR full-width lines per 72mm layer,
            # and where sub-pixel rounding split a pair by 2px the part
            # silhouette showed through the gap as a stray line across the
            # Side view. Snapped, the sheet is one line at the layer boundary
            # and the tray's edges land on it.
            seen = set()
            for o, s in boxes:
                k = (int(round(o[h] / cell_mm)), int(round(o[v] / cell_mm)),
                     int(round((o[h] + s[h]) / cell_mm)),
                     int(round((o[v] + s[v]) / cell_mm)))
                if k in seen:
                    continue
                seen.add(k)
                # ponytail: a component under half a cell on BOTH axes snaps to
                # a point and vanishes. At 12mm cells nothing in any shipped
                # BOM is that small; drop the snap for that element if one ever
                # is.
                # Clamp to the box: a 1150 inner snaps to 1152 at 12mm cells,
                # and a full-footprint sheet must not poke past the outline.
                x0, x1 = (min(max(k[i] * cell_mm, 0.0), inner[h]) for i in (0, 2))
                y0, y1 = (min(max(k[i] * cell_mm, 0.0), inner[v]) for i in (1, 3))
                ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                                       ec=C_CARD, lw=0.5, zorder=3))
        ax.add_patch(Rectangle((0.0, 0.0), inner[h], inner[v], fill=False,
                               ec=C_INK, lw=1.1, zorder=4))
        ax.set_title("%s\n%g x %g mm" % (name, inner[h], inner[v]),
                     fontsize=11, color=C_INK, family=SANS)
        pad = 0.03 * max(inner)
        ax.set_xlim(-pad, inner[h] + pad)
        ax.set_ylim(-pad, inner[v] + pad)
        ax.set_aspect("equal")
        # `aspect="equal"` shrinks the axes box to fit the data; anchoring it
        # north keeps the three panel tops -- and so the three titles -- on
        # one line instead of each floating at its own centred height.
        ax.set_anchor("N")
        ax.set_axis_off()

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="#FFFFFF")
    plt.close(fig)
    logger.info("ortho %s %s: grid %s, %d parts", asset_name, bom.archetype,
                p.grid, count)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# F17: the manufacturing sheets -- one dimensioned drawing per BOM element
# ---------------------------------------------------------------------------
# "What is the use of this whole process if I am not able to tell the person
# what insert to get manufactured -- he won't get the dimensions by looking at
# the animation." (Rahul, 2026-09-15). Everything above this line is a picture
# of the packed box; this is what a tray supplier quotes and cuts from.
#
# The rule that makes it trustworthy is hard rule 9 taken literally: every
# number printed on a sheet is a `dunnage.Bom` field, an `inner_lbh`, or an
# origin `_place` already computed -- with exactly two arithmetic results, the
# wall (pitch - pocket) and the edge margin (the first row origin), both
# LABELLED as such on the drawing. `_check_sheet_numbers` harvests the drawn
# text back out of the figure and asserts it.
SHEET_IN = (11.0, 8.5)           # landscape letter -- what a supplier prints


def _mm(v) -> str:
    return "%g" % round(float(v), 1)


def _dim_text(ax, x, y, text, *, rot=0.0) -> None:
    """A dimension number, tagged `gid="dim"`.

    The tag IS the contract: `_check_sheet_numbers` harvests exactly these
    texts, off EVERY sheet, and every number in them has to be a field of
    that element, a layout number or an origin `_place` computed.

    The title block's `size ... mm` line is tagged too -- it is a per-element
    dimension like any other, and untagged it was a hole in the harvest
    ("size 999.9 mm" passed). The only untagged numbers left on a sheet are
    the qty, the part count and the drawing index k/N, none of which is a
    dimension of anything.
    """
    ax.text(x, y, text, fontsize=8, color=C_INK, family=SANS, ha="center",
            va="center", rotation=rot, gid="dim", zorder=6,
            bbox=dict(facecolor="#FFFFFF", edgecolor="none", pad=0.8))


def _arrow(ax, p0, p1) -> None:
    ax.annotate("", xy=p1, xytext=p0, zorder=5,
                arrowprops=dict(arrowstyle="<|-|>", color=C_INK, lw=0.7,
                                shrinkA=0, shrinkB=0, mutation_scale=6))


def _hdim(ax, x0, x1, y, text, *, ext=None) -> None:
    _arrow(ax, (x0, y), (x1, y))
    if ext is not None:
        for x in (x0, x1):
            ax.plot([x, x], [ext, y], color=C_MUTE, lw=0.4, zorder=4)
    _dim_text(ax, (x0 + x1) / 2, y, text)


def _vdim(ax, y0, y1, x, text, *, ext=None) -> None:
    _arrow(ax, (x, y0), (x, y1))
    if ext is not None:
        for y in (y0, y1):
            ax.plot([ext, x], [y, y], color=C_MUTE, lw=0.4, zorder=4)
    _dim_text(ax, x, (y0 + y1) / 2, text, rot=90)


def _leader(ax, x, y, tx, ty, text) -> None:
    """Text off to one side with a leader back to the feature -- for a
    dimension with no length to put an arrow inside (a zero wall)."""
    ax.plot([x, tx], [y, ty], color=C_MUTE, lw=0.4, zorder=4)
    _dim_text(ax, tx, ty, text)


def _dax(fig, rect, xlim, ylim, title):
    ax = fig.add_axes(rect)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=10, color=C_INK, family=SANS,
                     weight="bold")
    return ax


def _outline(ax, o, s, *, lw=1.2, fc="none", gid=None, z=3) -> None:
    ax.add_patch(Rectangle(o, s[0], s[1], facecolor=fc, fill=fc != "none",
                           edgecolor=C_INK, lw=lw, zorder=z, gid=gid))


def _tray_pockets(p) -> list:
    """The tray's pockets in plan, ONE layer, off `_place`'s own voids.

    `_pocket_tray_rows` insets every void by `POCKET_WALL_MM`/2 per side so
    the wall survives a 12mm raster -- DRAW-ONLY, see the constant. A
    dimensioned sheet cannot carry that inset: the number beside the pocket
    is the BOM's `cell_mm`, so the same inset comes straight back out here
    and the drawn rectangle is the pocket the BOM states, at the position
    `_place` put it.
    """
    row = next((r for r in p.rows
                if r.name and r.geo is not None and p.el[r.name].matrix), None)
    if row is None:
        return []
    _solids, voids = row.geo()
    if not voids:
        return []
    z0 = min(o[2] for o, _s in voids)
    w = POCKET_WALL_MM
    return sorted(((o[0] - w / 2, o[1] - w / 2), (s[0] + w, s[1] + w))
                  for o, s in voids if abs(o[2] - z0) < 1e-6)


def _tray_sheet(fig, p, e, bom, voxels, cell_mm) -> None:
    """PLAN of the tray with every pocket, plus SECTION A-A through one row."""
    l, b = float(e.dims_mm[0]), float(e.dims_mm[1])
    pl, pb, pd = (float(v) for v in e.cell_mm)
    cols, nrows = e.matrix
    pockets = _tray_pockets(p)
    sheet_t = next((float(s.dims_mm[2]) for s in bom.elements
                    if s.cell_mm is None and s.dims_mm[2] is not None), 0.0)

    # ---- PLAN -------------------------------------------------------------
    ax = _dax(fig, (0.05, 0.53, 0.90, 0.40),
              (-0.26 * l, 1.16 * l), (-0.36 * b, 1.20 * b),
              "PLAN  -  %d x %d pockets" % (cols, nrows))
    _outline(ax, (0.0, 0.0), (l, b), lw=1.4)
    for o, s in pockets:
        _outline(ax, o, s, lw=0.9, gid="pocket")
    _hdim(ax, 0.0, l, -0.20 * b, _mm(l), ext=0.0)
    _vdim(ax, 0.0, b, -0.18 * l, _mm(b), ext=0.0)
    if pockets:
        (px, py), _s = pockets[0]
        _hdim(ax, px, px + pl, py + 0.62 * pb, _mm(pl))
        _vdim(ax, py, py + pb, px + 0.28 * pl, _mm(pb))
        # Edge margin: the first pocket's own origin. One of the two numbers
        # on this sheet that is not read straight off a field -- labelled.
        _hdim(ax, 0.0, px, -0.125 * b, "edge %s" % _mm(px), ext=0.0)
        _vdim(ax, 0.0, py, -0.11 * l, "edge %s" % _mm(py), ext=0.0)
        # Pitch: between two adjacent pocket origins, which is the measured
        # in-plane pitch the count came off.
        cxs = sorted({round(o[0], 3) for o, _s in pockets})
        cys = sorted({round(o[1], 3) for o, _s in pockets})
        walls = []
        if len(cxs) > 1:
            _hdim(ax, cxs[0], cxs[1], 1.08 * b, "pitch %s" % _mm(p.pitch[0]),
                  ext=b)
            walls.append(("L", cxs[0] + pl, py + 0.5 * pb,
                          p.pitch[0] - pl))
        if len(cys) > 1:
            _vdim(ax, cys[0], cys[1], 1.06 * l, "pitch %s" % _mm(p.pitch[1]),
                  ext=l)
            walls.append(("B", px + 0.5 * pl, cys[0] + pb,
                          p.pitch[1] - pb))
        # The wall is the other number this sheet computes rather than reads
        # -- pitch minus pocket, labelled as such, on one leader (there is no
        # room for an arrow inside a partition). Dimensioned only where there
        # IS one: at the shipped TRW pitch the pockets meet and the wall is 0
        # on both axes, and a zero on a manufacturing sheet reads as a
        # mistake. The pocket and pitch dimensions already say they meet, so
        # the line simply does not appear.
        walls = [w for w in walls if round(w[3], 1) > 0]
        if walls:
            _leader(ax, walls[0][1], walls[0][2], 0.5 * l, -0.30 * b,
                    "wall (pitch - pocket)  " + "  ".join(
                        "%s %s" % (ax_name, _mm(v))
                        for ax_name, _x, _y, v in walls))

    # ---- SECTION A-A ------------------------------------------------------
    step = p.pitch[2]
    ext_h = p.extent[2]
    top = max(step + sheet_t + ext_h, sheet_t + pd)
    sx = _dax(fig, (0.05, 0.17, 0.90, 0.33),
              (-0.26 * l, 1.16 * l), (-0.55 * top, 1.30 * top),
              "SECTION A-A  -  one pocket row, two layers")
    _outline(sx, (0.0, 0.0), (l, sheet_t), lw=0.9, fc="#E2E8F0")
    _outline(sx, (0.0, step), (l, sheet_t), lw=0.9, fc="#E2E8F0")
    _outline(sx, (0.0, sheet_t), (l, pd), lw=1.2)
    row_pockets = [(o, s) for o, s in pockets
                   if abs(o[1] - min(q[1] for q, _ in pockets)) < 1e-6]
    for o, s in row_pockets:                    # the pockets, cut through
        _outline(sx, (o[0], sheet_t), (s[0], pd), lw=0.9, fc="#FFFFFF",
                 gid="pocket-section", z=3.5)
    # The part in the pocket: `_place`'s own occupancy, flattened along B --
    # the same `.any(axis)` projection `ortho_png` draws -- stamped at the
    # part origins `_place` recorded for layer 0 (step 1) and layer 1 (step 3).
    mask = np.asarray(voxels).any(axis=1)
    def _front_row(step):
        got = p.parts_at_step.get(step) or []
        if not got:
            return []
        y0 = min(o[1] for o in got)
        return sorted(o for o in got if abs(o[1] - y0) < 1e-6)
    for x, _y, z in _front_row(1):
        _smooth_sil(sx, mask, cell_mm, x, z, z=4.0)
    for x, _y, z in _front_row(3):          # the layer above, to show the nest
        _smooth_sil(sx, mask, cell_mm, x, z, alpha=0.30, z=4.0)
    _vdim(sx, 0.0, sheet_t, -0.05 * l, "sheet %s" % _mm(sheet_t), ext=0.0)
    _vdim(sx, sheet_t, sheet_t + pd, -0.15 * l, "depth %s" % _mm(pd), ext=0.0)
    _vdim(sx, 0.0, step, 1.10 * l, "layer step %s" % _mm(step), ext=l)
    if bom.nest_depth_mm > 0:
        z_top = p.parts_at_step[1][0][2] + ext_h
        _leader(sx, 0.5 * l, z_top, 0.5 * l, -0.40 * top,
                "part nests %s into the layer above" % _mm(bom.nest_depth_mm))


def _bar_sheet(fig, p, e) -> None:
    """A bar: plan + end view, and where it lands in one layer."""
    row = next((r for r in p.rows if r.name == e.name), None)
    solids = row.geo()[0] if row is not None and row.geo is not None else []
    if not solids:
        _plain_sheet(fig, e)
        return
    length, w, h = (float(v) for v in e.dims_mm)
    ax = _dax(fig, (0.06, 0.62, 0.52, 0.30),
              (-0.14 * length, 1.10 * length), (-0.9 * w, 2.4 * w), "PLAN")
    _outline(ax, (0.0, 0.0), (length, w), lw=1.2)
    _hdim(ax, 0.0, length, -0.55 * w, _mm(length), ext=0.0)
    _vdim(ax, 0.0, w, -0.09 * length, _mm(w), ext=0.0)
    ex = _dax(fig, (0.64, 0.62, 0.30, 0.30),
              (-0.8 * w, 2.2 * w), (-0.8 * h, 1.9 * h), "END VIEW")
    _outline(ex, (0.0, 0.0), (w, h), lw=1.2)
    _hdim(ex, 0.0, w, -0.35 * h, _mm(w), ext=0.0)
    _vdim(ex, 0.0, h, -0.30 * w, _mm(h), ext=0.0)

    inner_l, inner_b = p.inner[0], p.inner[1]
    z0 = min(o[2] for o, _s in solids)
    layer = sorted((o, s) for o, s in solids if abs(o[2] - z0) < 1e-6)
    lx = _dax(fig, (0.06, 0.19, 0.88, 0.30),
              (-0.14 * inner_l, 1.10 * inner_l),
              (-0.62 * inner_b, 1.24 * inner_b),
              "LAYER PLAN  -  %d per layer boundary, from the inner edge"
              % len(layer))
    _outline(lx, (0.0, 0.0), (inner_l, inner_b), lw=1.4)
    for i, (o, s) in enumerate(layer):
        _outline(lx, (o[0], o[1]), (s[0], s[1]), lw=1.0, fc="#FDE68A")
        _hdim(lx, 0.0, o[0], -(0.10 + 0.13 * i) * inner_b, _mm(o[0]), ext=0.0)
    _hdim(lx, 0.0, inner_l, 1.14 * inner_b, "inner %s" % _mm(inner_l), ext=inner_b)
    _vdim(lx, 0.0, inner_b, 1.04 * inner_l, "inner %s" % _mm(inner_b), ext=inner_l)


def _rod_sheet(fig, e) -> None:
    """Round stock: d x L, side view and end view."""
    d, length = (float(v) for v in e.dims_mm)
    ax = _dax(fig, (0.06, 0.40, 0.70, 0.45),
              (-0.10 * length, 1.10 * length), (-0.16 * length, 0.16 * length),
              "SIDE VIEW")
    _outline(ax, (0.0, -d / 2), (length, d), lw=1.2, fc="#E2E8F0")
    _hdim(ax, 0.0, length, -0.07 * length, _mm(length), ext=-d / 2)
    _leader(ax, length / 2, d / 2, length / 2, 0.08 * length, "d%s" % _mm(d))
    ex = _dax(fig, (0.80, 0.40, 0.16, 0.45), (-1.4 * d, 1.4 * d),
              (-1.4 * d, 1.4 * d), "END VIEW")
    ex.add_patch(Circle((0.0, 0.0), d / 2, facecolor="#E2E8F0",
                        edgecolor=C_INK, lw=1.2, zorder=3))
    _dim_text(ex, 0.0, -1.0 * d, "d%s" % _mm(d))


def _plain_sheet(fig, e) -> None:
    """A flat sheet: plan outline with L and B, and a thickness callout."""
    dims = [None if v is None else float(v) for v in e.dims_mm]
    if len(dims) < 2 or dims[0] is None or dims[1] is None:
        fig.text(0.5, 0.55, "size not derivable from the lattice -- needs deck",
                 fontsize=13, color=C_MUTE, ha="center", family=SANS)
        return
    l, b = dims[0], dims[1]
    ax = _dax(fig, (0.06, 0.22, 0.88, 0.68),
              (-0.22 * l, 1.14 * l), (-0.26 * b, 1.12 * b), "PLAN")
    _outline(ax, (0.0, 0.0), (l, b), lw=1.4, fc="#F1F5F9")
    _hdim(ax, 0.0, l, -0.14 * b, _mm(l), ext=0.0)
    _vdim(ax, 0.0, b, -0.12 * l, _mm(b), ext=0.0)
    if len(dims) > 2 and dims[2] is not None:
        _leader(ax, 0.82 * l, 0.5 * b, 1.06 * l, 0.80 * b,
                "thickness %s" % _mm(dims[2]))


def _title_block(fig, e, asset_name: str, count: int, k: int, n: int) -> None:
    fig.add_artist(Rectangle((0.04, 0.035), 0.92, 0.115,
                             transform=fig.transFigure, fill=False,
                             edgecolor=C_INK, lw=1.0, zorder=5))
    fig.add_artist(plt.Line2D([0.62, 0.62], [0.035, 0.15], color=C_INK,
                              lw=0.8, transform=fig.transFigure, zorder=5))
    fig.text(0.06, 0.123, e.name, fontsize=12, weight="bold", color=C_INK,
             family=SANS, va="center")
    fig.text(0.06, 0.092, e.spec or "spec: needs deck", fontsize=9,
             color=C_MUTE, family=SANS, va="center")
    fig.text(0.06, 0.062, "size %s mm" % (e.size or "not derivable"),
             fontsize=9, color=C_INK, family=SANS, va="center", gid="dim")
    fig.text(0.64, 0.123, "qty %s per box"
             % (e.qty if e.qty is not None else "?"),
             fontsize=11, weight="bold", color=C_INK, family=SANS, va="center")
    fig.text(0.64, 0.092, "%s  -  %d parts per box" % (asset_name, count),
             fontsize=9, color=C_MUTE, family=SANS, va="center")
    fig.text(0.64, 0.062, "all dimensions mm      drawing %d/%d" % (k, n),
             fontsize=9, color=C_INK, family=SANS, va="center")


def _sheet_figs(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid, inner_lbh,
                bom: dunnage.Bom, asset_name: str, count: int,
                cell_mm: float = CELL_MM):
    """Yield (element, matplotlib figure) per BOM element, in `bom.elements`
    order. `insert_sheets_png` is this, saved; the self-check is this, with
    the drawn text and the drawn pockets read back off the figure."""
    p = _place(voxels=voxels, extent_lbh=extent_lbh, pitch_lbh=pitch_lbh,
               grid=grid, inner_lbh=inner_lbh, bom=bom, count=count,
               cell_mm=cell_mm)
    n = len(bom.elements)
    for k, e in enumerate(bom.elements, start=1):
        fig = plt.figure(figsize=SHEET_IN, dpi=110, facecolor="#FFFFFF")
        if e.matrix and e.cell_mm:
            _tray_sheet(fig, p, e, bom, voxels, cell_mm)
        elif "d" in e.labels:
            _rod_sheet(fig, e)
        elif "Bar" in e.name:
            _bar_sheet(fig, p, e)
        else:
            _plain_sheet(fig, e)
        _title_block(fig, e, asset_name, count, k, n)
        yield e, fig


def insert_sheets_png(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid,
                      inner_lbh, bom: dunnage.Bom, asset_name: str,
                      count: int, cell_mm: float = CELL_MM) -> list:
    """One dimensioned manufacturing sheet per BOM element, as PNG bytes, in
    `bom.elements` order -- what the tray supplier quotes and cuts from."""
    out = []
    for _e, fig in _sheet_figs(voxels=voxels, extent_lbh=extent_lbh,
                               pitch_lbh=pitch_lbh, grid=grid,
                               inner_lbh=inner_lbh, bom=bom,
                               asset_name=asset_name, count=count,
                               cell_mm=cell_mm):
        buf = BytesIO()
        fig.savefig(buf, format="png", facecolor="#FFFFFF")
        plt.close(fig)
        out.append(buf.getvalue())
    logger.info("insert sheets %s %s: %d elements", asset_name, bom.archetype,
                len(out))
    return out


def _dun_caption(dun_at_step: dict, step: int, rows: list, el: dict) -> str:
    """"Place 2 x Top Side Bar (750x70x66mm), 1 x Top Center Bar (750x60x66mm)"
    -- straight off the BOM element each row names, never retyped."""
    items = sorted(dun_at_step.get(step, {}).items())
    parts = ["%d x %s (%smm)" % (n, rows[i].name, el[rows[i].name].size)
             for i, n in items]
    return "Place " + ", ".join(parts) if parts else ""


def _captions(p: "_Placement", bom: dunnage.Bom, asset_name: str, count: int,
              step: int | None) -> tuple:
    """`(title, text, meta, parts placed so far)` for one build step.

    `step is None` is the GIF's final hold frame (not a sequence step).
    Shared by `build_gif`'s frames and the JSON `sequence` built off the same
    `_place`, so the animation can never caption a step differently from the
    GIF it replaces (hard rule 9).
    """
    per_layer = p.grid[0] * p.grid[1]
    if step is None:
        line1 = "Packed: %d parts" % count
        line2 = ("build %g of %g mm inner - %s"
                 % (round(bom.build_height_mm, 1), bom.inner_h_mm,
                    "FITS" if bom.fits else "DOES NOT FIT"))
        cum = count
    elif step % 2:                                  # parts step
        k = (step - 1) // 2
        line1 = "Layer %d of %d" % (k + 1, p.layers)
        line2 = "Place %d x Part" % per_layer
        cum = per_layer * (k + 1)
    else:
        j = step // 2
        line1 = ("Top dunnage" if step == 2 * p.layers else
                 "Layer %d of %d" % (j + 1, p.layers) if step else
                 # Step 0 is "empty" only when nothing actually lands
                 # there -- some archetypes/poses have no bottom
                 # dunnage at all (no vertical nest), so it can be.
                 "Base dunnage" if p.dun_at_step.get(0) else "Empty box")
        line2 = _dun_caption(p.dun_at_step, step, p.rows, p.el)
        cum = per_layer * j
    line3 = ("%d of %d parts placed  \u00b7  %s  \u00b7  %s insert"
             % (cum, count, asset_name, bom.archetype.replace("_", "-")))
    return line1, line2, line3, cum


def _sequence(p: "_Placement", bom: dunnage.Bom, asset_name: str, count: int,
              steps: list, pose_matrix) -> dict:
    """The packing order as JSON, off the placement `build_gif` just replayed.

    Same step indices, same order, same caption strings as the GIF's frames
    (`_captions`) and the same mm origins the voxels were rasterised from --
    there is no second placement expression for the 3D animation to drift
    from (hard rule 9). The final hold frame is not a step.

    A part `origin` is the min corner of the posed part's AABB. The voxel
    volume `pose_voxels` returns is seated up to one `cell_mm` BELOW that
    (trimesh snaps the raster to its own pitch lattice), which is a render
    artifact of the GIF only: the lattice, the pitch and this sequence all
    use the true AABB.
    """
    return {
        "inner": [float(v) for v in p.inner],
        "pose_matrix": (None if pose_matrix is None
                        else [[float(v) for v in row]
                              for row in _as_4x4(pose_matrix)]),
        "part_extent": [float(v) for v in p.extent],
        "steps": [
            {"i": int(s),
             "kind": "parts" if s % 2 else "dunnage",
             "title": t, "text": txt, "meta": meta,
             "cuboids": [{"name": p.rows[i].name, "colour": p.rows[i].colour,
                          "alpha": float(p.rows[i].alpha),
                          "origin": [round(v, 3) for v in o],
                          "size": [round(v, 3) for v in sz]}
                         for i, o, sz in p.dun_solids_at_step.get(s, [])],
             "parts": [{"origin": [round(float(v), 3) for v in o]}
                       for o in p.parts_at_step.get(s, [])]}
            for s, (t, txt, meta, _cum)
            in ((s, _captions(p, bom, asset_name, count, s)) for s in steps)
        ],
    }


def _step_masks(dun: np.ndarray, dun_step: np.ndarray, prt: np.ndarray,
                prt_step: np.ndarray, step: int) -> tuple:
    """Cells whose per-cell step index == `step` -- gated on the LABEL volume
    too, or a step of 0 also matches every cell nothing was ever written
    into (both default to 0), which is how the first GIF frame came out a
    solid highlighted cube instead of just the real step-0 dunnage. Shared
    by `build_gif` and its self-check so there is one expression for what
    "highlighted at this step" means, not a second one the test re-derives.
    """
    return (dun_step == step) & (dun != 0), (prt_step == step) & (prt != 0)


def build_gif(*, voxels: np.ndarray, extent_lbh, pitch_lbh, grid, inner_lbh,
             bom: dunnage.Bom, asset_name: str, count: int,
             cell_mm: float = CELL_MM, dunnage_ms: int = 600,
             parts_ms: int = 1500, hold_ms: int = 3000, pose_matrix=None,
             frames: bool = True) -> tuple[bytes | None, bytes, dict]:
    """The packing sequence as an animated GIF: the empty asset, then per
    layer the dunnage that goes in before it and that layer's parts, then the
    top dunnage, then a hold on the finished box before it loops.

    Returns `(gif_bytes, packed_png_bytes, sequence)`. `sequence` is the same
    build, step for step, as JSON for the 3D animation (`_sequence`): built
    here rather than in a `build_sequence()` of its own so the two come off
    ONE `_place` call and cannot drift. `pose_matrix` is the candidate
    rotation the caller posed `voxels` with, passed through for the animation
    to pose the GLB the same way.

    The second returned value is the GIF's own hold frame (the fully packed
    box, `frame(None, ...)`) re-encoded as a standalone PNG, so the
    complete-solution image is never a second
    rendering pass that could disagree with the GIF (hard rule 9).

    Off the SAME placement `explode_png` draws (`_place`): the geometry is
    never re-derived, only replayed cumulatively by the per-cell step index
    `_place` stamped alongside every label (hard rule 9 applies to the
    picture too). A step with no cell at all is dropped, not rendered empty.

    `frames=False` (F5 S1: a part with a live 3D animation has no more use
    for the GIF) skips rendering every per-step matplotlib frame -- the
    expensive part, ~18 s of a solve's render tail -- and returns `gif_bytes
    None`. It still runs `_place`/`_sequence`/`_captions`, the SAME
    expression as the `frames=True` path, and still renders the one hold
    frame `frame(None, ...)` for `packed_png_bytes`, so that PNG and the
    `sequence` are byte-for-byte and value-for-value what they would have
    been with the GIF built (see `_check_frames_false` self-check).
    """
    p = _place(voxels=voxels, extent_lbh=extent_lbh, pitch_lbh=pitch_lbh,
              grid=grid, inner_lbh=inner_lbh, bom=bom, count=count,
              cell_mm=cell_mm)
    rows, inner, layers = p.rows, p.inner, p.layers
    dun, prt, dun_step, prt_step, alt = (p.dun, p.prt, p.dun_step,
                                         p.prt_step, p.alt)

    # Highlight labels, one per dunnage row (its own alpha, recoloured to
    # accent) plus one shared opaque one for parts -- appended after every
    # label explode_png uses, so that palette is untouched.
    hl0 = alt + 1
    parts_hl = hl0 + len(rows)
    palette = np.zeros((parts_hl + 1, 4))
    for i, row in enumerate(rows):
        palette[LABEL0 + i] = matplotlib.colors.to_rgba(row.colour, row.alpha)
        if row.is_parts:
            palette[alt] = matplotlib.colors.to_rgba(PART_ALT, row.alpha)
        else:
            palette[hl0 + i] = matplotlib.colors.to_rgba(C_ACCENT, row.alpha)
    palette[parts_hl] = matplotlib.colors.to_rgba(C_ACCENT, 1.0)

    # The step rule (see `_dun_step`): parts of layer k -> 2k+1; dunnage
    # before layer j -> 2j, j==layers being "top dunnage" after the last
    # layer. Parts steps are always non-empty (the count assert in `_place`
    # already guarantees it); dunnage steps only exist where the BOM put one.
    non_empty = sorted(set(p.dun_at_step) | {2 * k + 1 for k in range(layers)})

    corners = np.array([[x, y, z] for x in (0, inner[0]) for y in (0, inner[1])
                        for z in (0, p.top)], float)
    cp = np.array([_proj(c) for c in corners])
    x_lo, x_hi = cp[:, 0].min() - 60, cp[:, 0].max() + 60
    y_lo, y_hi = cp[:, 1].min() - BASE_MM - 60, cp[:, 1].max() + 60

    def frame(step: int | None, idx: int) -> Image.Image:
        final = step is None
        dv = dun.copy() if final else np.where(dun_step <= step, dun, 0)
        pv = prt.copy() if final else np.where(prt_step <= step, prt, 0)
        if not final:
            dm, pm = _step_masks(dun, dun_step, prt, prt_step, step)
            if dm.any():
                dv[dm] = hl0 + (dun[dm] - LABEL0)
            if pm.any():
                pv[pm] = parts_hl

        line1, line2, line3, _cum = _captions(p, bom, asset_name, count, step)

        fig, ax = plt.subplots(figsize=(9.2, 6.6), dpi=80)
        _draw_asset(ax, inner, bom.build_height_mm if final else None)
        _paint(ax, [pv, dv], lambda lab: palette[lab], cell_mm, lw=0.35)

        ax.add_patch(Rectangle((0.015, 0.775), 0.955, 0.21,
                               transform=ax.transAxes, facecolor="#F8FAFC",
                               alpha=0.95, edgecolor="#E2E8F0", lw=1.0,
                               zorder=5))
        ax.text(0.03, 0.955, line1, transform=ax.transAxes, fontsize=11,
                weight="bold", color="#1E40AF", va="top", zorder=6)
        ax.text(0.03, 0.900, line2, transform=ax.transAxes, fontsize=8.8,
                color="#334155", va="top", wrap=True, zorder=6)
        ax.text(0.03, 0.815, line3, transform=ax.transAxes, fontsize=8.3,
                color=C_MUTE, va="top", family="DejaVu Sans Mono", zorder=6)

        frac = 1.0 if final else (idx + 1) / len(non_empty)
        ax.add_patch(Rectangle((0, 0), 1, 0.012, transform=ax.transAxes,
                               facecolor="#E2E8F0", zorder=5))
        ax.add_patch(Rectangle((0, 0), frac, 0.012, transform=ax.transAxes,
                               facecolor=C_ACCENT, zorder=6))

        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(y_lo, y_hi)
        # Fixed axes box, NOT tight_layout: tight_layout resizes the axes to
        # the caption text of THAT frame, so the box slid ~30 px sideways
        # between a dunnage step and a parts step. Same limits + same axes
        # rectangle = the box sits on the same pixels in every frame.
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        fig.canvas.draw()
        img = Image.frombytes(
            "RGBA", fig.canvas.get_width_height(),
            fig.canvas.buffer_rgba().tobytes()
        ).convert("P", palette=Image.ADAPTIVE, colors=128)
        plt.close(fig)
        return img

    packed_frame = frame(None, len(non_empty) - 1)        # the hold frame:
    packed_buf = BytesIO()                                # complete, packed box
    packed_frame.save(packed_buf, format="PNG")
    seq = _sequence(p, bom, asset_name, count, non_empty, pose_matrix)

    if not frames:
        logger.info("built %s %s packed png only (frames=False), %d bytes",
                    asset_name, bom.archetype, packed_buf.tell())
        return None, packed_buf.getvalue(), seq

    gif_frames = [frame(s, idx) for idx, s in enumerate(non_empty)]
    gif_frames.append(packed_frame)
    durations = [(parts_ms if s % 2 else dunnage_ms) for s in non_empty]
    durations.append(hold_ms)

    buf = BytesIO()
    gif_frames[0].save(buf, format="GIF", save_all=True,
                       append_images=gif_frames[1:], optimize=True,
                       duration=durations, loop=0)
    logger.info("built %s %s gif: %d frames (%d content + hold), %d bytes",
                asset_name, bom.archetype, len(gif_frames), len(non_empty),
                buf.tell())
    return buf.getvalue(), packed_buf.getvalue(), seq


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------
def _demo_voxels(extent, cell_mm: float, kind: str) -> np.ndarray:
    """Stand-in occupancy for the self-check: no CAD file, no network.

    The real caller passes `pose_voxels(mesh, ...)`. A box would pass the
    count checks and tell the reviewer nothing, so this is a bent tube for the
    bar and an annulus for the wheel -- enough shape to see the interleave and
    to see through the tray pockets.
    """
    n = [max(1, int(np.ceil(e / cell_mm))) for e in extent]
    c = [(np.arange(n[a]) + 0.5) * cell_mm for a in range(3)]
    out = np.zeros(tuple(n), dtype=bool)
    if kind == "bar":
        d = min(extent[1], extent[2]) / 4.0
        gy, gz = np.meshgrid(c[1], c[2], indexing="ij")
        for i, x in enumerate(c[0]):
            t = x / extent[0]
            cy = d / 2 + (extent[1] - d) * (1 - np.cos(2 * np.pi * t)) / 2
            cz = d / 2 + (extent[2] - d) * (1 - np.cos(4 * np.pi * t)) / 2
            out[i] = (gy - cy) ** 2 + (gz - cz) ** 2 <= (d / 2) ** 2
        return out
    # A wheel is a DISH: rim low, spokes lower, hub boss standing proud -- an
    # extruded ring is 135mm of solid wall and eight of them stack into one
    # unbroken column, which is exactly the thing the drawing has to show is
    # not happening.
    r = min(extent[0], extent[1]) / 2.0
    gx, gy = np.meshgrid(c[0] - extent[0] / 2, c[1] - extent[1] / 2, indexing="ij")
    rad = np.hypot(gx, gy)
    zt = ((np.arange(n[2]) + 0.5) / n[2])[None, None, :]
    rim = ((rad <= r) & (rad >= 0.68 * r))[:, :, None]
    spoke = ((rad < 0.72 * r) & ((np.abs(gy) < 0.10 * r)
                                 | (np.abs(gx) < 0.10 * r)))[:, :, None]
    hub = (rad <= 0.26 * r)[:, :, None]
    out |= (rim & (zt < 0.42)) | (spoke & (zt < 0.20)) | hub
    return out


def _check_pose_voxels() -> None:
    """Rotation only, then reseat -- the semantics Phase 3 inserts depend on."""
    box = trimesh.creation.box(extents=(120.0, 60.0, 24.0))
    flat = pose_voxels(box, np.eye(3), cell_mm=12.0)
    # Surface voxels round outward (nesting.occupancy), so the 120mm and 24mm
    # edges take one cell more than the exact division -- the pitch it measures
    # errs generous, which is safe.
    assert flat.shape == (11, 5, 3), flat.shape
    # +90 deg about x: the 24mm axis goes to y, the 60mm axis goes up.
    rx = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
    turned = pose_voxels(box, rx, cell_mm=12.0)
    assert turned.shape == (flat.shape[0], flat.shape[2], flat.shape[1]), \
        turned.shape
    # Reseated, not re-centred: occupancy touches index 0 on every axis.
    for ax in range(3):
        assert turned.any(axis=tuple(a for a in range(3) if a != ax))[0], \
            "axis %d is not seated at the origin" % ax
    print("PASS  pose_voxels rotates and reseats: %s -> %s"
          % (flat.shape, turned.shape))


def _check_translucent() -> None:
    """The parts must survive the dunnage drawn through them, and the far->near
    ordering must stay GLOBAL across both volumes.

    This is the trap the ticket calls the expensive one: one labelled volume
    means a later `_fill` overwrites the part it is meant to hold, and painting
    the volumes one after the other puts every dunnage face in front of every
    part face regardless of depth, which is the bug at the top of this file.
    """
    prt, dun = (np.zeros((4, 4, 4), dtype=np.int16) for _ in range(2))
    prt[1:3, 1:3, 1:3] = LABEL0          # a part
    dun[:, :, :] = LABEL0 + 1            # dunnage drawn straight through it

    ga, gb = _exposed(prt, 12.0), _exposed(dun, 12.0)
    assert len(ga[0]) == 12 and len(gb[0]) == 48, (len(ga[0]), len(gb[0]))
    d = np.concatenate([ga[1], gb[1]])
    src = np.concatenate([np.zeros(len(ga[1]), int), np.ones(len(gb[1]), int)])
    order = np.argsort(d, kind="stable")
    assert (np.diff(d[order]) >= 0).all(), "not sorted far->near"
    assert (np.diff(src[order]) != 0).sum() > 2, \
        "the volumes paint as two blocks, not as one ordering"

    fig, ax = plt.subplots()
    pal = np.zeros((LABEL0 + 2, 4))
    pal[LABEL0] = matplotlib.colors.to_rgba(C_PART, 1.0)
    pal[LABEL0 + 1] = matplotlib.colors.to_rgba("#38BDF8", A_TRAY)
    n = _paint(ax, [prt, dun], lambda lab: pal[lab], 12.0)
    fc = ax.collections[0].get_facecolor()
    plt.close(fig)
    assert n == 12 + 48, n
    assert (fc[:, 3] == 1.0).sum() == 12, "the part lost its opacity"
    assert (fc[:, 3] == A_TRAY).sum() == 48, "the dunnage lost its alpha"
    print("PASS  parts survive dunnage painted through them: %d quads, "
          "one ordering, alphas %s"
          % (n, sorted(set(np.round(fc[:, 3], 2)))))


def _check_count_bites(case) -> None:
    """Non-vacuity: the drawn-vs-BOM count check must be able to FAIL.

    It only can because the drawn counts come off the lattice and the expected
    counts off the BOM. Bump one qty and the render must refuse.
    """
    import dataclasses

    ref, asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    name = bom.elements[1].name
    tampered = dataclasses.replace(bom, elements=[
        dataclasses.replace(e, qty=e.qty + 2) if e.name == name else e
        for e in bom.elements])
    try:
        explode_png(voxels=_demo_voxels(extent, CELL_MM, kind),
                    extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                    inner_lbh=inner, bom=tampered, asset_name=asset,
                    count=count)
    except AssertionError as exc:
        print("PASS  count check bites: %s" % exc)
        return
    raise AssertionError("qty %r + 2 still rendered: the count check is "
                        "decoration" % name)


def _check_parts_bite(case) -> None:
    """Non-vacuity: the drawn-PARTS check must be able to FAIL.

    It could not while it counted loop trips -- `counts[None] = n` made the
    assertion `product(grid) == product(grid)`. Both cases below rendered a
    clean 0.6MB picture of an empty box and asserted 40 parts drawn.
    """
    ref, asset, extent, pitch, grid, inner, count, _kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    n = [max(1, int(np.ceil(e / CELL_MM))) for e in extent]
    empty = np.zeros(tuple(n), dtype=bool)
    # Occupied only where the volume cannot reach: `np.putmask` on a slice
    # that fell outside the volume writes nothing and raises nothing.
    beyond = np.zeros((n[0] * 3, n[1], n[2]), dtype=bool)
    beyond[n[0] * 2:] = True
    for what, vox in (("no occupied voxel", empty),
                      ("occupancy outside the volume", beyond)):
        try:
            explode_png(voxels=vox, extent_lbh=extent, pitch_lbh=pitch,
                        grid=grid, inner_lbh=inner, bom=bom,
                        asset_name=asset, count=count)
        except AssertionError as exc:
            print("PASS  parts check bites (%s): %s" % (what, exc))
            continue
        raise AssertionError("%s still rendered %d parts: the parts check is "
                             "decoration" % (what, count))


def _check_bar_width_follows_bom(case) -> None:
    """The drawn bar width must come off the BOM element, not a constant.

    `dunnage.bom(..., centre_bar_w_mm=120)` labelled the bar "750x120x66" and
    drew 60: two expressions for one number, hard rule 9.
    """
    import dataclasses

    _ref, _asset, extent, pitch, grid, inner, _count, _kind = case
    got = []
    for w in (dunnage.CENTRE_BAR_W_MM, 120.0):
        bom = dunnage.bom(extent, pitch, grid, inner, centre_bar_w_mm=w)
        el = {e.name: e for e in bom.elements}
        rows = _bar_and_rod_rows(el, extent, pitch, grid, inner, CELL_MM)
        row = next(r for r in rows if r.name == "Top Center Bar")
        solids, _voids = row.geo()
        drawn = {sz[0] for _o, sz in solids}
        assert drawn == {w}, "BOM says width %g, drawing draws %s" % (w, drawn)
        got.append((el["Top Center Bar"].size, drawn.pop()))
    assert got[0][1] != got[1][1], got
    # And with no width in the BOM at all, the DRAW-ONLY fallback stands in.
    bom = dunnage.bom(extent, pitch, grid, inner)
    el = {e.name: e for e in bom.elements}
    e = el["Top Center Bar"]
    el["Top Center Bar"] = dataclasses.replace(
        e, dims_mm=(e.dims_mm[0], None, e.dims_mm[2]))
    rows = _bar_and_rod_rows(el, extent, pitch, grid, inner, CELL_MM)
    solids, _v = next(r for r in rows if r.name == "Top Center Bar").geo()
    assert {sz[0] for _o, sz in solids} == {W_CENTRE_BAR}, solids[0]
    print("PASS  bar width follows the BOM: %s -> %g mm, %s -> %g mm, "
          "no width -> fallback %g mm"
          % (got[0][0], got[0][1], got[1][0], got[1][1], W_CENTRE_BAR))


def _check_spec_column(case) -> None:
    """R4 item 3: every number the spec column prints IS a BOM or layout field.

    The column is the drawing's contract with the BOM (hard rule 9). A
    dimension formatted from one of this module's DRAW-ONLY constants, or a
    qty counted off the lattice instead of read off the element, reads as
    entirely plausible on the picture -- W_CENTRE_BAR labelled over a BOM bar
    of another width is exactly the bug `_check_bar_width_follows_bom`
    exists for, one level down. So the numbers are pulled back OUT of the
    strings `explode_png` draws (`_labels`, its only text source for the
    column) and matched against the fields they claim to come from.
    """
    import dataclasses
    import re

    _ref, _asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    el = {e.name: e for e in bom.elements}
    rows = _place(voxels=_demo_voxels(extent, CELL_MM, kind),
                  extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                  inner_lbh=inner, bom=bom, count=count, cell_mm=CELL_MM).rows

    def nums(text: str) -> list:
        return [float(t) for t in re.findall(r"\d+(?:\.\d+)?", text)]

    def fields(title: str) -> set:
        """Every number this label is ALLOWED to print, off the BOM element or
        the layout -- nothing else."""
        if title == "Part":
            return ({float(v) for v in extent} | {float(v) for v in pitch}
                    | {float(v) for v in grid} | {float(count)})
        e = el[title]
        vals = {float(e.qty)} if e.qty is not None else set()
        for seq in (e.dims_mm, e.cell_mm or (), e.matrix or ()):
            vals |= {float(v) for v in seq if v is not None}
        # Free text the BOM wrote itself (spec, unknown): its numbers are the
        # element's own statement, not the drawing's.
        for t in (e.spec or "",) + tuple(e.unknown or ()):
            vals |= set(nums(t))
        return vals

    def stray(lb, allowed: set) -> list:
        out = []
        for line in [lb.qty, lb.dim] + list(lb.extras):
            for v in nums(line):
                if not any(abs(v - a) <= 1e-3 * max(1.0, abs(v))
                           for a in allowed):
                    out.append((line, v))
        return out

    labels = _labels(rows, el, extent, pitch, grid, count)
    assert len(labels) == len(rows), (len(labels), len(rows))
    seen = 0
    for lb in labels:
        assert lb.title == "Part" or lb.title in el, lb.title
        bad = stray(lb, fields(lb.title))
        assert not bad, ("%s: the spec column prints %s, which is no field of "
                         "the BOM element or the layout" % (lb.title, bad))
        seen += len(nums(lb.qty)) + len(nums(lb.dim))
    assert seen >= 2 * len(labels), seen        # every row printed qty + dims

    # Non-vacuity: move a qty in the BOM and the SAME predicate, against the
    # original fields, has to reject the label it produces. Without this the
    # loop above passes on a set that swallows everything.
    bit = False
    for name, e in el.items():
        if e.qty is None:
            continue
        moved = dict(el, **{name: dataclasses.replace(e, qty=e.qty + 2)})
        lb = next(l for l in _labels(rows, moved, extent, pitch, grid, count)
                  if l.title == name)
        bit = bit or bool(stray(lb, fields(name)))
    assert bit, "a moved qty still matched: the spec-column check is decoration"
    print("PASS  spec column is the BOM: %d labels, %d numbers, all off a BOM "
          "element or the layout" % (len(labels), seen))


def _check_undrawn_warns(case, catch) -> None:
    """The label-only exemption must stay NARROW: a NEW element the drawing
    has no geometry for still has to warn.

    `LABEL_ONLY` is a declaration, not an off switch -- the whole reason the
    suite's warning-as-error probe is worth keeping.
    """
    import dataclasses

    _ref, asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    added = dunnage.Element(name="Corner Angle", labels=("L", "B", "H"),
                            dims_mm=(750.0, 40.0, 40.0), qty=4,
                            spec="EVA 150 kg/cu m", basis="pattern")
    before = len(catch.msgs)
    explode_png(voxels=_demo_voxels(extent, CELL_MM, kind), extent_lbh=extent,
                pitch_lbh=pitch, grid=grid, inner_lbh=inner,
                bom=dataclasses.replace(bom, elements=bom.elements + [added]),
                asset_name=asset, count=count)
    new = catch.msgs[before:]
    del catch.msgs[before:]                 # expected here, an error anywhere else
    assert any("Corner Angle" in m for m in new), \
        "an undrawn element with a size and a qty went unreported: %s" % new
    assert not any("MS Rod" in m for m in new), new
    print("PASS  undrawn element still warns: %s" % new[0])


def _check_sequence(seq: dict, placement: _Placement, bom: dunnage.Bom,
                    asset_name: str, count: int, non_empty: list, inner,
                    ref: str) -> None:
    """F4: the JSON build sequence agrees with the BOM, the count and the GIF.

    Everything here is checked against a source OUTSIDE the sequence -- the
    BOM's own `qty`, the engine's `count`, the step set the GIF rendered --
    so it cannot pass by comparing the sequence with itself.
    """
    import json
    json.dumps(seq)                     # it has to survive result_json
    # ...and the response model (hard rule 9: pydantic drops undeclared keys
    # silently). Round-trip through the wire schema and compare to the
    # JSON form -- a key missing from schemas.py fails here, not in the UI.
    from .schemas import BuildSequenceOut
    wire = BuildSequenceOut(**seq).model_dump()
    assert json.loads(json.dumps(wire)) == json.loads(json.dumps(seq)), \
        "sequence lost or changed a field through BuildSequenceOut"
    assert [s["i"] for s in seq["steps"]] == list(non_empty), \
        "%s: sequence steps %s, non-empty steps %s (the gif frame count is " \
        "asserted against the same list)" \
        % (ref, [s["i"] for s in seq["steps"]], non_empty)
    drawn: dict = {}
    for step in seq["steps"]:
        assert step["kind"] == ("parts" if step["i"] % 2 else "dunnage"), step
        t, txt, meta, _c = _captions(placement, bom, asset_name, count,
                                     step["i"])
        assert (step["title"], step["text"], step["meta"]) == (t, txt, meta), \
            "%s: step %d caption was altered after _captions built it" % (ref, step["i"])
        for c in step["cuboids"]:
            drawn[c["name"]] = drawn.get(c["name"], 0) + 1
    for name, n in drawn.items():
        assert n == placement.el[name].qty, \
            "%s: sequence has %d x %r, BOM says qty %s" \
            % (ref, n, name, placement.el[name].qty)
    # Every element the picture draws must reach the sequence too, or a
    # missing row would pass the loop above vacuously.
    assert drawn.keys() == {r.name for r in placement.rows
                            if not r.is_parts and r.geo is not None}, \
        "%s: sequence draws %s, placement draws %s" \
        % (ref, sorted(drawn), sorted(r.name for r in placement.rows
                                      if not r.is_parts and r.geo is not None))
    origins = [o["origin"] for st in seq["steps"] for o in st["parts"]]
    assert len(origins) == count, \
        "%s: %d part origins, engine says %d" % (ref, len(origins), count)
    # The origins must BE the engine's lattice, in mm -- an implementation
    # emitting cell indices, metres, swapped axes or one repeated corner
    # stays inside the box and would pass the bound below.
    ox, oy = _origins(placement.extent, placement.pitch, placement.grid,
                      placement.inner)
    px, py, pz = placement.pitch
    z0 = next(r.z0 for r in placement.rows if r.is_parts)
    ga, gb, gk = placement.grid
    want = {(round(ox + a * px, 3), round(oy + b * py, 3), round(z0 + k * pz, 3))
            for k in range(gk) for a in range(ga) for b in range(gb)}
    assert {tuple(round(v, 3) for v in o) for o in origins} == want, \
        "%s: part origins are not the engine's lattice" % ref
    per_step = [len(st["parts"]) for st in seq["steps"] if st["kind"] == "parts"]
    assert per_step == [ga * gb] * gk, \
        "%s: parts per step %s, grid says %d x %d layers" % (ref, per_step, ga * gb, gk)
    for o in origins + [c["origin"] for st in seq["steps"]
                        for c in st["cuboids"]]:
        assert all(-1e-6 <= v <= inner[a] + 1e-6 for a, v in enumerate(o)), \
            "%s: origin %s is outside the %s inner" % (ref, o, tuple(inner))
    assert seq["pose_matrix"] is not None, "%s: sequence without a pose" % ref
    assert len(seq["pose_matrix"]) == 4 and all(
        len(r) == 4 for r in seq["pose_matrix"]), seq["pose_matrix"]
    assert seq["inner"] == [float(v) for v in inner], seq["inner"]


def _check_frames_false(case) -> None:
    """F5 S1: `build_gif(frames=False)` (no GIF -- a GLB part animates
    instead) must still produce the identical `packed_png` and `sequence` a
    `frames=True` call on the same fixture would -- both run the SAME
    `_place`/`_sequence`/`_captions` expression (hard rule 9); only whether
    the per-step matplotlib frames get rendered differs.
    """
    ref, asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    voxels = _demo_voxels(extent, CELL_MM, kind)
    kwargs = dict(voxels=voxels, extent_lbh=extent, pitch_lbh=pitch,
                 grid=grid, inner_lbh=inner, bom=bom, asset_name=asset,
                 count=count, pose_matrix=np.eye(4))
    gif, packed_with_frames, seq_with_frames = build_gif(**kwargs, frames=True)
    no_gif, packed_no_frames, seq_no_frames = build_gif(**kwargs, frames=False)
    assert gif is not None, "%s: frames=True must still build a gif" % ref
    assert no_gif is None, "%s: frames=False must return no gif bytes" % ref
    assert packed_no_frames == packed_with_frames, \
        "%s: frames=False packed PNG differs from frames=True's" % ref
    assert seq_no_frames == seq_with_frames, \
        "%s: frames=False sequence differs from frames=True's" % ref
    print("PASS  %-14s %-9s %s  frames=False packed PNG and sequence match "
          "frames=True byte-for-byte" % (bom.archetype, asset, ref))


def _check_layer_step_is_drawn() -> None:
    """The picture must stack at the step the count and the BOM charged (F11).

    With layer separators under the parts (no tray to share the pitch with)
    the vertical step is `extent_H + sheet` (T2: a rigid sheet defeats a
    vertical nest rather than sinking into it), which is what
    `nesting.layouts_for` solved the layer count on. Stacking the drawing at
    the raw pitch put every layer inside the sheet above it and topped the
    insert out below its own `build_height_mm` -- 24mm short on a real
    FLC12101 layout. Bar and tray archetypes step by the measured pitch
    (`layer_step_mm` returns it unchanged), so their pictures cannot move;
    this is the case that can.

    The fixture carries a REAL vertical nest -- extent_H 104 on a 100 pitch,
    4mm of nest depth. With extent_H == pitch_H the nest depth is zero, T2's
    `extent_H + sheet` and the rule it replaced (`pitch_H + max(0, sheet -
    nest_depth)`) both return 103, and this check cannot tell them apart; at
    104 they return 107 and 100, so it can.
    """
    from .nesting import lattice_count
    extent, pitch, inner = (300.0, 100.0, 104.0), (150.0, 100.0, 100.0), (1150.0, 750.0, 1003.0)
    _flat, in_plane, _ = lattice_count(extent, pitch, (inner[0], inner[1], extent[2]))
    dead = dunnage.dead_height_mm(extent, pitch, grid=in_plane)
    step = dunnage.layer_step_mm(extent, pitch, grid=in_plane)
    count, grid, _ = lattice_count(extent, (pitch[0], pitch[1], step),
                                   (inner[0], inner[1], inner[2] - dead))
    bom = dunnage.bom(extent, pitch, grid, inner)
    assert bom.archetype == "layer_sheets" and bom.layer_step_mm == step, \
        (bom.archetype, bom.layer_step_mm, step)

    vox = np.ones([int(np.ceil(e / CELL_MM)) for e in extent], dtype=bool)
    p = _place(voxels=vox, extent_lbh=extent, pitch_lbh=pitch, grid=grid,
               inner_lbh=inner, bom=bom, count=count, cell_mm=CELL_MM)
    top = max(o[2] + s[2] for r in p.rows
              if r.geo is not None and not r.is_parts for o, s in r.geo()[0])
    assert abs(top - bom.build_height_mm) < 1e-6, \
        "insert drawn %g mm tall, BOM says %g" % (top, bom.build_height_mm)
    print("PASS  %-14s step %g mm (pitch %g + %g nest depth and sheet): "
          "%d layers drawn, insert tops out at %g mm == BOM build height"
          % (bom.archetype, bom.layer_step_mm, pitch[2],
             bom.layer_step_mm - pitch[2], grid[2], top))


def _sheets_for(case):
    """(bom, [(element, figure)]) for one self-check case. One expression, so
    a check cannot render a different drawing from the one shipped."""
    _ref, asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    got = list(_sheet_figs(voxels=_demo_voxels(extent, CELL_MM, kind),
                           extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                           inner_lbh=inner, bom=bom, asset_name=asset,
                           count=count))
    return bom, got


def _dim_strings(fig) -> list:
    """Every dimension text on a sheet -- `gid="dim"`, so the title block's
    part count and drawing index are out of scope."""
    import matplotlib.text as mtext
    return [t.get_text() for t in fig.findobj(mtext.Text)
            if t.get_gid() == "dim"]


def _pocket_patches(fig) -> list:
    return [r for r in fig.findobj(Rectangle)
            if str(r.get_gid() or "").startswith("pocket")]


def _sheet_allowed(bom, e, p, inner, pitch) -> set:
    """Every number the sheet for element `e` is ALLOWED to print.

    Its own `dims_mm`, `cell_mm` and `qty`; the layout numbers a drawing of it
    legitimately carries (the asset inner it sits in, the lattice pitch, the
    vertical nest depth and layer step); the origins `_place` computed for
    THIS element -- and, on a tray, the two results the sheet computes and
    labels as such: the wall (pitch - pocket) and the edge margin (which is
    the first pocket's own origin, so already in the origin set).
    """
    vals = {float(v) for v in inner} | {float(v) for v in pitch}
    vals |= {float(bom.nest_depth_mm), float(bom.layer_step_mm)}
    vals |= {float(v) for v in e.dims_mm if v is not None}
    vals |= {float(v) for v in (e.cell_mm or ())}
    if e.qty is not None:
        vals.add(float(e.qty))
    row = next((r for r in p.rows if r.name == e.name), None)
    if row is not None and row.geo is not None:
        solids, _voids = row.geo()
        vals |= {round(float(o[a]), 1) for o, _s in solids for a in (0, 1, 2)}
    if e.matrix and e.cell_mm:
        vals |= {round(float(o[a]), 1) for o, _s in _tray_pockets(p)
                 for a in (0, 1)}
        vals |= {float(pitch[a]) - float(e.cell_mm[a]) for a in (0, 1)}
        # SECTION A-A has to show what the tray sits ON, and the sheet
        # thickness belongs to the other element.
        vals |= {float(x.dims_mm[2]) for x in bom.elements
                 if x.cell_mm is None and x.dims_mm[2] is not None}
    return vals


def _check_sheet_numbers(case) -> None:
    """F17, the whole point: a supplier sheet may print no number we did not
    measure -- on EVERY sheet, not just the tray.

    Harvested back OUT of the figure, exactly like `_check_spec_column` does
    for the exploded view. A number formatted from a DRAW-ONLY constant
    (`POCKET_WALL_MM` is 36mm and is inset into every drawn void), or a
    coordinate typed into a builder instead of read off `_place`, reads as
    entirely plausible on the page. Scoping this to the tray left exactly
    that hole on the bar and rod sheets.

    The pocket count rides along: a tray draws `matrix[0] x matrix[1]`
    pockets and nothing else draws any.
    """
    import re

    _ref, _asset, extent, pitch, grid, inner, count, kind = case
    bom, got = _sheets_for(case)
    try:
        assert len(got) == len(bom.elements), (len(got), len(bom.elements))
        p = _place(voxels=_demo_voxels(extent, CELL_MM, kind),
                   extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                   inner_lbh=inner, bom=bom, count=count, cell_mm=CELL_MM)
        seen = 0
        pockets = 0
        for e, fig in got:
            allowed = _sheet_allowed(bom, e, p, inner, pitch)
            for t in _dim_strings(fig):
                for v in (float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)):
                    seen += 1
                    assert any(abs(v - a) <= 0.05 for a in allowed), \
                        "the %r sheet prints %r, and %g is no dimension of " \
                        "that element, no inner/pitch/nest/step, and no " \
                        "origin _place computed" % (e.name, t, v)
            plan = [r for r in _pocket_patches(fig)
                    if r.get_gid() == "pocket"]
            want = e.matrix[0] * e.matrix[1] if e.matrix else 0
            assert len(plan) == want, \
                "the %r sheet draws %d pockets, expected %d" \
                % (e.name, len(plan), want)
            pockets += len(plan)
        assert seen >= 3 * len(got), seen    # every sheet states its own size
    finally:
        for _e, f in got:
            plt.close(f)
    print("PASS  %-13s %d sheets, %d pockets, %d dimension numbers, all off "
          "the BOM / inner / an origin" % (bom.archetype, len(got), pockets,
                                           seen))


def _check_layer_sheets_draw_no_pocket(case) -> None:
    """F11's third archetype has no tray, so no sheet may show a pocket --
    and there is still one drawing per BOM element."""
    bom, got = _sheets_for(case)
    try:
        assert bom.archetype == "layer_sheets", bom.archetype
        assert len(got) == len(bom.elements), (len(got), len(bom.elements))
        drawn = {e.name: len(_pocket_patches(f)) for e, f in got}
        assert not any(drawn.values()), \
            "a layer-sheet drawing shows pockets: %s" % drawn
    finally:
        for _e, f in got:
            plt.close(f)
    print("PASS  layer_sheets: %d sheets, %d elements, no pockets drawn"
          % (len(got), len(bom.elements)))


def _check_rod_sheet(case) -> None:
    """Mubea: one PNG per element, and the rod's own d x L reaches the page."""
    _ref, asset, extent, pitch, grid, inner, count, kind = case
    bom = dunnage.bom(extent, pitch, grid, inner)
    pngs = insert_sheets_png(voxels=_demo_voxels(extent, CELL_MM, kind),
                             extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                             inner_lbh=inner, bom=bom, asset_name=asset,
                             count=count)
    assert len(pngs) == len(bom.elements), (len(pngs), len(bom.elements))
    assert all(Image.open(BytesIO(b)).format == "PNG" for b in pngs)
    import re

    _bom, got = _sheets_for(case)
    try:
        rod = next(e for e in bom.elements if "d" in e.labels)
        fig = next(f for e, f in got if e.name == rod.name)
        # Same harvest as `_check_sheet_numbers`, asked the other way round:
        # not "is every number allowed" but "did the two numbers that ARE the
        # rod actually reach the page". A substring test on the raw strings
        # passed while a second copy of the label carried it.
        drawn = {float(x) for t in _dim_strings(fig)
                 for x in re.findall(r"\d+(?:\.\d+)?", t)}
        missing = [v for v in rod.dims_mm if not any(abs(v - d) <= 0.05
                                                     for d in drawn)]
        assert not missing, "the rod sheet never prints %s (it prints %s)" \
            % (missing, sorted(drawn))
    finally:
        for _e, f in got:
            plt.close(f)
    print("PASS  bar_and_rod: %d sheets for %d elements, rod sheet carries "
          "d%g and %g" % (len(pngs), len(bom.elements), *rod.dims_mm))


def _check_ortho_panels(outdir) -> None:
    """The orthographic report drawing must actually SEPARATE parts (F15).

    A 3 x 2 x 4 lattice of a well-separated box: Top is 6 silhouettes, Front
    is 4 occupied row bands (one per layer). Both numbers come off the decoded
    PNG, not off the lattice -- a view flattened along the wrong axis, a
    transposed mask or silhouettes merged into one blob all fail here while
    every count in `_place` still asserts clean.

    Panels are located by their own box outlines: the two full-height dark
    columns per panel. Clustering the blue by gaps cannot work -- the gap
    between two part columns inside a panel is the same order as the gutter
    between panels.
    """
    from pathlib import Path
    from scipy import ndimage

    extent, pitch, grid = (200.0, 150.0, 80.0), (300.0, 300.0, 150.0), (3, 2, 4)
    inner, count = (1000.0, 700.0, 700.0), 24
    bom = dunnage.bom(extent, pitch, grid, inner)
    vox = np.ones([int(np.ceil(e / CELL_MM)) for e in extent], dtype=bool)
    png = ortho_png(voxels=vox, extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                    inner_lbh=inner, bom=bom, asset_name="CHECK", count=count)
    path = Path(outdir) / "ortho_check_3x2x4.png"
    path.write_bytes(png)

    a = np.asarray(Image.open(BytesIO(png)).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    blue = (b > 100) & (b - r > 60) & (b - g > 60)
    dark = (r < 90) & (g < 90) & (b < 90)
    tall = dark.sum(axis=0)
    panels = _runs(tall >= 0.5 * tall.max())        # 2 box edges per panel
    assert len(panels) == 6, \
        "found %d full-height box edges, expected 6 (3 panels)" % len(panels)
    spans = [(panels[i][0], panels[i + 1][1]) for i in (0, 2, 4)]
    names = [v[0] for v in ORTHO_VIEWS]
    front, _side, top = (blue[:, x0:x1] for x0, x1 in spans)

    # Close 1-2px dropouts before counting: imshow resamples the boolean
    # raster onto pixels and the odd edge row antialiases out of the blue
    # test, splitting one band in two. The real gaps here are 70mm+ (~30px),
    # so nothing a closing of 4px can merge is a gap the drawing meant.
    n_top = ndimage.label(ndimage.binary_closing(top, np.ones((5, 5))))[1]
    assert n_top == 6, "%s panel has %d blue components, expected 6" \
        % (names[2], n_top)
    bands = _runs(ndimage.binary_closing(front.any(axis=1), np.ones(5)))
    assert len(bands) == 4, "%s panel has %d occupied row bands, expected 4" \
        % (names[0], len(bands))

    # The x4 bilinear smoothing must move the EDGE, never the area: it is a
    # resample of `_place`'s mask, not a dilation of it. Measured off the
    # decoded PNG against the mm area the lattice actually rasterises (the
    # part rounded up to whole cells x 6 silhouettes), with mm-per-pixel taken
    # from the Top box outline the panels were located by. Component and band
    # counts alone do NOT catch over-smoothing -- a 9x9 dilation and a
    # `> 0.0` threshold both left them at 6 and 4.
    mm_px = inner[0] / (np.mean(panels[5]) - np.mean(panels[4]))
    area = top.sum() * mm_px ** 2
    ref = 6 * np.prod([int(np.ceil(e / CELL_MM)) * CELL_MM for e in extent[:2]])
    assert abs(area / ref - 1) < 0.04, \
        "%s panel silhouettes cover %.0f mm2, lattice says %.0f (%.1f%% off) " \
        "-- the smoothing is growing the shape, not just its edge" \
        % (names[2], area, ref, 100 * (area / ref - 1))
    print("PASS  ortho panels %s: Top %d silhouettes (%.1f%% of the lattice "
          "area), Front %d layer bands  ->  %s"
          % ("/".join(names), n_top, 100 * area / ref, len(bands), path))


def _selfcheck(outdir) -> int:
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.nesting import lattice_count
    from tests.ground_truth import CASES

    _check_pose_voxels()
    _check_translucent()

    # The two shipped ground-truth lattices, plus the real-CAD bar lattice the
    # ticket quotes (it lives in tests/test_dunnage.py, not ground_truth.py).
    cases = []
    for c in CASES:
        count, grid, _lim = lattice_count(c.pose_lbh, c.pitch_lbh,
                                          c.asset.inner, c.part_kg,
                                          c.asset.max_weight_kg)
        cases.append((c.ref, c.asset.name, c.pose_lbh, c.pitch_lbh, grid,
                      c.asset.inner, count,
                      "bar" if c is CASES[0] else "wheel"))
    # The real-CAD bar lattice, straight off `tests/test_clearance.py` (which
    # asserts it against the customer IGES with shipped defaults): the YXA bar
    # measures 1092x300x148 and nests at pitch (1097, 145, 68) to grid
    # (1, 4, 10) = 40 in PLS12801. The old 73 / (1,4,9) / 36 here were the
    # single-clearance numbers this module's docstring calls wrong -- 5mm of
    # air charged BETWEEN layers as well as beside neighbours.
    real_bar = ("real-CAD bar", "PLS12801", (1092.0, 300.0, 148.0),
                (1097.0, 145.0, 68.0), (1, 4, 10), (1150, 750, 790), 40, "bar")
    # Retyped numbers go stale silently: this fixture sat at pitch 73 / grid
    # (1,4,9) / 36 for a whole phase. `test_clearance` owns the count and the
    # grid, so take them from it (import only, no CAD read).
    from tests.test_clearance import CASES as CAD_CASES
    _t, _g = next((c[3], c[4]) for c in CAD_CASES if c[2].name == "PLS12801")
    assert (real_bar[4], real_bar[6]) == (_g, _t), \
        "real-CAD fixture %s/%s vs test_clearance %s/%s" % (
            real_bar[4], real_bar[6], _g, _t)
    cases.append(real_bar)
    # No vertical interleave -> `dunnage` emits no bottom separator assembly
    # (correctly: there is no nest depth to provide), and the drawing used to
    # index it unconditionally. The worker swallowed the KeyError into
    # `drawing_url: None` with nothing in `warnings`.
    cases.append(("no vertical nest", "PLS1280", (200.0, 100.0, 60.0),
                  (100.0, 100.0, 60.0), (1, 7, 16), (1150, 750, 1000), 112,
                  "bar"))
    # F11's third archetype: interleaved in plan (pitch_L 150 under a 300mm
    # part), flat in z -> "layer_sheets", a BOM with sheets and NO tray. The
    # drawing routed every non-bar BOM to the pocket-tray builder and raised
    # StopIteration on this one. Synthetic numbers, not the customer's.
    cases.append(("interleaved in plan", "PLS12103", (300.0, 800.0, 70.0),
                  (150.0, 800.0, 72.0), (5, 1, 10), (1150, 950, 1000), 50,
                  "bar"))

    # A component the BOM lists and the picture does not contain is the
    # failure the count assertions cannot see: they count cuboids, not cells.
    # `explode_png` warns; here that warning is an error.
    class _Catch(logging.Handler):
        msgs: list = []

        def emit(self, record):
            self.msgs.append(record.getMessage())

    catch = _Catch(level=logging.WARNING)
    logger.addHandler(catch)

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for ref, asset, extent, pitch, grid, inner, count, kind in cases:
        bom = dunnage.bom(extent, pitch, grid, inner)
        t0 = time.perf_counter()
        png = explode_png(voxels=_demo_voxels(extent, CELL_MM, kind),
                          extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                          inner_lbh=inner, bom=bom, asset_name=asset,
                          count=count)
        dt = time.perf_counter() - t0
        img = Image.open(BytesIO(png))
        assert img.format == "PNG", img.format
        assert img.width > 400 and img.height > 400, img.size
        # It runs in the Celery worker on every solve, once per ranked layout.
        assert dt < 6.0, "render took %.1fs" % dt
        path = outdir / ("explode_%s_%s.png"
                         % (bom.archetype, ref.replace("/", "_").replace(" ", "_")))
        path.write_bytes(png)
        print("PASS  %-14s %-9s %s  %d parts, grid %s  ->  %s (%dx%d, "
              "%.2f MB, %.2fs)"
              % (bom.archetype, asset, ref, count, grid, path,
                 img.width, img.height, len(png) / 1e6, dt))
        opath = outdir / ("ortho_%s_%s.png"
                          % (bom.archetype,
                             ref.replace("/", "_").replace(" ", "_")))
        opath.write_bytes(ortho_png(
            voxels=_demo_voxels(extent, CELL_MM, kind), extent_lbh=extent,
            pitch_lbh=pitch, grid=grid, inner_lbh=inner, bom=bom,
            asset_name=asset, count=count))
        print("        ortho  ->  %s" % opath)
        safe = ref.replace("/", "_").replace(" ", "_")
        for k, blob in enumerate(insert_sheets_png(
                voxels=_demo_voxels(extent, CELL_MM, kind), extent_lbh=extent,
                pitch_lbh=pitch, grid=grid, inner_lbh=inner, bom=bom,
                asset_name=asset, count=count), start=1):
            (outdir / ("insert_%s_%s_%d.png"
                       % (bom.archetype, safe, k))).write_bytes(blob)
        print("        %d insert sheets  ->  %s"
              % (len(bom.elements), outdir / ("insert_%s_%s_*.png"
                                              % (bom.archetype, safe))))
        for e in bom.elements:
            print("        %-46s qty %-5s %s"
                  % (e.name, e.qty,
                     LABEL_ONLY.get(e.name, "drawn").replace("not drawn: ",
                                                             "label only: ")))

        # build_gif: expected frame count off the SAME step rule build_gif
        # uses (1 + non-empty steps, the extra one being the held final
        # frame) -- introspected via `_place` itself, not retyped.
        placement = _place(voxels=_demo_voxels(extent, CELL_MM, kind),
                          extent_lbh=extent, pitch_lbh=pitch, grid=grid,
                          inner_lbh=inner, bom=bom, count=count,
                          cell_mm=CELL_MM)
        n_layers = grid[2]
        non_empty = sorted(set(placement.dun_at_step)
                           | {2 * k + 1 for k in range(n_layers)})
        expected = 1 + len(non_empty)

        t0 = time.perf_counter()
        gif, packed, seq = build_gif(
            voxels=_demo_voxels(extent, CELL_MM, kind), extent_lbh=extent,
            pitch_lbh=pitch, grid=grid, inner_lbh=inner, bom=bom,
            asset_name=asset, count=count, pose_matrix=np.eye(4))
        gdt = time.perf_counter() - t0
        gimg = Image.open(BytesIO(gif))
        assert gimg.n_frames == expected, \
            "%s: %d frames, expected %d (%d non-empty steps + hold)" \
            % (ref, gimg.n_frames, expected, len(non_empty))
        assert gimg.width > 400, gimg.size
        assert gdt < 20.0, "gif render took %.1fs" % gdt
        # The step-0 highlight bug a design review caught: gate on the label
        # volume too, or `dun_step`/`prt_step` == 0 also match every cell
        # NOTHING was ever written into (both default to 0), painting frame 0
        # solid. Assert `_step_masks` (what `build_gif` itself paints) picks
        # out exactly the cells `_place` actually wrote at step 0 -- not the
        # whole inner volume -- by checking it narrows the UNGATED match,
        # which is what the pre-fix code used and is exactly the regression.
        dm0, pm0 = _step_masks(placement.dun, placement.dun_step,
                               placement.prt, placement.prt_step, 0)
        gated0 = int(dm0.sum() + pm0.sum())
        raw0 = int(np.count_nonzero(placement.dun_step == 0)
                   + np.count_nonzero(placement.prt_step == 0))
        assert gated0 < raw0, \
            "%s: step-0 highlight is %d cells of %d raw -- the gate on the " \
            "label volume is not narrowing the match" % (ref, gated0, raw0)
        if placement.dun_at_step.get(0):    # this case has step-0 dunnage
            assert gated0 > 0, "%s: has step-0 dunnage but 0 cells " \
                "highlighted" % ref
        # 1.5MB/20s is the ticket's target for the shipped ground-truth
        # cases (cases[0]/[1], bar and wheel); the extra fixtures below --
        # 16 layers vs the wheel's 8 -- are not what that budget was set
        # against, so only report their size, don't gate on it.
        if ref in (cases[0][0], cases[1][0]):
            assert len(gif) < 1.5e6, "gif is %.2f MB" % (len(gif) / 1e6)
        first = np.asarray(gimg.convert("RGB"))
        gimg.seek(gimg.n_frames - 1)
        last = np.asarray(gimg.convert("RGB"))
        assert not np.array_equal(first, last), \
            "%s: first and last gif frame are identical" % ref
        # The packed-box PNG must BE the finished box, not merely some frame
        # of the right size: compare against `last` (gimg is already seeked
        # there). Size/flat-colour checks alone passed when the PNG was the
        # empty-box FIRST frame.
        pimg = Image.open(BytesIO(packed))
        assert np.array_equal(np.asarray(pimg.convert("RGB")), last), \
            "%s: packed PNG is not the gif's final frame" % ref
        # The box must not move between frames (it did: tight_layout tracked
        # the caption width). The asset wireframe is the leftmost dark pixel
        # below the caption card; its column must be the same in every frame.
        # Both axes: a ylim jitter slid the box 16 px vertically and passed
        # an X-only version of this check.
        edges = []
        for fi in range(gimg.n_frames):
            gimg.seek(fi)
            a = np.asarray(gimg.convert("RGB"))[200:-20].sum(2) < 600
            edges.append((int(np.nonzero(a.any(0))[0].min()),
                          int(np.nonzero(a.any(1))[0].min())))
        assert len(set(edges)) == 1, \
            "%s: box edge moves between gif frames (x, y): %s" % (ref, edges)
        _check_sequence(seq, placement, bom, asset, count, non_empty, inner,
                        ref)
        print("PASS  %-14s %-9s %s  sequence: %d steps, %d part origins, "
              "%d dunnage cuboids"
              % (bom.archetype, asset, ref, len(seq["steps"]),
                 sum(len(s["parts"]) for s in seq["steps"]),
                 sum(len(s["cuboids"]) for s in seq["steps"])))
        gpath = outdir / ("build_%s_%s.gif"
                          % (bom.archetype, ref.replace("/", "_").replace(" ", "_")))
        gpath.write_bytes(gif)
        print("PASS  %-14s %-9s %s  gif: %d frames  ->  %s (%dx%d, %.2f MB, "
              "%.2fs)" % (bom.archetype, asset, ref, gimg.n_frames, gpath,
                          gimg.width, gimg.height, len(gif) / 1e6, gdt))

        if ref in (cases[0][0], cases[1][0]):   # one bar, one wheel: eyeball
            safe_ref = ref.replace("/", "_").replace(" ", "_")
            framedir = outdir / ("gif_frames_%s" % safe_ref)
            framedir.mkdir(exist_ok=True)
            for fi in range(gimg.n_frames):
                gimg.seek(fi)
                gimg.convert("RGB").save(framedir / ("%02d.png" % fi))
            print("        %d frames dumped to %s" % (gimg.n_frames, framedir))
    assert not catch.msgs, catch.msgs
    print("PASS  every drawn component survives into the picture at %gmm cells"
          " (label-only by declaration: %s)"
          % (CELL_MM, ", ".join(sorted(LABEL_ONLY))))
    _check_count_bites(cases[0])
    _check_parts_bite(cases[0])
    _check_bar_width_follows_bom(cases[0])
    _check_spec_column(cases[0])
    _check_spec_column(cases[1])
    _check_undrawn_warns(cases[0], catch)
    assert not catch.msgs, catch.msgs
    _check_frames_false(cases[0])
    _check_sheet_numbers(cases[1])              # F17: the TRW pocket tray
    _check_sheet_numbers(cases[0])              # ...the Mubea bars and rod
    _check_sheet_numbers(cases[-1])             # ...and the plain sheets
    _check_layer_sheets_draw_no_pocket(cases[-1])   # "interleaved in plan"
    _check_rod_sheet(cases[0])                  # F17: the Mubea bar and rod
    _check_ortho_panels(outdir)
    _check_layer_step_is_drawn()
    logger.removeHandler(catch)
    print("all insert-drawing checks passed")
    return 0


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path as _Path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    default = str(_Path(tempfile.gettempdir()) / "insert_drawing_check")
    raise SystemExit(_selfcheck(sys.argv[1] if len(sys.argv) > 1 else default))
