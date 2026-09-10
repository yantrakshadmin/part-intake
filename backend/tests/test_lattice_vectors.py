"""DIAGNOSTIC: is the lattice collision-free on its MIXED vectors, not just its axes?

`nesting.min_pitch` slides one copy of the part along ONE axis and returns the
first offset where every multiple of it is clear. That part is right. But a 3D
lattice contains the vectors (i.px, j.py, k.pz), and disjointness at (px,0,0)
and (0,py,0) does not imply disjointness at (px,py,0). Nothing in the engine
checks the mixed ones. Reachable exactly when two or more axes have
pitch < extent -- which is the Mubea/YXA archetype, the reason this project
exists.

This file MEASURES that. It deliberately does not enforce it: `nesting.occupancy`
rounds surface voxels OUTWARD, so a strict "no shared cell at 4mm" rule forbids
designs that physically ship, and Mubea shipping 40 per PLS12801 is CLAUDE.md's
one non-negotiable fact. The bound asserted below came off the measurement (see
MEASURED_*), not off anyone's taste.

Nothing here imports from `app.nesting` except `occupancy`, `min_pitch` and
`lattice_count`, and nothing here modifies the engine.

Run:  python tests/test_lattice_vectors.py            # 4mm, 2mm, 1mm  (~3 min)
      python tests/test_lattice_vectors.py 4          # coarse only    (~25 s)
"""
import sys
import time
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                           # noqa: E402

from app.nesting import lattice_count, min_pitch, occupancy   # noqa: E402
from tests.ground_truth import PLS1280, PLS12801             # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "customer"
BAR = FIXTURES / "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"

# (file, part_kg, asset, shipped count, shipped grid) -- same contract as
# tests/test_clearance.py, so a pose/pitch regression shows up here too.
SHIPPED = [
    (BAR, 5.0, PLS12801, 40, (1, 4, 10)),
    (WHEEL, 2.5, PLS1280, 48, (3, 2, 8)),
]

# ---------------------------------------------------------------------------
# The bound, and where the number came from.
#
# MEASURED, 2026-09-10, both shipped designs, shipped defaults, real CAD:
#
#   YXA bar   pose "Largest face down"  pitch (1097, 145, 68) grid (1,4,10)
#     4mm voxel: 54 mixed vectors, 1 overlaps -- (0,1,1), 10 of 16503 cells
#                = 0.0606% of the part, 640 mm^3
#     2mm voxel: 0 cells      1mm voxel: 0 cells
#   QY2i wheel pose "Largest face down" pitch (377, 361, 116) grid (3,2,8)
#     4mm / 2mm / 1mm: 102 mixed vectors, 0 overlap. Only ONE axis interleaves
#     (116 < 140); pitch >= extent on both floor axes, so every vector with a
#     nonzero floor component clears the bounding box outright.
#
# So the worst thing the shipped designs do is 0.0606% at the coarsest voxel,
# and nothing at all once the voxel shrinks. The bound is the next round number
# above the measurement. It is not a tolerance anyone chose; halve the observed
# headroom and this fails.
MEASURED_WORST_FRACTION = 0.000606          # YXA bar, (0,1,1), 4mm voxel
MIXED_OVERLAP_FRACTION_BOUND = 0.001        # 0.1% of the part's own cells
# ---------------------------------------------------------------------------


def lattice_overlap(grid: np.ndarray, offset_cells) -> int:
    """Cells shared by `grid` and a copy of itself translated by `offset_cells`.

    Signed offsets, basic slicing only (a range index would copy the grid --
    same reason `min_pitch` uses slices).
    """
    hi, lo = [], []
    for n, off in zip(grid.shape, offset_cells):
        if abs(off) >= n:
            return 0                        # translated clear of the bbox
        hi.append(slice(off, n) if off >= 0 else slice(0, n + off))
        lo.append(slice(0, n - off) if off >= 0 else slice(-off, n))
    return int((grid[tuple(hi)] & grid[tuple(lo)]).sum())


def mixed_vectors(counts):
    """Lattice vectors with two or more nonzero components, one per +/- pair.

    `counts` is the lattice size (nx, ny, nz) actually used, so this enumerates
    only vectors between two parts that really exist in the layout.
    """
    ranges = [range(-(c - 1), c) for c in counts]
    for v in product(*ranges):
        if sum(1 for c in v if c) < 2:
            continue
        if v < tuple(-c for c in v):        # keep the lexicographically larger
            continue
        yield v


