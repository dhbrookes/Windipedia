"""
Google Cloud Storage helpers — GCS client singleton + tile cache read/write.

Cache path convention:
  gs://{TILE_CACHE_BUCKET}/cache/{md5_hash}.png

The hash encodes all tile parameters (variable, date range, zoom, x, y) so each
unique query gets its own cache entry. ERA5 historical data doesn't change, so
cache entries never expire.
"""

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_gcs_client():
    """
    Return a cached GCS storage.Client.

    Authenticates via (in order):
      1. GOOGLE_APPLICATION_CREDENTIALS env var → service account JSON
      2. Application Default Credentials (gcloud auth application-default login)
      3. Workload Identity (automatic on GCP VMs / Cloud Run)
    """
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as e:
        raise ImportError(
            "google-cloud-storage is required. Run: pip install google-cloud-storage"
        ) from e

    return storage.Client()


def get_cached_tile(bucket_name: str, cache_key: str) -> Optional[bytes]:
    """
    Look up a tile in GCS by its MD5 cache key.

    Returns raw PNG bytes on a cache hit, or None on a miss.
    Returns None (not raises) on any GCS error so the caller can fall back
    to computing the tile.
    """
    try:
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/{cache_key}.png")
        data = blob.download_as_bytes()
        logger.debug("Cache HIT: %s", cache_key)
        return data
    except Exception as exc:
        # NotFound is expected on a cold cache; log other errors but don't crash
        if "404" not in str(exc) and "NotFound" not in type(exc).__name__:
            logger.warning("GCS cache read error for %s: %s", cache_key, exc)
        return None


def put_cached_tile(bucket_name: str, cache_key: str, png_bytes: bytes) -> None:
    """
    Upload a rendered tile PNG to the GCS cache.

    Failures are logged but not raised — a failed cache write is not fatal
    (the caller already has the PNG to return to the client).
    """
    try:
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/{cache_key}.png")
        blob.upload_from_string(png_bytes, content_type="image/png")
        logger.debug("Cache WRITE: %s (%d bytes)", cache_key, len(png_bytes))
    except Exception as exc:
        logger.warning("GCS cache write failed for %s: %s", cache_key, exc)


def get_cached_distribution(bucket_name: str, cache_key: str) -> "dict | None":
    """
    Look up a distribution JSON blob in GCS.

    Distribution data is stored as gzip-compressed JSON at:
      cache/dist_{cache_key}.json.gz

    Returns the parsed dict on a cache hit, or None on a miss.
    """
    import gzip, json
    try:
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/dist_{cache_key}.json.gz")
        compressed = blob.download_as_bytes()
        return json.loads(gzip.decompress(compressed).decode("utf-8"))
    except Exception as exc:
        if "404" not in str(exc) and "NotFound" not in type(exc).__name__:
            logger.warning("Distribution cache read error for %s: %s", cache_key, exc)
        return None


def put_cached_distribution(bucket_name: str, cache_key: str, data: dict) -> None:
    """
    Upload a distribution dict to GCS as gzip-compressed JSON.

    Failures are logged but not raised.
    """
    import gzip, json
    try:
        payload = gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"))
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/dist_{cache_key}.json.gz")
        blob.upload_from_string(payload, content_type="application/json")
        logger.debug("Distribution cache WRITE: %s (%d bytes compressed)", cache_key, len(payload))
    except Exception as exc:
        logger.warning("Distribution cache write failed for %s: %s", cache_key, exc)
