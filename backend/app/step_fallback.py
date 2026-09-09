"""
Fallback STEP → GLB converter using OCP (full OpenCascade bindings).
Also the *only* IGES → GLB path (cascadio is STEP-only).

cascadio runs OpenCascade's ShapeProcess healing (FixShape) on import and,
when a file contains degenerate geometry (e.g. a trimmed curve with U1 == U2,
seen in SolidWorks exports), the exception aborts the whole transfer and the
GLB comes out header-only. This module reads the same file with healing
skipped, meshes face-by-face so one bad face cannot abort the rest, and
writes a GLB compatible with the rest of the pipeline.

Output contract matches cascadio: GLB is in metres (glTF spec), so
`load_unified_mesh` can apply its usual x1000 scaling to get mm.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import trimesh

logger = logging.getLogger(__name__)

# Per-face meshing attempts, in order: deflection in mm, and whether to run
# ShapeFix_Face first. Raw fine pass first; healing only for the stragglers.
_MESH_ATTEMPTS = (
    (0.1, False),
    (0.5, False),
    (0.5, True),
    (2.0, True),
)


def step_to_glb_fallback(step_path: str | Path, glb_path: str | Path) -> Path:
    """Convert STEP → GLB, tolerating degenerate faces. Raises ValueError if
    no usable geometry can be extracted at all."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Reader

    step_path = Path(step_path)
    glb_path = Path(glb_path)

    # Skip the ShapeProcess healing sequence — FixShape is what throws.
    Interface_Static.SetCVal_s("read.step.sequence", "")

    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
        raise ValueError(f"OCP could not read STEP file: {step_path.name}")

    for i in range(1, reader.NbRootsForTransfer() + 1):
        try:
            reader.TransferRoot(i)
        except Exception:
            logger.warning("STEP root %d failed to transfer, skipping", i)
    if reader.NbShapes() == 0:
        raise ValueError("No shapes could be transferred from STEP file.")

    return _shape_to_glb(reader.OneShape(), glb_path)


def iges_to_glb(iges_path: str | Path, glb_path: str | Path) -> Path:
    """Convert IGES → GLB. OpenCascade honours the global-section unit flag and
    normalises to mm, so the output contract matches the STEP path."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader

    iges_path = Path(iges_path)
    reader = IGESControl_Reader()
    if reader.ReadFile(str(iges_path)) != IFSelect_RetDone:
        raise ValueError(f"OCP could not read IGES file: {iges_path.name}")
    reader.TransferRoots()
    if reader.NbShapes() == 0:
        raise ValueError("No shapes could be transferred from IGES file.")
    return _shape_to_glb(reader.OneShape(), Path(glb_path))


def _shape_to_glb(shape, glb_path: Path) -> Path:
    """Mesh a TopoDS shape face-by-face and write a metres-scaled GLB.
    Face-at-a-time so one degenerate face cannot abort the rest."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    meshes: list = []
    n_faces = n_skipped = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        n_faces += 1
        tri = None
        loc = TopLoc_Location()
        for deflection, heal in _MESH_ATTEMPTS:
            target = face
            if heal:
                try:
                    fixer = ShapeFix_Face(face)
                    fixer.Perform()
                    target = fixer.Face()
                except Exception:
                    continue
            try:
                BRepMesh_IncrementalMesh(target, deflection, False, 0.5, False)
            except Exception:
                continue
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(target, loc)
            if tri is not None:
                break
        if tri is None:
            n_skipped += 1
            explorer.Next()
            continue

        trsf = loc.Transformation()
        verts = np.empty((tri.NbNodes(), 3))
        for k in range(1, tri.NbNodes() + 1):
            p = tri.Node(k).Transformed(trsf)
            verts[k - 1] = (p.X(), p.Y(), p.Z())
        faces = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for k in range(1, tri.NbTriangles() + 1):
            t = tri.Triangle(k)
            faces[k - 1] = (t.Value(1) - 1, t.Value(2) - 1, t.Value(3) - 1)
        meshes.append(trimesh.Trimesh(vertices=verts, faces=faces, process=False))
        explorer.Next()

    if not meshes:
        raise ValueError("Reader produced no triangulated faces.")
    if n_skipped:
        logger.warning("Fallback meshing skipped %d of %d faces (degenerate "
                       "geometry); bounding dims may be slightly off",
                       n_skipped, n_faces)

    mesh = trimesh.util.concatenate(meshes)
    mesh.apply_scale(0.001)  # OCC gives mm, GLB contract is metres
    mesh.export(str(glb_path))
    logger.info("OCP conversion ok: %d/%d faces meshed → %s",
                n_faces - n_skipped, n_faces, glb_path)
    return glb_path
