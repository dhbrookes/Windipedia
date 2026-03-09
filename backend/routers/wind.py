"""
Wind field router — returns U/V grid data for client-side particle animation.

Route: GET /api/wind/field/{start_year}/{end_year}/{start_month}/{end_month}
                             /{hour_utc}/{averaging}

Returns a JSON object with downsampled U/V wind components (m/s) on a regular
lat/lon grid. The frontend uses this to animate flowing particle trails.
The endpoint shares the same time-subsetting logic as the tile router.
"""

import asyncio
import logging
from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.era5 import compute_wind_field

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_AVERAGING = {"daily", "weekly", "monthly"}
MAX_YEAR = 2024
MIN_YEAR = 1940


def _validate(start_year, end_year, start_month, end_month, hour_utc, averaging):
    if start_year > end_year:
        raise HTTPException(422, "start_year must be <= end_year")
    if not (MIN_YEAR <= start_year <= MAX_YEAR and MIN_YEAR <= end_year <= MAX_YEAR):
        raise HTTPException(422, f"Years must be {MIN_YEAR}–{MAX_YEAR}")
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        raise HTTPException(422, "Months must be 1–12")
    if averaging not in VALID_AVERAGING:
        raise HTTPException(422, f"Unknown averaging '{averaging}'")
    if hour_utc != "all":
        try:
            h = int(hour_utc)
            assert 0 <= h <= 23
        except (ValueError, AssertionError):
            raise HTTPException(422, "hour_utc must be 'all' or a zero-padded integer 00–23")


@router.get(
    "/wind/field/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}",
    summary="Wind U/V field for particle animation",
    description=(
        "Returns a downsampled 2° grid of mean U (eastward) and V (northward) "
        "wind components in m/s. Used by the frontend to drive animated flow particles."
    ),
)
async def get_wind_field(
    request: Request,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
) -> JSONResponse:
    _validate(start_year, end_year, start_month, end_month, hour_utc, averaging)

    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        raise HTTPException(503, "ERA5 Zarr store not available")

    loop = asyncio.get_event_loop()
    try:
        field = await loop.run_in_executor(
            None,
            partial(
                compute_wind_field,
                ds,
                start_year, end_year,
                start_month, end_month,
                hour_utc, averaging,
                2,  # step_deg: 2° resolution
            ),
        )
    except Exception as exc:
        logger.exception("Wind field compute failed: %s", exc)
        raise HTTPException(500, "Failed to compute wind field")

    return JSONResponse(content=field, headers={"Cache-Control": "public, max-age=3600"})
