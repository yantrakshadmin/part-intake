"""Phase 2 acceptance: the lattice nesting engine.

Run:  python tests/test_nesting.py

Same split as Phase 1, and for the same reason:

  ACCURACY  — `test_ground_truth`. Feeds each shipped proposal's own pitch into
              `lattice_count` and demands 40 and 48 exactly. This is the
              contract from CLAUDE.md and it is the reason the file exists.

  MECHANISM — `test_real_bar`. We have no CAD for the Mubea part, so this runs
              the pitch measurement on the YXA stabiliser bar, a real bent bar
              of the same archetype. It asserts that measured pitch comes in
              well under the bounding extent — i.e. that the engine sees the
              interleave at all. It is NOT a claim about the Mubea part.

Deliberately NOT here: mirrored/interleaved pairs (alternating 180 degrees),
the next rung of the PLANNING §4 angle ladder. Measured on the YXA bar it drops
the vertical pitch from 68mm to 40mm, which would put ~14 layers in a PLS12801
against the 10 that shipped. Overshooting ground truth is not a win — what
shipped reflects manufacturability we cannot see. Self-pitch already reproduces
both targets. Add mirroring when a real case needs it, and re-derive its
ground truth first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nesting import (Pose, lattice_count, min_pitch,  # noqa: E402
                         occupancy, plan_silhouettes, silhouette_runs)
from tests.ground_truth import CASES                          # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "customer"
BAR = FIXTURES / "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs"


def test_ground_truth():
    """Every shipped proposal, reproduced from its own pitch. Non-negotiable."""
    for c in CASES:
        count, grid, limited_by = lattice_count(
            c.pose_lbh, c.pitch_lbh, c.asset.inner, c.part_kg, c.asset.max_weight_kg
        )
        assert count == c.achieved, \
            f"{c.ref}: lattice gives {count} {grid}, proposal shipped {c.achieved}"
        assert grid[0] * grid[1] * grid[2] == c.achieved, f"{c.ref}: grid {grid}"
        assert c.layers in grid, \
            f"{c.ref}: no axis carries the deck's {c.layers} layers (grid {grid})"
        print(f"  {c.ref:<18} {count:>3} = {grid}  limited by {limited_by}")


def test_exact_fit_is_not_off_by_one():
    """Both cases fit an axis exactly (4 x 155 in 750 - 285; 3 x 376.6 in 1150).

    Float division makes that the fragile case, not the safe one: a pitch typed
    as 186.7 instead of 560/3 loses a whole row. Guard the tolerance directly.
    """
    assert lattice_count((100, 100, 100), (100, 100, 100), (300, 300, 300))[1] == (3, 3, 3)
    assert lattice_count((10, 10, 10), (10, 10, 10), (10, 10, 10))[1] == (1, 1, 1)
    assert lattice_count((11, 10, 10), (11, 10, 10), (10, 300, 300))[0] == 0, \
        "part longer than the asset must not fit"
    print("  exact-fit tolerance ok")


def test_weight_cap():
    """A light asset limit must beat geometry, say so, and stay self-consistent.

    `count` must always equal the product of `grid`. Returning 10 next to a
    3x3x3 grid is the PLANNING §7 "minimum of 48 and 46" defect: a UI showing
    both prints two numbers that disagree. So the cap drops whole layers -- 9
    parts in 3x3x1, not "10 of 27".
    """
    count, grid, limited_by = lattice_count((100,) * 3, (100,) * 3, (300,) * 3,
                                            part_kg=5.0, max_weight_kg=50.0)
    assert limited_by == "weight", (count, grid, limited_by)
    assert count == grid[0] * grid[1] * grid[2] == 9, (count, grid)
    assert count * 5.0 <= 50.0, "over the weight cap"

    # Too heavy for a full layer: shrink the footprint, do not just drop rows.
    # 20kg/5kg = 4 parts, and 2x2x1 spends the whole cap. The old cascade held
    # the long axis fixed and answered 3x1x1 = 3 here -- self-consistent, and
    # one part short.
    for cap, want in ((20.0, (2, 2, 1)), (10.0, (2, 1, 1)), (2.0, (0, 1, 1))):
        c, g, lim = lattice_count((100,) * 3, (100,) * 3, (300,) * 3,
                                  part_kg=5.0, max_weight_kg=cap)
        assert g == want and c == g[0] * g[1] * g[2], (cap, c, g)
        assert lim == "weight"

    # The cascade's worst case: a long thin asset, where holding the long axis
    # fixed costs 30%. Geometry gives 7x2x1 = 14; a 10-part cap fits 5x2x1 = 10
    # exactly, and the cascade returned 7.
    c, g, lim = lattice_count((100, 100, 100), (100, 100, 100), (750, 250, 150),
                              part_kg=60.0, max_weight_kg=600.0)
    assert (c, g) == (10, (5, 2, 1)), (c, g)
    assert c * 60.0 <= 600.0 and c == g[0] * g[1] * g[2]

    # A tie goes to the fullest footprint: 3x3x1 is an insert, 3x1x3 is a tower.
    c, g, _ = lattice_count((100,) * 3, (100,) * 3, (300,) * 3,
                            part_kg=5.0, max_weight_kg=50.0)
    assert g == (3, 3, 1), g

    print("  weight cap ok, count == product(grid) at every cap")


def test_pitch_is_clear_at_every_lattice_multiple():
    """A lattice puts copies at 0, p, 2p, 3p... so EVERY multiple must be clear.

    Regression for a real bug: `min_pitch` used to check only the neighbouring
    offset. Disjoint at p does not imply disjoint at 2p once the part is
    non-convex -- and non-convex comb/bent-bar parts are the entire reason this
    engine exists. A part occupying cells {0,1,4} is pairwise-clear at pitch 2,
    but copies 0 and 2 both want cell 4. The engine reported 4 parts where 2
    fit.
    """
    import numpy as np
    grid = np.zeros((7, 1, 1), dtype=bool)
    grid[[0, 1, 4], 0, 0] = True

    p = min_pitch(grid, 0, voxel_mm=1.0, clearance_mm=0.0)
    assert p >= 5.0, f"pitch {p} collides with itself at 2p (the old bug gave 2.0)"

    # Prove it directly: lay out the lattice this pitch implies and look for
    # two copies wanting the same cell.
    occupied = {}
    for copy in range(4):
        for cell in (0, 1, 4):
            at = cell + copy * int(p)
            assert at not in occupied, \
                f"cell {at} claimed by copies {occupied[at]} and {copy} at pitch {p}"
            occupied[at] = copy
    print(f"  {{0,1,4}} -> pitch {p}, no collision at any multiple")


def test_cuboid_has_no_interleave():
    """A box cannot nest into a box. Pitch must equal extent, or the whole
    engine is reporting phantom gains."""
    import trimesh
    box = trimesh.creation.box(extents=(200.0, 100.0, 60.0))
    grid = occupancy(box, None, voxel_mm=4.0)
    for axis, extent in enumerate((200.0, 100.0, 60.0)):
        p = min_pitch(grid, axis, voxel_mm=4.0, clearance_mm=0.0)
        assert p >= extent - 4.0, f"axis {axis}: pitch {p} < extent {extent}"
    print("  cuboid pitch == extent ok")


def _mask_from_runs(sil) -> "object":
    """Rebuild the boolean mask from the wire format. Round-trip check."""
    import numpy as np
    m = np.zeros((sil["rows"], sil["cols"]), dtype=bool)
    for r, c0, c1 in sil["runs"]:
        m[r, c0:c1 + 1] = True
    return m


def test_silhouette_round_trips_and_permutes_with_the_footprint():
    """The plan silhouette must rotate WITH the extent it belongs to.

    `Pose.footprint_orders` yields the two 90-degree in-plane assignments by
    permuting extent and pitch together. Order (1, 0, 2) means the mask is
    ROTATED a quarter turn (`np.rot90(mask, 1)`), NOT transposed -- a
    transpose is a mirror and a part cannot be mirrored (F7). Get this wrong
    and the insert drawing is 90 degrees out while every number on screen
    stays correct -- the exact defect class CLAUDE.md hard rule 9 exists for,
    and no count assertion can catch it.
    """
    import numpy as np
    # Deliberately asymmetric: an L, so a transpose is detectable.
    plan = np.zeros((4, 3), dtype=bool)
    plan[0, :] = True
    plan[:, 0] = True

    pose = Pose("test", (16.0, 12.0, 8.0), (16.0, 12.0, 8.0),
                silhouettes=plan_silhouettes(plan, 4.0))
    orders = list(pose.footprint_orders())
    assert len(orders) == 2, orders

    (ext0, _p0, sil0, _c0), (ext1, _p1, sil1, _c1) = orders
    assert np.array_equal(_mask_from_runs(sil0), plan), sil0
    assert np.array_equal(_mask_from_runs(sil1), np.rot90(plan, 1)), sil1
    assert not np.array_equal(_mask_from_runs(sil1), plan.T), \
        "order (1,0,2) mirrored the part instead of rotating it"
    assert (sil0["rows"], sil0["cols"]) == (4, 3)
    assert (sil1["rows"], sil1["cols"]) == (3, 4), \
        "order (1,0,2) did not turn the mask"

    # The assertion that catches a transposed mask: the silhouette's own
    # bounding size must equal the extent it was yielded with.
    for ext, sil in ((ext0, sil0), (ext1, sil1)):
        assert abs(sil["rows"] * sil["cell_mm"] - ext[0]) <= sil["cell_mm"], (ext, sil)
        assert abs(sil["cols"] * sil["cell_mm"] - ext[1]) <= sil["cell_mm"], (ext, sil)

    # No pose plan (synthesis builds one from bare numbers) -> no silhouette,
    # not a fabricated one.
    bare = list(Pose("bare", (1, 1, 1), (1, 1, 1)).footprint_orders())
    assert all(sil is None for _e, _p, sil, _c in bare), bare
    print("  silhouette round-trips and turns 90 degrees with order (1,0,2)")


def test_cuboid_silhouette_is_solid():
    """A box's plan view is a filled rectangle. If it comes back with holes,
    run-length encoding or the projection axis is wrong."""
    import trimesh
    box = trimesh.creation.box(extents=(200.0, 100.0, 60.0))
    grid = occupancy(box, None, voxel_mm=4.0)
    sil = silhouette_runs(grid.any(axis=2), 4.0)
    filled = sum(c1 - c0 + 1 for _r, c0, c1 in sil["runs"])
    assert filled == sil["rows"] * sil["cols"], \
        f"cuboid silhouette is not solid: {filled}/{sil['rows'] * sil['cols']}"
    assert len(sil["runs"]) == sil["rows"], "a solid mask is one run per row"
    print(f"  cuboid silhouette solid: {sil['rows']}x{sil['cols']} cells")


def test_bar_silhouette_is_not_solid():
    """MECHANISM. A bent bar's footprint must have holes in it.

    A solid footprint for a bent tube means we projected along the wrong axis
    -- and the interleaved drawing built from it would be a rectangle, i.e.
    exactly the cuboid picture this project exists to replace.
    """
    if not BAR.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import measure_poses

    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)
    poses = measure_poses(mesh, r.candidates)
    for pose in poses:
        for extent, _pitch, sil, _clr in pose.footprint_orders():
            assert sil is not None, f"{pose.label}: no silhouette measured"
            cells = sil["rows"] * sil["cols"]
            filled = sum(c1 - c0 + 1 for _r, c0, c1 in sil["runs"])
            frac = filled / cells
            assert 0.0 < frac < 1.0, \
                f"{pose.label}: filled fraction {frac} - a bent bar is neither " \
                "empty nor solid in plan"
            assert abs(sil["rows"] * sil["cell_mm"] - extent[0]) <= sil["cell_mm"]
            assert abs(sil["cols"] * sil["cell_mm"] - extent[1]) <= sil["cell_mm"]
        print(f"  {pose.label:<28} {sil['rows']:>3}x{sil['cols']:<3} cells, "
              f"{frac * 100:5.1f}% filled, {len(sil['runs'])} runs")


def test_real_bar():
    """MECHANISM, not accuracy. Does the engine see a real bar's interleave?"""
    if not BAR.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import nest
    from tests.ground_truth import PLS12801

    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)
    # DEFAULTS. This used to pass `clearance_mm=0.0`, which zeroed BOTH axes
    # and let this test reach 40 while the shipped defaults returned 36 -- a
    # test proving the engine COULD hit the ground truth while the product
    # did not. E-CLEAR fixed the engine (the vertical clearance is its own
    # constant now) and the override became dead weight; it is removed so
    # nothing here tests a configuration nothing ships with. Accuracy lives
    # in tests/test_clearance.py; this file checks the MECHANISM.
    layouts = nest(mesh, r.candidates, PLS12801, part_kg=5.0)
    assert layouts, "no layout found for a 1092mm bar in a 1150mm asset"
    best = layouts[0]

    ratio_h = best.interleave[2]
    assert ratio_h < 0.75, \
        f"no vertical interleave detected (pitch/extent {ratio_h}) - the engine " \
        "has regressed to box stacking, which loses Mubea 32 parts"
    # The bar's OWN cuboid baseline, not Mubea's. Comparing this part's count
    # against another part's baseline can never fail and proves nothing.
    from tests.ground_truth import PLS12801, cuboid_baseline
    base, _ = cuboid_baseline(best.extent_lbh, PLS12801)
    assert best.count > base, f"{best.count} is no better than the cuboid answer {base}"
    print(f"  YXA bar -> {best.count} in {best.asset_name} {best.grid}")
    print(f"     extent {best.extent_lbh}  pitch {best.pitch_lbh}")
    print(f"     interleave (pitch/extent) {best.interleave}  vs cuboid {base}")


