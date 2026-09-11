"""Phase 2 acceptance: the assembled result set, and the truck-tier reality check.

Run:  python tests/test_engine.py

The interesting assertion here is `test_box_gain_does_not_survive_to_truck`.
Everything else is plumbing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalogue import containers                       # noqa: E402
from app.engine import ResultSet, parts_per_truck, solve    # noqa: E402
from app.nesting import lattice_count                       # noqa: E402
from app.seed_data import VEHICLES                          # noqa: E402
from tests.ground_truth import CASES                        # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "customer"
BAR = FIXTURES / "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
SXL32 = next(v for v in VEHICLES if v[0] == "32_ft_sxl")


def test_box_gain_does_not_survive_to_truck():
    """The finding that stops us shipping a misleading recommendation.

    For Mubea, a stock PLS12103 holds 65 where the shipped PLS12801 holds 40
    (+62%). Per 32ft truck that is 1560 vs 1560 -- a TIE, **+0%** -- because at
    5kg a part both boxes run out of payload before they run out of space, and
    PLS12103's real tare (39kg, C-TARE) is heavier than PLS12801's (30kg), so
    its extra count buys back exactly the payload margin it spent. Report the
    box number alone and the team re-tools an insert for nothing.

    Tare is load-bearing in that sentence: with tare=0 the ranking INVERTS
    (1800 vs 1755). Values here are each box's OWN `Packaging.tare_kg`
    (C-TARE), not a flat guess applied to both -- at a flat assumed 30kg this
    used to read 1625 vs 1560, a false +4% PLS12103 win.
    """
    c = CASES[0]
    counts = {}
    for name in ("PLS12801", "PLS12103"):
        a = next(x for x in containers() if x.name == name)
        assert a.tare_kg is not None, f"{name}: tare missing from seed (C-TARE)"
        n, _, _ = lattice_count(c.pose_lbh, c.pitch_lbh, a.inner,
                                c.part_kg, a.max_weight_kg)
        fit = parts_per_truck(a.outer, n, c.part_kg, a.tare_kg, SXL32)
        counts[name] = (n, fit)
        assert fit.limited_by == "weight", \
            f"{name} is no longer weight-limited -- re-derive this whole test"
        print(f"  {name:<10} {n:>3}/box -> {fit.boxes:>3} boxes = {fit.parts:>5} parts/truck"
              f"  ({fit.kg_per_box:.0f}kg/box @ {a.tare_kg}kg tare, {fit.limited_by}-limited)")

    per_box = counts["PLS12103"][0] / counts["PLS12801"][0]
    per_truck = counts["PLS12103"][1].parts / counts["PLS12801"][1].parts
    assert per_box > 1.5, f"box-level gain vanished ({per_box:.2f}x)"
    assert per_truck <= 1.01, \
        f"real per-asset tare no longer ties these -- recheck the claim ({per_truck:.2f}x)"
    print(f"  box {per_box:.2f}x  ->  truck {per_truck:.2f}x"
          f"   (payload ceiling {int(SXL32[4] // c.part_kg)} parts)")


def test_truck_fit_is_consistent():
    """boxes x parts_per_box must equal parts, and volume-limited must be reachable."""
    light = parts_per_truck((1200, 800, 986), 40, 0.001, 0.0, SXL32)
    assert light.limited_by == "volume" and light.boxes == 48, light
    assert light.parts == light.boxes * 40
    heavy = parts_per_truck((1200, 800, 986), 40, 50.0, 30.0, SXL32)
    assert heavy.limited_by == "weight" and heavy.boxes < 48, heavy
    # The bounds are shipped, not re-derived by the load calculator.
    assert (light.by_volume, heavy.by_weight) == (48, heavy.boxes), (light, heavy)
    assert light.stack == SXL32[3] // 986 == 2, light
    # max_stack caps boxes-high: one high halves the volume bound.
    single = parts_per_truck((1200, 800, 986), 40, 0.001, 0.0, SXL32, max_stack=1)
    assert (single.stack, single.boxes) == (1, 24), single
    assert single.floor_grid[0] * single.floor_grid[1] * single.stack == single.by_volume
    print(f"  volume-limited {light.boxes} boxes / weight-limited {heavy.boxes} boxes"
          f" / max_stack=1 -> {single.boxes} ok")


def test_a_tie_is_not_reported_as_volume():
    """payload and cube binding at once is its own answer, not "volume".

    The frontend prints `limited_by` verbatim after "bound", so reporting
    "volume" on a tie sends the engineer looking for height when height buys
    nothing. Numbers picked so both limits land on exactly 8 boxes:
    per_floor 2x2 x 2 layers = 8 by volume, and 800kg payload / 100kg a box
    = 8 by weight.
    """
    veh = ("tie_rig", 2000, 2000, 2000, 800)
    fit = parts_per_truck((1000, 1000, 1000), 10, 7.0, 30.0, veh)
    assert fit.kg_per_box == 100.0, fit
    assert fit.boxes == 8, fit
    assert fit.limited_by == "weight and volume", fit
    # ...but no weight at all is not a tie: by_weight is only a stand-in.
    unweighed = parts_per_truck((1000, 1000, 1000), 10, 0.0, 0.0, veh)
    assert unweighed.limited_by == "volume" and unweighed.boxes == 8, unweighed
    print(f"  tie at {fit.boxes} boxes -> {fit.limited_by!r}; no weight -> "
          f"{unweighed.limited_by!r}")


def test_floor_grid_matches_the_number_it_illustrates():
    """The load drawing is built from floor_grid, so it must be THE floor fit.

    The frontend used to re-derive this with its own algorithm (bestFill,
    which allows mixed-orientation split strips) and drew 21/floor against
    the 18 the parts figure assumed. The invariant that stops that:
    floor_grid product x stackable layers == the volume bound.
    """
    veh = ("grid_rig", 1000, 1700, 2000, 10 ** 9)   # payload out of the way
    # 800x300 box: straight 1x5 = 5, rotated 3x2 = 6 -> rotation must win.
    fit = parts_per_truck((800, 300, 500), 1, 0.0, 1.0, veh)
    assert fit.floor_rotated is True, fit
    assert fit.floor_grid == (3, 2), fit
    assert fit.floor_grid[0] * fit.floor_grid[1] * (2000 // 500) == fit.boxes, fit

    # ...and the un-rotated case still reports the un-rotated grid.
    straight = parts_per_truck((300, 800, 500), 1, 0.0, 1.0, veh)
    assert straight.floor_rotated is False, straight
    assert straight.floor_grid == (3, 2), straight

    # Real wheel box, weight-free: the drawing and the count agree.
    real = parts_per_truck((1200, 800, 986), 48, 0.001, 0.0, SXL32)
    nx, ny = real.floor_grid
    assert nx * ny * (SXL32[3] // 986) == real.boxes, (real, SXL32)
    print(f"  rotated 3x2 ok; wheel box {nx}x{ny}/floor x "
          f"{SXL32[3] // 986} = {real.boxes} boxes")


def test_degenerate_box_rejected():
    try:
        parts_per_truck((0, 800, 986), 10, 1.0, 0.0, SXL32)
    except ValueError:
        print("  degenerate box rejected ok")
        return
    raise AssertionError("a zero-width box was accepted")


def test_result_set_shape():
    empty = ResultSet(catalogue=[], custom=None)
    assert empty.best_count == 0 and not empty.custom_beats_catalogue()
    print("  empty result set ok")


def test_solve_end_to_end():
    """The PLANNING §6 deliverable on a real file: top 2 catalogue + 1 custom."""
    if not BAR.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    from app.geometry import extract_part, load_unified_mesh

    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)
    res = solve(mesh, r.candidates, part_kg=5.0)

    assert len(res.catalogue) == 2, f"expected top-2, got {len(res.catalogue)}"
    assert res.custom is not None, "no custom design for a part that fits stock boxes"
    assert res.catalogue[0].count >= res.catalogue[1].count
    assert res.best_count >= res.catalogue[0].count

    # Arithmetic consistency (PLANNING §7): the custom design's own count must
    # survive recomputation from its own inner dims.
    n, _, _ = lattice_count(res.custom.extent_lbh, res.custom.pitch_lbh, res.custom.inner)
    assert n == res.custom.count, f"custom says {res.custom.count}, recompute says {n}"

    for l in res.catalogue:
        print(f"  catalogue  {l.asset_name:<10} {l.count:>4}  {l.grid}")
    print(f"  custom     {res.custom.footprint:<10} {res.custom.count:>4}  "
          f"{res.custom.grid}  inner {res.custom.inner}")
    print(f"  custom beats catalogue: {res.custom_beats_catalogue()}")


def test_wheel_reproduces_trw_proposal_from_cad():
    """ACCURACY, and the strongest check in the project.

    Everything else feeds the engine a pitch derived from a deck. This starts
    from the customer's STEP file and nothing else, and asks whether the engine
    independently lands on what TRW actually shipped: 48 wheels in a PLS1280
    (= PLS12803, inner 1150x750x1000) as a 3x2 matrix, 8 layers.

    It also cross-checks the measured pitch against the deck's own printed
    pocket, 376.6 x 367.5 x 117 plus a 3mm separator sheet. Two independent
    routes to the same three numbers.
    """
    if not WHEEL.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    from app.geometry import extract_part, load_unified_mesh

    r = extract_part(str(WHEEL))
    mesh, _ = load_unified_mesh(r.glb_path)
    res = solve(mesh, r.candidates, part_kg=2.5)

    picked = [l for l in res.catalogue if l.asset_name == "PLS12803"]
    assert picked, \
        f"the shipped asset is not in the top 2: {[l.asset_name for l in res.catalogue]}"
    got = picked[0]
    assert got.count == 48, f"engine says {got.count} from CAD, TRW shipped 48"
    assert got.grid == (3, 2, 8), f"grid {got.grid}, deck says 3x2 matrix x 8 layers"

    # Deck pocket 376.6 x 367.5 x 117 vs pitch measured off the mesh. The 3mm
    # separator sheet is added to the VERTICAL pitch only -- it sits between
    # layers, not between pockets in a tray.
    #
    # 8mm tolerance, and the B axis needs most of it: the reader measures this
    # wheel 353.9mm where the deck says 360 (documented in Phase 1, inside the
    # +/-5% accuracy check), so a ~6mm pitch difference on B is the reader's
    # known delta on that dimension, not a nesting error. Voxels are 4mm and
    # round outward, which covers the rest.
    for measured, deck, axis in zip(got.pitch_lbh, (376.6, 367.5, 120.0), "LBH"):
        assert abs(measured - deck) <= 8.0, \
            f"pitch {axis}: measured {measured}, deck implies {deck}"

    print(f"  CAD only -> {got.count} in {got.asset_name} {got.grid}")
    print(f"     measured pitch {got.pitch_lbh} vs deck pocket+sheet (376.6, 367.5, 120.0)")
    print(f"     dims {r.canonical_dims_lbh} vs deck 370x360x135")


def test_trw_truck_confirms_planning_7():
    """PLANNING §7 says the TRW deck states 46 kits by weight where 155kg/kit
    against a 9000kg payload gives 58, and then calls 48 "the minimum of" 48
    and 46. Independently: 58 by weight, 48 by volume, so 48 is right by volume
    and the deck's 46 was wrong. Confirms §7's complaint rather than the deck.
    """
    from app.catalogue import containers as _containers
    a = next(x for x in _containers() if x.name == "PLS12803")
    fit = parts_per_truck(a.outer, 48, 2.5, 35.0, SXL32)
    assert fit.kg_per_box == 155.0, fit
    assert fit.limited_by == "volume", f"expected volume-limited, got {fit}"
    assert int(SXL32[4] // fit.kg_per_box) == 58, "the deck's own weight figure"
    print(f"  48 kits/box, {fit.kg_per_box:.0f}kg -> 58 by weight, "
          f"{fit.boxes} by volume -> ships {fit.boxes} ({fit.parts} parts)")


if __name__ == "__main__":
    for fn in (test_box_gain_does_not_survive_to_truck, test_truck_fit_is_consistent,
               test_a_tie_is_not_reported_as_volume,
               test_floor_grid_matches_the_number_it_illustrates,
               test_degenerate_box_rejected, test_result_set_shape,
               test_solve_end_to_end, test_wheel_reproduces_trw_proposal_from_cad,
               test_trw_truck_confirms_planning_7):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
