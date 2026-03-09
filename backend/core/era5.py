"""
ERA5 data access and tile rendering.

This module owns all xarray/Zarr logic. The main entry points are:

  open_zarr_store(path)           — open the ARCO ERA5 Zarr dataset (call once at startup)
  compute_tile(ds, params, z, x, y) — subset → mean → render a 256×256 PNG tile

ERA5 data model (ARCO Zarr):
  - Dimensions: time (hourly from 1940), latitude (90→−90, 0.25° step), longitude (−180→180)
  - ~140 surface variables; we use a small subset (see ERA5_VAR_CONFIG below)
  - Latitude is stored north→south (descending), so slicing is reversed vs. normal convention
  - The Zarr store is chunked along time; reading a long time range triggers many chunk fetches.
    Colocating the backend in us-central1 with the ERA5 bucket avoids egress and speeds this up.

Zoom level note:
  ERA5 native resolution is 0.25° ≈ 28 km at the equator. At zoom 6 one tile covers
  ~156 km × ~156 km, so each tile contains only ~5×5 ERA5 grid cells — diminishing returns.
  We cap at zoom 5 (tiles ≈ 310 km) as the practical maximum. Higher zooms would just
  upscale the same sparse data.
"""

import hashlib
import io
import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ERA5 variable configuration
# ---------------------------------------------------------------------------
# Maps our internal variable ID → ERA5 dimension names and unit conversions.
#
# ERA5 units (raw):
#   u10, v10   : m/s      → knots (×1.94384)
#   msl        : Pa       → hPa   (÷100)
#   swh        : m        → m     (no change)
#   tp         : m/hr     → mm/day (×1000×24)  — ERA5 total_precipitation is accumulated
#                                                  m per hour in the ARCO dataset
#   t2m        : K        → °C    (−273.15)
ERA5_VAR_CONFIG: dict[str, dict[str, Any]] = {
    "wind_speed": {
        "era5_vars": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "derived": True,
        # Wind speed magnitude in knots from u/v components (both in m/s)
        # speed = sqrt(u² + v²) × 1.94384 m/s→knots
        "convert": lambda u, v: np.sqrt(u**2 + v**2) * 1.94384,
    },
    "wind_dir": {
        "era5_vars": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "derived": True,
        # Meteorological wind direction: the direction FROM which wind blows.
        # 0° = north, 90° = east. Formula: 270° − atan2(v, u) mod 360°
        "convert": lambda u, v: (270 - np.degrees(np.arctan2(v, u))) % 360,
    },
    "pressure": {
        "era5_vars": ["mean_sea_level_pressure"],
        "derived": False,
        "convert": lambda p: p / 100.0,  # Pa → hPa
    },
    "wave_height": {
        "era5_vars": ["significant_height_of_combined_wind_waves_and_swell"],
        "derived": False,
        "convert": lambda h: h,  # already metres
    },
    "precip": {
        "era5_vars": ["total_precipitation"],
        "derived": False,
        "convert": lambda p: p * 1000 * 24,  # m/hr → mm/day
    },
    "temp": {
        "era5_vars": ["2m_temperature"],
        "derived": False,
        "convert": lambda t: t - 273.15,  # K → °C
    },
}

# ---------------------------------------------------------------------------
# Color scales (mirrored from pipeline/color_scales.py and frontend/constants/variables.js)
# These three files must stay in sync — if you change a scale, update all three.
# ---------------------------------------------------------------------------
# Format: variable_id → list of (data_value, (R, G, B))
_COLOR_STOPS: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "wind_speed": [
        (0,   (240, 249, 255)),
        (5,   (125, 211, 252)),
        (10,  ( 34, 197,  94)),
        (20,  (250, 204,  21)),
        (30,  (249, 115,  22)),
        (40,  (239,  68,  68)),
        (55,  (124,  58, 237)),
        (64,  ( 30,  27,  75)),
    ],
    "wind_dir": [
        (0,   (239,  68,  68)),
        (90,  ( 34, 197,  94)),
        (180, ( 59, 130, 246)),
        (270, (249, 115,  22)),
        (360, (239,  68,  68)),
    ],
    "pressure": [
        (960,  (124,  58, 237)),
        (990,  (167, 139, 250)),
        (1013, (241, 245, 249)),
        (1025, (251, 146,  60)),
        (1040, (146,  64,  14)),
    ],
    "wave_height": [
        (0,   (248, 250, 252)),
        (1,   (186, 230, 253)),
        (3,   ( 56, 189, 248)),
        (6,   (  2, 132, 199)),
        (9,   ( 30,  58, 138)),
        (12,  ( 15,  10,  30)),
    ],
    "precip": [
        (0,   (248, 250, 252)),
        (1,   (219, 234, 254)),
        (3,   (147, 197, 253)),
        (7,   ( 59, 130, 246)),
        (12,  ( 29,  78, 216)),
        (20,  ( 15,  23,  42)),
    ],
    "temp": [
        (-40, ( 15,  23,  42)),
        (-20, ( 59, 130, 246)),
        (-5,  (103, 232, 249)),
        (10,  ( 74, 222, 128)),
        (20,  (250, 204,  21)),
        (30,  (249, 115,  22)),
        (40,  (220,  38,  38)),
        (45,  (127,  29,  29)),
    ],
}