def measure_mixed(grid, counts, pitch_mm, voxel_mm) -> dict:
    """Mixed-vector overlap of one lattice. Cells, fraction of part, mm^3."""
    occupied = int(grid.sum())
    cell = [int(round(p / voxel_mm)) for p in pitch_mm]
    rows = []
    for v in mixed_vectors(counts):
        n = lattice_overlap(grid, tuple(c * s for c, s in zip(v, cell)))
        if n:
            rows.append((v, n))
    rows.sort(key=lambda r: -r[1])
    worst = rows[0][1] if rows else 0
    return {
        "voxel_mm": voxel_mm,
        "occupied": occupied,
        "vectors": sum(1 for _ in mixed_vectors(counts)),
        "pitch_cells": tuple(cell),
        "pitch_used_mm": tuple(round(c * voxel_mm, 1) for c in cell),
        "overlapping": rows,
        "worst_cells": worst,
        "worst_fraction": worst / occupied if occupied else 0.0,
        "worst_mm3": worst * voxel_mm ** 3,
    }


# ---------------------------------------------------------------------------
# 1. Synthetic: an unambiguous mixed-vector collision.
# ---------------------------------------------------------------------------

def diagonal_blocks() -> np.ndarray:
    """Two 1-cell blocks on the plan diagonal: cells (0,0) and (1,1).

    Voxel-exact by construction -- built as a grid, never meshed, so there is
    no outward rounding to blame anything on. This is the smallest part that
    can interleave on two axes at once: one cell is a cube (pitch == extent,
    no mixed vector is reachable), and with two cells the diagonal pair is the
    only arrangement whose pitch is 1 on both floor axes.
    """
    g = np.zeros((2, 2, 1), dtype=bool)
    g[0, 0, 0] = True
    g[1, 1, 0] = True
    return g


def test_synthetic_diagonal_blocks():
    """Per-axis pitch says 1mm on every axis. The diagonal says otherwise."""
    g = diagonal_blocks()
    pitch = tuple(min_pitch(g, ax, voxel_mm=1.0, clearance_mm=0.0)
                  for ax in range(3))
    assert pitch == (1.0, 1.0, 1.0), pitch

    # Per-axis: exactly what min_pitch checked, and it is correct.
    for ax_off in ((1, 0, 0), (0, 1, 0)):
        assert lattice_overlap(g, ax_off) == 0, ax_off

    # Mixed: never checked, and it is a hard collision.
    assert lattice_overlap(g, (1, 1, 0)) == 1, "diagonal blocks must collide"

    extent = tuple(s * 1.0 for s in g.shape)
    inner = (4.0, 4.0, 1.0)
    count, grid_counts, _ = lattice_count(extent, pitch, inner)
    assert (count, grid_counts) == (9, (3, 3, 1)), (count, grid_counts)

    # Physically impossible, by pigeonhole and nothing subtler: 9 parts x 2
    # cells = 18 cells claimed inside a 4x4x1 = 16-cell region.
    claimed = count * int(g.sum())
    available = int(np.prod([int(round(i)) for i in inner]))
    assert claimed > available, (claimed, available)

    # 6 is the best honest lattice (pitch 2 on either floor axis).
    honest, honest_grid, _ = lattice_count(extent, (2.0, 1.0, 1.0), inner)
    assert (honest, honest_grid) == (6, (2, 3, 1)), (honest, honest_grid)
    for v in mixed_vectors(honest_grid):
        off = (v[0] * 2, v[1] * 1, v[2] * 1)
        assert lattice_overlap(g, off) == 0, off

    print(f"  diagonal blocks: per-axis pitch {pitch} -> engine {count} "
          f"{grid_counts}, claims {claimed} cells in a {available}-cell box; "
          f"honest lattice {honest} {honest_grid}")


def test_single_voxel_part_has_no_mixed_vector():
    """Why the 2-cell diagonal is minimal: one cell cannot get there."""
    g = np.ones((1, 1, 1), dtype=bool)
    pitch = tuple(min_pitch(g, ax, voxel_mm=1.0, clearance_mm=0.0)
                  for ax in range(3))
    assert pitch == (1.0, 1.0, 1.0), pitch
    # pitch == extent on every axis, so any nonzero component clears the bbox.
    _, grid_counts, _ = lattice_count((1.0, 1.0, 1.0), pitch, (4.0, 4.0, 4.0))
    for v in mixed_vectors(grid_counts):
        assert lattice_overlap(g, v) == 0, v
    print("  1-cell part: no mixed vector can overlap (pitch == extent)")


# ---------------------------------------------------------------------------
# 2 + 3. The two shipped designs, from real CAD, at shipped defaults.
# ---------------------------------------------------------------------------

def _pose_axis_lattice(layout, pose_extent):
    """Layout grid/pitch are in FOOTPRINT order; the occupancy grid is in pose
    order. `Pose.footprint_orders` yields (0,1,2) or (1,0,2), so pick whichever
    lines the layout extent up with the grid we actually have."""
    perm = min(((0, 1, 2), (1, 0, 2)),
               key=lambda p: sum(abs(layout.extent_lbh[i] - pose_extent[p[i]])
                                 for i in range(3)))
    counts, pitch = [0, 0, 0], [0.0, 0.0, 0.0]
    for layout_axis, pose_axis in enumerate(perm):
        counts[pose_axis] = layout.grid[layout_axis]
        pitch[pose_axis] = layout.pitch_lbh[layout_axis]
    return tuple(counts), tuple(pitch)


