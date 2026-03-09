"""
Unit tests for GCS cache helpers.

Uses an in-memory mock for GCS — no real GCS credentials required.
"""

import os
import sys
import gzip
import json
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.gcs import get_cached_tile, put_cached_tile, get_cached_distribution, put_cached_distribution


def make_mock_bucket(store: dict):
    """Return a mock GCS bucket backed by an in-memory dict."""
    def make_blob(name):
        blob = MagicMock()

        def download():
            if name not in store:
                from google.api_core.exceptions import NotFound
                raise NotFound(f"Object {name} not found")
            return store[name]

        def upload(data, content_type=None):
            store[name] = data

        blob.download_as_bytes.side_effect = download
        blob.upload_from_string.side_effect = upload
        blob.exists.side_effect = lambda: name in store
        return blob

    bucket = MagicMock()
    bucket.blob.side_effect = make_blob
    return bucket


class FakeNotFound(Exception):
    pass


def test_tile_cache_miss_returns_none():
    """get_cached_tile should return None when the tile is not in cache."""
    store = {}
    mock_client = MagicMock()
    mock_client.bucket.return_value = make_mock_bucket(store)

    with patch("core.gcs.get_gcs_client", return_value=mock_client):
        result = get_cached_tile("my-bucket", "nonexistent-key")
    assert result is None


def test_tile_cache_round_trip():
    """Writing then reading a tile should return the original bytes."""
    store = {}
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG

    mock_client = MagicMock()
    mock_client.bucket.return_value = make_mock_bucket(store)

    with patch("core.gcs.get_gcs_client", return_value=mock_client):
        put_cached_tile("my-bucket", "test-key", png_bytes)
        result = get_cached_tile("my-bucket", "test-key")

    assert result == png_bytes


def test_distribution_cache_round_trip():
    """Writing then reading a distribution dict should preserve all data."""
    store = {}
    dist_data = {
        "meta": {"variable": "wind_speed", "n_samples": 100},
        "grid": {"37_-122": {"p": [1.0, 2.0, 5.0, 10.0, 18.0, 25.0, 30.0],
                              "h": list(range(51)), "c": [5] * 50, "n": 100}},
    }

    mock_client = MagicMock()
    mock_client.bucket.return_value = make_mock_bucket(store)

    with patch("core.gcs.get_gcs_client", return_value=mock_client):
        put_cached_distribution("my-bucket", "dist-key", dist_data)
        result = get_cached_distribution("my-bucket", "dist-key")

    assert result is not None
    assert result["meta"]["variable"] == "wind_speed"
    assert "37_-122" in result["grid"]
    assert result["grid"]["37_-122"]["n"] == 100


def test_distribution_cache_miss_returns_none():
    store = {}
    mock_client = MagicMock()
    mock_client.bucket.return_value = make_mock_bucket(store)

    with patch("core.gcs.get_gcs_client", return_value=mock_client):
        result = get_cached_distribution("my-bucket", "no-such-key")
    assert result is None


def test_cache_write_failure_does_not_raise():
    """A failed GCS write should log and return None, not raise."""
    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.upload_from_string.side_effect = Exception("GCS unavailable")
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    with patch("core.gcs.get_gcs_client", return_value=mock_client):
        # Should not raise
        put_cached_tile("my-bucket", "key", b"data")
        put_cached_distribution("my-bucket", "key", {"meta": {}, "grid": {}})
