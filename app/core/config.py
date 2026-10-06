"""Application settings, loaded from environment variables (or a .env file)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GEO_", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    debug: bool = False

    # Persistence
    database_url: str = "sqlite:///./data/geo_measure.db"
    upload_dir: Path = Path("./data/uploads")

    # Upload / extraction limits (defence against oversized uploads and zip bombs)
    max_upload_size_mb: int = 50
    max_uncompressed_size_mb: int = 500
    max_archive_entries: int = 1000

    # Measurement
    # Features whose longitude span exceeds this are measured in a feature-centred
    # azimuthal projection instead of UTM (UTM distortion grows away from the zone).
    utm_max_lon_span_deg: float = 6.0
    utm_max_abs_lat_deg: float = 84.0

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.max_uncompressed_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
