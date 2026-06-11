"""
STEP file → dimensions extraction pipeline.

Pipeline:
  1. cascadio converts STEP → GLB (OpenCascade under the hood, pip-only install)
  2. trimesh loads GLB, unions all solids into one mesh
  3. Minimum-volume Oriented Bounding Box (OBB) on the convex hull
     → true L/B/H regardless of how the part was oriented in the file
  4. Generate candidate resting orientations (which OBB face is "down"),
     ranked by packing stability: largest footprint first, then lowest height.

The confirmed orientation matrix is stored on the Part Profile so Phase 3
(insert generation) never has to re-ask the user.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cascadio
import numpy as np
import trimesh

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass
class OrientationCandidate:
    """One way the part can rest on the container floor."""

    label: str                      # e.g. "Largest face down"
    rotation_matrix: list           # 4x4 row-major, applies to the GLB mesh
    dims_lbh: tuple                 # (L, B, H) mm in this orientation, L >= B
    footprint_area: float           # L * B in mm^2
    height: float                   # H in mm
    rank: int = 0


@dataclass
class ExtractionResult:
    glb_path: str
    units_assumed: str              # "mm" (cascadio normalizes; see notes)
    solid_count: int
    watertight: bool
    canonical_dims_lbh: tuple       # sorted extents of the min OBB, L >= B >= H
    obb_volume_mm3: float
    mesh_volume_mm3: float | None   # None if not watertight
    candidates: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# STEP header inspection (units sanity check)
# ---------------------------------------------------------------------------

_CONV_UNIT_RE = re.compile(
    rb"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", re.IGNORECASE
)
_SI_LEN_RE = re.compile(
    rb"SI_UNIT\s*\(\s*(\$|\.\w+\.)\s*,\s*\.METRE\.\s*\)", re.IGNORECASE
)


def detect_step_length_unit(step_path: str | Path) -> str:
    """Best-effort read of the declared length unit from the STEP data section.

    Returns one of: 'mm', 'm', 'inch', 'unknown'.
    OpenCascade normalizes geometry during conversion, so this is used only
    to sanity-check and warn — not to rescale.
    """
    try:
        data = Path(step_path).read_bytes()[:500_000]
    except OSError:
        return "unknown"
    # Conversion-based units (e.g. 'INCH', 'FOOT') take precedence — they are
    # the unit the model was authored in.
    conv = _CONV_UNIT_RE.search(data)
    if conv:
        name = conv.group(1).decode().upper()
        if "INCH" in name:
            return "inch"
        if "FOOT" in name or "FEET" in name:
            return "foot"
    si = _SI_LEN_RE.search(data)
    if si:
        prefix = si.group(1).decode().upper()
        if "MILLI" in prefix:
            return "mm"
        if prefix == "$":
            return "m"
    return "unknown"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def convert_step_to_glb(step_path: str | Path, glb_path: str | Path | None = None,
                        linear_deflection: float = 0.1,
                        angular_deflection: float = 0.5) -> Path:
    """STEP → GLB via cascadio. Deflection params control tessellation quality;
    defaults are fine for bounding-box work and viewer display."""
    step_path = Path(step_path)
    if glb_path is None:
        glb_path = Path(tempfile.mkstemp(suffix=".glb")[1])
    glb_path = Path(glb_path)
    cascadio.step_to_glb(
        str(step_path), str(glb_path),
        tol_linear=linear_deflection,
        tol_angular=angular_deflection,
    )
    if not glb_path.exists() or glb_path.stat().st_size == 0:
        raise ValueError("STEP conversion produced no geometry — file may be "
                         "corrupt or contain unsupported entities.")
    return glb_path


def load_unified_mesh(glb_path: str | Path) -> tuple:
    """Load GLB and union all solids into a single mesh, scaled to mm.

    glTF/GLB convention is meters; cascadio converts STEP (mm-normalized by
    OpenCascade) into meter-scale GLB, so we scale x1000 back to mm here.
    The viewer frontend must apply the same x1000 (or display the GLB as-is
    and only label dims in mm).

    Assemblies (multi-solid STEP) are treated as one packable unit; the solid
    count is surfaced so the UI can flag it to the user.
    """
    scene_or_mesh = trimesh.load(str(glb_path), force="scene")
    if isinstance(scene_or_mesh, trimesh.Scene):
        geometries = [
            g for g in scene_or_mesh.dump()  # applies node transforms
            if isinstance(g, trimesh.Trimesh) and len(g.faces) > 0
        ]
        solid_count = len(geometries)
        if solid_count == 0:
            raise ValueError("No triangulated geometry found in file.")
        mesh = trimesh.util.concatenate(geometries) if solid_count > 1 else geometries[0]
    else:
        mesh = scene_or_mesh
        solid_count = 1
    mesh = mesh.copy()
    mesh.apply_scale(1000.0)  # meters → mm
    return mesh, solid_count


def minimum_obb(mesh: trimesh.Trimesh) -> tuple:
    """Minimum-volume oriented bounding box via convex hull.

    Returns (to_obb_transform 4x4, extents ndarray(3,)).
    `to_obb_transform` maps mesh coordinates → OBB-aligned coordinates
    (OBB centered at origin, axes = XYZ).
    """
    hull = mesh.convex_hull
    to_obb, extents = trimesh.bounds.oriented_bounds(hull)
    return to_obb, np.asarray(extents, dtype=float)


# The six ways to rest an OBB on the floor: (axis that becomes Z/up, sign).
# After `to_obb`, the part is axis-aligned and centered at origin, so each
# resting pose = a rotation mapping one OBB axis to +Z, then a lift so the
# part sits on z=0.
_RESTING_POSES = [
    # (up_axis_index, flip)
    (2, False), (2, True),   # Z up / Z down
    (1, False), (1, True),   # Y up / Y down
    (0, False), (0, True),   # X up / X down
]


def _rotation_axis_to_z(axis_index: int, flip: bool) -> np.ndarray:
    """4x4 rotation that maps OBB axis `axis_index` (±) onto +Z."""
    target = np.zeros(3)
    target[axis_index] = -1.0 if flip else 1.0
    z = np.array([0.0, 0.0, 1.0])
    R = trimesh.geometry.align_vectors(target, z)
    return R


def generate_orientation_candidates(
    mesh: trimesh.Trimesh,
    to_obb: np.ndarray,
    extents: np.ndarray,
    max_candidates: int = 4,
) -> list:
    """Resting orientations ranked for packing: largest footprint first,
    tie-broken by lower center of gravity.

    Symmetric duplicates (same dims, ~same CG height) are merged so the user
    typically sees 3 distinct choices, not 6.
    """
    centroid_obb = trimesh.transform_points(
        mesh.centroid.reshape(1, 3), to_obb
    )[0]

    raw = []
    for axis, flip in _RESTING_POSES:
        R = _rotation_axis_to_z(axis, flip)
        # extents after rotation: the chosen axis becomes height
        e = extents.copy()
        h = e[axis]
        fp = np.delete(e, axis)               # footprint dims
        L, B = sorted(fp, reverse=True)
        # CG height above floor in this pose
        c = trimesh.transform_points(centroid_obb.reshape(1, 3), R)[0]
        cg_h = c[2] + h / 2.0
        # full transform: mesh -> obb -> rotated -> lifted onto floor
        lift = trimesh.transformations.translation_matrix([0, 0, h / 2.0])
        T = lift @ R @ to_obb
        raw.append({
            "dims": (round(float(L), 2), round(float(B), 2), round(float(h), 2)),
            "footprint": float(L * B),
            "height": float(h),
            "cg_h": float(cg_h),
            "T": T,
        })

    # Deduplicate: same (L,B,H) and CG height within tolerance → keep lower CG
    unique: list = []
    for cand in sorted(raw, key=lambda c: (-c["footprint"], c["cg_h"])):
        dup = any(
            u["dims"] == cand["dims"] and abs(u["cg_h"] - cand["cg_h"]) < 0.5
            for u in unique
        )
        if not dup:
            unique.append(cand)

    out = []
    for i, c in enumerate(unique[:max_candidates]):
        if i == 0:
            label = "Largest face down (most stable)"
        elif c["dims"] == unique[0]["dims"]:
            label = "Same footprint, flipped"
        elif c["height"] == min(u["height"] for u in unique):
            label = "Lowest profile"
        else:
            label = f"Alternative {i}"
        out.append(OrientationCandidate(
            label=label,
            rotation_matrix=np.asarray(c["T"]).tolist(),
            dims_lbh=c["dims"],
            footprint_area=round(c["footprint"], 2),
            height=round(c["height"], 2),
            rank=i,
        ))
    return out


def extract_part(step_path: str | Path, glb_out: str | Path | None = None) -> ExtractionResult:
    """Full Phase-1 extraction: STEP → GLB → min OBB → orientation candidates."""
    step_path = Path(step_path)
    warnings: list = []

    declared_unit = detect_step_length_unit(step_path)
    if declared_unit == "inch":
        warnings.append("STEP file declares INCH units — verify extracted "
                        "dimensions (values shown are converted to mm).")
    elif declared_unit == "unknown":
        warnings.append("Could not read length unit from STEP header — "
                        "assuming mm. Verify dimensions.")

    glb_path = convert_step_to_glb(step_path, glb_out)
    mesh, solid_count = load_unified_mesh(glb_path)

    if solid_count > 1:
        warnings.append(f"File contains {solid_count} solids — treated as one "
                        "assembly for dimensioning.")
    if not mesh.is_watertight:
        warnings.append("Geometry is not watertight (surface model or open "
                        "edges) — dimensions are from the convex hull and "
                        "should be reliable; volume is not.")

    to_obb, extents = minimum_obb(mesh)
    dims_sorted = tuple(round(float(v), 2) for v in sorted(extents, reverse=True))

    # Absurdity check
    if max(dims_sorted) > 5000 or max(dims_sorted) < 1:
        warnings.append(f"Extracted dimensions look unusual "
                        f"({dims_sorted} mm) — check units in source file.")

    candidates = generate_orientation_candidates(mesh, to_obb, extents)

    return ExtractionResult(
        glb_path=str(glb_path),
        units_assumed="mm",
        solid_count=solid_count,
        watertight=mesh.is_watertight,
        canonical_dims_lbh=dims_sorted,
        obb_volume_mm3=round(float(np.prod(extents)), 2),
        mesh_volume_mm3=round(float(mesh.volume), 2) if mesh.is_watertight else None,
        candidates=candidates,
        warnings=warnings,
    )
