"""Do we read, honour and report the unit a CAD file declares?

Run:  python tests/test_iges_units.py     (exit 0 = yes, it converts to mm)

All four real IGES fixtures are authored in mm, so tests/test_cad_import.py
never exercises the non-mm path — and IGES is 4 of our 5 customer files. This
generates the missing case with OCP itself: a cube measuring 1x1x1 in a file
that declares INCH, and the same cube in a file that declares MM, both read
back through `extract_part`, the function upload actually calls.

Measured answer: OCC converts on read. The INCH cube extracts as 25.4 mm, the
MM cube as 1.0 mm. If that ever stops being true, every inch-authored IGES is
silently wrong by 25.4x and this file is what says so.

No committed binaries — both IGES files are written to a temp dir at run time.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# mm per unit for each unit name the IGES global section can declare.
MM_PER_UNIT = {"MM": 1.0, "INCH": 25.4}
TOL = 1e-3


def _pin_mm():
    from app.step_fallback import _pin_cascade_unit_mm       # noqa: PLC0415
    _pin_cascade_unit_mm()


def _write_unit_cube(unit: str, path: Path) -> Path:
    """Write an IGES file declaring `unit` and holding a 1x1x1 cube *in that
    unit*. IGESControl_Writer converts from the cascade unit (mm) to the file
    unit, so the source solid is MM_PER_UNIT[unit] mm on a side."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox          # noqa: PLC0415
    from OCP.IGESControl import IGESControl_Writer           # noqa: PLC0415

    _pin_mm()
    side = MM_PER_UNIT[unit]
    writer = IGESControl_Writer(unit, 0)
    writer.AddShape(BRepPrimAPI_MakeBox(side, side, side).Shape())
    writer.ComputeModel()
    if not writer.Write(str(path)):
        raise RuntimeError(f"IGESControl_Writer failed to write {path}")
    return path


def _global_max_coord(path: Path) -> float | None:
    """Largest coordinate in the file, in file units, from the global section.

    This is the pin that makes the round trip a test of the *reader*: without
    it, a writer and reader that both ignored units would still agree.
    """
    from OCP.IFSelect import IFSelect_RetDone                # noqa: PLC0415
    from OCP.IGESControl import IGESControl_Reader           # noqa: PLC0415

    _pin_mm()
    reader = IGESControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        return None
    section = reader.WS().Model().GlobalSection()
    return section.MaxCoord() if section.HasMaxCoord() else None


def check_step_non_mm_warns(check) -> None:
    """A STEP file declaring METRE must warn, not only one declaring INCH.

    `detect_step_length_unit` has always been able to return 'm' and 'foot',
    and `extract_part` branched on `== "inch"` -- so a metre-authored STEP got
    exactly the silence G-UNIT removed from the IGES path, at 1000x rather than
    25.4x.

    The IGES cubes above are written by OCP, and that will not work here:
    `Interface_Static.SetCVal_s("write.step.unit", "M")` is silently REJECTED
    by this OCC build -- it reads back as "MM" and the file still says
    SI_UNIT(.MILLI.,.METRE.). (INCH does take, as CONVERSION_BASED_UNIT.) So
    the metre case is made by writing a normal mm STEP with OCP and rewriting
    the one SI_UNIT line, which is the exact text the detector reads.

    Geometry is deliberately not asserted: the doctored file declares metres
    while holding mm-magnitude coordinates, so its dimensions are meaningless
    by construction. What is asserted is the reporting -- which is the whole
    of what was broken.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox          # noqa: PLC0415
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer  # noqa: PLC0415, E501

    from app.geometry import (_UNIT_LABEL, detect_step_length_unit,  # noqa: PLC0415, E501
                              extract_part)

    _pin_mm()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metre_cube.stp"
        writer = STEPControl_Writer()
        writer.Transfer(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape(),
                        STEPControl_AsIs)
        writer.Write(str(path))

        text = path.read_text(errors="replace")
        doctored = text.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)")
        check(doctored != text, "OCP wrote an SI_UNIT length line to rewrite")
        path.write_text(doctored)

        got = detect_step_length_unit(path)
        check(got == "m", "METRE STEP header is detected as 'm'", f": got {got!r}")

        result = extract_part(str(path))
        unit_warnings = [w for w in result.warnings if "declares" in w]
        check(len(unit_warnings) == 1 and "METRE" in unit_warnings[0],
              "METRE STEP warns and names the unit readably",
              f": {unit_warnings}")

    # Every non-mm value the detector can return must have a readable label,
    # or the warning says "declares M units" and reads like a typo.
    unlabelled = [u for u in ("m", "foot", "inch") if u not in _UNIT_LABEL]
    check(not unlabelled, "every non-mm unit the detector returns has a label",
          f": {unlabelled}")


def main() -> int:
    from app.geometry import extract_part                    # noqa: PLC0415
    from app.step_fallback import iges_declared_unit         # noqa: PLC0415

    failures = []

    def check(ok, label, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
        if not ok:
            failures.append(f"{label}{detail}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for unit, mm in MM_PER_UNIT.items():
            igs = _write_unit_cube(unit, tmp / f"cube_{unit.lower()}.igs")

            max_coord = _global_max_coord(igs)
            check(max_coord is not None and abs(max_coord - 1.0) < TOL,
                  f"{unit:<4} file really holds a 1x1x1 cube in file units",
                  f": global-section max coord = {max_coord}")

            declared = iges_declared_unit(igs)
            check(declared == unit.lower(),
                  f"{unit:<4} declared unit read from the file",
                  f": {declared!r}")

            result = extract_part(igs, glb_out=tmp / f"cube_{unit}.glb")
            dims = result.canonical_dims_lbh
            check(all(abs(d - mm) < TOL for d in dims),
                  f"{unit:<4} 1x1x1 extracts as {mm} mm",
                  f": {dims} mm  (units_assumed={result.units_assumed!r})")

            check(result.units_assumed == "mm",
                  f"{unit:<4} output contract stays mm")

            # The reporting half: a non-mm IGES must say so, an mm one must not.
            unit_warnings = [w for w in result.warnings if "declares" in w]
            if unit == "MM":
                check(not unit_warnings, "MM   file raises no unit warning",
                      f": {unit_warnings}")
            else:
                check(len(unit_warnings) == 1 and "INCH" in unit_warnings[0]
                      and "already mm" in unit_warnings[0],
                      "INCH file warns, and says the values are already mm",
                      f": {unit_warnings}")

    check_step_non_mm_warns(check)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
