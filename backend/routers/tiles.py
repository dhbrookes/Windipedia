"""
Tile-serving router — on-demand ERA5 computation with GCS caching.

Route: GET /tiles/{variable}/{start_year}/{end_year}/{start_month}/{end_month}
            /{hour_utc}/{averaging}/{z}/{x}/{y}.png

Request flow:
  1. Validate inputs
  2. Check GCS cache (fast path — returns immediately on hit)
  3. Cache miss: run xarray computation in a thread executor (non-blocking)
  4. Upload result to GCS cache as a background task
  5. Return PNG bytes

The Zarr store (ds) is opened once at startup and stored in app.state.ds.
Job progress is tracked in app.state.jobs so the frontend can poll /api/status/{job_id}.

Zoom levels are capped at 5. ERA5 is 0.25 deg resolution (~28 km at equator).
Above zoom 5, tiles would just upscale the same sparse grid with no additional detail.
"""

import asyncio
import base64
import logging
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response

from core.config import Settings, get_settings
from core.era5 import compute_tile_png, make_cache_key, make_param_key, ERA5_VAR_CONFIG
from core.gcs import get_cached_tile, put_cached_tile

logger = logging.getLogger(__name__)
router = APIRouter()

# Minimal 1x1 transparent PNG returned on errors or in placeholder mode
_TRANSPARENT_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TRANSPARENT_PNG = base64.b64decode(_TRANSPARENT_PNG_B64)

VALID_VARIABLES = set(ERA5_VAR_CONFIG.keys())
VALID_AVERAGING = {"daily", "weekly", "monthly"}
MAX_ZOOM = 5  # ERA5 resolution limit — see module docstring


def _validate(variable, averaging, start_year, end_year, hour_utc, z):
    if variable not in VALID_VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'")
    if averaging not in VALID_AVERAGING:
        raise HTTPException(422, f"Unknown averaging '{averaging}'")
    if start_year > end_year:
        raise HTTPException(422, "start_year must be <= end_year")
    if z > MAX_ZOOM:
        raise HTTPException(422, f"Maximum supported zoom is {MAX_ZOOM} (ERA5 resolution limit)")
    if hour_utc != "all":
        try:
            h = int(hour_utc)
            assert 0 <= h <= 23
        except (ValueError, AssertionError):
            raise HTTPException(422, "hour_utc must be 'all' or a zero-padded integer 00-23")


@router.get(
    "/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}/{z}/{x}/{y}.png",
    response_class=Response,
    summary="Fetch a single ERA5 raster tile (computed on demand)",
    description=(
        "Returns a 256x256 PNG tile. "
        "Checks GCS cache first; computes from ERA5 Zarr on a cache miss. "
        "First request for a given parameter set may take 10-60 s depending on time range. "
        "Subsequent requests for the same tile return instantly from cache."
    ),
)
async def get_tile(
    request: Request,
    variable: str = Path(..., description="ERA5 variable ID, e.g. wind_speed"),
    start_year: int = Path(..., ge=1940, le=2024),
    end_year: int = Path(..., ge=1940, le=2024),
    start_month: int = Path(..., ge=1, le=12),
    end_month: int = Path(..., ge=1, le=12),
    hour_utc: str = Path(..., description="'all' or zero-padded hour '00'-'23'"),
    averaging: str = Path(..., description="daily | weekly | monthly"),
    z: int = Path(..., ge=0, le=MAX_ZOOM),
    x: int = Path(..., ge=0),
    y: int = Path(..., ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    _validate(variable, averaging, start_year, end_year, hour_utc, z)

    # --- Placeholder mode: no GCS/xarray needed (for local frontend dev) ---
    if settings.use_placeholder_tiles:
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    cache_key = make_cache_key(
        variable, start_year, end_year, start_month, end_month, hour_utc, averaging, z, x, y
    )

    # --- Fast path: GCS cache hit ---
    loop = asyncio.get_event_loop()
    cached = await loop.run_in_executor(
        None, get_cached_tile, settings.tile_cache_bucket, cache_key
    )
    if cached is not None:
        return Response(
            content=cached,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400", "X-Cache": "HIT"},
        )

    # --- Slow path: compute from Zarr ---
    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        # Backend started without a Zarr store (e.g. GCS auth not configured).
        # Return transparent rather than 500 so the map still renders.
        logger.error("ERA5 Zarr store not initialised (app.state.ds is None)")
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    # Register a job for this parameter set so the frontend can poll /api/status/{job_id}.
    # job_id is keyed on params (not per-tile) so multiple concurrent tile requests
    # for the same query share a single status entry.
    job_id = make_param_key(variable, start_year, end_year, start_month, end_month, hour_utc, averaging)
    jobs: dict = request.app.state.jobs
    if job_id not in jobs:
        jobs[job_id] = {"status": "computing", "progress": 0.0, "tiles_done": 0}

    def update_progress(p: float):
        if job_id in jobs:
            jobs[job_id]["progress"] = round(p, 2)

    try:
        png_bytes = await loop.run_in_executor(
            None,
            partial(
                compute_tile_png,
                ds, variable,
                start_year, end_year, start_month, end_month,
                hour_utc, averaging,
                z, x, y,
                update_progress,
            ),
        )
    except Exception as exc:
        logger.exception("Tile compute failed for %s z=%d/%d/%d: %s", variable, z, x, y, exc)
        jobs.pop(job_id, None)
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    # Increment tile counter; remove job entry when zoom-0 tile completes
    # (zoom 0 = 1 global tile, a reasonable proxy for "done enough to drop the spinner")
    if job_id in jobs:
        jobs[job_id]["tiles_done"] = jobs[job_id].get("tiles_done", 0) + 1
    if z == 0:
        jobs.pop(job_id, None)

    # Upload to GCS cache without blocking the response
    asyncio.create_task(
        loop.run_in_executor(
            None, put_cached_tile, settings.tile_cache_bucket, cache_key, png_bytes
        )
    )

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Cache": "MISS"},
    )
