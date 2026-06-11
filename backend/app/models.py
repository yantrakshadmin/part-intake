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
