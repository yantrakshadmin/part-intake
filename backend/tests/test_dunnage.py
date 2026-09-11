"""P4-1 acceptance: regenerate both shipped dunnage BOMs from the lattice.

Run:  python tests/test_dunnage.py      (also run by tests/ground_truth.py)

This is a contract at the same status as the 40/48 counts. It takes each
ground-truth case's own pose extent, lattice pitch, grid and asset inner dims
-- no CAD, no mesh, no database -- and demands that `app.dunnage.bom` produces
the element list that actually shipped: same elements, same order, and for
every size or quantity we CLAIM to derive, the deck's own number.

Numbers we cannot derive are marked, never disguised. The MS Rod (d8x805,
qty 6) and the bars' 60/70mm widths come off the proposal decks and carry
`basis="pattern"`; an element is only as trustworthy as its weakest number,
so a bar whose length and height are derived still reads pattern because its
width is not. Anything with no source at all still emits `None` plus a
"needs deck" flag, and this test asserts it emitted nothing rather than a
plausible guess. A wrong number here costs real money per shipment; a blank
costs a phone call.

The second half is the check the element-by-element comparison CANNOT make:
that the BOM physically fits the box it is for. Two samples cannot falsify a
formula that happens to be right on both.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dunnage                                    # noqa: E402
from tests.ground_truth import CASES                       # noqa: E402

TOL_MM = 0.6            # deck sizes are whole mm; we floor, so allow rounding


def _num(s: str) -> bool:
    return s.replace(".", "", 1).isdigit()


def _parse_deck_size(size: str):
    """The deck's `size` column is not always a size. -> (kind, value)

    "1150x750x123"  -> ("dims", (1150.0, 750.0, 123.0))
    "d8x805"        -> ("rod", (8.0, 805.0))
    "3x2 matrix"    -> ("matrix", (3, 2))
    "1200 GSM"      -> ("spec", "1200 GSM")
    """
    s = size.strip()
    if s.endswith("matrix"):
        parts = s[: -len("matrix")].strip().split("x")
        return "matrix", tuple(int(p) for p in parts)
    if s.startswith("d") and all(_num(p) for p in s[1:].split("x")):
        return "rod", tuple(float(p) for p in s[1:].split("x"))
    parts = s.split("x")
    if len(parts) >= 2 and all(_num(p) for p in parts):
        return "dims", tuple(float(p) for p in parts)
    return "spec", s


def check_case(case, check) -> None:
    got = dunnage.bom(case.pose_lbh, case.pitch_lbh, _grid(case),
                      case.asset.inner)

    check(bool(case.dunnage), f"{case.ref}: deck BOM present in ground truth")

    deck_names = [e[0] for e in case.dunnage]
    got_names = [e.name for e in got.elements]
    check(got_names == deck_names,
          f"{case.ref}: element list matches the deck, in order",
          f"\n       got  {got_names}\n       deck {deck_names}")

    by_name = {e.name: e for e in got.elements}
    for name, size, qty in case.dunnage:
        el = by_name.get(name)
        if el is None:
            continue        # already reported by the list check above
        kind, want = _parse_deck_size(size)

        if kind == "dims":
            stated = [(lbl, d, w) for lbl, d, w in zip(el.labels, el.dims_mm, want)]
            for lbl, d, w in stated:
                if d is None:
                    check(lbl in el.unknown,
                          f"{case.ref}/{name}: unstated {lbl} is flagged unknown",
                          f": unknown={el.unknown}")
                else:
                    check(abs(d - w) <= TOL_MM,
                          f"{case.ref}/{name}: {lbl} = {w:g}",
                          f": derived {d:g}")
        elif kind == "matrix":
            check(el.matrix == want,
                  f"{case.ref}/{name}: matrix {want[0]}x{want[1]}",
                  f": derived {el.matrix}")
        elif kind == "rod":
            # E-DECK: the rod is now filled from `TP_Mubea_1510_R2` slide 4.
            # What must NEVER happen is it claiming to be DERIVED -- 805mm and
            # qty 6 follow from nothing this module measures, so a different
            # part in the same box must not inherit them by implication.
            check(el.basis == "pattern" and el.size == size,
                  f"{case.ref}/{name}: {size} is PATTERN, never derived",
                  f": emitted size={el.size!r} basis={el.basis!r}")
        else:
            check(el.spec is not None and want in el.spec,
                  f"{case.ref}/{name}: spec carries {want!r}",
                  f": spec={el.spec!r}")

        if "qty" in el.unknown:
            check(el.qty is None,
                  f"{case.ref}/{name}: unknown qty emitted as None",
                  f": qty={el.qty}")
        else:
            check(el.qty == qty, f"{case.ref}/{name}: qty {qty}",
                  f": derived {el.qty}")

    # Acceptance 3: nothing reaches the engineer without these.
    for el in got.elements:
        check(bool(el.name) and bool(el.labels) and el.basis in
              ("derived", "pattern", "unknown"),
              f"{case.ref}/{el.name}: carries name/dims/basis",
              f": basis={el.basis!r}")
        check(el.spec is not None or "spec" in el.unknown,
              f"{case.ref}/{el.name}: material stated or flagged",
              f": spec={el.spec!r} unknown={el.unknown}")
        if el.basis == "unknown":
            check(el.size is None or el.qty is None,
                  f"{case.ref}/{el.name}: an 'unknown' element claims nothing",
                  f": size={el.size!r} qty={el.qty}")

    check(dunnage.CAVEAT in got.caveat and "not" in got.caveat.lower(),
          f"{case.ref}: BOM is not presented as final")


def _grid(case) -> tuple[int, int, int]:
    """The deck's own grid, as `nesting.lattice_count` returns it."""
    from app.nesting import lattice_count
    _count, grid, _lim = lattice_count(case.pose_lbh, case.pitch_lbh,
                                       case.asset.inner, case.part_kg,
                                       case.asset.max_weight_kg)
    return grid


def check_height_budget(case, check) -> None:
    """The BOM must fit the box. Element-by-element cannot see this.

    Zero-slack is the normal case: Mubea stacks 190 + 9x66.67 = 790.0mm of
    parts inside a 790mm inner. So any element that adds dead height above the
    parts overflows, and this is the assertion that says so.
    """
    got = dunnage.bom(case.pose_lbh, case.pitch_lbh, _grid(case), case.asset.inner)
    inner_h = case.asset.inner[2]
    check(got.build_height_mm <= inner_h + 1e-6,
          f"{case.ref}: BOM build height fits the {inner_h}mm inner",
          f": stack {got.stack_height_mm:.1f} + dead "
          f"{got.build_height_mm - got.stack_height_mm:.1f} = "
          f"{got.build_height_mm:.1f}mm")
    check(got.fits, f"{case.ref}: bom().fits agrees",
          f": {got.build_height_mm:.1f} vs {inner_h}")


def check_overflow_is_detected(check) -> None:
    """Non-vacuity: the height budget must actually be able to fail.

    A check that cannot fail is decoration. Same lattice, but the top
    separator is forced thicker than the nest depth it lives in.
    """
    case = CASES[0]
    got = dunnage.bom(case.pose_lbh, case.pitch_lbh, _grid(case),
                      case.asset.inner, top_separator_mm=400.0)
    check(not got.fits,
          "a 400mm top separator is reported as NOT fitting",
          f": build {got.build_height_mm:.1f} vs inner {case.asset.inner[2]}")


def check_archetype(check) -> None:
    """One rule, not two archetypes: in-plane interleave picks the system."""
    mubea, trw = CASES[0], CASES[1]
    check(dunnage.archetype_of(mubea.pose_lbh, mubea.pitch_lbh) == "bar_and_rod",
          "Mubea (pitch 155 < extent 285) -> bar_and_rod",
          f": {dunnage.archetype_of(mubea.pose_lbh, mubea.pitch_lbh)}")
    check(dunnage.archetype_of(trw.pose_lbh, trw.pitch_lbh) == "pocket_tray",
          "TRW (pitch 376.6 > extent 370) -> pocket_tray",
          f": {dunnage.archetype_of(trw.pose_lbh, trw.pitch_lbh)}")
    # A pitch given as-is (no clearance): touching cuboids do not nest.
    check(dunnage.archetype_of((100, 100, 100), (100, 100, 100)) == "pocket_tray",
          "a cuboid (no interleave) -> pocket_tray")
    # The engine's pitch carries 5mm of in-plane air. A part with a genuine
    # 10mm interleave measures pitch = extent - 10 + 5 = extent - 5, which
    # read as "5mm interleave"; at 3mm it read as extent + 2, "no interleave",
    # and got a pocket tray. With the clearance the predicate sees the parts.
    ext, pitch, clr = (100, 100, 100), (95, 105, 90), (5.0, 5.0, 0.0)
    check(dunnage.archetype_of(ext, pitch, clr) == "bar_and_rod",
          "10mm in-plane + 10mm vertical interleave under 5mm clearance -> bar_and_rod",
          f": got {dunnage.archetype_of(ext, pitch, clr)}")
    check(dunnage.archetype_of((100, 100, 100), (105, 105, 100), clr) == "pocket_tray",
          "cuboid + 5mm clearance -> still pocket_tray")
    # Both interleaves are needed for bars. In plane only (parts overlap in
    # plan, stack flat) is a slotted tray: charging a bar the height of a
    # whole layer pitch there was what turned the wheel's Alternative 1 pose
    # into 21 parts, and vertical only is TRW (pocket deeper than the part).
    check(dunnage.archetype_of((372, 140, 356), (377, 121, 356), clr) == "pocket_tray",
          "in-plane interleave, flat stacking -> pocket_tray (slotted), not bars")
    # An interleave inside one voxel is raster noise: the Nexon radiator read
    # 124 extent / 125 pitch at 5mm clearance (4mm) and lost 9 of 18 to bars.
    check(dunnage.archetype_of((716, 124, 248), (717, 125, 244), clr) == "pocket_tray",
          "4mm 'interleave' on both axes (Nexon, 4mm voxels) -> pocket_tray")
    check(dunnage.dead_height_mm((716, 124, 248), (717, 125, 244), clr) == 3.0,
          "...and its dead height is one 3mm sheet")


