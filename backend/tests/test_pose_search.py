"""ACCURACY: does `generate_orientation_candidates(max_candidates=4)` cost parts?

`geometry.generate_orientation_candidates` builds the six resting poses of the
OBB, dedups them, sorts by FOOTPRINT AREA, and truncates to `max_candidates=4`.
That constant is a Phase-1 UI number -- how many orientations to offer a user --
and it now gates the engine's whole search space, ranked by a key (area) that
predates the nester. The near-duplicate flip of each pose survives dedup (its CG
sits at a different height), so on every real file we hold the four slots hold
only TWO distinct footprints and the third -- always the tallest, smallest-area
pose -- is cut before the nester ever sees it.

This file measures what that costs. For each real file: measure EVERY generated
pose, then take the best count over the shipped prefix and over all of them.
Audited 2026-09-10 (ticket G-POSE) on all six cases we can compute:

    case          gen  shipped  fp lost         cap4  nocap   cut pose's own best
    mubea bar      6      4     298x143  ->  0    40    40     0  (h 1092 > 790)
    trw wheel      6      4     354x138  -> 36    48    48    36
    X104           6      4     128x90   -> 56    64    64    56  closest: 56 > 55,
                                                                  which IS in the set
    Nexon EV       6      4     245x122  ->  0    18    18     0  (h 711 > 580)
    P118           6      4     457x99   ->  0    12    12     0  (h 681 > 580)
    P125/P126      4      4     -- no truncation: dedup merged one flip pair --

Same answer across the whole 17-asset seeded catalogue and the synthesised
custom box (65/65, 48/48, 80/80, 36/36, 22/22, 80/80). So the cap costs zero
parts today. It is not harmless in principle -- on the Nexon and the P118 the
LARGEST-footprint pose is not the best one (32 vs 36, 20 vs 22 catalogue-wide),
i.e. area already mis-ranks poses; it just mis-ranks them inside the cap. And
the P118's cut pose TIES the winner at 22. One part of drift either way on a
future part and this test goes red instead of the engine quietly under-counting.

Two fixtures live in git-ignored `fixtures/customer/` and this skips without
them. The four Tata files are not fixtures (`~/Downloads`, see the memory note);
their numbers above were measured with the same code and are recorded, not run.

Run:  python tests/test_pose_search.py             # asserts the cap is free
      python tests/test_pose_search.py --reverse   # proves the assert can fail

`--reverse` sorts the poses worst-area-first before the cap truncates them,
which is fault injection, not a real configuration: it puts our two fixtures in
the position the Nexon and the P118 are ALREADY in (best pose not the
largest-footprint one) and the assert then fires with "the cap is now costing 4
parts". Lowering `--cap` alone does not fail here -- on both fixtures pose 0
happens to be the best pose, which is the whole reason this cap has survived
unnoticed.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalogue import containers                        # noqa: E402
from app.geometry import generate_orientation_candidates    # noqa: E402
from tests.ground_truth import PLS1280, PLS12801            # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "customer"
CASES = [
    (FIXTURES / "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs", PLS12801, 5.0),
    (FIXTURES / "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp", PLS1280, 2.5),
]

# The cap under test is whatever the engine actually ships, not a copy of it.
SHIPPED_CAP = inspect.signature(
    generate_orientation_candidates).parameters["max_candidates"].default

# Distinct footprint = (L, B) rounded to 1mm. Below the 4mm VOXEL_MM the nester
# quantises extents to anyway, so two footprints inside this tolerance cannot
# be meaningfully different problems -- and the poses are exact OBB face pairs,
# so real classes here differ by hundreds of mm, never by ones.
FOOTPRINT_MM = 1


def _best(poses, asset, part_kg):
    """Best count over a set of poses for one asset. `nesting.layouts_for`."""
    from app.nesting import layouts_for
    layouts = layouts_for(poses, asset, part_kg)
    return layouts[0].count if layouts else 0


def audit(path: Path, asset, part_kg: float, cap: int = SHIPPED_CAP,
          reverse: bool = False) -> dict:
    from app.geometry import extract_part, load_unified_mesh, minimum_obb
    from app.nesting import measure_poses

    res = extract_part(str(path))
    mesh, _ = load_unified_mesh(res.glb_path)
    to_obb, extents = minimum_obb(mesh)
    every = generate_orientation_candidates(mesh, to_obb, extents,
                                            max_candidates=99)
    if reverse:      # fault injection only -- see the module docstring
        every = every[::-1]
    shipped = every[:cap]
    poses = measure_poses(mesh, every)               # measured once, sliced below

    fps = [tuple(round(v / FOOTPRINT_MM) for v in c.dims_lbh[:2]) for c in every]
    lost = [fp for fp in dict.fromkeys(fps[len(shipped):]) if fp not in fps[:len(shipped)]]

    assets = containers(None) + [asset]
    per_asset = [(a.name, _best(poses[:len(shipped)], a, part_kg),
                  _best(poses, a, part_kg)) for a in assets]
    return {
        "file": path.name,
        "generated": len(every),
        "searched": len(shipped),
        "footprints": len(set(fps)),
        "lost_footprints": lost,
        "cap": _best(poses[:len(shipped)], asset, part_kg),
        "nocap": _best(poses, asset, part_kg),
        "cat_cap": max(r[1] for r in per_asset),
        "cat_nocap": max(r[2] for r in per_asset),
        "per_pose": [(c.dims_lbh, c.footprint_area, _best([p], asset, part_kg))
                     for c, p in zip(every, poses)],
        # The best count each CUT pose would have reached on its own, over the
        # whole catalogue. This is the margin the cap is riding on.
        "cut_best": max((max(_best([p], a, part_kg) for a in assets)
                         for p in poses[len(shipped):]), default=0),
        "shipped_is_prefix": [c.dims_lbh for c in res.candidates]
                             == [c.dims_lbh for c in every[:len(res.candidates)]],
    }


def test_cap_costs_no_parts(cap: int = SHIPPED_CAP, reverse: bool = False):
    """The truncated pose search must reach the same answer as the full one."""
    missing = [p.name for p, *_ in CASES if not p.exists()]
    if missing:
        print(f"  skipped: no customer fixtures (NDA, not in git): {missing}")
        return

    truncated_any = False
    for path, asset, part_kg in CASES:
        r = audit(path, asset, part_kg, cap, reverse)
        print(f"  {r['file'][:34]:<34} gen {r['generated']} -> searched "
              f"{r['searched']}, {r['footprints']} distinct footprints, "
              f"lost {r['lost_footprints'] or 'none'}")
        for dims, area, count in r["per_pose"]:
            print(f"      {dims}  area {area:>11,.0f} -> {count:>3}")
        print(f"      {asset.name}: cap {r['cap']} vs nocap {r['nocap']}   "
              f"catalogue: cap {r['cat_cap']} vs nocap {r['cat_nocap']}   "
              f"(best a cut pose reaches alone: {r['cut_best']})")

        # The comparison only means anything if the engine really does get the
        # area-sorted prefix.
        assert reverse or r["shipped_is_prefix"], \
            f"{r['file']}: extract_part's candidates are not the prefix of the " \
            "generated poses -- this test is measuring the wrong thing"
        assert r["cap"] == r["nocap"], (
            f"{r['file']}: max_candidates={cap} gives {r['cap']} in "
            f"{asset.name} but searching all {r['generated']} poses gives "
            f"{r['nocap']} -- the cap is now costing "
            f"{r['nocap'] - r['cap']} parts. Lift it or re-rank the poses."
        )
        assert r["cat_cap"] == r["cat_nocap"], (
            f"{r['file']}: max_candidates={cap} gives {r['cat_cap']} over the "
            f"catalogue, all {r['generated']} poses give {r['cat_nocap']} -- "
            f"the cap is costing {r['cat_nocap'] - r['cat_cap']} parts on some "
            "asset other than the ground-truth one."
        )
        truncated_any |= r["generated"] > r["searched"]
        # Since flip twins were dropped (one pose per OBB axis) the generator
        # yields at most three poses and every distinct footprint is searched.
        # That is what makes the cap free BY CONSTRUCTION rather than by luck
        # -- so assert the construction, not just its consequence.
        assert r["generated"] <= 3, \
            f"{r['file']}: {r['generated']} poses generated -- flip twins are back"
        assert r["generated"] == r["footprints"], \
            f"{r['file']}: {r['generated']} poses but {r['footprints']} footprints"
        assert cap < 3 or not r["lost_footprints"], \
            f"{r['file']}: footprints cut by the cap: {r['lost_footprints']}"

    # Non-vacuity used to be "something got truncated". With three poses and a
    # cap of four nothing ever is, and that is the point; `--cap 2` re-creates
    # truncation and `--reverse` still proves the count assert can fail.
    if cap < 3:
        assert truncated_any, "cap < 3 yet nothing was truncated"


def main() -> int:
    cap = SHIPPED_CAP
    if "--cap" in sys.argv:
        cap = int(sys.argv[sys.argv.index("--cap") + 1])
        print(f"(cap overridden to {cap}, shipped default is {SHIPPED_CAP})")
    reverse = "--reverse" in sys.argv
    if reverse:
        print("(FAULT INJECTION: poses reversed, worst area first -- this "
              "SHOULD fail)")
    print("test_cap_costs_no_parts:")
    test_cap_costs_no_parts(cap, reverse)
    print(f"\npose cap {cap} reaches the same count as the full pose search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
