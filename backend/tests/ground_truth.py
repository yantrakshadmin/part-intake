"""Ground truth from shipped technical proposals.

These are the acceptance contract for the packing engine. Any engine that cannot
reproduce `achieved` is wrong, however elegant.

Sources: TP_Mubea_1510_R2 (slides 3-4), TP_TRW Sun Steering Wheels_1704 (slides 3, 5).
Numbers are what Yantra Packs actually shipped, not what a model predicted.

Run:  python tests/ground_truth.py
"""
import sys
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass(frozen=True)
class Asset:
    name: str
    inner: tuple[int, int, int]      # L, B, H mm
    outer: tuple[int, int, int]
    tare_kg: float
    max_weight_kg: float


@dataclass(frozen=True)
class Case:
    ref: str
    part_name: str
    part_lbh: tuple[float, float, float]   # mm, as stated in the proposal
    part_kg: float
    pose_lbh: tuple[float, float, float]   # extents as the part rests, asset L/B/H order
    pitch_lbh: tuple[float, float, float]  # lattice spacing that shipped, same order
    asset: Asset
    achieved: int                    # parts per asset, as shipped
    layers: int
    per_layer: int
    orientation: str
    note: str = ""
    dunnage: list[tuple] = field(default_factory=list)   # (element, size, qty)


# Assets as quoted in the proposals. Where these disagree with the catalogue
# export, the proposal wins here — it describes what physically shipped.
PLS12801 = Asset("PLS12801", (1150, 750, 790), (1200, 800, 1000), 30.0, 600.0)
PLS1280 = Asset("PLS1280", (1150, 750, 1000), (1200, 800, 1200), 35.0, 600.0)

CASES = [
    Case(
        ref="YNT/TP/1510_R2",
        part_name="Stabiliser Bar (Mubea, Pune)",
        part_lbh=(1085, 190, 285),
        part_kg=5.0,
        # Resting on the 190mm face, so the 285mm bend spread lies in plane.
        pose_lbh=(1085, 285, 190),
        # Neither pitch is printed in the deck; these are the loosest spacings
        # consistent with 4/layer x 10 layers, and the vertical one is
        # corroborated by the deck's own dunnage - 11 Top Center Bars 66mm tall
        # for 10 layers. (Resting on 285 instead gives 186.67/56.11 and the same
        # 40; the formula is validated either way.)
        pitch_lbh=(1085.0, 155.0, 200.0 / 3.0),
        asset=PLS12801,
        achieved=40,
        layers=10,
        per_layer=4,
        orientation="horizontal",
        note="Bent tubular. Bars interleave: 10 layers of a 285mm part inside "
             "790mm, and 4x190=760 exceeds the 750mm width flat.",
        dunnage=[
            ("Bottom Separator Sheet Assy", "1150x750x123", 1),
            ("Top Center Bar", "750x60x66", 11),
            ("Top Side Bar", "750x70x66", 20),
            ("Side Separator", "750x790x35", 2),
            ("Top Separator", "1100x700x30", 1),
            ("MS Rod", "d8x805", 6),
        ],
    ),
    Case(
        ref="YNT/TP/1704",
        part_name="Steering Wheel (TRW Sun, Manesar)",
        part_lbh=(370, 360, 135),
        part_kg=2.5,
        # Straight off the deck: pocket 376.6 x 367.5 x 117, plus a 3mm
        # separator sheet between inserts. The 117mm pocket under a 135mm wheel
        # is why 8 layers fit in 1000mm - the wheels sit into the trays.
        pose_lbh=(370, 360, 135),
        pitch_lbh=(376.6, 367.5, 120.0),
        asset=PLS1280,
        achieved=48,
        layers=8,
        per_layer=6,
        orientation="vertical",
        note="Pocket matrix 3x2 per insert, 8 inserts. Pocket 376.6x367.5x117 "
             "- pocket height is under the part height, so wheels sit into the tray.",
        dunnage=[
            ("PP Bubble Guard insert (1200 GSM, 5mm)", "3x2 matrix", 8),
            ("Separator sheet (PP Bubble Guard + EVA, 3mm)", "1200 GSM", 9),
        ],
    ),
]


