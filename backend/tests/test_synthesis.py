"""Phase 2 acceptance: custom box synthesis (PLANNING §6).

Run:  python tests/test_synthesis.py

Every run returns top-2 catalogue + 1 custom, so synthesis is on the hot path.
The bar it has to clear: a box that solves for its own height must not do worse
than the stock asset that shipped.

  ACCURACY  — `test_beats_shipped`. Both ground-truth cases, from their own
              pose/pitch. >= 40 Mubea, >= 48 TRW.
  BOUNDS    — `test_design_space`. Footprint from the allowed set, inner height
              within the stocked catalogue's tallest.
  ARITHMETIC— `test_self_consistent`. PLANNING §7: two numbers must not
              disagree. The count a design reports must be the count
              `nesting.lattice_count` gives for that design's own inner dims.
  REFUSAL   — `test_oversize_part`. Too big for any standard pallet -> None.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nesting import lattice_count                          # noqa: E402
from app.synthesis import ALLOWED_INNER_FOOTPRINTS, MAX_INNER_HEIGHT_MM, synthesise  # noqa: E402
from tests.ground_truth import CASES                           # noqa: E402


def _designs():
    return [(c, synthesise(c.pose_lbh, c.pitch_lbh, c.part_kg)) for c in CASES]


def test_beats_shipped():
    """A custom box must not lose to the stock asset that shipped."""
    for c, d in _designs():
        assert d is not None, f"{c.ref}: no design synthesised"
        print(f"  {c.ref:<18} {d.count:>3} = {d.grid}  vs shipped {c.achieved}"
              f" in {c.asset.name}")
        print(f"     inner {d.inner}  outer {d.outer}  footprint {d.footprint}")
        assert d.count >= c.achieved, \
            f"{c.ref}: custom box holds {d.count}, {c.asset.name} shipped {c.achieved}"


def test_design_space():
    """Bounded space, not a blank sheet: standard footprint, stocked height."""
    allowed = {(l, b) for _, l, b in ALLOWED_INNER_FOOTPRINTS}
    for c, d in _designs():
        assert (d.inner[0], d.inner[1]) in allowed, f"{c.ref}: {d.inner} off-footprint"
        assert 0 < d.inner[2] <= MAX_INNER_HEIGHT_MM, f"{c.ref}: inner height {d.inner[2]}"
        assert all(o > i for o, i in zip(d.outer, d.inner)), f"{c.ref}: outer <= inner"
        print(f"  {c.ref:<18} footprint {d.footprint}, inner height {d.inner[2]:.1f}mm ok")


def usable(d, clearance_lbh=(0.0, 0.0, 0.0)):
    """The design's inner less the height its own insert occupies above the
    stack -- the inner the lattice gets. One expression, shared with
    `nesting.layouts_for` and `synthesise` via `dunnage.dead_height_mm`."""
    from app import dunnage
    dead = dunnage.dead_height_mm(d.extent_lbh, d.pitch_lbh, clearance_lbh)
    return (d.inner[0], d.inner[1], d.inner[2] - dead)


def test_self_consistent():
    """The design's own count, recomputed from its own dims. Must agree.

    Recomputed on the inner LESS the insert's dead height. A recompute on the
    full inner is not a check: when the dead height is at least a pitch it
    puts back the layer the box has no room for, and a review caught exactly
    that -- 42 reported in a box solved for 21 (wheel, Alternative 1 pose).
    """
    for c, d in _designs():
        count, grid, _ = lattice_count(d.extent_lbh, d.pitch_lbh, usable(d))
        assert (count, grid) == (d.count, d.grid), \
            f"{c.ref}: design says {d.count} {d.grid}, lattice_count says {count} {grid}"
        print(f"  {c.ref:<18} {count} = {grid} recomputed ok")


def test_own_bom_fits():
    """The box must be tall enough for the insert BOM it will be shipped with.

    A pocket tray's bottom sheet is 3mm of dead height above the parts stack;
    the wheel-shaped case used to synthesise an inner exactly stack-high and
    report `fits: False` on its own dunnage. Layers still 8, count still 48.
    """
    from app import dunnage
    clr = (5.0, 5.0, 0.0)
    wheel = synthesise((372.0, 356.0, 140.0), (377.0, 361.0, 116.0), 2.5,
                       clearance_lbh=clr)
    b = dunnage.bom(wheel.extent_lbh, wheel.pitch_lbh, wheel.grid, wheel.inner, clr)
    assert b.fits and abs(b.slack_lbh[2]) < 1e-6, (wheel.inner, b.slack_lbh)
    assert (wheel.count, wheel.grid) == (48, (3, 2, 8)), (wheel.count, wheel.grid)
    for c, d in _designs():
        bd = dunnage.bom(d.extent_lbh, d.pitch_lbh, d.grid, d.inner)
        assert bd.fits, f"{c.ref}: custom box does not fit its own BOM {bd.slack_lbh}"
    # The review's repro: the wheel's "Alternative 1" pose interleaves in plane
    # (121 < 140 on B) but stacks flat. It is a slotted tray now (both
    # interleaves are needed for bars), so the dead height is one sheet; the
    # point of the check is the invariant -- the count is the count of the box
    # reported, recomputed on inner-minus-dead, and the BOM fits it. The first
    # version of the loop accepted 1 layer and then recomputed 2 on the taller
    # inner it had just made room for the dunnage in.
    alt = synthesise((372.0, 140.0, 356.0), (377.0, 121.0, 356.0), 2.5, clearance_lbh=clr)
    ba = dunnage.bom(alt.extent_lbh, alt.pitch_lbh, alt.grid, alt.inner, clr)
    n, g, _ = lattice_count(alt.extent_lbh, alt.pitch_lbh, usable(alt, clr))
    assert ba.fits and (n, g) == (alt.count, alt.grid) and alt.layers == 2 \
        and ba.archetype == "pocket_tray", \
        (alt.count, alt.grid, alt.inner, ba.slack_lbh, ba.archetype, n, g)
    # And when the sheet would push past the cap, a layer goes, not the fit:
    # 8 layers of a 125-tall, 125-pitch part stack to exactly 1000.
    tight = synthesise((300.0, 300.0, 125.0), (305.0, 305.0, 125.0), 1.0, clearance_lbh=clr)
    bt = dunnage.bom(tight.extent_lbh, tight.pitch_lbh, tight.grid, tight.inner, clr)
    assert tight.layers == 7 and bt.fits and tight.inner[2] <= MAX_INNER_HEIGHT_MM, \
        (tight.layers, tight.inner, bt.slack_lbh)
    print(f"  wheel custom {wheel.count} in inner H {wheel.inner[2]} (fits); "
          f"sheet at the cap drops to {tight.layers} layers, inner H {tight.inner[2]}")


def test_oversize_part():
    """Wider than any standard pallet inner. Refuse, don't invent a box."""
    d = synthesise((2000.0, 900.0, 100.0), (2000.0, 900.0, 100.0), 5.0)
    assert not d, f"expected no design, got {d}"
    print("  2000x900x100 -> no design ok")


