"""Pydantic schemas — the data contracts for Phase 1 (Part Intake)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

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
