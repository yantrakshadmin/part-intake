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
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow
    )
