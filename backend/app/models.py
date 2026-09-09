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
    status: Mapped[str] = mapped_column(String(16), default="draft")  # checked | draft
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
