"""
STEP / IGES file → dimensions extraction pipeline.

Pipeline:
  1. cascadio converts STEP → GLB (OpenCascade under the hood, pip-only install).
     IGES has no cascadio path, so it goes straight to the OCP reader.
  2. trimesh loads GLB, unions all solids into one mesh
  3. Minimum-volume Oriented Bounding Box (OBB) on the convex hull
     → true L/B/H regardless of how the part was oriented in the file
  4. Generate candidate resting orientations (which OBB face is "down"),
     ranked by real contact stability: the support polygon (convex hull of the
     vertices touching the floor) as a fraction of the OBB footprint, then
     lowest centre of gravity.

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
from scipy.spatial import ConvexHull, QhullError

logger = logging.getLogger(__name__)

STEP_SUFFIXES = (".stp", ".step")
IGES_SUFFIXES = (".igs", ".iges")
SUPPORTED_SUFFIXES = STEP_SUFFIXES + IGES_SUFFIXES

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
    # R6 stability. None, not 0.0: a candidate dict stored by an older worker
    # (worker.py:279, 609) has no stability in it, and 0.0 there would read as
    # "measured, no contact" instead of "not measured".
    support_area_ratio: float | None = None   # contact patch / footprint, 0..1
    # Area-weighted centroid of the SURFACE above the floor -- these are open
    # surface models, so this is not a mass centre (hard rule 4).
    cg_height_mm: float | None = None
    tip_ratio: float | None = None            # cg_height_mm / min(L, B)
    support_polygon_mm: list | None = None    # <=32 hull points


@dataclass
class ExtractionResult:
    glb_path: str
    units_assumed: str              # unit of the values below, always "mm";
                                    # describes our output, not the source file
    solid_count: int
    watertight: bool
    canonical_dims_lbh: tuple       # sorted extents of the min OBB, L >= B >= H
    obb_volume_mm3: float
    mesh_volume_mm3: float | None   # None if not watertight
    candidates: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    rank_basis: str | None = None   # what candidates[0] is first BY


# ---------------------------------------------------------------------------
# STEP header inspection (units sanity check)
# ---------------------------------------------------------------------------

_CONV_UNIT_RE = re.compile(
    rb"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", re.IGNORECASE
)
_SI_LEN_RE = re.compile(
    rb"SI_UNIT\s*\(\s*(\$|\.\w+\.)\s*,\s*\.METRE\.\s*\)", re.IGNORECASE
)


# Readable names for what `detect_step_length_unit` returns. Bare `.upper()`
# renders 'm' as "declares M units", which reads like a typo.
_UNIT_LABEL = {"m": "METRE", "foot": "FOOT", "inch": "INCH"}


def detect_step_length_unit(step_path: str | Path) -> str:
    """Best-effort read of the declared length unit from the STEP data section.

    Returns one of: 'mm', 'm', 'inch', 'foot', 'unknown'.
    OpenCascade normalizes geometry during conversion, so this is used only
    to sanity-check and warn — not to rescale.
    """
    try:
        # SolidWorks writes unit entities late in large files (seen at ~2 MB
        # in a 14 MB export), so scan the whole file — capped for safety.
        data = Path(step_path).read_bytes()[:64_000_000]
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

    if step_path.suffix.lower() in IGES_SUFFIXES:
        from .step_fallback import iges_to_glb
        iges_to_glb(step_path, glb_path)
        if not _glb_has_geometry(glb_path):
            raise ValueError("IGES conversion produced no geometry — file may "
                             "be corrupt or contain unsupported entities.")
        return glb_path

    cascadio.step_to_glb(
        str(step_path), str(glb_path),
        tol_linear=linear_deflection,
        tol_angular=angular_deflection,
    )
    if _glb_has_geometry(glb_path):
        return glb_path

    # cascadio writes a header-only GLB when OpenCascade's healing throws on
    # degenerate geometry (e.g. SolidWorks exports with U1 == U2 trimmed
    # curves). Retry with the OCP-based reader that skips healing.
    logger.warning("cascadio produced an empty GLB for %s — retrying with "
                   "OCP fallback reader", step_path.name)
    from .step_fallback import step_to_glb_fallback
    step_to_glb_fallback(step_path, glb_path)
    if not _glb_has_geometry(glb_path):
        raise ValueError("STEP conversion produced no geometry — file may be "
                         "corrupt or contain unsupported entities.")
    return glb_path


def _glb_has_geometry(glb_path: Path) -> bool:
    """True if the GLB contains at least one triangle."""
    if not glb_path.exists() or glb_path.stat().st_size == 0:
        return False
    try:
        scene = trimesh.load(str(glb_path), file_type="glb")
    except Exception:
        return False
    if isinstance(scene, trimesh.Scene):
        return any(len(g.faces) > 0 for g in scene.geometry.values())
    return len(getattr(scene, "faces", ())) > 0


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


_MAX_SUPPORT_POINTS = 32


def support_polygon(mesh: trimesh.Trimesh, transform: np.ndarray,
                    footprint_min_mm: float) -> tuple:
    """Contact patch of the part in this pose: 2D convex hull of the vertices
    sitting in a thin band above the lowest z. Returns (area_mm2, points).

    Band = 2 mm, or 0.5% of the SHORT FOOTPRINT SIDE if that is larger. It
    scales with the face resting on the floor, not with the pose height: the
    height is the one dimension the contact patch has nothing to do with, and
    scaling by it made the same part read differently on its side than on its
    end. Vertices, not faces -- these are open surface models (hard rule 4)
    and a face-based contact test needs a closed solid to mean anything.

    # ponytail: on a CURVED contact this measures the chord the band cuts, so
    # a cylinder reads width sqrt(2 * tol * R) -- a thin rod scores higher than
    # a fat one (0.38 vs 0.12) though both touch on a line. Tessellation
    # artefact, not stability. Upgrade path when a curved part is ranked
    # wrongly because of it: classify the contact (face / line / point) from
    # the band's aspect ratio and score a line contact as zero area.
    """
    v = trimesh.transform_points(mesh.vertices, transform)
    z = v[:, 2]
    tol = max(2.0, 0.005 * float(footprint_min_mm))
    band = v[z <= z.min() + tol][:, :2]
    if len(band) < 3:
        return 0.0, np.round(band, 2).tolist()
    try:
        hull = ConvexHull(band)       # 2D: .volume is the area, .area the perimeter
    except QhullError:
        # Collinear contact -- a cone or a cylinder on its side. Zero area is
        # the right answer; it rocks.
        return 0.0, np.round(band[:_MAX_SUPPORT_POINTS], 2).tolist()
    pts = band[hull.vertices]
    if len(pts) > _MAX_SUPPORT_POINTS:
        # ponytail: even stride around the hull, not Douglas-Peucker. This is
        # a drawing aid; the area above is the number anything decides on.
        pts = pts[np.linspace(0, len(pts) - 1, _MAX_SUPPORT_POINTS).astype(int)]
    return float(hull.volume), np.round(pts, 2).tolist()


def dims_match(a, b, tol: float = 0.01) -> bool:
    """Same box within `tol` on every side (default 1%)."""
    return all(abs(x - y) <= tol * max(x, y, 1e-9) for x, y in zip(a, b))


def _same_resting_pose(a: dict, b: dict) -> bool:
    """True when two candidates are one resting pose rotated in plane: the same
    box within 1% on every side AND the same contact patch within 0.05.

    The ratio half of the test is what keeps the two signs of an axis apart
    when the face that is down matters -- a cone tip-down and base-down share
    an OBB exactly and are not the same pose.
    """
    return (dims_match(a["dims"], b["dims"])
            and abs(a["ratio"] - b["ratio"]) <= 0.05)


def _keep_every_footprint(ranked: list, max_candidates: int) -> list:
    """Truncate `ranked` to `max_candidates` without dropping a footprint.

    `max_candidates` is a UI number, but the nester searches whatever it leaves
    (tests/test_pose_search.py), and R6 put flip twins back in the list -- so a
    naive `ranked[:max_candidates]` can spend every slot on two boxes and cut a
    real footprint before the nester ever sees it. Best-ranked pose of each
    footprint first, then fill by rank; rank order is restored at the end so
    element 0 is still the most stable.

    `ranked` is the internal dict form used by `generate_orientation_candidates`
    ("dims" = (L, B, H)); it is returned, not copied.
    """
    picked, seen = [], set()
    for c in ranked:
        fp = (round(c["dims"][0]), round(c["dims"][1]))
        if fp not in seen:
            seen.add(fp)
            picked.append(c)
    picked += [c for c in ranked if id(c) not in {id(x) for x in picked}]
    keep = {id(c) for c in picked[:max_candidates]}
    return [c for c in ranked if id(c) in keep]


def generate_orientation_candidates(
    mesh: trimesh.Trimesh,
    to_obb: np.ndarray,
    extents: np.ndarray,
    max_candidates: int = 4,
) -> list:
    """Resting orientations ranked by real stability: support-polygon area as a
    fraction of the OBB footprint, tie-broken by lower centre of gravity.

    All six OBB faces are tried, not three. The two signs of an axis have the
    same footprint and height but not the same contact patch -- that is the
    whole of R6: a conical axle casing tip-down and base-down are the same box
    and different parts. Where the sign genuinely changes nothing (a plate,
    a symmetric bar) `_same_resting_pose` collapses the twin, which is the old
    per-axis dedup expressed as a measurement instead of an assumption.

    On the RASTER two collapsed twins can still differ by up to one voxel per
    axis, because `nesting.occupancy` re-voxelises per transform and the grid
    origin snaps independently -- measured on the YXA bar at 4mm, 60 vs 55 over
    the catalogue. That is the quantisation band `nesting.Layout.count_upper`
    reports, not geometry.
    """
    raw = []
    for axis, flip in _RESTING_POSES:
        R = _rotation_axis_to_z(axis, flip)
        h = float(extents[axis])              # the chosen axis becomes height
        L, B = (float(x) for x in sorted(np.delete(extents, axis), reverse=True))
        # full transform: mesh -> obb -> rotated -> lifted onto floor
        lift = trimesh.transformations.translation_matrix([0, 0, h / 2.0])
        T = lift @ R @ to_obb
        cg_h = float(trimesh.transform_points(
            mesh.centroid.reshape(1, 3), T)[0][2])
        area, poly = support_polygon(mesh, T, B)
        footprint = L * B
        raw.append({
            "dims": (round(L, 2), round(B, 2), round(h, 2)),
            "footprint": footprint,
            "height": h,
            "cg_h": cg_h,
            "ratio": min(1.0, area / footprint) if footprint else 0.0,
            "poly": poly,
            "T": T,
        })

    # Most stable first, then lowest CG. Near-duplicates drop out: the frontend
    # must never show two cards that look the same (R5, axle casing).
    unique: list = []
    for cand in sorted(raw, key=lambda c: (-c["ratio"], c["cg_h"])):
        if not any(_same_resting_pose(cand, u) for u in unique):
            unique.append(cand)
    unique = _keep_every_footprint(unique, max_candidates)

    max_fp = max(c["footprint"] for c in unique)
    out, alt, used, named = [], 0, set(), []
    for i, c in enumerate(unique):
        # Footprint description first -- it says which way up the part is.
        # Ranking no longer implies it, so it is assigned independently.
        twin = next((n for n in named if dims_match(c["dims"], n[0])), None)
        if twin is not None:
            # Same box, the other face on the floor -- kept because the
            # stability differs. Say so, or the card looks like a duplicate.
            label = f"{twin[1]} (other end down)"
        elif c["footprint"] == max_fp and "big" not in used:
            used.add("big")
            label = "Largest face down"
        elif c["height"] > c["dims"][0]:
            label = "Standing upright"
        else:
            alt += 1
            label = f"Alternative {alt}"
        named.append((c["dims"], label))
        out.append(OrientationCandidate(
            label=label,
            rotation_matrix=np.asarray(c["T"]).tolist(),
            dims_lbh=c["dims"],
            footprint_area=round(c["footprint"], 2),
            height=round(c["height"], 2),
            rank=i,
            support_area_ratio=round(c["ratio"], 4),
            cg_height_mm=round(c["cg_h"], 2),
            tip_ratio=round(c["cg_h"] / c["dims"][1], 3) if c["dims"][1] else 0.0,
            support_polygon_mm=c["poly"],
        ))
    return out


def extract_part(step_path: str | Path, glb_out: str | Path | None = None) -> ExtractionResult:
    """Full Phase-1 extraction: STEP → GLB → min OBB → orientation candidates."""
    step_path = Path(step_path)
    warnings: list = []

    # The gate lives HERE, not only in `extract_dimensions`. Upload goes
    # through this function, so a .SLDPRT used to be handed straight to
    # cascadio, fail, retry the OCP fallback, fail again, and surface as
    # "OCP could not read STEP file" -- which reads as our bug, not as
    # "this format has no reader, ask for a STEP export". Hard rule 5.
    suffix = step_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported CAD format '{suffix or step_path.name}': no "
            "open-source reader exists for it. Request a STEP (.stp/.step) "
            "or IGES (.igs/.iges) export from the customer."
        )

    # The unit is read from the file in both formats -- IGES used to be
    # hardcoded to "mm", which was true of every fixture we had and would have
    # silenced an inch-authored IGES. The geometry is mm either way (OCC
    # converts on read, see step_fallback.iges_to_glb); this is reporting only.
    if suffix in IGES_SUFFIXES:
        from .step_fallback import iges_declared_unit
        declared_unit = iges_declared_unit(step_path)
        if declared_unit != "mm":
            warnings.append(f"IGES file declares {declared_unit.upper()} units "
                            "— OpenCascade converted the geometry on read, so "
                            "the values shown are already mm. Verify them.")
    else:
        declared_unit = detect_step_length_unit(step_path)
        # Any non-mm unit, not just INCH. `detect_step_length_unit` has always
        # been able to return "m" and "foot", and neither warned -- the same
        # silence G-UNIT just removed from the IGES path, and a metre-declared
        # file is a 1000x risk rather than 25.4x. Matches the IGES branch's
        # `!= "mm"` above so the two formats cannot drift apart again.
        if declared_unit not in ("mm", "unknown"):
            label = _UNIT_LABEL.get(declared_unit, declared_unit.upper())
            warnings.append(f"STEP file declares {label} units — verify "
                            "extracted dimensions (values shown are "
                            "converted to mm).")
        elif declared_unit == "unknown":
            warnings.append("Could not read length unit from STEP header — "
                            "assuming mm. Verify dimensions.")

    glb_path = convert_step_to_glb(step_path, glb_out)
    mesh, solid_count = load_unified_mesh(glb_path)

    # ponytail: no warning for solid_count > 1 or for an open shell. Both are
    # facts on the result below (`solid_count`, `watertight`), not problems: a
    # sweep of 16 real customer files fired not-watertight on 16/16 and
    # multi-solid on 9/16. A warning that always fires carries no information.
    # `mesh_volume_mm3` is already None when not watertight, which is the only
    # consequence the caller has to act on (hard rule 4).

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
        rank_basis="support_area",
    )


def extract_dimensions(path: str) -> tuple[float, float, float]:
    """Part dimensions in mm from a STEP or IGES file, sorted L >= B >= H.

    Min-volume OBB over the tessellated faces of the model. Free curves,
    datum points and other non-surface entities are never part of the answer:
    only faces are meshed. Never returns volume or mass — these files are open
    surface models and both are meaningless for them.
    """
    # Format gate is in `extract_part`, which this delegates to.
    # Dimensions-only callers never want the GLB, and `extract_part` otherwise
    # leaves one behind per call via `tempfile.mkstemp`. Give it a directory
    # that cleans itself up.
    with tempfile.TemporaryDirectory() as tmp:
        glb_out = Path(tmp) / "dims.glb"
        return extract_part(path, glb_out=glb_out).canonical_dims_lbh