def check_in_plane_slack(check) -> None:
    """The budget `lattice_count` never charges, reported per axis.

    The Mubea deck: 1085mm bar in a 1150 inner L with two 35mm side
    separators = 1155. Slack -5 on L, and the deck shipped it, so this is a
    report (`overflow`) and not a change to `fits`, which stays the height
    budget the count was solved on.
    """
    mubea = CASES[0]
    got = dunnage.bom(mubea.pose_lbh, mubea.pitch_lbh, _grid(mubea), mubea.asset.inner)
    check(abs(got.slack_lbh[0] - (1150 - 1085 - 70)) < 1e-6,
          "Mubea slack L = inner - bar - 2 side separators = -5mm",
          f": got {got.slack_lbh[0]:.1f}")
    check(abs(got.slack_lbh[1] - 0.0) < 1e-6,
          "Mubea slack B = 750 - (285 + 3 x 155) = 0",
          f": got {got.slack_lbh[1]:.1f}")
    check(abs(got.slack_lbh[2] - (790 - got.build_height_mm)) < 1e-6,
          "slack H is the height budget", f": {got.slack_lbh[2]:.1f}")
    check(got.overflow == [("L", 5.0)] and got.fits,
          "overflow names L by 5mm; fits (height) still True",
          f": overflow {got.overflow}, fits {got.fits}")
    check(got.as_dict()["slack_lbh"] == [round(v, 2) for v in got.slack_lbh],
          "slack_lbh is on the wire")

    trw = CASES[1]
    got = dunnage.bom(trw.pose_lbh, trw.pitch_lbh, _grid(trw), trw.asset.inner)
    # 1150 - (370 + 2 x 376.6) = 26.8; 750 - (360 + 367.5) = 22.5. Pocket
    # walls live inside the pitch, nothing stands beside a tray.
    check(abs(got.slack_lbh[0] - 26.8) < 1e-6 and abs(got.slack_lbh[1] - 22.5) < 1e-6,
          "TRW slack (26.8, 22.5): nothing beside a pocket tray",
          f": got {tuple(round(v, 1) for v in got.slack_lbh[:2])}")
    check(not got.overflow, "TRW has no overflow")


def check_bottom_separator_is_the_nest_depth(check) -> None:
    """Thickness = extent_H - pitch_H: the depth the bottom row nests in.

    Not `inner_H - layers x pitch_H`. Both give 123 for Mubea because that
    stack fills its box exactly, but the second one is really "whatever
    thickness makes the build exactly fill the inner height" -- it manufactures
    dead foam for any part that does not. Real CAD bar in the same PLS12801:
    extent 148, pitch 68, 10 layers -> nest depth 80mm, while the other formula
    asks for 790 - 680 = 110mm, 30mm of which exists only to fill the box.
    """
    got = dunnage.bom((1092, 300, 148), (1097, 145, 68), (1, 4, 10), (1150, 750, 790))
    sep = next(e for e in got.elements if e.name == "Bottom Separator Sheet Assy")
    check(sep.dims_mm[2] == 80.0,
          "real-CAD bar bottom separator = extent_H - pitch_H = 80mm",
          f": got {sep.dims_mm[2]}")
    check(got.fits and got.build_height_mm <= 790 + 1e-6,
          "real-CAD bar BOM fits its box",
          f": build {got.build_height_mm:.1f}")

    # No vertical interleave -> there is no nest depth, so no such element.
    flat = dunnage.bom((200, 100, 60), (100, 100, 60), (1, 7, 16), (1150, 750, 1000))
    check(not any(e.name == "Bottom Separator Sheet Assy" for e in flat.elements),
          "no vertical interleave -> no bottom separator invented",
          f": {[e.name for e in flat.elements]}")


