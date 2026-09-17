"""Phase 2 acceptance: the assembled result set, and the truck-tier reality check.

Run:  python tests/test_engine.py

The interesting assertion here is `test_box_gain_does_not_survive_to_truck`.
Everything else is plumbing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import synthesis as synthesis_mod                  # noqa: E402
from app.catalogue import Container, containers             # noqa: E402
from app.engine import (ResultSet, cuboid_count, cuboid_layout,  # noqa: E402
                        landed_cost_per_part, noise_gate, parts_per_truck, solve)
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

    # T5 acceptance: the noise gate must never touch the shipped Mubea count.
    # Pinned through engine.solve with the shipped defaults, restricted to
    # the shipped asset, so a future noise-band change that (un)refuses the
    # real bar goes red here, not just on a synthetic fixture.
    pls12801 = [a for a in containers() if a.name == "PLS12801"]
    pinned = solve(mesh, r.candidates, part_kg=5.0, assets=pls12801, top_n=1)
    assert pinned.catalogue and pinned.catalogue[0].count == 40, \
        f"PLS12801 moved off the shipped 40: {pinned.catalogue}"
    print(f"  PLS12801 pinned at {pinned.catalogue[0].count} (noise gate untouched)")


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


# --- T5: gates and landed cost -----------------------------------------

def test_noise_gate_refuses_block_like_synthetic():
    """PLANNING §10 T5's noise gate on a hand-built block-like layout.

    10 x 3 x 5 = 150 against a 144-part cuboid (the UPP_P212 archetype) --
    but with margins picked so a pitch loosened by one raster voxel (4mm)
    really does cross a grid boundary on the first axis: pitch 111mm against
    a 1150mm inner and a 124mm extent needs a widened pitch > 114mm to drop
    10 -> 9 columns (9 x 114 = 1026 = 1150 - 124); 111 + 4 = 115 crosses it,
    dropping the count to 9 x 3 x 5 = 135, at/below the 144 cuboid baseline.
    """
    extent = (124.0, 300.0, 184.0)
    pitch = (111.0, 285.0, 184.0)
    inner = (1150.0, 950.0, 1000.0)
    cuboid = 144
    refuse, n_band = noise_gate(extent, pitch, inner, cuboid)
    assert refuse and n_band == 135, (refuse, n_band)

    # Red proof: the SAME layout at the true (un-loosened) pitch is not noise
    # -- 150 clears 144 outright. Only the +4mm recount refuses it; break the
    # widening (band 0) and the gate goes quiet, proving it is doing real work.
    n_true, _, _ = lattice_count(extent, pitch, inner)
    assert n_true == 150 and n_true > cuboid, n_true
    import app.engine as engine_mod
    old_band = engine_mod.NOISE_BAND_MM
    engine_mod.NOISE_BAND_MM = 0.0
    try:
        refuse_broken, n_broken = engine_mod.noise_gate(extent, pitch, inner, cuboid)
        assert not refuse_broken and n_broken == 150, (refuse_broken, n_broken)
    finally:
        engine_mod.NOISE_BAND_MM = old_band
    # still refuses with the real constant restored
    refuse2, _ = noise_gate(extent, pitch, inner, cuboid)
    assert refuse2
    print(f"  band=0mm -> refuse={refuse_broken} (red); "
          f"band={old_band:g}mm -> refuse={refuse} n@band={n_band} (green)")


def test_cuboid_count_matches_achievable_layout():
    """`cuboid_count` must never claim a grid `cuboid_layout` cannot build.

    A 100mm part in a 350mm inner, 1kg/part, 25kg cap: `int(25 // 1)` = 25
    is not an achievable product of 3 per-axis integer divisions -- the real
    best is 18, grid (3, 3, 2). `cuboid_count` has to be the SAME 18: it is
    now one expression off `cuboid_layout`, not a second formula that can
    disagree with it.
    """
    part, inner = (100.0, 100.0, 100.0), (350.0, 350.0, 350.0)
    count, extent, grid = cuboid_layout(part, inner, 1.0, 25.0)
    assert (count, grid) == (18, (3, 3, 2)), (count, extent, grid)
    assert cuboid_count(part, inner, 1.0, 25.0) == count, \
        "cuboid_count disagrees with cuboid_layout's own achievable grid"

    # Red proof: the old formula (`min(best, int(max_weight_kg // part_kg))`)
    # would have said 25 here -- unachievable, and NOT what cuboid_layout
    # builds a BOM for.
    old_wrong = min(3 * 3 * 3, int(25.0 // 1.0))   # best geometric = 3x3x3 = 27
    assert old_wrong == 25 and old_wrong != count, \
        "test would not catch the old, unachievable weight cap"
    print(f"  cuboid_layout/cuboid_count agree at {count} {grid} "
          f"(old formula would have said {old_wrong})")


def test_weight_gate_continues_not_refuses():
    """S5: payload binding zeroes the forward-freight GAIN, it never refuses.

    `count` is always the forward-freight denominator now, in BOTH
    `limited_by` branches -- "weight" no longer re-derives a second,
    unachievable cap (`int(max_weight_kg // part_kg)`; see
    `cuboid_layout`'s docstring for why that number is not real). Uses the
    same 100mm-part/350mm-inner/1kg/25kg-cap case as
    `test_cuboid_count_matches_achievable_layout`, where the real achievable
    count (18) and the naive cap (25) actually differ -- FSC at 900kg/120kg
    would coincide with the real grid and prove nothing.
    """
    from app.config import settings

    elements = [{"name": "PP sheet", "spec": "PP", "labels": ["L", "B", "H"],
                "dims_mm": [1000.0, 1000.0, 5.0], "qty": 2}]
    count, _, _ = cuboid_layout((100.0, 100.0, 100.0), (350.0, 350.0, 350.0),
                                1.0, 25.0)
    assert count == 18, count

    geo = landed_cost_per_part(count, 1.0, "geometry", 25.0, elements, 850.0,
                               "unknown", None)
    capped = landed_cost_per_part(count, 1.0, "weight", 25.0, elements, 850.0,
                                  "unknown", None)
    assert geo > 0 and capped > 0, (geo, capped)
    # "weight" must no longer change the forward term: both branches use the
    # SAME achievable count.
    assert capped == geo, "limited_by must not re-derive a second cap any more"

    # Hand reference off the real, achievable count.
    h_eff = 850.0
    stack_count = int(settings.stack_H_max_mm // h_eff)
    boxes = settings.n_footprints * stack_count
    want = (settings.f_fwd_per_trip / count
           + (settings.f_ret_per_trip / boxes) / count
           + 400.0 / (settings.T_pool * count)
           + settings.wage_per_hour * 3.0 / 3600.0)
    assert abs(capped - want) < 1e-6, (capped, want)

    # Red proof: the OLD formula priced forward freight off
    # `int(max_weight_kg // part_kg)` = 25, an unachievable grid, understating
    # the real cost (repro from code review: 49.88 vs the correct 66.90-class
    # figure -- reproduced here in this case's own numbers).
    wrong_n_fwd = int(25.0 // 1.0)
    wrong = (settings.f_fwd_per_trip / wrong_n_fwd
            + (settings.f_ret_per_trip / boxes) / count
            + 400.0 / (settings.T_pool * count)
            + settings.wage_per_hour * 3.0 / 3600.0)
    assert abs(capped - wrong) > 1e-3, \
        "test would not catch the old, unachievable-cap formula"
    print(f"  count=18 (25kg cap, 1kg/part): geometry={geo:.2f} weight={capped:.2f} "
          f"(old wrong formula would give {wrong:.2f})")

    # Real CAD leg: a weight-capped part must still be RANKED with a real
    # landed cost, never dropped -- the refusal only ever comes from the
    # noise gate or the cost-fallback comparison, not from `limited_by`.
    if not BAR.exists():
        print("  (CAD leg skipped: no customer fixtures)")
        return
    from app.geometry import extract_part, load_unified_mesh
    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)
    # 120 kg/part pins every asset's count to its payload cap.
    res = solve(mesh, r.candidates, part_kg=120.0, top_n=1)
    assert res.catalogue, "weight-capped part was refused outright"
    winner = res.catalogue[0]
    assert winner.limited_by == "weight", winner
    assert res.landed_cost_per_part is not None and res.landed_cost_per_part > 0
    print(f"  CAD (120kg/part): {winner.asset_name} count={winner.count} "
          f"limited_by=weight landed_cost_per_part={res.landed_cost_per_part}")


def test_dual_branch_ranking_unreliable():
    """T5/T6 §1.13: a missing mass/surface_class/annual_volume/fold_type runs
    the dual branch and flags `ranking_unreliable` -- never a refusal.

    `synthesis.synthesise` is patched out so the catalogue candidate (whose
    asset carries a real `fold_type`) wins instead of the always-`unknown`
    custom design, which would otherwise flag `ranking_unreliable` on its own
    regardless of the inputs under test.
    """
    if not BAR.exists():
        print("  skipped: no customer fixtures (NDA, not in git)")
        return
    from app.geometry import extract_part, load_unified_mesh

    asset = Container(name="ZZ-T5-TEST", inner=(1150.0, 750.0, 790.0),
                      outer=(1200.0, 800.0, 850.0), max_weight_kg=600.0,
                      kind="container", tare_kg=30.0, fold_type="collapsible",
                      folded_h_mm=200.0)
    r = extract_part(str(BAR))
    mesh, _ = load_unified_mesh(r.glb_path)

    old_synthesise = synthesis_mod.synthesise
    synthesis_mod.synthesise = lambda *a, **kw: None
    try:
        complete = solve(mesh, r.candidates, part_kg=5.0, assets=[asset],
                         top_n=1, surface_class="raw", annual_volume=100000)
        missing_mass = solve(mesh, r.candidates, part_kg=0.0, assets=[asset],
                             top_n=1, surface_class="raw", annual_volume=100000)
    finally:
        synthesis_mod.synthesise = old_synthesise

    assert complete.custom is None and complete.catalogue, \
        "test setup failed to force the catalogue candidate to win"
    assert complete.ranking_unreliable is False, complete
    assert complete.landed_cost_per_part is not None

    assert missing_mass.ranking_unreliable is True, missing_mass
    assert missing_mass.landed_cost_per_part is not None, \
        "missing mass must still get a landed cost (a default, not a refusal)"
    assert (missing_mass.landed_cost_per_part
           == missing_mass.landed_cost_per_part_class_a), missing_mass
    print(f"  all 4 inputs given -> ranking_unreliable={complete.ranking_unreliable}")
    print(f"  mass missing -> ranking_unreliable={missing_mass.ranking_unreliable} "
          f"landed_cost_per_part={missing_mass.landed_cost_per_part} "
          f"(== class_a branch {missing_mass.landed_cost_per_part_class_a})")


def test_landed_cost_hand_computable():
    """Arithmetic check on the T5 formula, by hand.

    count=10, part_kg=5, geometry-limited, one element (2 x 1x1m PP sheets,
    Rs 200/m2), outer_h 850mm, fold_type unknown (keeps its full height on
    return). `app.config.settings` defaults: f_fwd 1094, f_ret 35000,
    n_footprints 16, stack_H_max 2000, wage 50, T_pool 66.

        forward/part   = 1094 / 10                          = 109.4
        h_eff          = 850 (no fold, no rigid elements)
        stack_count    = floor(2000/850)                    = 2
        boxes/return   = 16 x 2                              = 32
        return/part    = (35000/32) / 10                    = 109.375
        element cost   = 200 x (1x1) x 2                     = Rs 400
        T_e            = min(150 [PP], 66 [T_pool])          = 66
        element/part   = 400 / (66 x 10)                     = 0.606060...
        labour/part    = 50 x 3/3600                         = 0.041666...
        total          = 219.42272727...
    """
    elements = [{"name": "PP sheet", "spec": "PP", "labels": ["L", "B", "H"],
                "dims_mm": [1000.0, 1000.0, 5.0], "qty": 2}]
    got = landed_cost_per_part(10, 5.0, "geometry", 600.0, elements, 850.0,
                               "unknown", None)
    want = 109.4 + 109.375 + 400.0 / (66.0 * 10) + 50.0 * 3.0 / 3600.0
    assert abs(got - want) < 1e-6, (got, want)

    # Red proof: if `T_e` used the material life alone (150 trips) instead of
    # `min(material, T_pool)` = 66, the element term -- and the total -- would
    # be smaller. The hand computation catches that; assert it actually would.
    wrong_without_pool_cap = (109.4 + 109.375 + 400.0 / (150.0 * 10)
                             + 50.0 * 3.0 / 3600.0)
    assert abs(got - wrong_without_pool_cap) > 1e-3, \
        "test would not catch a missing T_pool cap"
    print(f"  hand-computed {want:.6f} == landed_cost_per_part {got:.6f}")


if __name__ == "__main__":
    for fn in (test_box_gain_does_not_survive_to_truck, test_truck_fit_is_consistent,
               test_a_tie_is_not_reported_as_volume,
               test_floor_grid_matches_the_number_it_illustrates,
               test_degenerate_box_rejected, test_result_set_shape,
               test_solve_end_to_end, test_wheel_reproduces_trw_proposal_from_cad,
               test_trw_truck_confirms_planning_7,
               test_noise_gate_refuses_block_like_synthetic,
               test_cuboid_count_matches_achievable_layout,
               test_weight_gate_continues_not_refuses,
               test_dual_branch_ranking_unreliable,
               test_landed_cost_hand_computable):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
