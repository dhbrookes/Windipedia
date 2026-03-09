# Windipedia Testing Guide

Four stages of testing — from no-dependency unit tests up to full end-to-end with real ERA5 data.

---

## Stage 1 — Frontend only (no backend)

**Goal**: Verify the UI renders, controls update the map, histogram popup works.

**Setup**: No GCP credentials needed. No backend needed.

```bash
cd frontend
# -n means "no-overwrite" — preserves your real token if .env already exists
cp -n ../.env.example .env
# Edit .env: set VITE_MAPBOX_TOKEN to your real token and VITE_MOCK_API=true
# WARNING: running cp without -n will overwrite your token with a placeholder
npm install
npm run dev
```

Open http://localhost:3000 and verify:

- [x] Map loads with dark basemap and reference lines (equator, tropics)
- [x] All 6 variable buttons select correctly and legend updates
- [x] Map mode toggle switches between "Average" and "Exceedance"
- [x] Threshold slider appears in Exceedance mode
- [x] Beaufort markers (B6–B10) appear on the wind speed threshold slider
- [ ] Tile overlay renders a semi-transparent color block over the basemap
- [x] Hovering the map shows a lat/lon tooltip
- [ ] Clicking the map opens a HistogramPopup with bars, percentile dashes, and a draggable threshold line
- [ ] Dragging the threshold line updates the exceedance % label and syncs to the slider
- [ ] Closing the popup (×) dismisses it
- [x] Year/month/hour controls apply a 500 ms debounce before the map updates (watch for the pulse dot)

---

## Stage 2 — Backend unit tests (mock ERA5)

**Goal**: Verify backend Python logic without GCS or network access.

**Setup**: Install backend dependencies, set `MOCK_ERA5=true`.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run all tests
MOCK_ERA5=true pytest tests/ -v

# Run specific test files
pytest tests/test_era5.py -v
pytest tests/test_cache.py -v
```

Expected output: all tests pass. Key tests:

| Test | What it verifies |
|------|-----------------|
| `test_unit_conversions` | ERA5 raw → display units (m/s→kts, Pa→hPa, K→°C, m/hr→mm/day) |
| `test_mock_dataset_structure` | Mock dataset has correct shape, variables, coordinates |
| `test_tile_png_all_variables` | PNG tile renders for all 6 variables, correct size, RGBA |
| `test_tile_nan_transparent` | NaN ocean cells → transparent pixels, no RuntimeWarning |
| `test_distribution_shape` | Distribution dict has correct keys, percentile ordering |
| `test_distribution_units` | Percentile values are in display units, not ERA5 raw units |
| `test_exceedance_range` | Exceedance probabilities in [0, 1] |
| `test_exceedance_monotonic` | Higher threshold → lower or equal exceedance |
| `test_cache_key_determinism` | Same params → same key; different params → different key |
| `test_tile_cache_roundtrip` | PNG bytes survive write→read→compare |
| `test_distribution_cache_roundtrip` | JSON dict survives gzip write→read→compare |

---

## Stage 3a — Backend with downloaded ERA5 snapshot (no GCP account needed)

**Goal**: Run the full backend with real ERA5 data and a local file cache — no GCP credentials or network access after the one-time download.

**Requirements**: Internet access for the initial download (~288 MB transfer). Python dependencies: `gcsfs xarray zarr numpy tqdm`.

```bash
# One-time: download ~50 MB local ERA5 snapshot (takes ~5 min on a typical connection)
cd pipeline
pip install gcsfs xarray zarr numpy tqdm   # skip packages already installed
python download_sample.py --year 2020 --output ../era5_sample.zarr
# Expected: era5_sample.zarr/ directory, ~50 MB on disk
# Grid: 12 timesteps × 91 lat × 181 lon at 2° resolution

# Start backend pointing at local Zarr + local tile cache (no GCS writes)
cd ../backend
source .venv/bin/activate
ERA5_ZARR_PATH=../era5_sample.zarr \
TILE_CACHE_BUCKET=./local_cache    \
uvicorn main:app --port 8000
```

In another terminal:

```bash
# Verify backend is ready
curl http://localhost:8000/health
# Expected: {"status":"ok","zarr_ready":true,"placeholder_mode":false}

# Request a zoom-0 tile (whole world, wind speed)
curl "http://localhost:8000/tiles/wind_speed/2020/2020/1/12/all/monthly/0/0/0.png" \
  --output /tmp/t.png