def check_coplanar_bars_charge_once(check) -> None:
    """Bars at the same layer boundary must bill one bar's worth, not two.

    `bom()` SUMS `net_height_mm` over the elements, so an element's value is
    only correct relative to what every other element claims. The centre bar
    and the two side bars share a layer boundary -- same height, side by side
    across the breadth -- and each used to charge `max(0, bar_h - nest_depth)`,
    billing two bars for one bar of geometry.

    It was invisible on both shipped cases, which is why it survived: Mubea's
    bar_h (68) is under its nest depth (80), so both terms are zero, and TRW is
    the pocket-tray archetype with no bars at all. It bit only the poses with
    little or no vertical interleave -- reporting DOES NOT FIT for stacks that
    fit -- so the check has to build such a pose deliberately.

    The right total is one bar, the topmost: interior bars lie inside the pitch
    by construction (bar_h = floor(pitch_H)), so the parts stack has already
    paid for them.
    """
    # 10mm of vertical interleave (60 -> 50): past the one-voxel noise floor
    # `archetype_of` demands, so this IS the bar system, and bar_h 50 > nest 10
    # so the bar's dead height is charged and the double-billing would show.
    # (Zero vertical interleave is a slotted tray now, with no bars at all.)
    ext, pitch, grid, inner = (200, 100, 60), (100, 100, 50), (1, 7, 19), (1150, 750, 1000)
    got = dunnage.bom(ext, pitch, grid, inner)
    check(got.archetype == "bar_and_rod", "10mm interleave both ways -> bars")
    bar_h = float(math.floor(pitch[2]))
    dead = got.build_height_mm - got.stack_height_mm

    side = next(e for e in got.elements if e.name == "Top Side Bar")
    check(side.net_height_mm == 0.0,
          "side bar charges no height (coplanar with the centre bar)",
          f": got {side.net_height_mm}")
    # ...and it is zero because it is coplanar, NOT because the element lost
    # its geometry. A side bar with no height would pass the line above.
    check(side.dims_mm[2] == bar_h,
          f"side bar still HAS a height ({bar_h}mm), it just does not bill it",
          f": got {side.dims_mm[2]}")

    expect = (bar_h - got.nest_depth_mm
              + max(0.0, dunnage.TOP_SEPARATOR_MM - got.nest_depth_mm))
    check(abs(dead - expect) < 1e-6,
          f"shallow-interleave bar dead height = one bar + top sep = {expect:.1f}mm",
          f": got {dead:.1f} (two bars would be {expect + bar_h:.1f})")

    # The algebra the fix rests on: one bar's worth, derived two ways.
    #     layers*pitch_H + bar_h - stack  ==  bar_h - nest_depth
    for e2, p2, g2 in ((148, 68, 10), (60, 50, 19), (80, 60, 10)):
        stack = e2 + (g2 - 1) * p2
        nest = max(0.0, e2 - p2)
        check(abs((g2 * p2 + math.floor(p2) - stack) - (math.floor(p2) - nest)) < 1e-6,
              f"one-bar algebra holds for extent {e2}, pitch {p2}, {g2} layers")

    # Mubea must not move: its bar sits inside its nest depth either way, so
    # this fix is provably free on the one fact that drives everything.
    mubea = dunnage.bom((1092, 300, 148), (1097, 145, 68), (1, 4, 10), (1150, 750, 790))
    check(mubea.build_height_mm == mubea.stack_height_mm,
          "real-CAD Mubea bar still charges zero dead height (bar_h < nest depth)",
          f": build {mubea.build_height_mm:.1f} vs stack {mubea.stack_height_mm:.1f}")


def main() -> int:
    failures: list[str] = []

    def check(ok, label, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
        if not ok:
            failures.append(f"{label}{detail}")

    for case in CASES:
        check_case(case, check)
        check_height_budget(case, check)
    check_overflow_is_detected(check)
    check_archetype(check)
    check_in_plane_slack(check)
    check_bottom_separator_is_the_nest_depth(check)
    check_coplanar_bars_charge_once(check)

    print()
    for case in CASES:
        got = dunnage.bom(case.pose_lbh, case.pitch_lbh, _grid(case),
                          case.asset.inner)
        print(f"{case.ref}  {case.part_name}  [{got.archetype}]  "
              f"build {got.build_height_mm:.1f}/{case.asset.inner[2]}mm")
        for el in got.elements:
            print(f"    {el.name:<44} {str(el.size):<20} "
                  f"qty {str(el.qty):>4}  {el.basis:<8} "
                  f"{('needs deck: ' + ','.join(el.unknown)) if el.unknown else ''}")
        print()

    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all dunnage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
