"""Phase 1 acceptance: CAD import against real customer files.

Run:  python tests/test_cad_import.py

Skips cleanly when tests/fixtures/customer/ is empty — those files are NDA
material and are not in git. See that directory's README.

Reference dimensions come from an independent control-point hull, so they are
approximate UPPER bounds. A min-volume OBB should land at or below them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures" / "customer"

# name -> (sorted L,B,H reference upper bound, tolerance fraction)
REFERENCE = {
    "Y2V_YK9_Housing.igs": ((767, 534, 182), 0.20),
    "Y2V_YK9_Rack.igs": ((693, 230, 185), 0.20),
    "Y2V_YK9_IBJ.igs": ((356, 99, 37), 0.25),
    "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs": ((1092, 736, 249), 0.20),
}

# The Housing's whole point: naive AABB over all bodies is 4622x1470x1459.
# Anything above this means the largest-body filter did not run.
HOUSING_MAX_ANY_AXIS = 1500

STEEL_WHEEL = "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
UNSUPPORTED = "26137849_001-radiator_asm-eng_c_asm.SLDASM"


def _extract(path: Path):
    """Phase 1 must provide this. Returns sorted (L, B, H) in mm, L >= B >= H."""
    from app.geometry import extract_dimensions      # noqa: PLC0415
    return tuple(sorted(extract_dimensions(str(path)), reverse=True))


def _close(got, ref, tol):
    return all(abs(g - r) <= tol * r for g, r in zip(got, ref))


def main() -> int:
    if not FIXTURES.exists() or not any(FIXTURES.glob("*.ig*")):
        print("SKIP: no customer fixtures present (NDA material, not in git)")
        return 0

    failures = []

    for name, (ref, tol) in REFERENCE.items():
        path = FIXTURES / name
        if not path.exists():
            print(f"SKIP  {name}: missing")
            continue
        try:
            got = _extract(path)
        except Exception as e:
            failures.append(f"{name}: raised {type(e).__name__}: {e}")
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
            continue
        ok = _close(got, ref, tol)
        dims = " x ".join(f"{d:.0f}" for d in got)
        print(f"{'PASS' if ok else 'FAIL'}  {name:<45} {dims:>22}  (ref {ref}, +/-{tol:.0%})")
        if not ok:
            failures.append(f"{name}: got {dims}, expected ~{ref}")

    # The largest-body filter is the whole point of this phase.
    housing = FIXTURES / "Y2V_YK9_Housing.igs"
    if housing.exists():
        try:
            got = _extract(housing)
            if max(got) > HOUSING_MAX_ANY_AXIS:
                failures.append(
                    f"Housing max axis {max(got):.0f}mm > {HOUSING_MAX_ANY_AXIS}mm "
                    "- stray reference geometry was not filtered")
                print(f"FAIL  Housing largest-body filter: {max(got):.0f}mm")
            else:
                print(f"PASS  Housing largest-body filter: max axis {max(got):.0f}mm")
        except Exception as e:
            failures.append(f"Housing filter check: {e}")

    # Open-shell STEP: 1,195 OPEN_SHELL, zero solids. Must not crash.
    wheel = FIXTURES / STEEL_WHEEL
    if wheel.exists():
        try:
            got = _extract(wheel)
            plausible = all(50 <= d <= 800 for d in got)
            print(f"{'PASS' if plausible else 'FAIL'}  open-shell STEP: "
                  f"{' x '.join(f'{d:.0f}' for d in got)}")
            if not plausible:
                failures.append(f"steering wheel dims implausible: {got}")
        except Exception as e:
            failures.append(f"open-shell STEP raised {type(e).__name__}: {e}")
            print(f"FAIL  open-shell STEP: {type(e).__name__}: {e}")

    # SLDASM has no open-source reader. Must fail loudly and say what to do.
    sldasm = FIXTURES / UNSUPPORTED
    if sldasm.exists():
        try:
            _extract(sldasm)
            failures.append("SLDASM did not raise - it must fail loudly")
            print("FAIL  SLDASM accepted silently")
        except Exception as e:
            msg = str(e).lower()
            helpful = any(w in msg for w in ("step", "iges", "unsupported"))
            print(f"{'PASS' if helpful else 'FAIL'}  SLDASM rejected: {type(e).__name__}: {e}")
            if not helpful:
                failures.append("SLDASM error message must name STEP/IGES as the fix")

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
