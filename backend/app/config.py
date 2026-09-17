"""Settings — env-driven so the same image runs locally and on AWS."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://intake:intake@db:5432/intake"
    redis_url: str = "redis://redis:6379/0"

    # Storage: "local" for dev (shared volume), "s3" for production.
    storage_backend: str = "local"
    local_storage_dir: str = "/data/files"
    s3_bucket: str = ""
    aws_region: str = "ap-south-1"

    max_upload_mb: int = 200

    # --- T5: landed-cost gates (engine.py). PM ruling 2026-09-16, debate
    # `final_verdict.md` provenance -- named parameters, never literals in
    # engine.py (PLANNING §10). ---
    # Forward (erect) leg, Rs per box: 32 boxes/32ft truck at ~Rs 35,000/trip.
    f_fwd_per_trip: float = 1094.0
    # Return leg, Rs per TRUCK trip (same physical truck as the forward leg);
    # divided by however many boxes actually stack on the return leg, which
    # depends on fold_type/folded_h_mm -- that's the whole point of T5.
    f_ret_per_trip: float = 35000.0
    # Floor positions per truck (8 x 2 in the debate's own worked example).
    n_footprints: int = 16
    # Pool life in trips at 1.5%/trip shrinkage (PM ruling; was the debate's 66).
    T_pool: float = 66.0
    wage_per_hour: float = 50.0       # Rs/hour, loading labour (debate A3)
    stack_H_max_mm: float = 2000.0    # usable return-truck stacking height, mm
    # Programs sharing one tool's amortisation. Conservative default: no BOM
    # element carries a tooling cost yet (T7 lands die/knife-cut elements),
    # so this is inert today.
    K_programs: int = 1

    class Config:
        env_prefix = "INTAKE_"


settings = Settings()