def test_weight_cap_does_not_split_the_answer():
    """A heavy part must shrink the box, not report a count its own dims deny.

    Weight-capping the count while keeping the tall box is exactly the TRW
    deck's "minimum of 48 and 46" defect (PLANNING §7). 30kg x 400x300x200
    gives 4/layer = 120kg, so the 600kg cap bites at 5 layers of a possible 5.
    """
    extent = pitch = (400.0, 300.0, 200.0)
    d = synthesise(extent, pitch, 30.0)
    assert d is not None
    count, grid, _ = lattice_count(d.extent_lbh, d.pitch_lbh, d.inner)
    assert (count, grid) == (d.count, d.grid), (count, grid, d)
    assert d.count * 30.0 <= 600.0, f"{d.count} x 30kg over the 600kg cap"
    print(f"  30kg part -> {d.count} = {d.grid}, inner height {d.inner[2]:.1f}mm ok")

    # And when even one layer breaks the cap, there is no such box. Saying so is
    # correct; a box 8x over its weight limit is not.
    assert synthesise(extent, pitch, 300.0) is None
    print("  300kg part -> no design ok")


if __name__ == "__main__":
    for fn in (test_beats_shipped, test_design_space, test_self_consistent,
               test_own_bom_fits, test_oversize_part, test_weight_cap_does_not_split_the_answer):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
