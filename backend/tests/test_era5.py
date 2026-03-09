"""
Unit tests for backend/core/era5.py.

Run with: pytest backend/tests/ -v
These tests use a synthetic mock dataset — no GCS credentials required.
Set MOCK_ERA5=true or call create_mock_dataset() directly.
"""

import os
import sys

import numpy as np
import pytest

# Add backend/ to path so imports work from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.era5 import (
    create_mock_dataset,
    compute_tile_png,
    compute_distribution,
    compute_exceedance,
    make_cache_key,
    make_param_key,
    _values_to_rgba,
    ERA5_VAR_CONFIG,
    N_HISTOGRAM_BINS,
)


@pytest.fixture(scope="module")
def mock_ds():
    """Shared mock dataset for all tests in this module."""
    return create_mock_dataset()


# --- Unit conversion tests ---

def test_unit_conversion_wind():
    """1 m/s should equal 1.94384 knots."""
    config = ERA5_VAR_CONFIG["wind_speed"]
    u = np.array([[1.0]], dtype=np.float32)
    v = np.array([[0.0]], dtype=np.float32)
    result = config["convert"](u, v)
    assert abs(result[0, 0] - 1.94384) < 0.001, f"Expected ~1.944, got {result[0,0]}"


def test_unit_conversion_pressure():
    """101300 Pa should equal 1013 hPa."""
    config = ERA5_VAR_CONFIG["pressure"]
    p = np.array([[101300.0]])
    assert abs(config["convert"](p)[0, 0] - 1013.0) < 0.1


def test_unit_conversion_temperature():
    """273.15 K should equal 0.0 degC."""
    config = ERA5_VAR_CONFIG["temp"]
    t = np.array([[273.15]])
    assert abs(config["convert"](t)[0, 0]) < 0.01


def test_unit_conversion_precip():
    """1e-3 m/hr should equal 24 mm/day."""
    config = ERA5_VAR_CONFIG["precip"]
    p = np.array([[1e-3]])
    expected = 1e-3 * 1000 * 24  # = 24.0
    assert abs(config["convert"](p)[0, 0] - expected) < 0.01


# --- Mock dataset tests ---

def test_mock_dataset_structure(mock_ds):
    """Mock dataset must have all required ERA5 variable names and coordinates."""
    required_vars = [
        "10m_u_component_of_wind",
        "10m_v_component_of_wind",
        "mean_sea_level_pressure",
        "significant_height_of_combined_wind_waves_and_swell",
        "total_precipitation",
        "2m_temperature",
    ]
    for v in required_vars:
        assert v in mock_ds.data_vars, f"Missing variable: {v}"
    assert "time" in mock_ds.dims
    assert "latitude" in mock_ds.dims
    assert "longitude" in mock_ds.dims


# --- Tile rendering tests ---

def test_tile_renders_as_png(mock_ds):
    """compute_tile_png should return valid PNG bytes (starts with PNG magic bytes)."""
    png = compute_tile_png(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        z=0, x=0, y=0,
    )
    assert isinstance(png, bytes), "Expected bytes"
    assert png[:4] == b"\x89PNG", f"Expected PNG magic bytes, got {png[:4]!r}"
    assert len(png) > 100, "PNG is suspiciously small"


def test_tile_all_variables(mock_ds):
    """Each variable should produce a valid PNG tile at zoom 0."""
    for variable in ERA5_VAR_CONFIG:
        png = compute_tile_png(
            mock_ds, variable,
            2020, 2020, 1, 1, "all", "monthly",
            z=0, x=0, y=0,
        )
        assert png[:4] == b"\x89PNG", f"{variable}: bad PNG"


# --- Distribution tests ---

def test_compute_distribution_shape(mock_ds):
    """Distribution should have meta and grid keys; grid cells should be non-empty."""
    dist = compute_distribution(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
    )
    assert "meta" in dist
    assert "grid" in dist
    assert len(dist["grid"]) > 0, "Grid is empty"

    # Check a random cell
    cell = next(iter(dist["grid"].values()))
    assert "p" in cell and len(cell["p"]) == 7, "Expected 7 percentiles (p5-p95)"
    assert "h" in cell and len(cell["h"]) == N_HISTOGRAM_BINS + 1, "Wrong histogram bin count"
    assert "c" in cell and len(cell["c"]) == N_HISTOGRAM_BINS, "Wrong histogram count length"
    assert "n" in cell and cell["n"] > 0, "n_samples must be positive"


