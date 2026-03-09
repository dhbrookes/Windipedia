"""
Google Cloud Storage helpers — GCS client singleton + tile cache read/write.

Cache path convention:
  gs://{TILE_CACHE_BUCKET}/cache/{md5_hash}.png     (GCS bucket)
  {local_dir}/cache/{md5_hash}.png                  (local filesystem)

Set TILE_CACHE_BUCKET to a local directory path (e.g. ./local_cache) to use
the filesystem instead of GCS — no credentials required.

The hash encodes all tile parameters (variable, date range, zoom, x, y) so each
unique query gets its own cache entry. ERA5 historical data doesn't change, so
cache entries never expire.
"""

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def _is_local(bucket: str) -> bool:
    """Return True if bucket is a local filesystem path rather than a GCS bucket name."""
    return bool(bucket) and not bucket.startswith("gs://")


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
    Look up a tile by its MD5 cache key.

    Checks a local directory if bucket_name is a filesystem path, otherwise GCS.
    Returns raw PNG bytes on a cache hit, or None on a miss.
    Returns None (not raises) on any error so the caller can fall back to computing.
    """
    if not bucket_name:
        return None
    if _is_local(bucket_name):
        path = os.path.join(bucket_name, "cache", f"{cache_key}.png")
        try:
            with open(path, "rb") as f:
                data = f.read()
            logger.debug("Cache HIT (local): %s", cache_key)
            return data
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("Local cache read error for %s: %s", cache_key, exc)
            return None
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
    Store a rendered tile PNG in the cache.

    Writes to a local directory if bucket_name is a filesystem path, otherwise GCS.
    Failures are logged but not raised — a failed cache write is not fatal.
    """
    if not bucket_name:
        return
    if _is_local(bucket_name):
        path = os.path.join(bucket_name, "cache", f"{cache_key}.png")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(png_bytes)
            logger.debug("Cache WRITE (local): %s (%d bytes)", cache_key, len(png_bytes))
        except Exception as exc:
            logger.warning("Local cache write failed for %s: %s", cache_key, exc)
        return
    try:
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/{cache_key}.png")
        blob.upload_from_string(png_bytes, content_type="image/png")
        logger.debug("Cache WRITE: %s (%d bytes)", cache_key, len(png_bytes))
    except Exception as exc:
        logger.warning("GCS cache write failed for %s: %s", cache_key, exc)


def get_cached_distribution(bucket_name: str, cache_key: str) -> "dict | None":
    """
    Look up a distribution JSON blob in the cache.

    Distribution data is stored as gzip-compressed JSON at:
      cache/dist_{cache_key}.json.gz

    Returns the parsed dict on a cache hit, or None on a miss.
    """
    import gzip, json
    if not bucket_name:
        return None
    if _is_local(bucket_name):
        path = os.path.join(bucket_name, "cache", f"dist_{cache_key}.json.gz")
        try:
            with open(path, "rb") as f:
                compressed = f.read()
            return json.loads(gzip.decompress(compressed).decode("utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("Local distribution cache read error for %s: %s", cache_key, exc)
            return None
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
    Store a distribution dict in the cache as gzip-compressed JSON.

    Failures are logged but not raised.
    """
    import gzip, json
    if not bucket_name:
        return
    payload = gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    if _is_local(bucket_name):
        path = os.path.join(bucket_name, "cache", f"dist_{cache_key}.json.gz")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(payload)
            logger.debug("Distribution cache WRITE (local): %s (%d bytes)", cache_key, len(payload))
        except Exception as exc:
            logger.warning("Local distribution cache write failed for %s: %s", cache_key, exc)
        return
    try:
        client = get_gcs_client()
        blob = client.bucket(bucket_name).blob(f"cache/dist_{cache_key}.json.gz")
        blob.upload_from_string(payload, content_type="application/json")
        logger.debug("Distribution cache WRITE: %s (%d bytes compressed)", cache_key, len(payload))
    except Exception as exc:
        logger.warning("Distribution cache write failed for %s: %s", cache_key, exc)