_SCALE_RANGES: dict[str, tuple[float, float]] = {
    "wind_speed":   (0,    64),
    "wind_dir":     (0,   360),
    "pressure":     (960, 1040),
    "wave_height":  (0,    12),
    "precip":       (0,    20),
    "temp":         (-40,  45),
}


def _values_to_rgba(variable: str, data: np.ndarray, alpha: int = 230) -> np.ndarray:
    """
    Vectorised mapping of a 2-D float array to RGBA uint8 pixels.

    NaN values become fully transparent. All other values are linearly
    interpolated between the two nearest colour stops and assigned `alpha`.
    """
    stops = _COLOR_STOPS[variable]
    vmin, vmax = _SCALE_RANGES[variable]

    flat = data.ravel()
    out = np.zeros((len(flat), 4), dtype=np.uint8)

    stop_vals = np.array([s[0] for s in stops], dtype=np.float32)
    stop_rgb  = np.array([s[1] for s in stops], dtype=np.float32)  # (N, 3)

    # Clamp and interpolate each pixel.
    # Fill NaN with vmin before interpolation (we zero those pixels out below anyway).
    nan_mask = np.isnan(flat)
    safe = np.where(nan_mask, vmin, flat)
    clamped = np.clip(safe, vmin, vmax)

    # np.interp works per-channel
    for c in range(3):
        out[:, c] = np.interp(clamped, stop_vals, stop_rgb[:, c]).astype(np.uint8)

    out[:, 3] = alpha
    out[nan_mask] = 0  # fully transparent for missing data

    return out.reshape(data.shape[0], data.shape[1], 4)


# ---------------------------------------------------------------------------
# Zarr store lifecycle
# ---------------------------------------------------------------------------

def open_zarr_store(gcs_path: str) -> Any:
    """
    Open the ARCO ERA5 Zarr dataset and return an xr.Dataset.

    Accepts either a GCS path (gs://…) or a local directory path.
    Local paths are opened directly with xarray/zarr — no GCP credentials needed.
    This is a lazy open — no data is downloaded until .load() or .compute()
    is called. The returned Dataset should be stored in app.state and reused
    across requests; reopening per-request is expensive (~1–2 s of network round trips).

    Authentication: the ERA5 bucket is public, so token="anon" works for reads.
    For the cache bucket you'll need ADC or a service account key.
    """
    try:
        import xarray as xr  # type: ignore
    except ImportError as e:
        raise ImportError(
            f"Missing dependency: {e}. Run: pip install -r backend/requirements.txt"
        ) from e

    logger.info("Opening ERA5 Zarr store: %s", gcs_path)

    if gcs_path.startswith("gs://"):
        try:
            import gcsfs  # type: ignore
        except ImportError as e:
            raise ImportError(
                f"gcsfs is required for GCS paths: {e}. Run: pip install gcsfs"
            ) from e
        fs = gcsfs.GCSFileSystem(token="anon")
        store = fs.get_mapper(gcs_path)
        ds = xr.open_zarr(store, consolidated=True)
    else:
        # Local Zarr directory — opened directly, no GCP credentials required.
        # consolidated=False reads per-variable metadata rather than .zmetadata,
        # which is more robust for zarr v2 stores written under zarr v3.
        ds = xr.open_zarr(gcs_path, consolidated=False)

    logger.info(
        "Zarr store opened. Variables: %d, time range: %s to %s",
        len(ds.data_vars),
        str(ds.time.values[0])[:10],
        str(ds.time.values[-1])[:10],
    )
    return ds


# ---------------------------------------------------------------------------
# Tile computation
# ---------------------------------------------------------------------------

