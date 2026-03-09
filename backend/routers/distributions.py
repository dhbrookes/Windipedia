"""
Distribution and exceedance endpoints.

GET /api/distribution/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}
  Returns per-cell value distribution statistics (percentiles + histogram bins)
  at 1-degree resolution for the given parameter set. Fetched once per session
  and cached client-side; the frontend does individual cell lookups without
  additional API calls.

GET /api/tiles/exceedance/{variable}/{threshold}/{start_year}/.../{z}/{x}/{y}.png
  Returns a 256x256 PNG tile colored by P(value > threshold).
  Same caching pattern as the main tile endpoint.
"""

import asyncio
import base64
import logging
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response, JSONResponse

from core.config import Settings, get_settings
from core.era5 import (
    compute_distribution, compute_exceedance_tile_png,
    make_distribution_cache_key, make_exceedance_cache_key,
    ERA5_VAR_CONFIG,
)
from core.gcs import (
    get_cached_distribution, put_cached_distribution,
    get_cached_tile, put_cached_tile,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_TRANSPARENT_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TRANSPARENT_PNG = base64.b64decode(_TRANSPARENT_PNG_B64)

VALID_VARIABLES = set(ERA5_VAR_CONFIG.keys())
VALID_AVERAGING = {"daily", "weekly", "monthly"}
MAX_ZOOM = 5

# Threshold range per variable (for validation)
THRESHOLD_RANGES = {
    "wind_speed":  (0, 100),
    "wind_dir":    (0, 360),
    "pressure":    (900, 1060),
    "wave_height": (0, 20),
    "precip":      (0, 100),
    "temp":        (-60, 60),
}


def _validate_params(variable, averaging, start_year, end_year, hour_utc):
    if variable not in VALID_VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'")
    if averaging not in VALID_AVERAGING:
        raise HTTPException(422, f"Unknown averaging '{averaging}'")
    if start_year > end_year:
        raise HTTPException(422, "start_year must be <= end_year")
    if hour_utc != "all":
        try:
            h = int(hour_utc)
            assert 0 <= h <= 23
        except (ValueError, AssertionError):
            raise HTTPException(422, "hour_utc must be 'all' or a zero-padded integer 00-23")


@router.get(
    "/distribution/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}",
    summary="Fetch per-cell value distribution statistics",
    description=(
        "Returns percentiles (p5,p10,p25,p50,p75,p90,p95) and histogram bin counts "
        "for every grid cell at 1-degree resolution. Fetch once per parameter set; "
        "the response is cached in GCS and served gzip-compressed. "
        "Client-side lookup: snap click lat/lon to nearest integer degree."
    ),
)
async def get_distribution(
    request: Request,
    variable: str = Path(...),
    start_year: int = Path(..., ge=1940, le=2024),
    end_year: int = Path(..., ge=1940, le=2024),
    start_month: int = Path(..., ge=1, le=12),
    end_month: int = Path(..., ge=1, le=12),
    hour_utc: str = Path(...),
    averaging: str = Path(...),
    settings: Settings = Depends(get_settings),
):
    _validate_params(variable, averaging, start_year, end_year, hour_utc)

    cache_key = make_distribution_cache_key(
        variable, start_year, end_year, start_month, end_month, hour_utc, averaging
    )

    # Check GCS cache
    loop = asyncio.get_event_loop()
    cached = await loop.run_in_executor(
        None, get_cached_distribution, settings.tile_cache_bucket, cache_key
    )
    if cached is not None:
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        raise HTTPException(503, "ERA5 Zarr store not available")

    try:
        data = await loop.run_in_executor(
            None,
            partial(
                compute_distribution,
                ds, variable,
                start_year, end_year, start_month, end_month,
                hour_utc, averaging,
                None,
            ),
        )
    except Exception as exc:
        logger.exception("Distribution compute failed for %s: %s", variable, exc)
        raise HTTPException(500, "Distribution computation failed")

    asyncio.create_task(
        loop.run_in_executor(
            None, put_cached_distribution, settings.tile_cache_bucket, cache_key, data
        )
    )

    return JSONResponse(content=data, headers={"X-Cache": "MISS"})


@router.get(
    "/tiles/exceedance/{variable}/{threshold}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}/{z}/{x}/{y}.png",
    response_class=Response,
    summary="Exceedance probability tile",
    description=(
        "Returns a 256x256 PNG tile colored by P(value > threshold). "
        "threshold is in the variable's display unit (e.g. knots for wind_speed). "
        "Color scale: transparent (0%) through yellow/orange to deep red/black (100%)."
    ),
)
async def get_exceedance_tile(
    request: Request,
    variable: str = Path(...),
    threshold: float = Path(...),
    start_year: int = Path(..., ge=1940, le=2024),
    end_year: int = Path(..., ge=1940, le=2024),
    start_month: int = Path(..., ge=1, le=12),
    end_month: int = Path(..., ge=1, le=12),
    hour_utc: str = Path(...),
    averaging: str = Path(...),
    z: int = Path(..., ge=0, le=MAX_ZOOM),
    x: int = Path(..., ge=0),
    y: int = Path(..., ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    _validate_params(variable, averaging, start_year, end_year, hour_utc)

    tmin, tmax = THRESHOLD_RANGES.get(variable, (-1e9, 1e9))
    if not (tmin <= threshold <= tmax):
        raise HTTPException(422, f"threshold {threshold} out of range [{tmin}, {tmax}] for {variable}")

    if settings.use_placeholder_tiles:
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    cache_key = make_exceedance_cache_key(
        variable, threshold,
        start_year, end_year, start_month, end_month,
        hour_utc, averaging, z, x, y,
    )

    loop = asyncio.get_event_loop()
    cached = await loop.run_in_executor(
        None, get_cached_tile, settings.tile_cache_bucket, cache_key
    )
    if cached is not None:
        return Response(content=cached, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400", "X-Cache": "HIT"})

    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    try:
        png_bytes = await loop.run_in_executor(
            None,
            partial(
                compute_exceedance_tile_png,
                ds, variable,
                start_year, end_year, start_month, end_month,
                hour_utc, averaging,
                threshold, z, x, y,
                None,
            ),
        )
    except Exception as exc:
        logger.exception("Exceedance tile failed for %s z=%d/%d/%d: %s", variable, z, x, y, exc)
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    asyncio.create_task(
        loop.run_in_executor(
            None, put_cached_tile, settings.tile_cache_bucket, cache_key, png_bytes
        )
    )

    return Response(
        content=png_bytes, media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Cache": "MISS"},
    )