def test_catalogue_ranking():
    """MECHANISM. One entry per asset, best first, small crates excluded.

    Also pins the cost hoist: pitch is a property of the pose, not the asset, so
    ranking 15 containers must not voxelise 15 times. It measures 4 poses once.
    """
    if not BAR.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    import time
    from app.catalogue import containers
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import rank_catalogue

    assets = containers()
    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)

    t = time.time()
    ranked = rank_catalogue(mesh, r.candidates, assets, part_kg=5.0, top_n=0)
    elapsed = time.time() - t

    names = [l.asset_name for l in ranked]
    assert len(names) == len(set(names)), \
        f"an asset appears twice - ranking should collapse poses per asset: {names}"
    assert [l.count for l in ranked] == sorted((l.count for l in ranked), reverse=True), \
        "ranking is not sorted best-first"
    assert all(not n.startswith("CRT") for n in names), \
        f"a 550mm crate accepted a 1092mm bar: {names}"
    assert len(ranked) < len(assets), "every asset fit, including the small crates"

    # 4 poses x ~2.4s at 4mm. Looping nest() per asset would be ~15x this.
    assert elapsed < 60, f"ranking took {elapsed:.0f}s - the pitch hoist regressed"

    top2 = rank_catalogue(mesh, r.candidates, assets, part_kg=5.0, top_n=2)
    assert len(top2) == 2 and top2 == ranked[:2], "top_n disagrees with the full ranking"

    print(f"  {len(ranked)}/{len(assets)} containers fit, {elapsed:.1f}s, "
          f"{len(r.candidates)} poses measured once")
    for l in ranked[:3]:
        print(f"     {l.asset_name:<10} {l.count:>4}  {l.grid}  pitch {l.pitch_lbh}")


