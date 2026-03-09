# Windipedia Data Pipeline

ERA5 data exploration and verification scripts.

> **Architecture note**: The original pre-baked tile pipeline has been replaced
> by on-demand computation in the backend (`backend/core/era5.py`). Tiles are
> now computed from the ERA5 Zarr store at request time and cached to GCS.
> This directory contains exploration utilities — not the tile generation code.

---

## What's here

| File | Purpose |
|------|---------|
| `explore_era5.py` | Open the Zarr store, inspect variables, run a sample computation |
| `color_scales.py` | Color scale definitions — kept here as a reference; backend uses its own copy in `backend/core/era5.py` |
| `tile_generator.py` | Tile rendering utilities — kept here for reference; backend inlines this logic |

---

## Authentication

The ERA5 source bucket is **public** — no credentials needed for reads.

To write to the tile cache bucket you need GCP credentials:

```bash
# Option 1: local dev
gcloud auth application-default login

# Option 2: service account key file
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# Option 3: running on GCP — Workload Identity is used automatically
```

---

## Verifying ERA5 access

Before deploying the backend, confirm that the Zarr store is accessible and
your variable names are correct:

```bash
cd pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Print dataset info only (no data downloaded beyond metadata)
python explore_era5.py --info-only

# Print info + run a sample global mean computation (downloads ~100 MB)
python explore_era5.py --variable wind_speed --year 2020 --month 1
python explore_era5.py --variable temp        --year 2019 --month 7
python explore_era5.py --variable pressure    --year 2020 --month 1
```

Expected output:
```
Opening ERA5 Zarr store...
  gs://gcp-public-data-arco-era5/ar/...

ERA5 ARCO Dataset Info
======================
Dimensions:
  time:      741888  (hourly from 1940)
  latitude:  721
  longitude: 1440
...
```

---

## Colocate with the ERA5 bucket

The ERA5 bucket lives in `us-central1`. Run the backend (and this script)
from a GCP VM or Cloud Run instance in the same region to avoid:
- Cross-region egress fees (~$0.08/GB out of us-central1)
- Latency: in-region Zarr reads take ~2-5 s vs ~30-60 s cross-region

Recommended VM for testing: `n2-standard-4` in `us-central1` (~$0.19/hr).

---

## ERA5 data model reference

The ARCO ERA5 Zarr store at `gs://gcp-public-data-arco-era5`:

- **Time**: hourly from 1940-01-01 00:00 UTC to present
- **Latitude**: 721 points, 90 N to 90 S at 0.25 deg spacing (north-first / descending)
- **Longitude**: 1440 points, -180 to 180 at 0.25 deg spacing
- **Variables**: ~140 surface and pressure-level fields

Variables used by Windipedia and their unit conversions:

| ERA5 name | ERA5 unit | Output unit | Conversion |
|-----------|-----------|-------------|------------|
| `10m_u_component_of_wind` | m/s | kts | x1.94384 |
| `10m_v_component_of_wind` | m/s | kts | x1.94384 |
| `mean_sea_level_pressure` | Pa | hPa | /100 |
| `significant_height_of_combined_wind_waves_and_swell` | m | m | none |
| `total_precipitation` | m/hr | mm/day | x24000 |
| `2m_temperature` | K | degC | -273.15 |

---

## Adding a new variable

1. Add an entry to `ERA5_VAR_CONFIG` in `backend/core/era5.py`
2. Add matching color stops to `_COLOR_STOPS` and `_SCALE_RANGES` in `backend/core/era5.py`
3. Add the variable to `VARIABLES` in `frontend/src/constants/variables.js`
4. Add it to `VALID_VARIABLES` in `backend/routers/tiles.py`
5. Add it to the `/metadata` endpoint in `backend/main.py`
6. (Optional) Update `color_scales.py` here to keep it in sync as a reference

---

## Cache warming

To pre-populate the GCS cache for common queries before users hit the backend:

```bash
# Using curl (backend must be running)
curl -X POST http://localhost:8000/api/cache/warm \
  -H "Content-Type: application/json" \
  -d '{
    "variable": "wind_speed",
    "start_year": 1991, "end_year": 2020,
    "start_month": 1, "end_month": 12,
    "hour_utc": "all", "averaging": "monthly"
  }'

# Returns: {"job_id": "abc123", "message": "Cache warming started for zoom 0-2"}

# Poll progress
curl http://localhost:8000/api/status/abc123
```