def compute_tile_png(
    ds: Any,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,  # 'all' or zero-padded int string '00'–'23'
    averaging: str,  # 'daily' | 'weekly' | 'monthly' — reserved for future grouping logic
    z: int,
    x: int,
    y: int,
    update_progress=None,  # optional callable(float 0–1) for progress tracking
) -> bytes:
    """
    Subset ERA5 data, compute the temporal mean, and render a 256×256 PNG tile.

    Returns raw PNG bytes ready to serve or upload to GCS.

    The `averaging` param is stored in the cache key and URL for future use
    but currently all paths return a simple temporal mean. Daily/weekly
    grouping (e.g. mean of each day then average those) is a TODO.
    """
    import mercantile  # type: ignore

    config = ERA5_VAR_CONFIG[variable]
    era5_var_names = config["era5_vars"]

    # --- 1. Select only the variables we need ---
    ds_sub = ds[era5_var_names]

    if update_progress: update_progress(0.05)

    # --- 2. Time subsetting ---
    # Slice to the year range first (cheap — just slices the time index)
    ds_sub = ds_sub.sel(time=slice(f"{start_year}-01-01", f"{end_year}-12-31"))

    # Filter to the requested months.
    # ds.time.dt.month returns integer month numbers (1=Jan … 12=Dec).
    time = ds_sub.time
    if start_month <= end_month:
        # Normal range: e.g. Jan–Jun
        month_mask = (time.dt.month >= start_month) & (time.dt.month <= end_month)
    else:
        # Wrapping range: e.g. Oct–Mar crosses the year boundary
        month_mask = (time.dt.month >= start_month) | (time.dt.month <= end_month)

    ds_sub = ds_sub.sel(time=month_mask)

    # Filter to a specific UTC hour if requested.
    # ERA5 is hourly so --hour-utc 15 selects ~1/24 of timesteps.
    # Note: this is UTC hour, not local solar time — see comment in process_era5.py.
    if hour_utc != "all":
        hour_int = int(hour_utc)
        ds_sub = ds_sub.sel(time=ds_sub.time.dt.hour == hour_int)

    if update_progress: update_progress(0.10)

    # --- 3. Geographic subset for this tile ---
    # Only load the lat/lon region covered by this tile to minimise I/O.
    # We add a 1° buffer to avoid edge artefacts from bilinear interpolation.
    bounds = mercantile.bounds(mercantile.Tile(x, y, z))
    buf = 1.0
    lat_slice = slice(
        min(bounds.north + buf, 90),
        max(bounds.south - buf, -90),
    )
    # ERA5 latitude is stored north→south (descending), so the slice is reversed.
    lon_slice = slice(
        max(bounds.west - buf, -180),
        min(bounds.east + buf, 180),
    )
    ds_sub = ds_sub.sel(latitude=lat_slice, longitude=lon_slice)

    if update_progress: update_progress(0.15)

    # --- 4. Load data and compute temporal mean ---
    # .load() downloads the required Zarr chunks into memory.
    # This is the slow step — can take several seconds for long time ranges.
    if not config["derived"]:
        da = ds_sub[era5_var_names[0]].mean(dim="time").load()
        values = config["convert"](da.values.astype(np.float32))
    else:
        # Wind: load u and v separately, compute mean of each, then derive.
        # Averaging u and v before computing speed/direction is a simplification
        # (correct answer is per-timestep derivation then average). For climatological
        # means the error is small and the performance saving is large.
        u = ds_sub[era5_var_names[0]].mean(dim="time").load()
        v = ds_sub[era5_var_names[1]].mean(dim="time").load()
        values = config["convert"](
            u.values.astype(np.float32),
            v.values.astype(np.float32),
        )

    if update_progress: update_progress(0.70)

    lats = da.latitude.values if not config["derived"] else u.latitude.values
    lons = da.longitude.values if not config["derived"] else u.longitude.values

    # --- 5. Interpolate onto a 256×256 tile pixel grid ---
    # ERA5 lat is north→south; ensure ascending for np.interp
    if lats[0] > lats[-1]:
        values = values[::-1, :]
        lats = lats[::-1]

    # Handle 0–360 lon storage (ARCO uses −180–180, but be defensive)
    if lons.max() > 180:
        shift = lons > 180
        lons = lons.copy()
        lons[shift] -= 360
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        values = values[:, sort_idx]

    # Pixel grid: TILE_SIZE×TILE_SIZE evenly spanning the tile bounds
    TILE_SIZE = 256
    tile_lons = np.linspace(bounds.west, bounds.east, TILE_SIZE)
    tile_lats = np.linspace(bounds.north, bounds.south, TILE_SIZE)  # north→south for image rows

    pixel_values = _bilinear_interp(values, lats, lons, tile_lats, tile_lons)

    if update_progress: update_progress(0.85)

    # --- 6. Map values to RGBA and encode as PNG ---
    rgba = _values_to_rgba(variable, pixel_values)
    png_bytes = _rgba_to_png(rgba)

    if update_progress: update_progress(1.0)
    return png_bytes


