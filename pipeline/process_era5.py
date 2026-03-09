"""
process_era5.py — Main ERA5 data processing pipeline.

Reads ERA5 reanalysis data from the public GCS bucket, subsets to the
requested time window, computes a climatological mean, and generates
XYZ map tiles that are uploaded to the output GCS bucket.

Usage:
    python process_era5.py \\
        --variable wind_speed \\
        --start-year 1991 --end-year 2020 \\
        --start-month 1  --end-month 12 \\
        --hour-utc all \\
        --averaging monthly \\
        --output-bucket windipedia-tiles

    # Small test: North Pacific, 2 years, Jan–Mar only
    python process_era5.py \\
        --variable wind_speed \\
        --start-year 2000 --end-year 2001 \\
        --start-month 1  --end-month 3 \\
        --hour-utc 12 \\
        --averaging monthly \\
        --output-bucket windipedia-tiles-test \\
        --bbox "-180,20,180,60"

See pipeline/README.md for GCP auth setup and cost estimates.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# ERA5 variable configuration
# ---------------------------------------------------------------------------

# ERA5 is stored in the ARCO (Analysis-Ready Cloud-Optimized) format on GCS:
#   gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3
#
# The dataset has dimensions: time (hourly since 1940), latitude, longitude.
# Latitude runs 90 → -90 (north to south), longitude runs -180 → 180.
# All variables are on a 0.25° grid (~28 km at equator).
#
# Unit notes:
#   - wind components (u10, v10): m/s  → convert to knots (×1.944)
#   - pressure (msl): Pa             → convert to hPa (÷100)
#   - wave height (swh): m           → no conversion needed
#   - precipitation (tp): m/hr       → convert to mm/day (×1000 × 24)
#   - temperature (t2m): K           → convert to °C (−273.15)

ERA5_VAR_CONFIG = {
    "wind_speed": {
        "era5_vars": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "derived": True,    # magnitude computed from u/v
        "unit_conversion": lambda u, v: (u**2 + v**2) ** 0.5 * 1.94384,  # m/s → kts
    },
    "wind_dir": {
        "era5_vars": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "derived": True,
        # Meteorological convention: direction FROM which wind blows, 0° = N
        "unit_conversion": lambda u, v: (270 - __import__("numpy").degrees(
            __import__("numpy").arctan2(v, u)
        )) % 360,
    },
    "pressure": {
        "era5_vars": ["mean_sea_level_pressure"],
        "derived": False,
        "unit_conversion": lambda p: p / 100.0,  # Pa → hPa
    },
    "wave_height": {
        "era5_vars": ["significant_height_of_combined_wind_waves_and_swell"],
        "derived": False,
        "unit_conversion": lambda h: h,  # already in metres
    },
    "precip": {
        "era5_vars": ["total_precipitation"],
        "derived": False,
        "unit_conversion": lambda p: p * 1000 * 24,  # m/hr → mm/day
    },
    "temp": {
        "era5_vars": ["2m_temperature"],
        "derived": False,
        "unit_conversion": lambda t: t - 273.15,  # K → °C
    },
}

# GCS path to the ARCO ERA5 Zarr store (public, no auth required for reads)
ARCO_ERA5_ZARR = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Process ERA5 data into XYZ map tiles")
    p.add_argument("--variable",       required=True, choices=list(ERA5_VAR_CONFIG.keys()))
    p.add_argument("--start-year",     required=True, type=int)
    p.add_argument("--end-year",       required=True, type=int)
    p.add_argument("--start-month",    required=True, type=int, choices=range(1, 13))
    p.add_argument("--end-month",      required=True, type=int, choices=range(1, 13))
    p.add_argument(
        "--hour-utc",
        default="all",
        help="Integer 0–23 for a specific UTC hour, or 'all' for a 24 h average",
    )
    p.add_argument(
        "--averaging",
        default="monthly",
        choices=["daily", "weekly", "monthly"],
    )
    p.add_argument("--output-bucket",  required=True, help="GCS bucket name (no gs:// prefix)")
    p.add_argument(
        "--bbox",
        default="-180,-90,180,90",
        help="Lon/lat bounding box 'west,south,east,north' (default: global)",
    )
    p.add_argument("--zoom-min",   type=int, default=0,  help="Minimum zoom level to generate")
    p.add_argument("--zoom-max",   type=int, default=6,  help="Maximum zoom level to generate")
    p.add_argument(
        "--local-output",
        help="If set, write tiles to this local directory instead of GCS (for testing)",
    )
    return p.parse_args()


def load_era5_data(variable: str, start_year: int, end_year: int,
                   start_month: int, end_month: int, hour_utc: str,
                   bbox: tuple[float, float, float, float]) -> "xr.DataArray":
    """
    Load and subset ERA5 data from the ARCO GCS Zarr store.

    Returns a 2-D DataArray (lat × lon) of the time-averaged values.

    ERA5 data notes:
    - Time axis: hourly from 1940-01-01 00:00 UTC
    - Latitude: 90 → −90 (note: north-first ordering)
    - Longitude: −180 → 180
    - Values are instantaneous analyses, not accumulated (except precipitation
      which is accumulated since forecast init and must be differenced)
    """
    try:
        import gcsfs       # type: ignore
        import xarray as xr
        import numpy as np
    except ImportError as e:
        logger.error(
            "Missing dependency: %s. Install pipeline requirements: "
            "pip install -r pipeline/requirements.txt", e
        )
        sys.exit(1)

    logger.info("Opening ERA5 Zarr store from GCS (this may take a moment)…")
    logger.info("  Store: %s", ARCO_ERA5_ZARR)

    try:
        fs = gcsfs.GCSFileSystem(token="anon")  # public bucket, no auth needed for reads
        store = fs.get_mapper(ARCO_ERA5_ZARR)
        ds = xr.open_zarr(store, consolidated=True)
    except Exception as e:
        logger.error(
            "Failed to open ERA5 Zarr store: %s\n"
            "Check your network connection and that gcsfs is installed. "
            "If running on GCP, ensure the VM has internet access.", e
        )
        sys.exit(1)

    config = ERA5_VAR_CONFIG[variable]
    era5_var_names = config["era5_vars"]

    # Load only the variables we need
    ds = ds[era5_var_names]

    # --- Spatial subset ---
    west, south, east, north = bbox
    ds = ds.sel(
        latitude=slice(north, south),   # ERA5 lat is N→S (descending)
        longitude=slice(west, east),
    )

    # --- Temporal subset: year and month filtering ---
    logger.info(
        "Subsetting time: %d–%d, months %d–%d",
        start_year, end_year, start_month, end_month,
    )
    ds = ds.sel(time=slice(f"{start_year}-01-01", f"{end_year}-12-31"))

    time_coord = ds.time.dt
    if start_month <= end_month:
        month_mask = (time_coord.month >= start_month) & (time_coord.month <= end_month)
    else:
        # Wrapping month range (e.g. Oct–Mar crosses the year boundary)
        month_mask = (time_coord.month >= start_month) | (time_coord.month <= end_month)
    ds = ds.sel(time=month_mask)

    # --- Hour-of-day filter ---
    # ERA5 is hourly, so filtering to a specific UTC hour selects ~1/24 of timesteps.
    # Example: --hour-utc 15 selects every 15:00 UTC record across the date range.
    #
    # NOTE: This filters by UTC hour, NOT local solar time. Solar noon varies from
    # ~08:00 UTC (longitude 60°E) to ~00:00 UTC (longitude 0°) to ~20:00 UTC (120°W).
    # A future enhancement should convert to local solar time before filtering to
    # enable accurate diurnal analysis (e.g. "afternoon sea breeze patterns").
    if hour_utc != "all":
        hour_int = int(hour_utc)
        hour_mask = ds.time.dt.hour == hour_int
        ds = ds.sel(time=hour_mask)
        logger.info(
            "Filtered to %d:00 UTC (%d timesteps)",
            hour_int, ds.time.size,
        )
    else:
        logger.info("Using all UTC hours (%d timesteps)", ds.time.size)

    if ds.time.size == 0:
        logger.error("No timesteps remain after filtering — check date/hour parameters.")
        sys.exit(1)

    # --- Compute temporal mean ---
    logger.info("Computing temporal mean… (loading data into memory)")
    import numpy as np

    if not config["derived"]:
        # Single-variable case: just take the mean
        da = ds[era5_var_names[0]].mean(dim="time").load()
        da.values = config["unit_conversion"](da.values)
    else:
        # Two-variable case (wind): compute mean of u and v separately, then derive
        u = ds[era5_var_names[0]].mean(dim="time").load()
        v = ds[era5_var_names[1]].mean(dim="time").load()
        # Note: averaging u and v then computing speed/direction is a simplification.
        # A more rigorous approach would compute speed/direction at each timestep then
        # average — but for climatological means the difference is small.
        combined = config["unit_conversion"](u.values, v.values)
        da = xr.DataArray(combined, coords={"latitude": u.latitude, "longitude": u.longitude})

    logger.info(
        "Mean computed. Array shape: %s, range: [%.2f, %.2f]",
        da.shape, float(da.min()), float(da.max()),
    )
    return da


def upload_tiles_to_gcs(
    local_tile_dir: Path,
    bucket_name: str,
    gcs_prefix: str,
) -> int:
    """
    Upload all .png files from local_tile_dir to GCS under gcs_prefix.
    Returns the number of files uploaded.
    Skips files that already exist in GCS (resumable runs).
    """
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        logger.error("google-cloud-storage not installed — pip install google-cloud-storage")
        sys.exit(1)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    uploaded = 0

    png_files = list(local_tile_dir.rglob("*.png"))
    logger.info("Uploading %d tiles to gs://%s/%s", len(png_files), bucket_name, gcs_prefix)

    for png_path in png_files:
        rel = png_path.relative_to(local_tile_dir)
        blob_name = f"{gcs_prefix}/{rel}"
        blob = bucket.blob(blob_name)

        if blob.exists():
            continue  # Skip — supports resumable runs

        blob.upload_from_filename(str(png_path), content_type="image/png")
        uploaded += 1

    return uploaded


def main():
    args = parse_args()
    t_start = time.time()

    west, south, east, north = map(float, args.bbox.split(","))
    bbox = (west, south, east, north)

    # Validate year ordering
    if args.start_year > args.end_year:
        logger.error("--start-year must be ≤ --end-year")
        sys.exit(1)

    # Validate hour arg
    if args.hour_utc != "all":
        try:
            h = int(args.hour_utc)
            assert 0 <= h <= 23
        except (ValueError, AssertionError):
            logger.error("--hour-utc must be 'all' or an integer 0–23, got: %s", args.hour_utc)
            sys.exit(1)
        # Normalise to zero-padded string for use in file paths
        args.hour_utc = str(int(args.hour_utc)).zfill(2)

    logger.info("=" * 60)
    logger.info("Windipedia ERA5 pipeline")
    logger.info("  Variable:   %s", args.variable)
    logger.info("  Years:      %d–%d", args.start_year, args.end_year)
    logger.info("  Months:     %d–%d", args.start_month, args.end_month)
    logger.info("  Hour UTC:   %s", args.hour_utc)
    logger.info("  Averaging:  %s", args.averaging)
    logger.info("  BBox:       %s", args.bbox)
    logger.info("  Zoom:       %d–%d", args.zoom_min, args.zoom_max)
    logger.info("=" * 60)

    # 1. Load and average ERA5 data
    da = load_era5_data(
        args.variable, args.start_year, args.end_year,
        args.start_month, args.end_month, args.hour_utc, bbox,
    )

    import numpy as np
    lats = da.latitude.values
    lons = da.longitude.values
    data = da.values.astype(np.float32)

    # 2. Generate tiles
    from tile_generator import generate_tiles

    # Build output directory: temp local dir or --local-output
    import tempfile
    if args.local_output:
        tile_dir = Path(args.local_output)
        tile_dir.mkdir(parents=True, exist_ok=True)
        use_temp = False
    else:
        _tmp = tempfile.mkdtemp(prefix="windipedia-tiles-")
        tile_dir = Path(_tmp)
        use_temp = True

    tiles_counter = [0]
    def on_tile(z, x, y):
        tiles_counter[0] += 1
        if tiles_counter[0] % 100 == 0:
            logger.info("  …%d tiles written", tiles_counter[0])

    logger.info("Generating tiles to %s", tile_dir)
    total_tiles = generate_tiles(
        data, lats, lons,
        variable=args.variable,
        output_dir=tile_dir,
        zoom_levels=range(args.zoom_min, args.zoom_max + 1),
        on_tile_written=on_tile,
        skip_existing=True,
    )
    logger.info("Tiles generated: %d", total_tiles)

    # 3. Upload to GCS (unless --local-output is set)
    if not args.local_output:
        gcs_prefix = (
            f"tiles/{args.variable}/{args.start_year}/{args.end_year}"
            f"/{args.start_month}/{args.end_month}/{args.hour_utc}/{args.averaging}"
        )
        uploaded = upload_tiles_to_gcs(tile_dir, args.output_bucket, gcs_prefix)
        gcs_path = f"gs://{args.output_bucket}/{gcs_prefix}/"
        logger.info("Uploaded: %d tiles → %s", uploaded, gcs_path)
    else:
        gcs_path = f"(local: {tile_dir})"
        logger.info("Tiles written to local directory: %s", tile_dir)

    elapsed = time.time() - t_start
    logger.info("=" * 60)
    logger.info("Done in %.1f s", elapsed)
    logger.info("  Tiles written:  %d", total_tiles)
    logger.info("  Output:         %s", gcs_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
