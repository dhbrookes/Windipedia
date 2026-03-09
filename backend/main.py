"""
Windipedia FastAPI backend.

Responsibilities:
  - Open the ERA5 Zarr store once at startup and keep it in app.state
  - Serve on-demand ERA5 raster tiles via /tiles/
  - GCS tile cache: cache hits are fast; misses trigger Zarr computation
  - Job status tracking via /api/status/{job_id}
  - Cache pre-warming via POST /api/cache/warm

Run locally:
    uvicorn main:app --reload --port 8000

With Docker:
    docker build -t windipedia-backend .
    docker run -p 8000:8000 --env-file ../.env windipedia-backend
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config import get_settings
from core.era5 import open_zarr_store, compute_tile_png, make_cache_key, make_param_key, create_mock_dataset
from core.gcs import put_cached_tile
from routers import tiles, distributions, wind, field

logging.basicConfig(
    level=logging.DEBUG if os.getenv("ENVIRONMENT", "development") == "development" else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the ERA5 Zarr store at startup; initialise shared job-tracking dict."""
    app.state.jobs = {}   # {job_id: {status, progress, tiles_done}}
    app.state.ds = None

    mock_era5 = os.getenv("MOCK_ERA5", "false").lower() == "true"
    if mock_era5:
        app.state.ds = create_mock_dataset()
        logger.warning(
            "MOCK_ERA5=true — using synthetic dataset. "
            "All tiles/distributions use fake data. Do NOT use in production."
        )
    elif not settings.use_placeholder_tiles:
        try:
            loop = asyncio.get_event_loop()
            app.state.ds = await loop.run_in_executor(
                None, open_zarr_store, settings.era5_zarr_path
            )
            logger.info("ERA5 Zarr store ready")
        except Exception as exc:
            logger.error(
                "Failed to open ERA5 Zarr store: %s\n"
                "Set USE_PLACEHOLDER_TILES=true to run without GCP access.", exc
            )
    else:
        logger.info("USE_PLACEHOLDER_TILES=true - skipping Zarr store open")

    yield
    logger.info("Backend shutting down")


app = FastAPI(
    title="Windipedia API",
    description="On-demand ERA5 climatological raster tiles.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(tiles.router, prefix="/tiles", tags=["tiles"])
app.include_router(distributions.router, prefix="/api", tags=["distributions"])
app.include_router(wind.router, prefix="/api", tags=["wind"])
app.include_router(field.router, prefix="/api", tags=["field"])


# ---------------------------------------------------------------------------
# Ops endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe. Returns zarr_ready=true once the Zarr store has opened."""
    return {
        "status": "ok",
        "version": app.version,
        "zarr_ready": app.state.ds is not None,
        "placeholder_mode": settings.use_placeholder_tiles,
    }


@app.get("/metadata", tags=["ops"])
async def metadata():
    """Returns supported variables and parameter constraints for the frontend."""
    return {
        "variables": [
            {"id": "wind_speed",   "label": "Wind Speed",              "unit": "kts"},
            {"id": "wind_dir",     "label": "Wind Direction",          "unit": "deg"},
            {"id": "pressure",     "label": "Air Pressure",            "unit": "hPa"},
            {"id": "wave_height",  "label": "Significant Wave Height", "unit": "m"},
            {"id": "precip",       "label": "Precipitation",           "unit": "mm/day"},
            {"id": "temp",         "label": "Temperature",             "unit": "degC"},
        ],
        "year_range": {"min": 1940, "max": 2024},
        "zoom_range":  {"min": 0,    "max": 5},
        "tile_url_template": (
            "/tiles/{variable}/{start_year}/{end_year}"
            "/{start_month}/{end_month}/{hour_utc}/{averaging}/{z}/{x}/{y}.png"
        ),
    }


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

@app.get("/api/status/{job_id}", tags=["ops"])
async def get_status(job_id: str):
    """
    Poll computation progress for an active tile job.

    Response while running:   {status: "computing", progress: 0.6, tiles_done: 3}
    Response when complete:   {status: "not_found"}

    The job_id for a parameter set can be computed client-side with:
      MD5(variable|start_year|end_year|start_month|end_month|hour_utc|averaging)[:12]

    The frontend can use this to show "Computing averages..." when tiles are slow.
    """
    job = app.state.jobs.get(job_id)
    if job is None:
        return {"status": "not_found"}
    return {"status": "computing", **job}


# ---------------------------------------------------------------------------
# Cache warm
# ---------------------------------------------------------------------------

class WarmRequest(BaseModel):
    variable: str
    start_year: int
    end_year: int
    start_month: int
    end_month: int
    hour_utc: str = "all"
    averaging: str = "monthly"


@app.post("/api/cache/warm", tags=["ops"], status_code=202)
async def warm_cache(req: WarmRequest):
    """
    Pre-warm the GCS tile cache for zoom levels 0-2 (21 tiles total).

    Returns immediately with a job_id; computation runs in the background.
    Poll /api/status/{job_id} for progress.

    Useful to call for popular parameter combinations (e.g. default view on load)
    so that the first real user request hits the cache.
    """
    if app.state.ds is None:
        raise HTTPException(503, "ERA5 Zarr store not available (start without USE_PLACEHOLDER_TILES)")

    job_id = make_param_key(
        req.variable, req.start_year, req.end_year,
        req.start_month, req.end_month, req.hour_utc, req.averaging,
    )

    if job_id in app.state.jobs:
        return {"job_id": job_id, "message": "Already warming"}

    app.state.jobs[job_id] = {"status": "computing", "progress": 0.0, "tiles_done": 0}

    async def _warm():
        try:
            import mercantile  # type: ignore
        except ImportError:
            logger.error("mercantile not installed — run pip install mercantile")
            app.state.jobs.pop(job_id, None)
            return

        loop = asyncio.get_event_loop()
        tiles_list = list(mercantile.tiles(-180, -85.05, 180, 85.05, zooms=range(3)))
        total = len(tiles_list)

        for i, tile in enumerate(tiles_list):
            cache_key = make_cache_key(
                req.variable, req.start_year, req.end_year,
                req.start_month, req.end_month, req.hour_utc, req.averaging,
                tile.z, tile.x, tile.y,
            )
            try:
                png = await loop.run_in_executor(
                    None,
                    partial(
                        compute_tile_png,
                        app.state.ds, req.variable,
                        req.start_year, req.end_year,
                        req.start_month, req.end_month,
                        req.hour_utc, req.averaging,
                        tile.z, tile.x, tile.y,
                        None,
                    ),
                )
                await loop.run_in_executor(
                    None, put_cached_tile, settings.tile_cache_bucket, cache_key, png
                )
            except Exception as exc:
                logger.warning("Warm failed for tile z=%d/%d/%d: %s", tile.z, tile.x, tile.y, exc)

            if job_id in app.state.jobs:
                app.state.jobs[job_id]["progress"] = round((i + 1) / total, 2)
                app.state.jobs[job_id]["tiles_done"] = i + 1

        app.state.jobs.pop(job_id, None)
        logger.info("Cache warm complete: %s", job_id)

    asyncio.create_task(_warm())
    return {"job_id": job_id, "message": "Cache warming started for zoom 0-2 (21 tiles)"}
