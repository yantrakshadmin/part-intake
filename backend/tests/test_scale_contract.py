"""Hard rule 1: "Never change the metre<->mm scaling in only one place.
Backend scales mesh x1000; frontend scales model x1000. A contract test
enforces this."

That contract test did not exist. CLAUDE.md has asserted it since Phase 1 and
a code review found only comments -- `geometry.py:191` says "meters -> mm" and
`OrientationViewer.jsx` says "must match the backend", which is documentation,
not enforcement.

There are THREE sites, not two, and they are in two languages:

    app/step_fallback.py:146   x0.001   OCC gives mm, the GLB contract is metres
    app/geometry.py:191        x1000    GLB metres -> mm for measurement
    frontend glbModel.js       x1000    the same GLB, for display

The fallback reader's x0.001 is what makes the GLB a metres file in the first
place, so it must be the exact reciprocal of the other two -- if it drifted,
the cascadio path and the OCP fallback path would disagree with each other and
only ONE of them would match the frontend. That is the failure this file exists
to catch, and it is invisible to any test that looks at one language.

Source-scanned deliberately: the frontend constant cannot be imported into
Python, and a test that hard-codes 1000 on both sides would pass while the two
files disagreed -- which is exactly the hole being closed.

Run:  python tests/test_scale_contract.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "app"
GLB_MODEL = ROOT / "frontend" / "src" / "lib" / "glbModel.js"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
    if not ok:
        failures.append(label)


def _sole(pattern: str, path: Path) -> str | None:
    """The one match in `path`, or None if absent/ambiguous."""
    found = re.findall(pattern, path.read_text())
    return found[0] if len(found) == 1 else None


def main() -> int:
    # --- backend: the measurement path -----------------------------------
    up = _sole(r"mesh\.apply_scale\(([\d.]+)\)\s*#\s*meters", BACKEND / "geometry.py")
    check(up == "1000.0", "geometry.py scales the GLB mesh metres -> mm",
          f" (got {up!r})")

    # --- backend: the fallback reader, which MAKES the GLB metric --------
    down = _sole(r"mesh\.apply_scale\(([\d.]+)\)\s*#\s*OCC gives mm",
                 BACKEND / "step_fallback.py")
    check(down == "0.001", "step_fallback.py scales OCC mm -> GLB metres",
          f" (got {down!r})")

    # The reciprocal is the whole contract: whatever the fallback divides by,
    # the measurement path must multiply by. Drift here makes the two READER
    # paths disagree, and only one of them matches the frontend.
    if up and down:
        check(abs(float(up) * float(down) - 1.0) < 1e-12,
              "the two backend factors are exact reciprocals",
              f" ({up} x {down} = {float(up) * float(down)})")

    # --- frontend: the display path --------------------------------------
    if not GLB_MODEL.is_file():
        check(False, "frontend glbModel.js is where the contract says it is",
              f" (missing: {GLB_MODEL})")
    else:
        fe = _sole(r"export const MM_PER_M = (\d+)", GLB_MODEL)
        check(fe is not None, "frontend exports a single MM_PER_M", f" (got {fe!r})")
        if fe and up:
            check(float(fe) == float(up),
                  "frontend MM_PER_M == backend mesh scale",
                  f" ({fe} vs {up})")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        print("\nHard rule 1: the scaling must change in every place at once.")
        return 1
    print("scale contract holds across all three sites, both languages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