def _bilinear_interp(
    data: np.ndarray,
    src_lats: np.ndarray,
    src_lons: np.ndarray,
    query_lats: np.ndarray,
    query_lons: np.ndarray,
) -> np.ndarray:
    """Bilinearly interpolate data[src_lats, src_lons] onto a (query_lats × query_lons) grid."""
    lat_idx = np.interp(query_lats, src_lats, np.arange(len(src_lats)))
    lon_idx = np.interp(query_lons, src_lons, np.arange(len(src_lons)))

    lat_lo = np.clip(np.floor(lat_idx).astype(int), 0, len(src_lats) - 2)
    lat_hi = lat_lo + 1
    lon_lo = np.clip(np.floor(lon_idx).astype(int), 0, len(src_lons) - 2)
    lon_hi = lon_lo + 1

    dlat = (lat_idx - lat_lo)[:, np.newaxis]
    dlon = (lon_idx - lon_lo)[np.newaxis, :]

    q00 = data[np.ix_(lat_lo, lon_lo)]
    q01 = data[np.ix_(lat_lo, lon_hi)]
    q10 = data[np.ix_(lat_hi, lon_lo)]
    q11 = data[np.ix_(lat_hi, lon_hi)]

    return (
        q00 * (1 - dlat) * (1 - dlon)
        + q01 * (1 - dlat) * dlon
        + q10 * dlat * (1 - dlon)
        + q11 * dlat * dlon
    )


def _rgba_to_png(rgba: np.ndarray) -> bytes:
    """Encode a (H, W, 4) uint8 RGBA array as a PNG and return bytes."""
    from PIL import Image  # type: ignore
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def make_cache_key(
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    z: int,
    x: int,
    y: int,
) -> str:
    """
    Return a short MD5 hex digest identifying this exact tile request.
    Used as the GCS cache object name prefix.
    """
    canonical = f"{variable}|{start_year}|{end_year}|{start_month}|{end_month}|{hour_utc}|{averaging}|{z}|{x}|{y}"
    return hashlib.md5(canonical.encode()).hexdigest()


