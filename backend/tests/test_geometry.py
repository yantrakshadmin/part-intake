"""Geometry pipeline tests.

Run:  pytest tests/ -v
Or against one of your real part files:
      python tests/test_geometry.py path/to/part.stp
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.geometry import extract_part  # noqa: E402

SAMPLE = Path(__file__).parent / "fixtures" / "sample.step"


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


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(SAMPLE)
    r = extract_part(path)
    print(f"dims (L,B,H): {r.canonical_dims_lbh} mm")
    print(f"solids: {r.solid_count}  watertight: {r.watertight}")
    for w in r.warnings:
        print(f"  warning: {w}")
    for c in r.candidates:
        print(f"  [{c.rank}] {c.label}: {c.dims_lbh} mm")