def cuboid_baseline(part_lbh, asset: Asset) -> tuple[int, tuple]:
    """Best parts-per-asset treating the part as its bounding box.

    Tries all 6 axis permutations and keeps the best — a fair baseline. Fixing
    one orientation flatters the nesting engine, so don't.

    Returns (count, winning_permutation).
    """
    best, best_perm = 0, None
    for perm in set(permutations(part_lbh)):
        n = 1
        for p, cap in zip(perm, asset.inner):
            n *= int(cap // p)
        if n > best:
            best, best_perm = n, perm
    return best, best_perm


def baseline_for(case: Case) -> tuple[int, tuple]:
    """Cuboid baseline for a case, capped by the asset's weight limit."""
    geo, perm = cuboid_baseline(case.part_lbh, case.asset)
    by_weight = int(case.asset.max_weight_kg // case.part_kg)
    return min(geo, by_weight), perm


def report() -> list[dict]:
    rows = []
    for c in CASES:
        base, perm = baseline_for(c)
        rows.append({
            "ref": c.ref,
            "part": c.part_name,
            "asset": c.asset.name,
            "cuboid": base,
            "cuboid_orientation": perm,
            "achieved": c.achieved,
            "gain": c.achieved / base if base else float("inf"),
        })
    return rows


def evaluate(engine) -> list[dict]:
    """Score a packing engine against ground truth.

    `engine(part_lbh, asset) -> int` (parts per asset). Later phases plug in here.

    Note what a bounding-box-only signature can and cannot do: no engine given
    only `part_lbh` can beat `cuboid_baseline`, so 40 is unreachable through it.
    The nesting engine consumes `case.pitch_lbh` (or measures pitch from the
    real mesh) - see `app/nesting.py` and `tests/test_nesting.py`.
    """
    out = []
    for c in CASES:
        got = engine(c.part_lbh, c.asset)
        out.append({
            "ref": c.ref, "target": c.achieved, "got": got,
            "error_pct": 100.0 * (got - c.achieved) / c.achieved,
            "pass": got == c.achieved,
        })
    return out


def _selftest():
    assert len(CASES) == 2

    for c in CASES:
        assert sorted(c.pose_lbh, reverse=True) == sorted(c.part_lbh, reverse=True), \
            f"{c.ref}: pose_lbh is not a permutation of part_lbh"
        # A pitch below its extent means the parts interleave; above it means
        # clearance or a pocket wall (TRW's 376.6mm pocket for a 370mm wheel).
        # Both are real. Only a pitch that overflows the asset is impossible.
        assert all(0 < p <= i for p, i in zip(c.pitch_lbh, c.asset.inner)), \
            f"{c.ref}: pitch does not fit the asset"
        assert c.per_layer * c.layers == c.achieved, \
            f"{c.ref}: {c.per_layer}/layer x {c.layers} != {c.achieved}"
        base, _ = baseline_for(c)
        assert base > 0, f"{c.ref}: part does not fit its own asset at all"
        assert base < c.achieved, \
            f"{c.ref}: cuboid baseline {base} >= shipped {c.achieved} - baseline is wrong"

    # Weight sanity against the proposals' own stated totals.
    assert CASES[0].achieved * CASES[0].part_kg == 200.0     # Mubea deck: 200 kg
    assert CASES[1].achieved * CASES[1].part_kg == 120.0     # TRW deck: 120 kg

    for c in CASES:
        assert c.achieved * c.part_kg <= c.asset.max_weight_kg, f"{c.ref}: over weight cap"

    # A perfect engine passes; a cuboid engine must not.
    assert all(r["pass"] for r in evaluate(lambda lbh, a: {(1085,190,285): 40,
                                                           (370,360,135): 48}[tuple(lbh)]))
    assert not any(r["pass"] for r in evaluate(lambda lbh, a: cuboid_baseline(lbh, a)[0]))
    print("selftest ok")


if __name__ == "__main__":
    print(f"{'ref':<18}{'asset':<11}{'cuboid':>8}{'shipped':>9}{'gain':>7}   cuboid orientation")
    for r in report():
        print(f"{r['ref']:<18}{r['asset']:<11}{r['cuboid']:>8}{r['achieved']:>9}"
              f"{r['gain']:>6.1f}x   {r['cuboid_orientation']}")
    print()
    _selftest()

    # `evaluate()` above is only ever handed a stub engine, so nothing in this
    # file puts the REAL nesting engine on the hook for 40 and 48. This does:
    # customer CAD in, default parameters, counts out. Skips itself when the
    # fixtures are absent (NDA, not in git).
    print()
    print("engine vs CAD contract (tests/test_clearance.py):")
    from tests.test_clearance import main as _clearance_main
    if _clearance_main() != 0:
        raise SystemExit(1)

    # The four Tata Autocomp cases. Not in CASES above: they are near-cuboid
    # parts whose shipped counts sit BELOW the cuboid baseline (a PP-flute
    # insert has walls), so `_selftest`'s `baseline < achieved` guard -- which
    # exists to catch a wrong baseline -- does not apply to them.
    print()
    print("Tata Autocomp pocket contract (tests/test_tata.py):")
    from tests.test_tata import main as _tata_main
    if _tata_main() != 0:
        raise SystemExit(1)

    # The dunnage BOM is a contract at the same status as the counts above
    # (ticket P4-1), so the same runner covers it. It lives in a sibling
    # because it imports app/ and this module is deliberately pure data.
    print()
    print("dunnage BOM contract (tests/test_dunnage.py):")
    from tests.test_dunnage import main as _dunnage_main
    if _dunnage_main() != 0:
        raise SystemExit(1)