def make_param_key(
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
) -> str:
    """
    Return a short MD5 key identifying the parameter set (without z/x/y).
    Used as the job_id for status tracking across all tiles in a parameter set.
    """
    canonical = f"{variable}|{start_year}|{end_year}|{start_month}|{end_month}|{hour_utc}|{averaging}"
    return hashlib.md5(canonical.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Mock ERA5 dataset (MOCK_ERA5=true)
# ---------------------------------------------------------------------------

def create_mock_dataset() -> Any:
    """
    Return a synthetic xarray Dataset that mimics the ERA5 ARCO Zarr structure.

    Used when MOCK_ERA5=true. Covers a 2-degree resolution global grid
    with 24 hourly timesteps (one day), which is fast to create and small
    enough to run full computation paths quickly in tests and demos.

    MOCK DATA — replace with real ERA5 Zarr access for production.
    """
    try:
        import xarray as xr  # type: ignore
        import pandas as pd
    except ImportError as e:
        raise ImportError(f"xarray/pandas required for mock mode: {e}") from e

    rng = np.random.default_rng(42)  # fixed seed for reproducibility

    # 2-degree resolution: 91 lats x 181 lons
    lats = np.linspace(90, -90, 91)    # N→S (ERA5 convention)
    lons = np.linspace(-180, 180, 181)
    times = pd.date_range("2020-01-01", periods=24, freq="h")

    H, W, T = len(lats), len(lons), len(times)

    # Generate spatially-varying plausible values for each ERA5 variable.
    # lat_factor: stronger winds at mid-latitudes, weaker at equator/poles.
    lat_grid = lats[:, np.newaxis]  # (H, 1)
    lat_factor = np.abs(np.sin(np.radians(lat_grid * 1.5)))  # peaks ~40-60°

    def tile_time(arr2d):
        """Broadcast 2D spatial array to 3D (time, lat, lon) with small noise."""
        base = np.broadcast_to(arr2d[np.newaxis, :, :], (T, H, W)).copy()
        base += rng.normal(0, 0.5, base.shape)
        return base.astype(np.float32)

    u10 = tile_time(rng.normal(loc=8 * lat_factor, scale=3.0, size=(H, W)))
    v10 = tile_time(rng.normal(loc=2 * lat_factor, scale=3.0, size=(H, W)))
    msl = tile_time(101300 + 1500 * np.sin(np.radians(lat_grid * 1.8))
                    + rng.normal(0, 300, (H, W)))
    swh = tile_time(np.abs(rng.normal(1.5 + np.abs(lat_grid) * 0.05, 0.8, (H, W))))
    tp  = tile_time(np.maximum(0, rng.exponential(
        scale=np.maximum(0.0001, 0.00005 * (1 - np.abs(lat_grid) / 90)), size=(H, W)
    )))
    t2m = tile_time(298 - np.abs(lat_grid) * 0.6 + rng.normal(0, 2, (H, W)))

    coords = {
        "time":      times,
        "latitude":  lats,
        "longitude": lons,
    }
    dims = ("time", "latitude", "longitude")

    ds = xr.Dataset(
        {
            "10m_u_component_of_wind": xr.DataArray(u10, dims=dims, coords=coords),
            "10m_v_component_of_wind": xr.DataArray(v10, dims=dims, coords=coords),
            "mean_sea_level_pressure": xr.DataArray(msl, dims=dims, coords=coords),
            "significant_height_of_combined_wind_waves_and_swell": xr.DataArray(swh, dims=dims, coords=coords),
            "total_precipitation":     xr.DataArray(tp,  dims=dims, coords=coords),
            "2m_temperature":          xr.DataArray(t2m, dims=dims, coords=coords),
        }
    )
    return ds


# ---------------------------------------------------------------------------
# Exceedance color scale
# ---------------------------------------------------------------------------
# White (0%) through yellows/oranges to deep red (100%).
# Fully transparent at probability 0 so ocean with 0% risk shows basemap.
_EXCEEDANCE_STOPS: list[tuple[float, tuple[int, int, int, int]]] = [
    (0.00, (255, 255, 255,   0)),  # transparent — no exceedance
    (0.01, (255, 255, 200, 180)),  # pale yellow
    (0.05, (255, 200, 100, 200)),  # yellow-orange
    (0.10, (255, 120,  50, 220)),  # orange
    (0.20, (220,  40,  40, 230)),  # red
    (0.50, (120,   0,   0, 245)),  # deep red
    (1.00, ( 40,   0,   0, 255)),  # near black
]


def _exceedance_to_rgba(data: np.ndarray) -> np.ndarray:
    """Map a 2-D probability array (0.0–1.0) to RGBA pixels."""
    flat = data.ravel()
    out  = np.zeros((len(flat), 4), dtype=np.uint8)

    stop_vals = np.array([s[0] for s in _EXCEEDANCE_STOPS], dtype=np.float32)
    stop_rgba = np.array([s[1] for s in _EXCEEDANCE_STOPS], dtype=np.float32)  # (N,4)

    clamped = np.clip(flat, 0.0, 1.0)
    for c in range(4):
        out[:, c] = np.interp(clamped, stop_vals, stop_rgba[:, c]).astype(np.uint8)

    return out.reshape(data.shape[0], data.shape[1], 4)


# ---------------------------------------------------------------------------
# Shared subsetting helper
# ---------------------------------------------------------------------------

def _subset_da(ds: Any, variable: str, start_year: int, end_year: int,
               start_month: int, end_month: int, hour_utc: str) -> "tuple[Any, ...]":
    """
    Apply time filtering and return (ds_sub, era5_var_names, config).

    Shared by compute_tile_png, compute_distribution, and compute_exceedance
    to avoid duplicating the time-subsetting logic.
    """
    config = ERA5_VAR_CONFIG[variable]
    era5_var_names = config["era5_vars"]

    ds_sub = ds[era5_var_names]
    ds_sub = ds_sub.sel(time=slice(f"{start_year}-01-01", f"{end_year}-12-31"))

    time = ds_sub.time
    if start_month <= end_month:
        month_mask = (time.dt.month >= start_month) & (time.dt.month <= end_month)
    else:
        month_mask = (time.dt.month >= start_month) | (time.dt.month <= end_month)
    ds_sub = ds_sub.sel(time=month_mask)

    if hour_utc != "all":
        ds_sub = ds_sub.sel(time=ds_sub.time.dt.hour == int(hour_utc))

    return ds_sub, era5_var_names, config


def _load_values(ds_sub: Any, variable: str, era5_var_names: list,
                 config: dict) -> "tuple[np.ndarray, np.ndarray, np.ndarray, int]":
    """
    Load data into memory and apply unit conversions.

    Returns (values_3d, lats, lons, n_samples) where values_3d has shape
    (n_times, n_lats, n_lons) in display units, with lons in −180 to 180.
    """
    n_samples = int(ds_sub.time.size)

    if not config["derived"]:
        raw = ds_sub[era5_var_names[0]].load().values.astype(np.float32)
        values = config["convert"](raw)
        lats = ds_sub[era5_var_names[0]].latitude.values
        lons = ds_sub[era5_var_names[0]].longitude.values
    else:
        # Wind: compute speed/direction per timestep (correct approach for distributions)
        u = ds_sub[era5_var_names[0]].load().values.astype(np.float32)
        v = ds_sub[era5_var_names[1]].load().values.astype(np.float32)
        values = config["convert"](u, v)
        lats = ds_sub[era5_var_names[0]].latitude.values
        lons = ds_sub[era5_var_names[0]].longitude.values

    # Normalise longitudes to −180 to 180 (ARCO uses −180→180 natively but
    # locally-downloaded zarrs may use 0→360 depending on the source chunk).
    if lons.max() > 180:
        lons = lons.copy()
        lons[lons > 180] -= 360
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        values = values[:, :, sort_idx]

    return values, lats, lons, n_samples


# ---------------------------------------------------------------------------
# Distribution computation
# ---------------------------------------------------------------------------

N_HISTOGRAM_BINS = 50


def compute_distribution(
    ds: Any,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    update_progress=None,
) -> dict:
    """
    Compute per-cell value distribution statistics at 1-degree spatial resolution.

    Returns a dict with:
      - meta: dataset metadata (variable, unit, n_samples, resolution)
      - grid: dict keyed by "{int_lat}_{int_lon}" containing per-cell statistics

    The 1-degree resolution (every 4th ERA5 cell) keeps the response to ~5-15 MB
    gzipped, suitable for a single client-side fetch. The frontend snaps click
    coordinates to the nearest 1-degree grid point for lookup.

    n_samples is the number of ERA5 timesteps that matched the filters — surface
    this in the UI so users understand the statistical confidence of the distribution.
    """
    if update_progress: update_progress(0.02)
    ds_sub, era5_var_names, config = _subset_da(
        ds, variable, start_year, end_year, start_month, end_month, hour_utc
    )

    # Downsample to ~1-degree resolution.
    # Stride is computed from the actual zarr resolution so this works for both
    # the native 0.25° ARCO ERA5 grid and the 2° local sample zarr.
    lat_vals = ds_sub[era5_var_names[0]].latitude.values
    lat_spacing = abs(float(lat_vals[1]) - float(lat_vals[0])) if len(lat_vals) > 1 else 0.25
    stride = max(1, round(1.0 / lat_spacing))
    ds_sub = ds_sub.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )

    if update_progress: update_progress(0.10)

    values, lats, lons, n_samples = _load_values(ds_sub, variable, era5_var_names, config)
    # values shape: (n_times, n_lats, n_lons)

    if update_progress: update_progress(0.60)

    vmin, vmax = _SCALE_RANGES[variable]
    bin_edges = np.linspace(vmin, vmax, N_HISTOGRAM_BINS + 1)

    # Compute percentiles and histograms across the time axis
    # percentiles shape: (7, n_lats, n_lons)
    percs = np.nanpercentile(values, [5, 10, 25, 50, 75, 90, 95], axis=0)

    if update_progress: update_progress(0.80)

    grid = {}
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            cell_vals = values[:, i, j]
            valid = cell_vals[~np.isnan(cell_vals)]
            if len(valid) == 0:
                continue

            counts, _ = np.histogram(valid, bins=bin_edges)
            cell_percs = percs[:, i, j].tolist()

            # Key format: integer lat/lon for compact keys and easy client-side lookup
            key = f"{round(float(lat))}_{round(float(lon))}"
            grid[key] = {
                "p": [round(float(x), 2) for x in cell_percs],  # p5,p10,p25,p50,p75,p90,p95
                "h": [round(float(x), 2) for x in bin_edges],   # bin edges (N_BINS+1 values)
                "c": counts.tolist(),                             # counts per bin
                "n": int(n_samples),
            }

    if update_progress: update_progress(1.0)

    unit_map = {
        "wind_speed":  "kts",   "wind_dir":    "deg",
        "pressure":    "hPa",   "wave_height": "m",
        "precip":      "mm/day","temp":        "degC",
    }

    return {
        "meta": {
            "variable":        variable,
            "unit":            unit_map.get(variable, ""),
            "n_samples":       n_samples,
            "resolution_deg":  1.0,
            "bin_edges":       [round(float(x), 3) for x in bin_edges],
        },
        "grid": grid,
    }


# ---------------------------------------------------------------------------
# Exceedance computation
# ---------------------------------------------------------------------------

def compute_exceedance(
    ds: Any,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    threshold: float,
    update_progress=None,
) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    """
    Compute P(value > threshold) per grid cell.

    Returns (exceedance_2d, lats, lons) where exceedance_2d is a float32 array
    of shape (n_lats, n_lons) with values in [0.0, 1.0].

    Uses the full ERA5 0.25-degree grid (no spatial downsampling) so that
    the exceedance tile renders at full resolution.
    """
    if update_progress: update_progress(0.05)
    ds_sub, era5_var_names, config = _subset_da(
        ds, variable, start_year, end_year, start_month, end_month, hour_utc
    )
    if update_progress: update_progress(0.10)

    values, lats, lons, n_samples = _load_values(ds_sub, variable, era5_var_names, config)
    if update_progress: update_progress(0.75)

    # P(value > threshold) = fraction of timesteps exceeding threshold
    exceedance = np.mean(values > threshold, axis=0).astype(np.float32)
    if update_progress: update_progress(1.0)

    return exceedance, lats, lons


def compute_exceedance_tile_png(
    ds: Any,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    threshold: float,
    z: int,
    x: int,
    y: int,
    update_progress=None,
) -> bytes:
    """
    Render a 256x256 exceedance probability tile as PNG bytes.

    Same geographic subsetting and interpolation as compute_tile_png,
    but the color scale maps probability (0-1) instead of data value.
    """
    import mercantile  # type: ignore

    exceedance, lats, lons = compute_exceedance(
        ds, variable, start_year, end_year, start_month, end_month,
        hour_utc, averaging, threshold, update_progress,
    )

    # Ensure lats ascending for interpolation
    if lats[0] > lats[-1]:
        exceedance = exceedance[::-1, :]
        lats = lats[::-1]

    bounds = mercantile.bounds(mercantile.Tile(x, y, z))
    TILE_SIZE = 256
    tile_lons = np.linspace(bounds.west, bounds.east, TILE_SIZE)
    tile_lats = np.linspace(bounds.north, bounds.south, TILE_SIZE)

    pixel_values = _bilinear_interp(exceedance, lats, lons, tile_lats, tile_lons)
    rgba = _exceedance_to_rgba(pixel_values)
    return _rgba_to_png(rgba)


def make_exceedance_cache_key(
    variable: str,
    threshold: float,
    start_year: int, end_year: int,
    start_month: int, end_month: int,
    hour_utc: str, averaging: str,
    z: int, x: int, y: int,
) -> str:
    """MD5 cache key for an exceedance tile (threshold is part of the key)."""
    canonical = (
        f"exc|{variable}|{threshold:.4f}|{start_year}|{end_year}|"
        f"{start_month}|{end_month}|{hour_utc}|{averaging}|{z}|{x}|{y}"
    )
    return hashlib.md5(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Wind field for particle animation
# ---------------------------------------------------------------------------

def compute_wind_field(
    ds: Any,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    step_deg: int = 2,
) -> dict:
    """
    Compute a downsampled U/V wind field for client-side particle animation.

    Returns a dict with:
      lats   — list of latitudes (N→S, descending)
      lons   — list of longitudes (W→E, ascending)
      u      — flat list (row-major: lat then lon), eastward component in m/s
      v      — flat list, northward component in m/s
      width  — number of longitude points
      height — number of latitude points

    step_deg controls spatial resolution. 2 → 2° grid (91×181 points).
    U/V are in raw m/s (not knots) — the frontend uses them only for
    direction and relative speed, not for display.
    """
    ds_sub = ds[["10m_u_component_of_wind", "10m_v_component_of_wind"]]
    ds_sub = ds_sub.sel(time=slice(f"{start_year}-01-01", f"{end_year}-12-31"))

    time = ds_sub.time
    if start_month <= end_month:
        month_mask = (time.dt.month >= start_month) & (time.dt.month <= end_month)
    else:
        month_mask = (time.dt.month >= start_month) | (time.dt.month <= end_month)
    ds_sub = ds_sub.sel(time=month_mask)

    if hour_utc != "all":
        ds_sub = ds_sub.sel(time=ds_sub.time.dt.hour == int(hour_utc))

    # Compute stride from actual zarr resolution so this works for both
    # the native 0.25° ARCO ERA5 grid and the 2° local sample zarr.
    lat_vals = ds_sub["10m_u_component_of_wind"].latitude.values
    lat_spacing = abs(float(lat_vals[1]) - float(lat_vals[0])) if len(lat_vals) > 1 else 0.25
    stride = max(1, round(step_deg / lat_spacing))

    ds_sub = ds_sub.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )

    u_da = ds_sub["10m_u_component_of_wind"].mean(dim="time").load()
    v_da = ds_sub["10m_v_component_of_wind"].mean(dim="time").load()

    u_arr = u_da.values.astype(np.float32)
    v_arr = v_da.values.astype(np.float32)
    lats = u_da.latitude.values

    # Normalise longitudes to −180 to 180 (ARCO ERA5 uses −180→180 natively,
    # but locally-downloaded zarrs may use 0→360 depending on the source chunk).
    lons = u_da.longitude.values.copy()
    if lons.max() > 180:
        lons[lons > 180] -= 360
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        u_arr = u_arr[:, sort_idx]
        v_arr = v_arr[:, sort_idx]

    return {
        "lats":   [round(float(x), 2) for x in lats],
        "lons":   [round(float(x), 2) for x in lons],
        "u":      [round(float(x), 2) for x in u_arr.ravel()],
        "v":      [round(float(x), 2) for x in v_arr.ravel()],
        "width":  len(lons),
        "height": len(lats),
    }