def test_ties_go_to_the_smaller_box():
    """Equal parts-per-box is NOT equal value -- the truck is volume-limited.

    Real case, measured over real HTTP on the TRW wheel: PLS12103 (inner
    1150x950x1000) ties PLS12803 (1150x750x1000) at 48 per box, because the
    wheel's 3x2 floor grid never uses the extra 200mm of breadth. Sorting by
    count alone is a STABLE sort, so PLS12103 placed second on nothing but its
    position in the catalogue -- and it ships 1728 parts/truck against 2304, a
    25% loss offered to the engineer as the runner-up recommendation.

    No CAD needed: `Pose` takes bare numbers (synthesis builds them that way),
    so this drives `rank_catalogue` with a synthetic pose and mesh=None.
    """
    from app.catalogue import Container
    from app.nesting import rank_catalogue

    # 100mm cube, pitch == extent: no interleave, so counts are pure division.
    # A pocket tray's 3mm bottom sheet is charged against the inner height
    # (`dunnage.dead_height_mm`), so the inner is 303 for exactly 3 layers.
    pose = Pose("test", (100.0, 100.0, 100.0), (100.0, 100.0, 100.0))
    inner = (300.0, 300.0, 303.0)                    # 3x3x3 = 27 either way
    roomy = Container("ROOMY", inner, (900.0, 900.0, 900.0), 1000.0, "container")
    tight = Container("TIGHT", inner, (310.0, 310.0, 310.0), 1000.0, "container")

    # Identical inner, so identical counts -- only the outer differs. ROOMY is
    # listed first to prove the order comes from volume and not from input order.
    ranked = rank_catalogue(None, None, [roomy, tight], part_kg=1.0, top_n=0,
                            poses=[pose])
    assert [l.count for l in ranked] == [27, 27], [l.count for l in ranked]
    assert [l.asset_name for l in ranked] == ["TIGHT", "ROOMY"], \
        f"tie not broken by outer volume: {[l.asset_name for l in ranked]}"

    # ...and the tie-break must never outrank a genuinely higher count, however
    # much bigger the winning box is.
    big = Container("BIG", (600.0, 300.0, 303.0), (2000.0, 2000.0, 2000.0),
                    1000.0, "container")
    ranked = rank_catalogue(None, None, [tight, big], part_kg=1.0, top_n=0,
                            poses=[pose])
    assert (ranked[0].asset_name, ranked[0].count) == ("BIG", 54), \
        f"a smaller box outranked a higher count: " \
        f"{[(l.asset_name, l.count) for l in ranked]}"

    # The feedback itself: at inner H 300 the 3mm sheet costs the third layer.
    flush = Container("FLUSH", (300.0, 300.0, 300.0), (310.0, 310.0, 310.0),
                      1000.0, "container")
    got = rank_catalogue(None, None, [flush], part_kg=1.0, top_n=0, poses=[pose])[0]
    assert (got.count, got.grid) == (18, (3, 3, 2)), (got.count, got.grid)
    print("  equal counts -> smaller outer first (TIGHT before ROOMY); "
          "higher count still wins over a smaller box; 3mm sheet charged "
          "(300 inner -> 2 layers)")


