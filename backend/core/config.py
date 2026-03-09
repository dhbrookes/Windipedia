"""
Application settings loaded from environment variables.

All secrets and deployment-specific values live here — nothing is hardcoded.
See .env.example at the repo root for the full list of required variables.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # GCS bucket for the on-demand tile cache.
    # Cache path: cache/{md5_hash}.png
    tile_cache_bucket: str = "windipedia-tile-cache"

    # Full gs:// path to the ARCO ERA5 Zarr store.
    # The default is the public GCP bucket — no credentials needed for reads.
    era5_zarr_path: str = (
        "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
    )

    # GCP region where the backend runs. Should be us-central1 to colocate
    # with the ERA5 bucket and avoid egress charges / latency.
    gcp_region: str = "us-central1"

    # Optional: path to a GCP service account JSON key file.
    # Not needed if running on GCP with Workload Identity or if you've
    # run `gcloud auth application-default login` locally.
    google_application_credentials: str = ""

    # When True the backend returns a transparent placeholder PNG for every
    # tile request without touching GCS or xarray. Useful for frontend dev
    # when GCP access isn't set up.
    use_placeholder_tiles: bool = False

    # CORS origin(s) allowed to call the API. Comma-separated in env.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # FastAPI environment label — affects log verbosity
    environment: str = "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
