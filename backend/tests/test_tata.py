"""ACCURACY: the four Tata Autocomp cases, from `TP_Tata Autocomp _ 1574 & 1578`.

These are a DIFFERENT SHAPE of case from Mubea and TRW, and that is why they
live in their own file rather than in `ground_truth.CASES`.

Mubea and TRW are non-convex parts whose shipped count BEATS the cuboid
baseline -- `ground_truth._selftest` asserts `baseline < achieved` for exactly
that reason. All four Tata parts are near-cuboid heat exchangers, and their
shipped counts sit BELOW the cuboid baseline, because a PP-flute insert has
walls: every pocket in these decks is 5-25mm larger than the part it holds.
Adding them to `CASES` would mean weakening a guard that is there to catch a
wrong baseline, so they get their own contract instead.

What this file pins down: **given the deck's own pocket pitch, the lattice
arithmetic reproduces the shipped count.** That isolates the two things that
were previously tangled -- whether `lattice_count` is right, and whether the
engine picks the same pose a packaging engineer picked. The first is now
tested. The second is an open question and is reported, not asserted.

Measured 2026-09-10 with the real engine on the decks' own STEP files, default
parameters (`python scratchpad/tata.py`, 41-511s per file):

    X104 intercooler   engine  64  (8,1,8) pitch (137, 673, 96)   deck 60
    Nexon EV radiator  engine  18  (9,1,2) pitch (125, 717, 244)  deck 18
    P118 radiator      engine  12  (2,1,6) pitch (465, 689, 92)   deck 10
    P125/6 intercooler engine  64  (8,1,8) pitch (137, 629, 92)   deck 65

The engine agrees exactly on the Nexon and disagrees on the other three
BECAUSE IT CHOOSES A DIFFERENT POSE: every deck states "component will be
placed in Vertical orientation", while the engine lays these parts flat, gets a
smaller vertical pitch, and buys layers. Nobody has told us whether that pose
is available -- the decks also say "TATA logo to be printed on the assets", so
the orientation may be a customer requirement rather than an optimum. Do NOT
tune the engine to these four numbers until that is answered: matching a pose
somebody else chose is the same flattery CLAUDE.md warns about with fixed
orientations, just pointed the other way.

CAD-vs-deck dimensions, on the other hand, agree on all four to under 3mm --
an independent check of the extraction pipeline on four files it had never seen:

    deck 668x128x90     cad 667.6x127.8x90.1
    deck 714x122x246    cad 711.1x245.3x122.4
    deck 681x98x457     cad 680.9x457.3x99.2
    deck 625x130x89.7   cad 624.6x129.5x89.7

Run:  python tests/test_tata.py          # deck-pitch arithmetic
      python tests/test_tata.py --cad    # the engine on the four STEP files (slow)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nesting import lattice_count                        # noqa: E402
from tests.ground_truth import Asset                         # noqa: E402

# Both from the deck's own asset slides; neither is in the SCS report.
PLS12801 = Asset("PLS12801", (1150, 750, 790), (1200, 800, 980), 30.0, 600.0)
PLS12804 = Asset("PLS12804", (1150, 750, 580), (1200, 800, 790), 25.0, 600.0)

# (name, part extents as it rests in asset L/B/H order, pocket pitch in the
#  same order, asset, parts/insert, inserts/kit, parts/kit as shipped)
# `pose` is the CAD measurement, not the deck's rounded figure, so the
# arithmetic is exercised on the numbers the engine would actually feed it.
CASES = [
    ("X104 DSL Intercooler",
     (90.1, 667.6, 127.8), (90.4, 685.0, 150.0), PLS12801, 12, 5, 60, 1.75),
    ("Nexon EV Radiator",
     (122.4, 711.1, 245.3), (122.2, 740.0, 260.0), PLS12804, 9, 2, 18, 1.15),
    ("P118 Radiator",
     (99.2, 680.9, 457.3), (109.5, 690.0, 485.0), PLS12804, 10, 1, 10, 2.30),
    ("P125/P126 Intercooler",
     (89.7, 624.6, 129.5), (90.4, 635.0, 135.0), PLS12801, 13, 5, 65, 1.75),
]

# P125/P126 is the one case whose shipped count is NOT a regular lattice. Its
# deck reads matrix "12x1(1)", parts/insert 13: twelve in a row plus one more,
# five times over. A pure lattice gives 12 x 1 x 5 = 60, and the deck ships 65.
# The five extras are real and we cannot place them from the deck text alone --
# 12 x 90.4 = 1085 leaves 65mm across, too little for an 89.7mm part, but five
# layers at 135mm leave 115mm of height, which is enough for one lying flat.
# Recorded as a gap in what the engine models (regular lattices only), not
# absorbed into a fudged pitch.
IRREGULAR = {"P125/P126 Intercooler": 5}

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
    if not ok:
        failures.append(label)


def main() -> int:
    for name, pose, pitch, asset, per_insert, inserts, shipped, kg in CASES:
        count, grid, limited = lattice_count(
            pose, pitch, asset.inner, kg, asset.max_weight_kg)
        extra = IRREGULAR.get(name, 0)
        want = shipped - extra

        check(count == want, f"{name:<24} {asset.name} lattice",
              f" -> {count} in {grid}, deck ships {shipped}"
              + (f" ({want} regular + {extra} outside the lattice)" if extra else ""))
        # The grid must be the deck's own matrix, not just the right product.
        check(grid[2] == inserts and grid[0] * grid[1] == per_insert - extra // inserts,
              f"{name:<24} matrix",
              f" -> {grid[0]}x{grid[1]} x {grid[2]} inserts, deck says "
              f"{per_insert - extra // inserts}x{inserts}")
        check(count > 0, f"{name:<24} fits at all", f" (limited_by={limited})")
        check(shipped * kg <= asset.max_weight_kg,
              f"{name:<24} within {asset.name} weight cap",
              f" ({shipped * kg:.2f} <= {asset.max_weight_kg} kg)")

    # Non-vacuity: the pockets are what make three of these counts, so a
    # lattice fed the PART extents instead of the POCKET pitch must not
    # reproduce the deck -- otherwise the pocket walls do no work and this file
    # checks nothing. The Nexon is the honest exception: its pocket is 122.2mm
    # for a part CAD measures at 122.4 (the deck rounds the part to 122), so it
    # is part-tight on the axis that matters and the two agree at 18.
    tight = {}
    for name, pose, _pitch, asset, _pi, _ins, shipped, kg in CASES:
        n, _, _ = lattice_count(pose, pose, asset.inner, kg, asset.max_weight_kg)
        tight[name] = (n, shipped)
    gained = {k: v for k, v in tight.items() if v[0] > v[1]}
    check(len(gained) == 3 and "Nexon EV Radiator" not in gained,
          "pocket walls cost parts on 3 of 4 (Nexon's pocket is part-tight)",
          f" part-tight vs shipped: "
          + ", ".join(f"{k.split()[0]} {a}/{b}" for k, (a, b) in tight.items()))

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all four Tata counts reproduced from the decks' own pocket pitches")
    return 0




# ---------------------------------------------------------------------------
# The engine against the four STEP files (`--cad`). Opt-in: 1-9 minutes per
# file, almost all of it OpenCascade reading a 5-46MB assembly. The fixtures
# are gitignored NDA copies (tests/fixtures/customer/README.md).
#
# Two numbers per case. "all poses" is what the engine recommends left to
# itself; "deck pose" is the engine restricted to the resting pose the deck
# packed (the customer wants the logo up, `SolveIn.confirmed_pose_only`). The
# gap between them is the +4/+2 the audit could not decide, made visible
# rather than tuned away. Both are PINNED so drift shows up as a failure.
#
# Measured 2026-09-10 (4mm voxels, 5mm in-plane clearance, 3 poses each):
#
#     X104    all 64 (8,1,8)   deck pose 55 (11,1,5)   deck ships 60   42s
#     Nexon   all 18 (9,1,2)   deck pose 18 (9,1,2)    deck ships 18  132s
#     P118    all 12 (2,1,6)   deck pose 11 (11,1,1)   deck ships 10  484s
#     P125/6  all 64 (8,1,8)   deck pose 55 (11,1,5)   deck ships 65   43s
#
# In the deck's pose the remaining gap is the in-plane CLEARANCE, not the
# geometry: X104 ships 12 across at a 90.4 pocket for a 90.1 part (0.3mm),
# the engine's 5mm makes it 11; P118 ships 10 at 109.5 for 99.2 (10.3mm),
# the engine's 5mm makes it 11. One constant cannot be right for both, which
# is why `SolveIn.clearance_mm` is a parameter the engineer sets per job.
# ---------------------------------------------------------------------------
FIXTURES = Path(__file__).parent / "fixtures" / "customer"
# (case name, file, asset, kg, deck pose height mm, pinned all-poses count,
#  pinned deck-pose count). Heights identify the deck's pose among the
#  engine's candidates: X104 stands on its 668x90 face (128 up), etc.
CAD_CASES = [
    ("X104 DSL Intercooler", "a0tnc5001m00_asm 1-X104 Intercooler- Sanand.stp",
     PLS12801, 1.75, 128, 64, 55),
    ("Nexon EV Radiator", "A0TZB5021M00_ASM 1-Nexon EV Radiator-Sanand.stp",
     PLS12804, 1.15, 245, 18, 18),
    ("P118 Radiator", "a7hqb5061m00_asm (1)-P118 Radiator- Haridwar.stp",
     PLS12804, 2.30, 457, 12, 11),
    ("P125/P126 Intercooler", "a7hqc5001m00_asm 1- P125-P126 Intercooler Haridwar.stp",
     PLS12801, 1.75, 130, 64, 55),
]


def cad_main() -> int:
    import time
    from app.geometry import extract_part, load_unified_mesh
    from app.nesting import layouts_for, measure_poses

    shipped = {c[0]: c[6] for c in CASES}
    for name, fname, asset, kg, deck_h, pin_all, pin_deck in CAD_CASES:
        path = FIXTURES / fname
        if not path.exists():
            check(False, f"{name:<24} fixture present", f": missing {fname}")
            continue
        t0 = time.time()
        r = extract_part(str(path))
        mesh, _ = load_unified_mesh(r.glb_path)
        poses = measure_poses(mesh, r.candidates)
        t1 = time.time()

        best_all = layouts_for(poses, asset, kg)[0]
        deck = [p for p in poses if abs(p.extent[2] - deck_h) <= 8.0]
        best_deck = layouts_for(deck, asset, kg)[0] if deck else None
        got_deck = best_deck.count if best_deck else 0

        print(f"{name:<24} {asset.name}  all poses {best_all.count:>3} {best_all.grid} "
              f"pitch {best_all.pitch_lbh}   deck pose {got_deck:>3} "
              f"{best_deck.grid if best_deck else '-'}   deck ships "
              f"{shipped[name]}   [{t1 - t0:.0f}s, {len(poses)} poses]")
        check(best_deck is not None,
              f"{name:<24} deck pose (H~{deck_h}) is among the candidates",
              f": {[p.extent for p in poses]}")
        check(best_all.count >= got_deck,
              f"{name:<24} all-poses >= deck-pose", f" ({best_all.count} vs {got_deck})")
        if pin_all is not None:
            check(best_all.count == pin_all, f"{name:<24} all-poses pinned",
                  f" ({best_all.count}, pinned {pin_all})")
        if pin_deck is not None:
            check(got_deck == pin_deck, f"{name:<24} deck-pose pinned",
                  f" ({got_deck}, pinned {pin_deck})")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    if "--cad" in sys.argv:
        raise SystemExit(cad_main())
    raise SystemExit(main())
