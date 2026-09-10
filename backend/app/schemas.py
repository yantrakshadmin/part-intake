"""Pydantic schemas — the data contracts for Phase 1 (Part Intake)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class OrientationCandidateOut(BaseModel):
    label: str
    rotation_matrix: list[list[float]]  # 4x4, mesh-space -> resting pose
    dims_lbh: tuple[float, float, float]
    footprint_area: float
    height: float
    rank: int


class ExtractionResultOut(BaseModel):
    glb_url: str
    units_assumed: str
    solid_count: int
    watertight: bool
    canonical_dims_lbh: tuple[float, float, float]
    obb_volume_mm3: float
    mesh_volume_mm3: Optional[float]
    candidates: list[OrientationCandidateOut]
    warnings: list[str]


class JobStatusOut(BaseModel):
    job_id: str
    status: Literal["pending", "processing", "done", "failed"]
    error: Optional[str] = None
    result: Optional[ExtractionResultOut] = None


class PartProfileIn(BaseModel):
    """Manual entry, or STP-extracted values confirmed by the user."""

    part_number: str = Field(min_length=1, max_length=64)
    part_name: str = Field(min_length=1, max_length=128)
    length_mm: float = Field(gt=0, le=3000)
    breadth_mm: float = Field(gt=0, le=3000)
    height_mm: float = Field(gt=0, le=3000)
    weight_kg: float = Field(gt=0, le=500)
    source: Literal["manual", "stp"] = "manual"
    # STP-only fields
    job_id: Optional[str] = None
    confirmed_orientation: Optional[list[list[float]]] = None  # 4x4

    @field_validator("breadth_mm")
    @classmethod
    def _noop(cls, v):  # placeholder for future cross-field rules
        return v

    def canonical_dims(self) -> tuple[float, float, float]:
        """Always store L >= B >= H."""
        return tuple(sorted(
            (self.length_mm, self.breadth_mm, self.height_mm), reverse=True
        ))


class PackagingIn(BaseModel):
    """User-added custom box. Arrives as draft until the team verifies it."""

    item_code: str = Field(min_length=1, max_length=32)
    # Without this the model default made EVERY user-added row a container, so
    # adding a rack and flipping it to checked put it in front of the nesting
    # engine -- exactly what models.py says the column exists to prevent.
    kind: Literal["container", "pallet", "rack", "accessory"] = "container"
    inner_l_mm: float = Field(gt=0, le=3000)
    inner_b_mm: float = Field(gt=0, le=3000)
    inner_h_mm: float = Field(gt=0, le=3000)
    outer_l_mm: float = Field(gt=0, le=3000)
    outer_b_mm: float = Field(gt=0, le=3000)
    outer_h_mm: float = Field(gt=0, le=3000)
    max_weight_kg: float = Field(gt=0, le=2000)


class PackagingOut(PackagingIn):
    id: int
    status: str
    # Hard rule 9, fourth instance of the same shape. `models.Packaging`
    # carries both and C-TARE made `tare_kg` decide the per-truck ranking, but
    # neither was declared here, so pydantic dropped them silently and the
    # catalogue API reported no tare at all -- not blank, absent. Deliberately
    # on OUT and not IN: a user-added draft box has no tare on file, and the
    # seed is the only writer today.
    tare_kg: Optional[float] = None
    material: Optional[str] = None

    class Config:
        from_attributes = True


class VehicleOut(BaseModel):
    id: int
    name: str
    cargo_l_mm: float
    cargo_b_mm: float
    cargo_h_mm: float
    payload_kg: float

    class Config:
        from_attributes = True


class SolveIn(BaseModel):
    """POST /api/parts/{id}/solve body. All optional."""

    # Override only (C-TARE): default is each asset's own Packaging.tare_kg,
    # resolved per option in worker.run_solve, not a single value applied to
    # whichever asset wins. Cap raised 200 -> 400: the SCS report holds real
    # returnables up to 285kg (Metal Pallet 1850/2650) / 190kg (Metal Pallet +
    # Layer Frame) that 200 would reject outright.
    tare_kg: Optional[float] = Field(default=None, gt=0, le=400)
    vehicle: str = "32_ft_sxl"
    top_n: int = Field(default=2, ge=1, le=5)
    # Named boxes to rank instead of every status="checked" container. None
    # (or omitted) keeps today's unrestricted behaviour.
    # `min_length=1` matters: an EMPTY list used to validate, then fail
    # `if requested:` in the worker and fall through to the unrestricted
    # catalogue -- "rank none of them" silently became "rank all of them".
    # None still means unrestricted; [] is now a 422 that names the field.
    assets: Optional[list[Annotated[str, Field(min_length=1, max_length=32)]]] = \
        Field(default=None, min_length=1, max_length=20)


class SilhouetteOut(BaseModel):
    """One part's plan footprint in its resting pose, as row run-lengths.

    `runs` are [row, col_start, col_end] with INCLUSIVE ends, on a
    rows x cols grid of `cell_mm` squares. Exact at the voxel size the pitch
    was measured at -- the drawing and the number come from one expression.
    """

    cell_mm: float
    rows: int
    cols: int
    runs: list[tuple[int, int, int]]


class DunnageElementOut(BaseModel):
    """One insert BOM line. `dims_mm[i]` is null where we hold no basis."""

    name: str
    labels: list[str]                       # ("L","B","H") or ("d","L") for rod
    dims_mm: list[Optional[float]]
    size: Optional[str]                     # "1150x750x123", "750x?x66", null
    qty: Optional[int]                      # null = needs deck
    spec: Optional[str]                     # material / GSM / density
    basis: Literal["derived", "pattern", "unknown"]
    unknown: list[str]                      # labels + "qty"/"spec" needing a deck
    matrix: Optional[list[int]] = None      # pocket matrix, tray archetype
    cell_mm: Optional[list[float]] = None   # one pocket: L, B, depth
    net_height_mm: float = 0.0
    note: str = ""


class DunnageOut(BaseModel):
    """The generated insert BOM for one layout (PLANNING §7)."""

    archetype: Literal["bar_and_rod", "pocket_tray"]
    elements: list[DunnageElementOut]
    stack_height_mm: float
    build_height_mm: float
    inner_h_mm: float
    nest_depth_mm: float
    fits: bool
    caveat: str


class LayoutOut(BaseModel):
    asset_name: str
    pose_label: str
    count: int
    grid: tuple[int, int, int]
    extent_lbh: tuple[float, float, float]
    pitch_lbh: tuple[float, float, float]
    limited_by: str
    interleave: tuple[float, float, float]
    # Declared here or pydantic drops them silently and the interleaved insert
    # drawing is back to a bounding rectangle with a BOM the UI cannot show
    # (CLAUDE.md hard rule 9 -- this response model IS the contract).
    silhouette: Optional[SilhouetteOut] = None
    dunnage: Optional[DunnageOut] = None
    # The exploded insert drawing rendered at solve time (worker.run_solve).
    # Absent, not a broken link, when rendering failed for this layout — the
    # count is the valuable output and a render failure must never fail the
    # solve (CLAUDE.md hard rule 9: declare it here or pydantic drops it).
    drawing_url: Optional[str] = None


class BoxDesignOut(BaseModel):
    footprint: str
    inner: tuple[float, float, float]
    outer: tuple[float, float, float]
    count: int
    grid: tuple[int, int, int]
    layers: int
    # The insert drawing is built from extent + pitch. Omitting them here does
    # not error -- pydantic just drops them -- so the custom box arrived over
    # HTTP as a count with no geometry, and the only way to draw it would have
    # been the cuboid math this engine exists to replace.
    pose_label: str
    extent_lbh: tuple[float, float, float]
    pitch_lbh: tuple[float, float, float]
    silhouette: Optional[SilhouetteOut] = None
    dunnage: Optional[DunnageOut] = None
    drawing_url: Optional[str] = None


class TruckFitOut(BaseModel):
    vehicle: str
    boxes: int
    parts: int
    kg_per_box: float
    limited_by: str
    # "custom" for the synthesised design, else a Packaging.item_code.
    asset_name: str
    # The floor arrangement these numbers came from, so the load drawing does
    # not re-derive one that disagrees. Same failure as the omitted
    # extent/pitch above: pydantic drops what is not declared here.
    floor_grid: tuple[int, int]
    floor_rotated: bool
    # The tare actually used for THIS option (C-TARE, hard rule 9): per-asset
    # now, not the flat request value -- omitting it here would make the
    # frontend re-derive it out of kg_per_box, and pydantic would drop it
    # silently if it were left off this model.
    tare_kg: float


class SolveResultOut(BaseModel):
    catalogue: list[LayoutOut]
    custom: Optional[BoxDesignOut]
    custom_beats_catalogue: bool
    best_count: int
    truck: Optional[TruckFitOut]
    warnings: list[str]


class SolveJobStatusOut(BaseModel):
    solve_job_id: str
    status: Literal["pending", "processing", "done", "failed"]
    error: Optional[str] = None
    result: Optional[SolveResultOut] = None


class PartProfileOut(BaseModel):
    id: int
    part_number: str
    part_name: str
    length_mm: float
    breadth_mm: float
    height_mm: float
    weight_kg: float
    source: str
    glb_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
