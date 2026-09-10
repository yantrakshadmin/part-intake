"""Phase 1 acceptance: CAD import against real customer files.

Run:  python tests/test_cad_import.py

Skips cleanly when tests/fixtures/customer/ is EMPTY — those files are NDA
material and are not in git. See that directory's README.

A PARTIALLY populated directory fails, deliberately. Without the steering
wheel this suite has no external ground truth at all, only snapshots of our
own output, and an earlier version printed "all checks passed" in exactly that
state. Loud beats a reassuring green.

Two different kinds of check live here, and the difference matters:

  ACCURACY  — the steering wheel. Its real envelope is on the TRW technical
              proposal (a part that shipped), so this is the one file where we
              can assert the reader is *right* rather than merely consistent.

  DRIFT     — the four IGES parts. We have no trustworthy independent
              measurement for these yet, so the numbers below are a snapshot of
              what the reader currently returns. They catch a regression in the
              reader; they do NOT confirm it is correct. Replace them with
              measured envelopes when the team supplies them (see TODO).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures" / "customer"

# TODO(team): real envelope dims from the customer drawings. Until then these
# are snapshots of our own output, not ground truth. Do not cite them as such.
DRIFT_BASELINE = {
    "Y2V_YK9_Housing.igs": (590, 133, 105),
    "Y2V_YK9_Rack.igs": (664, 110, 28),
    "Y2V_YK9_IBJ.igs": (339, 38, 37),
    "20230331_YXA_SB_26.00x5_CAD_FINAL_R1.igs": (1092, 298, 143),
}
DRIFT_TOL = 0.02

# From the TRW technical proposal — a real shipped part, not our own arithmetic.
WHEEL = "QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp"
WHEEL_REF, WHEEL_TOL = (370, 360, 135), 0.05

# Nothing we palletise is bigger than this. Catches metre/mm unit slips and
# stray geometry generically, without asserting a story about any one file.
MAX_ANY_AXIS = 2000

UNSUPPORTED = "26137849_001-radiator_asm-eng_c_asm.SLDASM"


def _extract(path: Path):
    """Returns sorted (L, B, H) in mm, L >= B >= H."""
    from app.geometry import extract_dimensions      # noqa: PLC0415
    return tuple(sorted(extract_dimensions(str(path)), reverse=True))


def _close(got, ref, tol):
    return all(abs(g - r) <= tol * r for g, r in zip(got, ref))


def _fmt(dims):
    return " x ".join(f"{d:.0f}" for d in dims)


def main() -> int:
    if not FIXTURES.exists() or not any(
        p for pat in ("*.ig*", "*.st*") for p in FIXTURES.glob(pat)
    ):
        print("SKIP: no customer fixtures present (NDA material, not in git)")
        return 0

    failures = []

    def check(ok, label, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")
        if not ok:
            failures.append(f"{label}{detail}")

    # --- ACCURACY: the one file with external ground truth -------------------
    wheel = FIXTURES / WHEEL
    if wheel.exists():
        try:
            got = _extract(wheel)
            check(_close(got, WHEEL_REF, WHEEL_TOL),
                  "steering wheel vs TRW proposal",
                  f": {_fmt(got)} (ref {WHEEL_REF}, +/-{WHEEL_TOL:.0%})")
        except Exception as e:
            check(False, "steering wheel", f": raised {type(e).__name__}: {e}")
    else:
        check(False, f"MISSING accuracy fixture {WHEEL}",
              " - this suite is drift-only without it, and drift baselines are "
              "snapshots of our own output, not ground truth")

    # --- DRIFT: reader must stay where it is on the IGES fixtures ------------
    for name, base in DRIFT_BASELINE.items():
        path = FIXTURES / name
        if not path.exists():
            # NOT a skip. The module docstring promises a partially populated
            # fixture dir fails, and an earlier version printed "all checks
            # passed" with every drift baseline unchecked -- which is exactly
            # what `continue` restored.
            check(False, f"MISSING drift fixture {name}",
                  " - the suite cannot report drift it never measured")
            continue
        try:
            got = _extract(path)
        except Exception as e:
            check(False, name, f": raised {type(e).__name__}: {e}")
            continue
        check(_close(got, base, DRIFT_TOL) and max(got) <= MAX_ANY_AXIS,
              f"{name:<45}", f" {_fmt(got):>18}  (baseline {_fmt(base)})")

    # --- SolidWorks has no open-source reader: fail loudly, say what to do ---
    # Both entry points, because they used to disagree. The gate lived only in
    # `extract_dimensions` while `extract_part` -- the one upload calls -- fed
    # the binary to cascadio and then to OCP and surfaced "OCP could not read
    # STEP file". `any(w in msg for w in ("step", ...))` matched that happily,
    # so this check passed while the real path gave useless advice. Require the
    # guidance itself: name the format, and name what to ask the customer for.
    from app.geometry import extract_part                  # noqa: PLC0415
    for entry in (_extract, extract_part):
        sldasm = FIXTURES / UNSUPPORTED
        if not sldasm.exists():
            continue
        try:
            entry(sldasm)
            check(False, f"{entry.__name__}: SLDASM accepted silently")
        except Exception as e:
            msg = str(e).lower()
            check("unsupported cad format" in msg and "export" in msg
                  and (".stp" in msg or ".step" in msg),
                  f"SLDASM rejected by {entry.__name__:<18}",
                  f": {type(e).__name__}: {str(e)[:60]}...")

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
