"""
Tile generator: ERA5 xarray DataArray → XYZ PNG tiles.

Converts a 2-D (lat × lon) DataArray of averaged ERA5 data into a tree of
256×256 PNG tiles in the standard Web Mercator XYZ scheme used by Mapbox.

Zoom levels 0–6 are generated (zoom 6 = 64×64 tiles, ~156 km/px at equator).
Higher zoom levels are not useful for ERA5 data at its native ~31 km resolution.

Dependencies: mercantile, rasterio, numpy, Pillow
"""

import io
import logging
import math
from pathlib import Path
from typing import Callable, Optional

import mercantile
import numpy as np
from PIL import Image

from color_scales import value_to_rgba, SCALE_RANGES

logger = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_ZOOM = 0
MAX_ZOOM = 6


def generate_tiles(
    data: "np.ndarray",
    lats: "np.ndarray",
    lons: "np.ndarray",
    variable: str,
    output_dir: Path,
    zoom_levels: range = range(MIN_ZOOM, MAX_ZOOM + 1),
    on_tile_written: Optional[Callable[[int, int, int], None]] = None,
    skip_existing: bool = True,
) -> int:
    """
    Generate XYZ tiles from a 2-D lat/lon data array and write them to disk.

    Args:
        data:          2-D numpy array shaped (len(lats), len(lons)).
                       Values should be in the variable's native unit (e.g. m/s
                       for wind components — convert to knots before calling).
        lats:          1-D array of latitude values, south→north or north→south.
        lons:          1-D array of longitude values, west→east (-180 to 180).
        variable:      ERA5 variable ID (must be in color_scales.SCALES).
        output_dir:    Directory where tile PNGs will be written.
                       Tile path: output_dir/{z}/{x}/{y}.png
        zoom_levels:   Which zoom levels to generate (default 0–6).
        on_tile_written: Optional callback(z, x, y) called after each tile write.
        skip_existing: If True, skip tiles whose .png file already exists.
                       Allows resuming an interrupted run.

    Returns:
        Number of tiles written (skipped tiles not counted).
    """
    # Ensure lats are in ascending order for interpolation
    if lats[0] > lats[-1]:
        data = data[::-1, :]
        lats = lats[::-1]

    # Unwrap longitudes to -180..180 if stored as 0..360
    if lons.max() > 180:
        shift = lons > 180
        lons = lons.copy()
        lons[shift] -= 360
        # Re-sort so lons are monotonically increasing
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        data = data[:, sort_idx]

    tiles_written = 0

    for zoom in zoom_levels:
        logger.info("Generating zoom %d tiles…", zoom)
        # All tiles at this zoom level that intersect the full globe
        world_tiles = list(mercantile.tiles(-180, -85.051129, 180, 85.051129, zooms=zoom))

        for tile in world_tiles:
            z, x, y = tile.z, tile.x, tile.y
            tile_path = output_dir / str(z) / str(x) / f"{y}.png"

            if skip_existing and tile_path.exists():
                continue

            # Bounding box of this tile in geographic coordinates
            bounds = mercantile.bounds(tile)  # BoundingBox(west, south, east, north)

            # Sample points: create a grid of TILE_SIZE×TILE_SIZE lat/lon
            # coordinates covering the tile's bounding box
            tile_lons = np.linspace(bounds.west, bounds.east, TILE_SIZE)
            tile_lats = np.linspace(bounds.north, bounds.south, TILE_SIZE)  # N→S for image rows

            # Bilinear interpolation of data values at the tile grid points
            pixel_values = _interpolate_grid(data, lats, lons, tile_lats, tile_lons)

            # Convert values to RGBA pixels
            rgba = _values_to_rgba_image(pixel_values, variable)

            # Write PNG
            tile_path.parent.mkdir(parents=True, exist_ok=True)
            img = Image.fromarray(rgba, mode="RGBA")
            img.save(tile_path, format="PNG", optimize=True)

            tiles_written += 1
            if on_tile_written:
                on_tile_written(z, x, y)

    return tiles_written


def _interpolate_grid(
    data: np.ndarray,
    src_lats: np.ndarray,
    src_lons: np.ndarray,
    query_lats: np.ndarray,
    query_lons: np.ndarray,
) -> np.ndarray:
    """
    Bilinearly interpolate data[src_lats, src_lons] at each point in the
    (query_lats × query_lons) grid.

    Returns a 2-D array shaped (len(query_lats), len(query_lons)).

    Uses numpy for speed — no scipy dependency needed at this resolution.
    """
    # Index arrays: floating-point position in src_lats / src_lons arrays
    lat_idx = np.interp(query_lats, src_lats, np.arange(len(src_lats)))
    lon_idx = np.interp(query_lons, src_lons, np.arange(len(src_lons)))

    lat_lo = np.clip(np.floor(lat_idx).astype(int), 0, len(src_lats) - 2)
    lat_hi = lat_lo + 1
    lon_lo = np.clip(np.floor(lon_idx).astype(int), 0, len(src_lons) - 2)
    lon_hi = lon_lo + 1

    # Fractional parts for interpolation weights
    dlat = (lat_idx - lat_lo)[:, np.newaxis]  # shape (H, 1)
    dlon = (lon_idx - lon_lo)[np.newaxis, :]  # shape (1, W)

    # Four corners of the bilinear cell
    q00 = data[lat_lo[:, np.newaxis], lon_lo[np.newaxis, :]]
    q01 = data[lat_lo[:, np.newaxis], lon_hi[np.newaxis, :]]
    q10 = data[lat_hi[:, np.newaxis], lon_lo[np.newaxis, :]]
    q11 = data[lat_hi[:, np.newaxis], lon_hi[np.newaxis, :]]

    return (
        q00 * (1 - dlat) * (1 - dlon)
        + q01 * (1 - dlat) * dlon
        + q10 * dlat * (1 - dlon)
        + q11 * dlat * dlon
    )


def _values_to_rgba_image(values: np.ndarray, variable: str) -> np.ndarray:
    """
    Convert a 2-D float array to a (H, W, 4) uint8 RGBA array.

    NaN pixels become fully transparent.
    """
    H, W = values.shape
    rgba = np.zeros((H, W, 4), dtype=np.uint8)

    # Vectorising value_to_rgba would require rewriting it; at zoom 0–6 the
    # total number of pixels is manageable (~1M), so a loop is acceptable.
    # TODO: for production, replace with a fully vectorised numpy implementation.
    flat = values.ravel()
    flat_rgba = np.array([value_to_rgba(variable, float(v)) for v in flat], dtype=np.uint8)
    rgba = flat_rgba.reshape(H, W, 4)

    return rgba
