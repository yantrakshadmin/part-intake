"""Ground truth from shipped technical proposals.

These are the acceptance contract for the packing engine. Any engine that cannot
reproduce `achieved` is wrong, however elegant.

Sources: TP_Mubea_1510_R2 (slides 3-4), TP_TRW Sun Steering Wheels_1704 (slides 3, 5).
Numbers are what Yantra Packs actually shipped, not what a model predicted.

Run:  python tests/ground_truth.py
"""
from dataclasses import dataclass, field
from itertools import permutations


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
