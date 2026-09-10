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


def test_self_consistent():
    """The design's own count, recomputed from its own dims. Must agree."""
    for c, d in _designs():
        count, grid, _ = lattice_count(d.extent_lbh, d.pitch_lbh, d.inner)
        assert (count, grid) == (d.count, d.grid), \
            f"{c.ref}: design says {d.count} {d.grid}, lattice_count says {count} {grid}"
        print(f"  {c.ref:<18} {count} = {grid} recomputed ok")


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
               test_oversize_part, test_weight_cap_does_not_split_the_answer):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
