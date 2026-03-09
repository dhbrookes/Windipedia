"""
Download a minimal ERA5 snapshot for local development and testing.

Fetches one noon-UTC timestep per month for 6 variables at 2° resolution
from the public ARCO ERA5 Zarr on GCS (no credentials required).

Usage:
    python download_sample.py --year 2020 --output ../era5_sample.zarr

The resulting Zarr store is compatible with the backend's open_zarr_store()
and can be used directly:

    ERA5_ZARR_PATH=../era5_sample.zarr \\
    TILE_CACHE_BUCKET=./local_cache    \\
    uvicorn main:app --port 8000

Approximate sizes:
    Network transfer: ~288 MB  (12 timesteps × 6 vars × full 0.25° grid per chunk)
    On-disk output:   ~50 MB   (after 2° spatial subsampling)
    Duration:         ~5 min   (depends on connection speed and GCS latency)
"""

import argparse
import sys

# ---------------------------------------------------------------------------
# ERA5 variables to download (must match ERA5_VAR_CONFIG keys in backend/core/era5.py)
# ---------------------------------------------------------------------------
ERA5_VARS = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "mean_sea_level_pressure",
    "significant_height_of_combined_wind_waves_and_swell",
    "total_precipitation",
    "2m_temperature",
]

ARCO_ZARR_URL = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"


def download(year: int, resolution_deg: float, output: str) -> None:
    try:
        import gcsfs          # type: ignore
        import xarray as xr   # type: ignore
        import numpy as np    # type: ignore
    except ImportError as e:
        sys.exit(f"Missing dependency: {e}\nRun: pip install gcsfs xarray zarr numpy tqdm")

    try:
        from tqdm import tqdm  # type: ignore
    except ImportError:
        # Fallback: no progress bar
        def tqdm(iterable, **kw):  # type: ignore
            return iterable

    # ERA5 native resolution is 0.25°; compute the stride to achieve resolution_deg
    stride = max(1, round(resolution_deg / 0.25))
    print(f"Opening public ARCO ERA5 Zarr (no credentials required)…")
    fs = gcsfs.GCSFileSystem(token="anon")
    store = fs.get_mapper(ARCO_ZARR_URL)

    import xarray as xr
    ds_full = xr.open_zarr(store, consolidated=True)

    # Build list of target timestamps: noon UTC on the 1st of each month
    import pandas as pd
    timestamps = pd.date_range(f"{year}-01-01 12:00", periods=12, freq="MS") + pd.Timedelta(hours=12)
    # Snap to nearest available ERA5 hour (ERA5 is hourly; these will be exact)
    timestamps = [pd.Timestamp(t) for t in timestamps]

    print(f"Downloading {len(ERA5_VARS)} variables × {len(timestamps)} timesteps at {resolution_deg}° resolution…")

    arrays = {}
    for var in ERA5_VARS:
        if var not in ds_full:
            print(f"  WARNING: '{var}' not found in dataset — skipping")
            continue

        slices = []
        for ts in tqdm(timestamps, desc=var, unit="month"):
            da = ds_full[var].sel(time=ts, method="nearest")
            # Spatially subsample to reduce output size
            da = da.isel(
                latitude=slice(None, None, stride),
                longitude=slice(None, None, stride),
            )
            slices.append(da)

        arrays[var] = xr.concat(slices, dim="time")
        print(f"  {var}: {arrays[var].shape}")

    if not arrays:
        sys.exit("No variables downloaded — aborting.")

    ds_out = xr.Dataset(arrays)

    print(f"\nSaving to {output} …")
    # zarr 2.x always writes zarr v2 format — no extra flag needed.
    ds_out.to_zarr(output, mode="w")

    # Write consolidated metadata so xr.open_zarr can find all variables reliably.
    try:
        import zarr  # type: ignore
        zarr.consolidate_metadata(output)
        print("Consolidated metadata written (.zmetadata)")
    except Exception as e:
        print(f"Note: could not write consolidated metadata: {e}")

    print(f"Done. Variables: {list(ds_out.data_vars)}")
    print(f"      Time range: {ds_out.time.values[0]} → {ds_out.time.values[-1]}")
    print(f"      Grid: {ds_out.dims}")

    # Read-back verification: load one value per variable to confirm the zarr is readable.
    print("\nVerifying read-back …")
    try:
        ds_check = xr.open_zarr(output, consolidated=False)
        for var in ERA5_VARS:
            if var in ds_check:
                val = float(ds_check[var].isel(time=0, latitude=0, longitude=0).values)
                print(f"  ✓ {var}: sample={val:.4f}")
            else:
                print(f"  ✗ {var}: NOT FOUND in saved zarr")
    except Exception as e:
        print(f"  Read-back failed: {e}")

    print(f"\nTo use with the backend:")
    print(f"  ERA5_ZARR_PATH={output} TILE_CACHE_BUCKET=./local_cache uvicorn main:app --port 8000")


def main():
    parser = argparse.ArgumentParser(
        description="Download a minimal ERA5 snapshot for local dev/testing."
    )
    parser.add_argument(
        "--year", type=int, default=2020,
        help="Year to download (one noon timestep per month). Default: 2020",
    )
    parser.add_argument(
        "--resolution-deg", type=float, default=2.0,
        help="Output spatial resolution in degrees. Default: 2.0 (stride=8 from 0.25°)",
    )
    parser.add_argument(
        "--output", type=str, default="./era5_sample.zarr",
        help="Output path for the local Zarr store. Default: ./era5_sample.zarr",
    )
    args = parser.parse_args()

    download(args.year, args.resolution_deg, args.output)


if __name__ == "__main__":
    main()
