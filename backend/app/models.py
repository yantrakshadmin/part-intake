"""SQLAlchemy models. Postgres in docker-compose; SQLite works for quick dev."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    status: Mapped[str] = mapped_column(String(16), default="pending")
    step_path: Mapped[str] = mapped_column(Text)
    glb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )


class Packaging(Base):
    """Packaging master list (Phase 2). Seeded from the packaging_and_vehicles
    sheet; user-added custom boxes get status='draft' until verified."""

    __tablename__ = "packaging"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # Inner = usable cavity for parts; outer = footprint for truck loading. mm.
    inner_l_mm: Mapped[float] = mapped_column(Float)
    inner_b_mm: Mapped[float] = mapped_column(Float)
    inner_h_mm: Mapped[float] = mapped_column(Float)
    outer_l_mm: Mapped[float] = mapped_column(Float)
    outer_b_mm: Mapped[float] = mapped_column(Float)
    outer_h_mm: Mapped[float] = mapped_column(Float)
    max_weight_kg: Mapped[float] = mapped_column(Float)
    # Per-asset tare (kg). Nullable, NO default, deliberately: the SCS report
    # this is seeded from (see seed_data.PACKAGING_TARE) reads 0 for 13 of 49
    # rows and 0 there means "no tare on file," not weightless -- a false 0
    # would silently win the truck-tier ranking in engine.parts_per_truck.
    tare_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    material: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # checked | draft
    # container | pallet | rack | accessory. PLANNING §5 item 4: the export mixes
    # these and an optimiser that doesn't distinguish packs parts into a rack.
    # server_default too: there is no Alembic, so an existing dev.db needs a
    # manual ALTER TABLE ADD COLUMN, and that is only correct if the DB
    # itself knows the default. PLANNING §5 item 4 -- without this column
    # the optimiser packs parts into a warehouse rack.
    kind: Mapped[str] = mapped_column(String(16), default="container",
                                      server_default="container")
    # T6: retention-rule inputs, None/'unknown' until the team enters real
    # numbers (hard rule 2 — never auto-filled). fold_type is what the
    # engine will need to decide whether a return leg can fold the box down;
    # folded_h_mm/lid_void_mm feed that leg's insert/stack math later.
    fold_type: Mapped[str] = mapped_column(String(16), default="unknown",
                                           server_default="unknown")
    folded_h_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    lid_void_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )


class Vehicle(Base):
    """Vehicle master list — cargo bay inner dims (mm) and payload (kg)."""

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    cargo_l_mm: Mapped[float] = mapped_column(Float)
    cargo_b_mm: Mapped[float] = mapped_column(Float)
    cargo_h_mm: Mapped[float] = mapped_column(Float)
    payload_kg: Mapped[float] = mapped_column(Float)


class SolveJob(Base):
    """Phase 2 nesting solve, run async because voxelising is ~11s+ (B3-1).

    A new table, not a column on ExtractionJob or PartProfile: `create_all`
    makes new tables for free; there is no Alembic here (see Packaging.kind
    above), so a new column needs a manual ALTER TABLE on every dev.db.
    """

    __tablename__ = "solve_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    part_id: Mapped[int] = mapped_column(Integer, index=True)
    # F1: which Project this run belongs to, nullable — solves from before
    # projects existed, and the standalone /api/parts/{id}/solve path, both
    # have none. Added via main._ensure_added_columns (landmine 5), not
    # create_all, since solve_jobs already exists on every dev.db.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # F1: the SAME params dict sent to the task (tare_kg, vehicle, top_n,
    # assets, confirmed_pose_only, clearance_mm), recorded at enqueue time so
    # a run's inputs survive over HTTP. result_json cannot hold this: the
    # worker overwrites that column with the result (see run_solve's
    # docstring), so this is a separate column, not a reuse of that one.
    inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )


class PartProfile(Base):
    __tablename__ = "part_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_number: Mapped[str] = mapped_column(String(64), index=True)
    part_name: Mapped[str] = mapped_column(String(128))
    # Canonical: length >= breadth >= height, millimetres
    length_mm: Mapped[float] = mapped_column(Float)
    breadth_mm: Mapped[float] = mapped_column(Float)
    height_mm: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(8))  # manual | stp
    # STP provenance — kept so Phase 3 (insert generation) never re-asks
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    glb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_orientation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # F1: which Project this part belongs to, nullable — parts created before
    # projects existed have none. Added via main._ensure_added_columns.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # T6: raw | painted | ecoat | class_a, nullable — the retention rules need
    # to know when a surface can't touch bare cardboard/foam. Never guessed.
    surface_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )


class Project(Base):
    """F1: a customer part being pursued, owning one PartProfile and many
    SolveJobs. Status is hand-driven (no automation on solve completion) —
    the ladder is enforced only via schemas.ProjectPatch's Literal."""

    __tablename__ = "projects"

    STATUSES = ("draft", "solved", "proposal_sent", "trial", "approved", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer: Mapped[str] = mapped_column(String(128), index=True)
    part_number: Mapped[str] = mapped_column(String(64), index=True)
    part_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft",
                                        server_default="draft")
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    annual_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    route_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The customer's OWN current parts-per-box and box, entered — never
    # estimated (hard rule 2/PRD F3(b)). "customer_count" not "baseline_count":
    # this is what the customer told us, not a number the engine derived.
    customer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_box: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_per_trip: Mapped[float | None] = mapped_column(Float, nullable=True)
    emission_factor_kg_per_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


class Proposal(Base):
    """F7: a rendered PDF proposal for one project, from one run.

    A NEW table -- `create_all` makes it for free, so no `_ensure_added_columns`
    shim is needed (contrast `SolveJob.project_id`, added to an EXISTING table).
    """

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    # Which SolveJob the PDF's numbers come from -- recommended_run_id if the
    # project has one, else the newest done run, resolved once at POST time
    # (see main.create_proposal) so a later re-solve does not silently change
    # what an already-created proposal says.
    run_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )
