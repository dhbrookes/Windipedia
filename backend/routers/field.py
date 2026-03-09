"""
Scalar field router — returns a 1° resolution median-value grid for map annotations.

Route: GET /api/field/{variable}/{start_year}/{end_year}/{start_month}/{end_month}
                       /{hour_utc}/{averaging}

Returns a JSON object with the median value at each 1° lat/lon cell, used by the
frontend ScalarAnnotations component to render a staggered grid of value labels.
Significantly cheaper than the full distribution endpoint (no histograms).
"""

import asyncio
import logging
from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.era5 import compute_scalar_field, ERA5_VAR_CONFIG

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_VARIABLES = set(ERA5_VAR_CONFIG.keys())
VALID_AVERAGING = {"daily", "weekly", "monthly"}


def _validate(variable, start_year, end_year, start_month, end_month, hour_utc, averaging):
    if variable not in VALID_VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'")
    if start_year > end_year:
        raise HTTPException(422, "start_year must be <= end_year")
    if not (1940 <= start_year <= 2024 and 1940 <= end_year <= 2024):
        raise HTTPException(422, "Years must be 1940–2024")
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
    "/field/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}",
    summary="Global 1° scalar field (median) for map annotations",
    description=(
        "Returns median value per 1° grid cell for the requested variable and time range. "
        "Used by the frontend ScalarAnnotations component to render a staggered grid of "
        "value labels. Gzip-encoded; typically <100 KB."
    ),
)
async def get_scalar_field(
    request: Request,
    variable: str,
    start_year: int,
    end_year: int,
    start_month: int,
    end_month: int,
    hour_utc: str,
    averaging: str,
) -> JSONResponse:
    _validate(variable, start_year, end_year, start_month, end_month, hour_utc, averaging)

    ds = getattr(request.app.state, "ds", None)
    if ds is None:
        raise HTTPException(503, "ERA5 Zarr store not available")

    loop = asyncio.get_event_loop()
    try:
        field = await loop.run_in_executor(
            None,
            partial(
                compute_scalar_field,
                ds, variable,
                start_year, end_year,
                start_month, end_month,
                hour_utc, averaging,
            ),
        )
    except Exception as exc:
        logger.exception("Scalar field compute failed for %s: %s", variable, exc)
        raise HTTPException(500, "Failed to compute scalar field")

    return JSONResponse(
        content=field,
        headers={"Cache-Control": "public, max-age=86400"},
    )
