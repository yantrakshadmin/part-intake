"""ACCURACY: both shipped proposals, from the customer CAD and nothing else.

This is the test `ground_truth.py` was always supposed to be. `evaluate()` there
is fed a stub engine (`lambda lbh, a: {...}[tuple(lbh)]`), so it only ever
checked the fixture arithmetic -- the real nesting engine was never on the hook
for 40 and 48. It is now.

Every other engine check either feeds a pitch taken off a deck
(`test_nesting.test_ground_truth`) or zeroes the clearance by hand
(`test_nesting.test_real_bar`, `clearance_mm=0.0`). Both hide the axis mix-up
this file caught: one `DEFAULT_CLEARANCE_MM` charged 5mm of air BETWEEN LAYERS
as well as beside neighbours, and 148 + 9 x 73 = 805 > 790 dropped the Mubea bar
from 10 layers to 9 -- 36 parts against the 40 that shipped. So: real files,
default parameters, no overrides.

Run:  python tests/test_clearance.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.ground_truth import PLS1280, PLS12801            # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "customer"
# The Mubea deck's part has no CAD; the YXA bar is the same archetype and is
# what Phase 1 measured 1092 x 298 x 143 from. PLS12801 ground truth is 40.
BAR = FIXTURES / "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs"
WHEEL = FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"

CASES = [
    (BAR, 5.0, PLS12801, 40, (1, 4, 10)),
    (WHEEL, 2.5, PLS1280, 48, (3, 2, 8)),
]


def test_shipped_counts_from_cad():
    """Non-negotiable. Move the engine, never these numbers."""
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import nest

    missing = [p.name for p, *_ in CASES if not p.exists()]
    if missing:
        print(f"  skipped: no customer fixtures (NDA, not in git): {missing}")
        return

    for path, part_kg, asset, target, grid in CASES:
        r = extract_part(str(path))
        mesh, _ = load_unified_mesh(r.glb_path)
        # DEFAULTS ONLY. Passing clearance_mm/stack_clearance_mm here would
        # test a configuration nothing ships with.
        layouts = nest(mesh, r.candidates, asset, part_kg=part_kg)
        assert layouts, f"{path.name}: no layout at all in {asset.name}"
        best = layouts[0]
        assert best.count == target, (
            f"{path.name}: engine says {best.count} in {asset.name} "
            f"{best.grid} pitch {best.pitch_lbh}, the proposal shipped {target}"
        )
        assert best.grid == grid, \
            f"{path.name}: {best.count} via grid {best.grid}, deck says {grid}"
        print(f"  {path.name[:34]:<34} -> {best.count} in {asset.name} "
              f"{best.grid}  pitch {best.pitch_lbh}")


def main() -> int:
    for fn in (test_shipped_counts_from_cad,):
        print(f"{fn.__name__}:")
        fn()
    print("\nall ground-truth counts reproduced from CAD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
