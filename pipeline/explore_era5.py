"""
explore_era5.py — Interactive exploration of the ARCO ERA5 Zarr dataset.

Use this script to:
  - Verify your GCS authentication is working
  - Inspect dataset structure, variable names, and date range
  - Run a quick sample computation before deploying the backend

This script replaces the old process_era5.py precompute pipeline.
Tiles are now computed on demand by the backend (see backend/core/era5.py).

Usage:
    python explore_era5.py
    python explore_era5.py --variable wind_speed --year 2020 --month 1

Authentication:
    The ERA5 source bucket is public — no credentials needed for reads.
    To write to your cache bucket: gcloud auth application-default login
"""

import argparse
import sys

# ARCO ERA5 Zarr store path (public GCS bucket, no auth needed for reads)
ZARR_PATH = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"


def check_imports():
    missing = []
    for pkg in ("gcsfs", "xarray", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"ERROR: Missing packages: {', '.join(missing)}")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)


def open_store():
    import gcsfs
    import xarray as xr

    print(f"\nOpening ERA5 Zarr store...")
    print(f"  {ZARR_PATH}\n")

    try:
        fs = gcsfs.GCSFileSystem(token="anon")  # public bucket
        store = fs.get_mapper(ZARR_PATH)
        ds = xr.open_zarr(store, consolidated=True)
        return ds
    except Exception as e:
        print(f"ERROR: Could not open Zarr store: {e}")
        print("\nTroubleshooting:")
        print("  1. Check internet/GCP connectivity")
        print("  2. Make sure gcsfs is installed: pip install gcsfs")
        print("  3. Try from a GCP VM in us-central1 for best performance")
        sys.exit(1)


def print_dataset_info(ds):
    import numpy as np

    print("=" * 60)
    print("ERA5 ARCO Dataset Info")
    print("=" * 60)

    # Dimensions
    print(f"\nDimensions:")
    for dim, size in ds.dims.items():
        print(f"  {dim}: {size}")

    # Coordinates
    print(f"\nCoordinate ranges:")
    if "time" in ds.coords:
        t = ds.time.values
        print(f"  time:      {str(t[0])[:10]} to {str(t[-1])[:10]}  ({len(t):,} hourly steps)")
    if "latitude" in ds.coords:
        lat = ds.latitude.values
        print(f"  latitude:  {lat[-1]:.2f} to {lat[0]:.2f}  ({len(lat)} points, {lat[0]-lat[1]:.2f} deg step)")
        print(f"  NOTE: latitude is stored north->south (descending)")
    if "longitude" in ds.coords:
        lon = ds.longitude.values
        print(f"  longitude: {lon[0]:.2f} to {lon[-1]:.2f}  ({len(lon)} points, {lon[1]-lon[0]:.2f} deg step)")

    # Variables we use
    print(f"\nWindipedia variables (subset of {len(ds.data_vars)} total):")
    vars_we_use = {
        "10m_u_component_of_wind":                        "u10  [m/s] -> knots x1.944",
        "10m_v_component_of_wind":                        "v10  [m/s] -> knots x1.944",
        "mean_sea_level_pressure":                        "msl  [Pa]  -> hPa /100",
        "significant_height_of_combined_wind_waves_and_swell": "swh  [m]   (no conversion)",
        "total_precipitation":                            "tp   [m/hr] -> mm/day x24000",
        "2m_temperature":                                 "t2m  [K]   -> degC -273.15",
    }
    for var, note in vars_we_use.items():
        available = var in ds.data_vars
        status = "OK" if available else "MISSING"
        print(f"  [{status}] {var}")
        if available:
            print(f"         {note}")


def run_sample_computation(ds, variable: str, year: int, month: int):
    import numpy as np

    # Variable config matching backend/core/era5.py ERA5_VAR_CONFIG
    configs = {
        "wind_speed": {
            "vars": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
            "compute": lambda u, v: np.sqrt(u**2 + v**2) * 1.94384,
            "unit": "kts",
        },
        "pressure": {
            "vars": ["mean_sea_level_pressure"],
            "compute": lambda p: p / 100.0,
            "unit": "hPa",
        },
        "temp": {
            "vars": ["2m_temperature"],
            "compute": lambda t: t - 273.15,
            "unit": "degC",
        },
    }

    if variable not in configs:
        print(f"Sample computation not implemented for '{variable}'. Try: wind_speed, pressure, temp")
        return

    cfg = configs[variable]
    print(f"\n{'=' * 60}")
    print(f"Sample: global mean {variable} for {year}-{month:02d}")
    print(f"{'=' * 60}")
    print("Subsetting time range (this downloads ~50-200 MB of Zarr chunks)...")

    ds_sub = ds[cfg["vars"]].sel(
        time=slice(f"{year}-{month:02d}-01", f"{year}-{month:02d}-28")
    )
    ds_sub = ds_sub.sel(time=ds_sub.time.dt.month == month)

    print(f"  Timesteps loaded: {ds_sub.time.size}")
    print("Computing temporal mean...")

    if len(cfg["vars"]) == 2:
        u = ds_sub[cfg["vars"][0]].mean(dim="time").load()
        v = ds_sub[cfg["vars"][1]].mean(dim="time").load()
        result = cfg["compute"](u.values, v.values)
    else:
        da = ds_sub[cfg["vars"][0]].mean(dim="time").load()
        result = cfg["compute"](da.values)

    print(f"\nResult shape: {result.shape}  (lat x lon)")
    print(f"Global stats:")
    print(f"  min:    {np.nanmin(result):.2f} {cfg['unit']}")
    print(f"  max:    {np.nanmax(result):.2f} {cfg['unit']}")
    print(f"  mean:   {np.nanmean(result):.2f} {cfg['unit']}")
    print(f"  median: {np.nanmedian(result):.2f} {cfg['unit']}")
    print(f"\nSample grid (every 10th row/col):")
    print(result[::10, ::10].round(1))
    print(f"\nComputation successful! Backend is ready to serve tiles.")


def main():
    parser = argparse.ArgumentParser(description="Explore ERA5 Zarr dataset")
    parser.add_argument("--variable", default="wind_speed",
                        choices=["wind_speed", "pressure", "temp"],
                        help="Variable for the sample computation")
    parser.add_argument("--year",  type=int, default=2020, help="Year for sample")
    parser.add_argument("--month", type=int, default=1,    help="Month for sample (1-12)")
    parser.add_argument("--info-only", action="store_true",
                        help="Only print dataset info, skip sample computation")
    args = parser.parse_args()

    check_imports()
    ds = open_store()
    print_dataset_info(ds)

    if not args.info_only:
        run_sample_computation(ds, args.variable, args.year, args.month)


if __name__ == "__main__":
    main()
