"""Geometry pipeline tests.

Run:  pytest tests/ -v
Or against one of your real part files:
      python tests/test_geometry.py path/to/part.stp
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.geometry import extract_part  # noqa: E402
from app.schemas import JobStatusOut  # noqa: E402

SAMPLE = Path(__file__).parent / "fixtures" / "sample.step"
SAMPLE_IGES = Path(__file__).parent / "fixtures" / "sample.igs"


def test_extraction_end_to_end(tmp_path):
    assert SAMPLE.exists(), "Place a sample STEP at tests/fixtures/sample.step"
    r = extract_part(SAMPLE, tmp_path / "out.glb")

    L, B, H = r.canonical_dims_lbh
    assert L >= B >= H > 0
    assert r.solid_count >= 1
    assert len(r.candidates) >= 2
    # First candidate is the most stable: largest footprint
    fps = [c.footprint_area for c in r.candidates]
    assert fps[0] == max(fps)
    # All candidate dim-sets are permutations of the canonical extents
    for c in r.candidates:
        assert sorted(c.dims_lbh, reverse=True) == sorted(
            r.canonical_dims_lbh, reverse=True
        )


def test_iges_extraction(tmp_path):
    """IGES goes through the OCP reader (no cascadio path) and must land in mm."""
    assert SAMPLE_IGES.exists(), "Place a sample IGES at tests/fixtures/sample.igs"
    r = extract_part(SAMPLE_IGES, tmp_path / "out.glb")

    L, B, H = r.canonical_dims_lbh
    assert L >= B >= H > 0
    # Known extents of this fixture (Creo, MM unit flag) — catches a unit
    # scaling regression, which is the failure mode that looks plausible.
    assert (round(L), round(B), round(H)) == (339, 38, 37)
    assert len(r.candidates) >= 2


def test_open_shell_is_a_fact_not_a_warning(tmp_path):
    """Ticket 3a. sample.step is an open surface model -- like all 16 real
    customer files. That must show up as `watertight: False`, never as a
    yellow warning, and the fact must survive pydantic on the way out (hard
    rule 9: an undeclared field is dropped silently, not an error)."""
    r = extract_part(SAMPLE, tmp_path / "out.glb")
    assert r.watertight is False
    assert r.solid_count >= 1
    noise = [w for w in r.warnings
             if "watertight" in w.lower() or "solids" in w.lower()]
    assert not noise, noise
    # The INCH warning this fixture does raise must survive the cull.
    assert any("INCH" in w for w in r.warnings), r.warnings

    payload = JobStatusOut(
        job_id="t", status="done",
        result={**dataclasses.asdict(r), "glb_url": "/api/files/out.glb"},
    ).model_dump()["result"]
    assert payload["watertight"] is False
    assert payload["solid_count"] == r.solid_count


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(SAMPLE)
    r = extract_part(path)
    print(f"dims (L,B,H): {r.canonical_dims_lbh} mm")
    print(f"solids: {r.solid_count}  watertight: {r.watertight}")
    for w in r.warnings:
        print(f"  warning: {w}")
    for c in r.candidates:
        print(f"  [{c.rank}] {c.label}: {c.dims_lbh} mm")