def test_count_upper_is_the_quantisation_ceiling():
    """`count` is the floor of the raster's band and `count_upper` its ceiling.

    Surface voxels round outward, so extent is over by up to a voxel and the
    touching pitch by up to a voxel: one voxel tighter on both is the most the
    geometry could allow. The YXA bar at 4mm reads 40 with ceiling 44 -- and a
    3mm raster returns 44 outright (audit, 2026-09-10). Bare-number poses
    carry no voxel and get no band.
    """
    from app.catalogue import Container
    from app.nesting import layouts_for
    box = Container("PLS12801", (1150.0, 750.0, 790.0), (1200.0, 800.0, 986.0),
                    600.0, "container")
    bar = Pose("bar", (1092.0, 300.0, 148.0), (1092.0, 145.0, 68.0), voxel_mm=4.0)
    best = layouts_for([bar], box, part_kg=5.0)[0]
    assert (best.count, best.count_upper) == (40, 44), (best.count, best.count_upper)
    assert best.count_upper >= best.count
    bare = Pose("bare", (1092.0, 300.0, 148.0), (1092.0, 145.0, 68.0))
    b = layouts_for([bare], box, part_kg=5.0)[0]
    assert b.count_upper == b.count == 40, (b.count, b.count_upper)
    print(f"  YXA-shaped bar at 4mm: {best.count} (ceiling {best.count_upper}); "
          f"bare numbers: {b.count} (no band)")