def test_shipped_designs_mixed_overlap(voxels=(4.0, 2.0, 1.0)):
    """MEASURE, then assert only what the shipped designs demonstrate.

    Both are known-good by definition -- they physically ship -- so whatever
    mixed-vector overlap they carry IS the empirical tolerance. Printed per
    vector; asserted against MIXED_OVERLAP_FRACTION_BOUND above.
    """
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import nest

    missing = [p.name for p, *_ in SHIPPED if not p.exists()]
    if missing:
        print(f"  skipped: no customer fixtures (NDA, not in git): {missing}")
        return

    for path, part_kg, asset, target, grid in SHIPPED:
        result = extract_part(str(path))
        mesh, _ = load_unified_mesh(result.glb_path)
        # DEFAULTS ONLY -- the shipped configuration, like test_clearance.py.
        best = nest(mesh, result.candidates, asset, part_kg=part_kg)[0]
        assert (best.count, best.grid) == (target, grid), (
            f"{path.name}: engine says {best.count} {best.grid}, shipped "
            f"{target} {grid} -- fix that before reading anything below")
        cand = next(c for c in result.candidates if c.label == best.pose_label)

        print(f"\n  {path.name[:44]}")
        print(f"    {best.count} in {asset.name} {best.grid}  pose "
              f"{best.pose_label!r}\n    extent {best.extent_lbh}  pitch "
              f"{best.pitch_lbh}  interleaves on "
              f"{sum(1 for p, e in zip(best.pitch_lbh, best.extent_lbh) if p < e)}"
              f"/3 axes")

        coarse = fine = None
        for voxel_mm in voxels:
            t0 = time.time()
            g = occupancy(mesh, cand.rotation_matrix, voxel_mm)
            counts, pitch = _pose_axis_lattice(
                best, tuple(s * voxel_mm for s in g.shape))
            m = measure_mixed(g, counts, pitch, voxel_mm)
            coarse = coarse or m
            fine = m
            print(f"    voxel {voxel_mm:>4}mm  grid {g.shape} "
                  f"occupied {m['occupied']:>7}  {m['vectors']:>3} mixed "
                  f"vectors  ({time.time() - t0:.0f}s)")
            print(f"      lattice pitch {m['pitch_used_mm']}mm "
                  f"= {m['pitch_cells']} cells")
            if m["overlapping"]:
                for v, n in m["overlapping"]:
                    print(f"      {str(v):>12}: {n:>5} cells = "
                          f"{100 * n / m['occupied']:.4f}% of part = "
                          f"{n * voxel_mm ** 3:.0f} mm^3")
            else:
                print("      no overlap on any mixed vector")
            print(f"      WORST {m['worst_cells']} cells = "
                  f"{100 * m['worst_fraction']:.4f}% = {m['worst_mm3']:.0f} mm^3")

            # The bound. Measured, not chosen -- see MEASURED_WORST_FRACTION.
            assert m["worst_fraction"] <= MIXED_OVERLAP_FRACTION_BOUND, (
                f"{path.name} at {voxel_mm}mm: mixed vector "
                f"{m['overlapping'][0][0]} overlaps "
                f"{100 * m['worst_fraction']:.4f}% of the part, worse than the "
                f"{100 * MIXED_OVERLAP_FRACTION_BOUND:.3f}% the shipped "
                f"designs demonstrate")

        # Artefact, not interpenetration: outward-rounded surface voxels touch
        # at the coarse grid and stop touching as the grid refines. Real
        # interpenetration would hold its volume. Measured on the bar at the
        # raw touching pitch (140, 68), where the effect is largest:
        # 1792 mm^3 at 4mm -> 232 at 2mm -> 10 at 1mm.
        if len(voxels) > 1:
            assert fine["worst_mm3"] <= coarse["worst_mm3"] + 1e-9, (
                f"{path.name}: mixed overlap did NOT shrink with the voxel "
                f"({coarse['worst_mm3']:.0f} -> {fine['worst_mm3']:.0f} mm^3) "
                f"-- that is real interpenetration, not rounding slop")
            print(f"    voxel {voxels[0]}mm -> {voxels[-1]}mm: worst overlap "
                  f"{coarse['worst_mm3']:.0f} -> {fine['worst_mm3']:.0f} mm^3 "
                  f"({'artefact' if fine['worst_mm3'] < coarse['worst_mm3']
                      else 'unchanged'})")


def main() -> int:
    voxels = tuple(float(a) for a in sys.argv[1:]) or (4.0, 2.0, 1.0)
    for fn in (test_synthetic_diagonal_blocks,
               test_single_voxel_part_has_no_mixed_vector):
        print(f"{fn.__name__}:")
        fn()
    print("test_shipped_designs_mixed_overlap:")
    test_shipped_designs_mixed_overlap(voxels)
    print("\nmixed-vector overlap within the bound the shipped designs set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
