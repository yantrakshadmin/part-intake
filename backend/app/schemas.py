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
    # R6. None, not 0.0: an extraction job stored before this shipped carries
    # no stability, and 0.0 would read as "measured, no contact" rather than
    # "not measured". Defaulted at all because a missing required field is a
    # 500 on those old jobs (hard rule 9 -- pydantic is silent either way).
    support_area_ratio: Optional[float] = None   # contact patch / footprint
    # Area-weighted SURFACE centroid height, not a mass centre: these are open
    # surface models and mass is meaningless for them (hard rule 4).
    cg_height_mm: Optional[float] = None
    tip_ratio: Optional[float] = None            # cg height / footprint min dim
    support_polygon_mm: Optional[list[list[float]]] = None   # <=32 points, mm


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
    rank_basis: Optional[str] = None     # what candidates[0] is first BY


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
    # F1: which Project this part belongs to. None keeps today's standalone
    # part-profile behaviour.
    project_id: Optional[int] = None

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
    # Search only the resting pose confirmed in the viewer. Default searches
    # every pose and warns when the winner is a different one.
    confirmed_pose_only: bool = False
    # In-plane air between neighbouring parts, mm (`nesting.DEFAULT_CLEARANCE_MM`
    # when omitted). A parameter, not a constant: the Tata decks pack their
    # dense axis at 0.3mm (X104) and 10.3mm (P118) -- no single value is right
    # for both, and DOMAIN.md does not exist. Stacking clearance stays 0 (a
    # layer rests on the layer below; see `nesting.DEFAULT_STACK_CLEARANCE_MM`).
    clearance_mm: Optional[float] = Field(default=None, ge=0, le=50)


class TruckFitIn(BaseModel):
    """POST /api/truck-fit body: the standalone load calculator's question."""

    outer_l_mm: float = Field(gt=0, le=20000)
    outer_b_mm: float = Field(gt=0, le=20000)
    outer_h_mm: float = Field(gt=0, le=20000)
    kg_per_box: float = Field(default=0.0, ge=0, le=50000)
    vehicle: str
    max_stack: int = Field(default=0, ge=0, le=50)


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
    # Inner minus (lattice span + dunnage beside it) per axis. Negative on L
    # or B means the side separators do not fit beside the parts; the worker
    # turns that into a warning. `fits` is the height budget only.
    slack_lbh: tuple[float, float, float]
    fits: bool
    caveat: str


class SequenceCuboidOut(BaseModel):
    """One dunnage cuboid placed at a build step, mm, box inner corner at
    (0,0,0)."""
    name: str
    colour: str
    alpha: float
    origin: tuple[float, float, float]
    size: tuple[float, float, float]


class SequenceStepOut(BaseModel):
    """One step of the packing order. `i`, the order and the three caption
    strings are the packing GIF's own (`insert_drawing._captions`), off the
    same `_place` call -- the animation and the GIF cannot disagree."""
    i: int
    kind: Literal["dunnage", "parts"]
    title: str
    text: str
    meta: str
    cuboids: list[SequenceCuboidOut] = []
    # Min corner of each posed part's AABB, mm.
    parts: list[dict] = []


class BuildSequenceOut(BaseModel):
    """F4: the packing order as data, for the 3D animation. `pose_matrix` is
    the layout's candidate rotation (4x4 row-major, mesh mm -> resting pose),
    the same one the drawing voxelised with."""
    inner: tuple[float, float, float]
    pose_matrix: Optional[list[list[float]]] = None
    part_extent: tuple[float, float, float]
    steps: list[SequenceStepOut] = []


