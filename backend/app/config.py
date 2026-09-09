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

    class Config:
        env_prefix = "INTAKE_"


settings = Settings()
