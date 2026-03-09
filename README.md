# Windipedia

An interactive historical weather atlas. Explore climatological averages from ERA5 reanalysis data on an interactive map — think Ventusky/Windy, but for historical patterns rather than forecasts.

**Use cases:** sailors planning passages, pilots checking prevailing winds, researchers studying seasonal patterns.

---

## Getting started in 5 minutes

### Prerequisites

- [Node.js 20+](https://nodejs.org/)
- [Python 3.11+](https://www.python.org/)
- A [Mapbox access token](https://account.mapbox.com/access-tokens/) (free tier is fine)
- Docker + Docker Compose (optional, for the compose workflow)

### 1. Clone and configure

```bash
git clone https://github.com/your-username/windipedia
cd windipedia
cp .env.example .env
# Edit .env and set VITE_MAPBOX_TOKEN to your Mapbox token
```

### 2a. Run with Docker Compose (easiest)

```bash
# Set USE_PLACEHOLDER_TILES=true in .env to skip GCS for now
docker-compose up
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

### 2b. Run frontend and backend separately

**Frontend:**
```bash
cd frontend
npm install
npm run dev
# -> http://localhost:3000
```

**Backend:**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# -> http://localhost:8000
# -> http://localhost:8000/docs  (Swagger UI)
# -> http://localhost:8000/health
```

> **Tip:** Set `USE_PLACEHOLDER_TILES=true` in `.env` to run the backend without GCS access. The map will load with a transparent overlay until real tiles are generated.

---

## Project structure

```
windipedia/
├── frontend/         React + Vite + Mapbox GL JS + Tailwind CSS
├── backend/          FastAPI tile-serving API
├── pipeline/         ERA5 data processing + tile generation
├── docker-compose.yml
├── .env.example      All required environment variables documented
└── README.md
```

---

## Data pipeline

The pipeline reads ERA5 data from the public GCS bucket, computes climatological means, and generates XYZ PNG tiles.

```bash
cd pipeline
pip install -r requirements.txt

# Small test run (writes tiles locally, no GCS needed)
python process_era5.py \
    --variable wind_speed \
    --start-year 2000 --end-year 2001 \
    --start-month 1  --end-month 3 \
    --hour-utc all \
    --averaging monthly \
    --output-bucket windipedia-tiles \
    --bbox "-180,20,180,65" \
    --local-output ./test-tiles
```

See [pipeline/README.md](pipeline/README.md) for full documentation including GCP auth setup, cost estimates, and resumability.

---

## Supported variables

| ID | Label | Unit | ERA5 source |
|----|-------|------|-------------|
| `wind_speed` | Wind Speed | kts | u10 + v10 components |
| `wind_dir` | Wind Direction | deg | derived from u10/v10 |
| `pressure` | Air Pressure | hPa | mean_sea_level_pressure |
| `wave_height` | Significant Wave Height | m | swh |
| `precip` | Precipitation | mm/day | total_precipitation |
| `temp` | Temperature | degC | 2m_temperature |

---

## Architecture

```
Browser
  └── Mapbox GL JS
        ├── Dark basemap (Mapbox navigation-night-v1)
        └── Raster overlay --> FastAPI backend --> GCS tile bucket
                                                         ^
                                               pipeline/process_era5.py
                                               (run manually or on GCP)
```

Tiles are pre-computed and static once generated. The backend is a thin proxy that reads from GCS — no database, no computation at request time.

---

## Tile URL scheme

```
/tiles/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour_utc}/{averaging}/{z}/{x}/{y}.png
```

Example:
```
/tiles/wind_speed/1991/2020/1/12/all/monthly/3/4/2.png
```
Wind speed, 1991-2020 climatological normal, all months, all hours, monthly averaging, zoom 3 tile (4, 2).

---

## Development notes

- **Adding a variable:** See "Adding a new variable" in [pipeline/README.md](pipeline/README.md)
- **Color scales:** Defined in both `pipeline/color_scales.py` and `frontend/src/constants/variables.js` — keep them in sync
- **Point queries:** The hover tooltip currently shows lat/lon but not the actual data value. A `GET /point/{variable}/...?lat=&lon=` endpoint is the next step.
- **Local solar time:** The `--hour-utc` filter selects by UTC hour. A future enhancement should support local solar time (LST) filtering for accurate diurnal analysis.

---

## Deployment (GCP)

```bash
# Build and push backend to Artifact Registry
docker build -t gcr.io/YOUR_PROJECT/windipedia-backend ./backend
docker push gcr.io/YOUR_PROJECT/windipedia-backend

# Deploy to Cloud Run
gcloud run deploy windipedia-backend \
    --image gcr.io/YOUR_PROJECT/windipedia-backend \
    --region us-central1 \
    --set-env-vars GCS_TILE_BUCKET=windipedia-tiles \
    --allow-unauthenticated

# Build frontend for static hosting (Firebase Hosting, GCS, etc.)
cd frontend
VITE_API_BASE_URL=https://your-cloud-run-url.run.app npm run build
```