class LayoutOut(BaseModel):
    asset_name: str
    pose_label: str
    count: int
    grid: tuple[int, int, int]
    extent_lbh: tuple[float, float, float]
    pitch_lbh: tuple[float, float, float]
    limited_by: str
    interleave: tuple[float, float, float]
    # Ceiling of the raster's quantisation band: `lattice_count` one voxel
    # tighter on every extent and pitch. `count` is the floor. Equal when the
    # difference is nothing.
    count_upper: int
    # F3/F4: the cuboid baseline for this SAME asset, and the "why this
    # design" sentences (`app.reasons.reasons_for`). Declared here or
    # pydantic drops them silently (hard rule 9) -- 0 / [] on older stored
    # results that predate these fields.
    cuboid_count: int = 0
    reasons: list[str] = []
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
    # The packing-sequence GIF, same file/failure discipline as drawing_url.
    gif_url: Optional[str] = None
    # The complete, fully packed box -- the GIF's own final frame, so it can
    # never disagree with the animation. The first image the engineer should
    # see after a solve; null wherever no GIF was built (same discipline as
    # gif_url -- declare it here or pydantic drops it, hard rule 9).
    packed_url: Optional[str] = None
    # The same build the GIF animates, as data for the 3D animation. Present
    # wherever a GIF was built; same failure discipline (a sequence failure
    # never fails the solve) and the same declare-it-or-pydantic-drops-it
    # rule as gif_url above.
    sequence: Optional[BuildSequenceOut] = None


class BoxDesignOut(BaseModel):
    footprint: str
    inner: tuple[float, float, float]
    outer: tuple[float, float, float]
    count: int
    count_upper: int      # quantisation ceiling, as LayoutOut.count_upper
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
    gif_url: Optional[str] = None
    packed_url: Optional[str] = None
    # As LayoutOut.sequence -- declared or pydantic drops it (hard rule 9).
    sequence: Optional[BuildSequenceOut] = None
    # Same as LayoutOut.cuboid_count / reasons (F3/F4), against this box's own
    # inner.
    cuboid_count: int = 0
    reasons: list[str] = []


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
    # The bounds `boxes` is the minimum of, and the boxes-high used. The load
    # calculator shows these; it must not compute its own.
    by_volume: int
    by_weight: int
    stack: int


class SolveResultOut(BaseModel):
    catalogue: list[LayoutOut]
    custom: Optional[BoxDesignOut]
    custom_beats_catalogue: bool
    best_count: int
    truck: Optional[TruckFitOut]
    warnings: list[str]
    # Pose labels the engine searched -- all candidates, or the one confirmed
    # pose when `SolveIn.confirmed_pose_only` was set.
    poses_searched: list[str] = []
    # The in-plane clearance this result was solved at (hard rule 9: the UI
    # labels the number with the parameter that made it, never assumes 5).
    clearance_mm: float = 5.0
    # Ticket 2: counts land (status="done") before drawing_url/gif_url/
    # packed_url do -- render_run fills those in a second pass. "pending"
    # while it runs, "done" once every url above is final, "failed" with
    # render_error set (never failing the solve itself) if it blew up.
    # Declared here or pydantic drops both silently (hard rule 9) -- the
    # frontend polls render_status to know when the pictures are ready.
    render_status: Literal["pending", "done", "failed"] = "done"
    render_error: Optional[str] = None


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
    project_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- F1: Project entity -----------------------------------------------------


class ProjectIn(BaseModel):
    customer: str = Field(min_length=1, max_length=128)
    part_number: str = Field(min_length=1, max_length=64)
    part_name: str = Field(min_length=1, max_length=128)
    owner: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None
    annual_volume: Optional[int] = Field(default=None, ge=1)
    route_km: Optional[float] = Field(default=None, ge=0)
    vehicle_id: Optional[int] = None
    # The customer's OWN current parts-per-box and box — entered, never
    # estimated (hard rule 2).
    customer_count: Optional[int] = Field(default=None, ge=1)
    customer_box: Optional[str] = Field(default=None, max_length=32)
    cost_per_trip: Optional[float] = Field(default=None, ge=0)
    emission_factor_kg_per_km: Optional[float] = Field(default=None, ge=0)


class ProjectPatch(BaseModel):
    """Every ProjectIn field, optional, plus status and recommended_run_id.

    `model_dump(exclude_unset=True)` in the route means a field omitted from
    the request body is left untouched, while an explicit `null` clears it.
    """

    customer: Optional[str] = Field(default=None, min_length=1, max_length=128)
    part_number: Optional[str] = Field(default=None, min_length=1, max_length=64)
    part_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    owner: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None
    annual_volume: Optional[int] = Field(default=None, ge=1)
    route_km: Optional[float] = Field(default=None, ge=0)
    vehicle_id: Optional[int] = None
    customer_count: Optional[int] = Field(default=None, ge=1)
    customer_box: Optional[str] = Field(default=None, max_length=32)
    cost_per_trip: Optional[float] = Field(default=None, ge=0)
    emission_factor_kg_per_km: Optional[float] = Field(default=None, ge=0)
    status: Optional[Literal[
        "draft", "solved", "proposal_sent", "trial", "approved", "archived"
    ]] = None
    recommended_run_id: Optional[str] = None