def compute_scalar_field(
    ds: Any,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
    update_progress=None,
) -> dict:
    """
    Compute a global 1° resolution scalar field (median value per cell).

    Returns a dict ready to JSON-serialise:
      variable, unit, resolution, width, height, la1, lo1,
      values — flat list [height × width], row-major N→S, median in display units.

    Used by the frontend ScalarAnnotations component.
    """
    if update_progress: update_progress(0.02)

    ds_sub, era5_var_names, config = _subset_da(
        ds, variable, start_year, end_year, start_month, end_month, hour_utc
    )
    lat_vals = ds_sub[era5_var_names[0]].latitude.values
    lat_spacing = abs(float(lat_vals[1]) - float(lat_vals[0])) if len(lat_vals) > 1 else 0.25
    stride = max(1, round(1.0 / lat_spacing))
    ds_sub = ds_sub.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )
    if update_progress: update_progress(0.10)

    values, lats, lons, _ = _load_values(ds_sub, variable, era5_var_names, config)
    # values: (n_times, n_lats, n_lons)
    if update_progress: update_progress(0.70)

    median_grid = np.nanmedian(values, axis=0).astype(np.float32)
    # Ensure N→S order (la1=90)
    if lats[0] < lats[-1]:
        median_grid = median_grid[::-1, :]
        lats = lats[::-1]

    if update_progress: update_progress(0.95)

    unit_map = {
        "wind_speed": "kts", "wind_dir": "deg", "pressure": "hPa",
        "wave_height": "m",  "precip": "mm/day", "temp": "°C",
    }
    flat = [round(float(v), 2) if not np.isnan(v) else None for v in median_grid.ravel()]

    if update_progress: update_progress(1.0)
    return {
        "variable":   variable,
        "unit":       unit_map.get(variable, ""),
        "resolution": 1.0,
        "width":      int(median_grid.shape[1]),
        "height":     int(median_grid.shape[0]),
        "la1":        float(lats[0]),
        "lo1":        float(lons[0]),
        "values":     flat,
    }


def make_field_cache_key(
    variable: str,
    start_year: int, end_year: int,
    start_month: int, end_month: int,
    hour_utc: str, averaging: str,
) -> str:
    canonical = f"field|{variable}|{start_year}|{end_year}|{start_month}|{end_month}|{hour_utc}|{averaging}"
    return hashlib.md5(canonical.encode()).hexdigest()


def make_distribution_cache_key(
    variable: str,
    start_year: int, end_year: int,
    start_month: int, end_month: int,
    hour_utc: str, averaging: str,
) -> str:
    """MD5 cache key for a distribution JSON blob."""
    canonical = (
        f"dist|{variable}|{start_year}|{end_year}|"
        f"{start_month}|{end_month}|{hour_utc}|{averaging}"
    )
    return hashlib.md5(canonical.encode()).hexdigest()