def test_compute_distribution_units(mock_ds):
    """Wind speed distribution values should be in knots (not m/s), temp in degC (not K)."""
    dist_wind = compute_distribution(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
    )
    cell = next(iter(dist_wind["grid"].values()))
    # Median wind speed (p50) in knots should be plausible (0-60 kts), not m/s (~0-30 m/s range but converted)
    p50 = cell["p"][3]  # index 3 = p50
    assert 0 <= p50 <= 100, f"Wind speed p50={p50} kts seems wrong — maybe not converted to knots?"

    dist_temp = compute_distribution(
        mock_ds, "temp",
        2020, 2020, 1, 1, "all", "monthly",
    )
    cell_temp = next(iter(dist_temp["grid"].values()))
    p50_temp = cell_temp["p"][3]
    assert -80 <= p50_temp <= 60, f"Temperature p50={p50_temp} seems wrong — maybe still in Kelvin?"


def test_histogram_bins_cover_range(mock_ds):
    """Histogram bin edges should span the full variable range with no gaps."""
    from core.era5 import _SCALE_RANGES
    dist = compute_distribution(
        mock_ds, "pressure",
        2020, 2020, 1, 1, "all", "monthly",
    )
    cell = next(iter(dist["grid"].values()))
    bins = cell["h"]
    vmin, vmax = _SCALE_RANGES["pressure"]
    assert abs(bins[0] - vmin) < 1.0, f"Bins don't start at vmin={vmin}"
    assert abs(bins[-1] - vmax) < 1.0, f"Bins don't end at vmax={vmax}"
    # Bins should be monotonically increasing
    for i in range(len(bins) - 1):
        assert bins[i] < bins[i + 1], f"Bin edges not monotonic at index {i}"


# --- Exceedance tests ---

def test_compute_exceedance_range(mock_ds):
    """All exceedance values must be in [0.0, 1.0]."""
    exc, lats, lons = compute_exceedance(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        threshold=15.0,
    )
    assert exc.min() >= 0.0, f"Exceedance below 0: {exc.min()}"
    assert exc.max() <= 1.0, f"Exceedance above 1: {exc.max()}"


def test_compute_exceedance_monotonic(mock_ds):
    """Higher threshold must give <= exceedance probability at every cell."""
    exc_low, lats, lons = compute_exceedance(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        threshold=5.0,
    )
    exc_high, _, _ = compute_exceedance(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        threshold=30.0,
    )
    assert np.all(exc_high <= exc_low + 1e-6), (
        "Higher threshold should give lower or equal exceedance probability"
    )


def test_exceedance_extreme_thresholds(mock_ds):
    """Threshold above all values should give 0 exceedance; below all gives 1."""
    exc_above, _, _ = compute_exceedance(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        threshold=9999.0,
    )
    assert exc_above.max() < 0.01, "P(wind > 9999 kts) should be ~0"

    exc_below, _, _ = compute_exceedance(
        mock_ds, "wind_speed",
        2020, 2020, 1, 1, "all", "monthly",
        threshold=-1.0,
    )
    assert exc_below.min() > 0.99, "P(wind > -1 kts) should be ~1"


# --- Cache key tests ---

def test_cache_key_deterministic():
    key1 = make_cache_key("wind_speed", 1991, 2020, 1, 12, "all", "monthly", 2, 1, 0)
    key2 = make_cache_key("wind_speed", 1991, 2020, 1, 12, "all", "monthly", 2, 1, 0)
    assert key1 == key2, "Cache key should be deterministic"


def test_cache_key_unique():
    key1 = make_cache_key("wind_speed", 1991, 2020, 1, 12, "all", "monthly", 2, 1, 0)
    key2 = make_cache_key("wind_speed", 1991, 2020, 1, 12, "all", "monthly", 2, 1, 1)
    assert key1 != key2, "Different tiles should have different cache keys"


# --- Color scale tests ---

def test_nan_pixels_are_transparent():
    data = np.array([[float("nan"), 10.0]], dtype=np.float32)
    rgba = _values_to_rgba("wind_speed", data)
    assert rgba[0, 0, 3] == 0, "NaN pixel should be fully transparent (alpha=0)"
    assert rgba[0, 1, 3] > 0, "Valid pixel should have non-zero alpha"