class RunOut(BaseModel):
    """A SolveJob summarised server-side — hard rule 9: the frontend never
    digs into result_json for these, it reads this model."""

    solve_job_id: str
    part_id: int
    status: Literal["pending", "processing", "done", "failed"]
    created_at: datetime
    error: Optional[str] = None
    inputs: dict = {}
    # best_count/best_asset describe the SAME option: result.best_count (the
    # engine's overall winner, catalogue or custom) paired with "custom" when
    # the custom design is that winner, else catalogue[0].asset_name. Never
    # pair best_count with catalogue[0]'s name -- when custom_beats_catalogue
    # those are two different boxes (hard rule 9).
    best_count: Optional[int] = None
    best_asset: Optional[str] = None
    # catalogue[0] specifically, so a reader who wants "the best STOCKED
    # option" (as opposed to best_count/best_asset, which may be custom) has
    # a count and a name that are guaranteed to agree with each other.
    catalogue_count: Optional[int] = None
    catalogue_asset: Optional[str] = None
    custom_count: Optional[int] = None
    truck_boxes: Optional[int] = None
    truck_vehicle: Optional[str] = None
    clearance_mm: Optional[float] = None
    poses_searched: list[str] = []
    # F3: baseline and gain, for the SAME object best_count/best_asset
    # describe (custom when custom wins, else catalogue[0]). None on older
    # stored results that predate these fields, never a crash (hard rule 9
    # reads server-computed only -- this is read-time in `_run_out`, so
    # editing customer_count later updates the gain without a re-solve).
    cuboid_count: Optional[int] = None
    customer_count: Optional[int] = None      # project.customer_count, verbatim
    gain_vs_cuboid: Optional[float] = None    # best_count / cuboid_count, 2dp
    gain_vs_customer_pct: Optional[float] = None  # (best_count/customer_count - 1) x 100, 1dp
    # F4: the reasons of that same object. [] on older stored results.
    reasons: list[str] = []
    # F6: truck.parts verbatim, and trips/year computed read-time from
    # project.annual_volume -- same reason as gain_vs_customer_pct above.
    parts_per_truck: Optional[int] = None
    trips_per_year: Optional[int] = None


class ProjectSummaryOut(BaseModel):
    id: int
    customer: str
    part_number: str
    part_name: str
    status: str
    owner: Optional[str] = None
    updated_at: datetime
    run_count: int
    best_count: Optional[int] = None
    best_asset: Optional[str] = None
    catalogue_count: Optional[int] = None
    catalogue_asset: Optional[str] = None
    # F3, same semantics as RunOut's.
    cuboid_count: Optional[int] = None
    customer_count: Optional[int] = None
    gain_vs_cuboid: Optional[float] = None
    gain_vs_customer_pct: Optional[float] = None

    class Config:
        from_attributes = True


class ProjectOut(BaseModel):
    id: int
    customer: str
    part_number: str
    part_name: str
    status: str
    owner: Optional[str] = None
    notes: Optional[str] = None
    annual_volume: Optional[int] = None
    route_km: Optional[float] = None
    vehicle_id: Optional[int] = None
    customer_count: Optional[int] = None
    customer_box: Optional[str] = None
    cost_per_trip: Optional[float] = None
    emission_factor_kg_per_km: Optional[float] = None
    recommended_run_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    part: Optional[PartProfileOut] = None
    runs: list[RunOut] = []

    class Config:
        from_attributes = True


# --- F7: proposal PDF --------------------------------------------------------


class ProposalOut(BaseModel):
    id: int
    project_id: int
    run_id: str
    status: Literal["pending", "processing", "done", "failed"]
    # `/api/files/<basename of pdf_path>`, same discipline as LayoutOut's
    # drawing_url -- None, not a broken link, until the render is done.
    pdf_url: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