def test_turned_layout_carries_the_rotation_it_won_in():
    """F7. A layout that wins in footprint order (1, 0, 2) must say so, and
    the in-plane turn must be a ROTATION the worker can pose the mesh with.

    The defect: `layouts_for` ranked both orders but nothing recorded which
    one won, so the worker posed the voxels and shipped `pose_matrix` with
    the raw un-turned resting rotation while extent/pitch/grid were the
    turned ones. The drawing stamped 876mm-long parts across a 332mm lattice
    and logged "parts clipped by the volume"; the 3D animation did the same
    in the browser.

    `IN_PLANE_TURN` and `plan_silhouettes`' second mask are one expression:
    rotating the mesh by the matrix must give the same occupancy as
    `np.rot90(mask, 1)` of the un-turned one -- same shape AND same cells,
    not a transpose (a transpose is a mirror; a part cannot be mirrored).
    """
    import numpy as np
    import trimesh
    from types import SimpleNamespace
    from app.catalogue import Container
    from app.insert_drawing import CELL_MM, pose_voxels
    from app.nesting import IN_PLANE_TURN, layouts_for, measure_poses

    # Asymmetric AND non-square in plan, so a mirror and a wrong sign both show.
    a = trimesh.creation.box(extents=(200.0, 60.0, 40.0))
    b = trimesh.creation.box(extents=(60.0, 120.0, 40.0))
    a.apply_translation((100.0, 30.0, 20.0))
    b.apply_translation((30.0, 90.0, 20.0))
    part = trimesh.util.concatenate([a, b])

    flat = occupancy(part, np.eye(4), 4.0).any(axis=2)
    turned = occupancy(part, IN_PLANE_TURN, 4.0).any(axis=2)
    assert turned.shape == np.rot90(flat, 1).shape, (turned.shape, flat.shape)
    assert np.array_equal(turned, np.rot90(flat, 1)), \
        "IN_PLANE_TURN does not agree with np.rot90(mask, 1)"
    assert not np.array_equal(turned, flat.T), \
        "the turn is a mirror, not a rotation"

    cand = SimpleNamespace(label="flat", rotation_matrix=np.eye(4))
    pose = measure_poses(part, [cand], voxel_mm=4.0)[0]
    assert np.array_equal(_mask_from_runs(pose.silhouettes[1]),
                          np.rot90(_mask_from_runs(pose.silhouettes[0]), 1))

    # Inner is too short for order (0,1,2)'s 204mm length, so only the turned
    # order fits at all -- the winner is turned by construction.
    box = Container("TURNME", (170.0, 230.0, 60.0), (190.0, 250.0, 80.0),
                    100.0, "container")
    best = layouts_for([pose], box)[0]
    assert best.turned, f"turned layout not recorded: {best}"
    assert best.extent_lbh[0] < best.extent_lbh[1], best.extent_lbh

    # The composed matrix the worker poses with must produce the extent the
    # numbers were computed from. Un-composed (the F7 defect) this is 200 x 120
    # against a (120, 200) extent and every part is clipped.
    rotation = (IN_PLANE_TURN @ np.asarray(cand.rotation_matrix, dtype=float)
                if best.turned else cand.rotation_matrix)
    shape = pose_voxels(part, rotation).shape
    for axis in (0, 1):
        got = shape[axis] * CELL_MM
        assert abs(got - best.extent_lbh[axis]) <= CELL_MM, \
            (f"posed voxels {shape} ({got}mm on axis {axis}) do not match "
             f"the turned extent {best.extent_lbh}")
    print(f"  turned winner {best.grid} extent {best.extent_lbh}; posed voxels "
          f"{shape[0]}x{shape[1]} cells at {CELL_MM}mm")


if __name__ == "__main__":
    for fn in (test_ground_truth, test_exact_fit_is_not_off_by_one, test_weight_cap,
               test_count_upper_is_the_quantisation_ceiling,
               test_pitch_is_clear_at_every_lattice_multiple,
               test_cuboid_has_no_interleave,
               test_silhouette_round_trips_and_permutes_with_the_footprint,
               test_turned_layout_carries_the_rotation_it_won_in,
               test_cuboid_silhouette_is_solid, test_bar_silhouette_is_not_solid,
               test_real_bar, test_catalogue_ranking,
               test_ties_go_to_the_smaller_box):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
