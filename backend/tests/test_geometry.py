"""Geometry pipeline tests.

Run:  pytest tests/ -v
Or against one of your real part files:
      python tests/test_geometry.py path/to/part.stp
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import trimesh  # noqa: E402

from app.geometry import (  # noqa: E402
    extract_part,
    generate_orientation_candidates,
    _keep_every_footprint,
    minimum_obb,
    support_polygon,
)
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
    # Candidates are ranked by contact stability, not by footprint (R6).
    ratios = [c.support_area_ratio for c in r.candidates]
    assert ratios[0] == max(ratios)
    assert all(0.0 <= x <= 1.0 for x in ratios)
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


def _poses(mesh):
    to_obb, extents = minimum_obb(mesh)
    return generate_orientation_candidates(mesh, to_obb, extents)


def test_cone_on_its_tip_has_no_support():
    """R6, the metric itself. A cone base-down rests on its whole base; the same
    cone tip-down touches at a point. Explicit transforms, no OBB in the way:
    the min-volume OBB of a cone is tilted 20 degrees off the cone axis (real,
    not tessellation -- it is 3.5% smaller than the axis-aligned box), so
    "flat on the base" is not one of the six OBB-face poses at all."""
    cone = trimesh.creation.cone(radius=50, height=200)     # base at z=0
    flip = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
    flip[2, 3] = 200.0                                      # tip back onto z=0
    footprint = 100.0 * 100.0

    base_area, base_poly = support_polygon(cone, np.eye(4), 100.0)
    tip_area, tip_poly = support_polygon(cone, flip, 100.0)

    assert tip_area / footprint < 0.05, tip_area
    # A round base can never exceed pi/4 = 0.785 of its bounding rectangle, so
    # the ticket's ">= 0.9 base down" is unreachable for any cone by geometry.
    assert 0.75 <= base_area / footprint <= 0.79, base_area
    assert base_area > 100 * tip_area
    assert 3 <= len(base_poly) <= 32 and len(tip_poly) <= 32


def test_tapered_part_ranks_flat_end_over_largest_footprint():
    """R5/R6, Rahul's axle casing: a part with one end narrower than the other.
    The old ranking put the largest OBB footprint first and called it "most
    stable" -- for this shape that is the part lying on its side, on a line
    contact, and it rocks. Stability ranking puts the wide end down first."""
    frustum = trimesh.creation.revolve([[50.0, 0.0], [20.0, 200.0]], sections=48)
    cands = _poses(frustum)

    on_its_side = max(cands, key=lambda c: c.footprint_area)
    assert on_its_side.support_area_ratio < 0.05, on_its_side
    assert on_its_side.rank != 0, "largest footprint must not win on stability"

    wide_end_down = cands[0]
    assert wide_end_down.support_area_ratio >= 0.75, wide_end_down
    assert wide_end_down.cg_height_mm < 200 / 2         # mass is in the wide end
    # Same box, narrow end down: kept (the face that is down changes stability),
    # ranked below, and honestly labelled.
    narrow_end_down = next(c for c in cands[1:]
                           if c.dims_lbh == wide_end_down.dims_lbh)
    assert narrow_end_down.support_area_ratio < 0.25
    assert narrow_end_down.cg_height_mm > wide_end_down.cg_height_mm
    assert narrow_end_down.tip_ratio > 1.0              # taller than it is wide
    assert "(other end down)" in narrow_end_down.label, narrow_end_down.label
    # C1: no verdict word in the label at all -- the ratio ships as a number
    # and the frontend tags it. On seven real files rank-0 scored 0.07-0.57,
    # so "stable" never fired and "narrow contact" fired on the RECOMMENDED
    # pose of three of them: an always-on warning, which is what R2 removed.
    assert not any(w in c.label.lower() for c in cands
                   for w in ("stable", "narrow contact")), [c.label for c in cands]


def test_truncation_never_drops_a_footprint():
    """C4. `max_candidates` is a UI number but the nester searches what it
    leaves, so the cap must keep one pose per footprint, not the ranked prefix.
    Six poses, three footprints, cap 3: the naive prefix keeps A, A, B and
    loses C entirely."""
    ranked = [{"dims": d, "n": n} for n, d in enumerate([
        (300.0, 200.0, 20.0),      # A, best ranked
        (300.0, 200.0, 20.0),      # A flipped -- same box, other face down
        (300.0, 20.0, 200.0),      # B
        (300.0, 20.0, 200.0),      # B flipped
        (200.0, 20.0, 300.0),      # C, worst ranked
        (200.0, 20.0, 300.0),      # C flipped
    ])]
    kept = _keep_every_footprint(ranked, 3)

    assert [c["n"] for c in kept] == [0, 2, 4], [c["n"] for c in kept]
    assert len({c["dims"] for c in kept}) == 3
    naive = {c["dims"] for c in ranked[:3]}
    assert len(naive) == 2, "fixture is not exercising the bug"
    # Rank order survives the reshuffle: element 0 is still the best ranked.
    assert kept[0]["n"] == 0
    # Cap above the count is a no-op.
    assert _keep_every_footprint(ranked, 99) == ranked


def test_thin_plate_candidates_are_flat_and_distinct():
    """R5/R6. A plate rests flat every way up; no two cards may show the same
    box (the axle casing showed three near-identical ones)."""
    cands = _poses(trimesh.creation.box(extents=(300, 200, 20)))

    assert all(c.support_area_ratio >= 0.9 for c in cands), \
        [(c.label, c.support_area_ratio) for c in cands]
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            assert not all(abs(x - y) <= 0.01 * max(x, y)
                           for x, y in zip(a.dims_lbh, b.dims_lbh)), \
                f"duplicate pose {a.label} / {b.label}: {a.dims_lbh}"
    assert all("most stable" not in c.label for c in cands)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(SAMPLE)
    r = extract_part(path)
    print(f"dims (L,B,H): {r.canonical_dims_lbh} mm")
    print(f"solids: {r.solid_count}  watertight: {r.watertight}  "
          f"rank_basis: {r.rank_basis}")
    for w in r.warnings:
        print(f"  warning: {w}")
    for c in r.candidates:
        print(f"  [{c.rank}] {c.label}: {c.dims_lbh} mm  "
              f"support={c.support_area_ratio:.3f}  cg={c.cg_height_mm:.1f}mm  "
              f"tip={c.tip_ratio:.2f}  hull_pts={len(c.support_polygon_mm)}")