ls -lh /tmp/t.png
# Expected: HTTP 200, file > 2 KB (real color gradient, not placeholder grey)

# Second request is served instantly from local_cache/
curl "http://localhost:8000/tiles/wind_speed/2020/2020/1/12/all/monthly/0/0/0.png" \
  --output /dev/null -w "HTTP %{http_code} in %{time_total}s\n"
# Expected: HTTP 200 in < 0.05 s
```

Frontend (optional):

```bash
# In frontend/.env set VITE_MOCK_API=false, then:
cd frontend && npm run dev
```

Verify:

- [ ] `/health` returns `zarr_ready: true`
- [ ] Tile PNG > 2 KB and shows visible color variation (not a solid block)
- [ ] `local_cache/cache/*.png` files appear after first tile request
- [ ] Subsequent tile requests for the same params complete instantly (< 50 ms)
- [ ] Frontend tiles, annotations, and histogram popup all show real values

---

## Stage 3 — Backend with real ERA5 (small region, live GCS)

**Goal**: Verify ERA5 Zarr access works and tiles render real data.

**Requirements**: GCP credentials (read-only to `gcp-public-data-arco-era5`). Ideally run from `us-central1` to avoid egress costs (~$0.08/GB).

```bash
cd backend
source .venv/bin/activate

# Check ERA5 store is accessible (downloads metadata only)
cd ../pipeline
python explore_era5.py --info-only

# Start the backend with real data (no GCS write cache needed)
cd ../backend
TILE_CACHE_BUCKET=none ERA5_ZARR_PATH=gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3 \
  uvicorn main:app --port 8000

# In another terminal, request a single tile (zoom 0 = 1 tile for the whole world)
curl -v "http://localhost:8000/tiles/wind_speed/1991/2020/1/12/all/monthly/0/0/0.png" \
  --output /tmp/tile.png
# Expected: HTTP 200, Content-Type: image/png

# Check file size > 0
ls -lh /tmp/tile.png

# Request distribution data (downloads ~1 month of ERA5, may take 30-120 s)
curl "http://localhost:8000/api/distribution/wind_speed/2020/2020/1/1/all/monthly" | python3 -m json.tool | head -30

# Request an exceedance tile
curl "http://localhost:8000/api/tiles/exceedance/wind_speed/22.0/1991/2020/1/12/all/monthly/0/0/0.png" \
  --output /tmp/exceedance.png
ls -lh /tmp/exceedance.png
```

Verify:
- [ ] `/health` returns `zarr_ready: true`
- [ ] Tile PNG > 5 KB (real data rendered)
- [ ] Distribution JSON has keys like `"0_0"`, `"51_-1"` etc. with `percentiles`, `bins`, `bin_edges`, `n`
- [ ] Exceedance PNG > 1 KB (non-uniform probability map)

---

## Stage 4 — Full docker-compose

**Goal**: End-to-end integration test with both services running.

**Requirements**: Docker, GCP credentials.

```bash
cp .env.example .env
# Edit .env — fill in:
#   VITE_MAPBOX_TOKEN=pk.eyJ1...
#   GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
#   TILE_CACHE_BUCKET=your-bucket

docker-compose up --build
```

Open http://localhost:3000 and verify:

- [ ] Frontend loads without console errors
- [ ] Default wind speed map renders (tiles may take 10-60 s on first load)
- [ ] "Loading tiles…" badge appears, then "Computing averages…" after 3 s
- [ ] After first load, switching back to the same params is instant (cache hit)
- [ ] Switching to Exceedance mode loads a different set of tiles
- [ ] Clicking the map shows a histogram with real distribution data
- [ ] Dragging the threshold line in the histogram syncs back to the slider

---

## Common issues

| Symptom | Fix |
|---------|-----|
| `zarr_ready: false` on `/health` | ERA5 store failed to open — check GCP credentials and network. Set `USE_PLACEHOLDER_TILES=true` to skip. |
| Tiles time out (> 60 s) | Running outside `us-central1` — cross-region latency. Move to GCP VM in same region. |
| `MOCK_ERA5=true` but tests still try GCS | Make sure env var is set before starting uvicorn / pytest |
| Frontend shows blank map | Check browser console for Mapbox token error. Set `VITE_MAPBOX_TOKEN` in `.env`. |
| Distribution fetch returns 503 | Backend ERA5 store not ready — wait for startup or use `MOCK_ERA5=true`. |
