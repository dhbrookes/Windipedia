/**
 * Mock API responses for frontend development without a live backend.
 * Activated by setting VITE_MOCK_API=true in .env.
 *
 * Tile URLs return a procedural gradient image keyed to each variable's color scale.
 * Distribution data is synthetically generated per-degree cell.
 */

import { VARIABLES } from '../constants/variables'

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

function hexToRgb(hex) {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

/** Linear interpolation between two color stops for a given value. */
function interpolateColorStops(stops, value) {
  if (value <= stops[0][0]) return hexToRgb(stops[0][1])
  if (value >= stops[stops.length - 1][0]) return hexToRgb(stops[stops.length - 1][1])
  for (let i = 0; i < stops.length - 1; i++) {
    const [v0, c0] = stops[i]
    const [v1, c1] = stops[i + 1]
    if (value >= v0 && value <= v1) {
      const t = (value - v0) / (v1 - v0)
      const rgb0 = hexToRgb(c0)
      const rgb1 = hexToRgb(c1)
      return rgb0.map((ch, j) => Math.round(ch + t * (rgb1[j] - ch)))
    }
  }
  return hexToRgb(stops[stops.length - 1][1])
}

// ---------------------------------------------------------------------------
// Tile URL: procedural gradient world image based on variable's color scale
// ---------------------------------------------------------------------------

/**
 * Returns a mock tile URL (data URI) for the given variable.
 * Generates a 512×256 world-map image with per-pixel spatial variation
 * using the variable's color scale, so gradients and color ranges are visible.
 */
export function getMockTileUrl(variable) {
  const varDef = VARIABLES[variable]
  if (!varDef || typeof document === 'undefined') {
    return `data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==`
  }

  const { colorStops, min, max } = varDef
  const range = max - min
  const W = 512, H = 256

  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  const imageData = ctx.createImageData(W, H)
  const data = imageData.data

  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      // Map pixel to geographic coordinates
      const lon = (x / W) * 360 - 180
      const lat = 90 - (y / H) * 180

      // Spatial factor: same formula as mockCellDistribution for consistency
      const spatialFactor = (
        Math.sin(lat * Math.PI / 90) * 0.4 +
        Math.cos(lon * Math.PI / 180) * 0.2 +
        1
      ) / 2

      const normVal = 0.2 + spatialFactor * 0.6  // 0.2–0.8
      const val = min + range * normVal

      const [r, g, b] = interpolateColorStops(colorStops, val)
      const idx = (y * W + x) * 4
      data[idx]     = r
      data[idx + 1] = g
      data[idx + 2] = b
      data[idx + 3] = 255  // fully opaque — Mapbox layer opacity controls transparency
    }
  }

  ctx.putImageData(imageData, 0, 0)
  return canvas.toDataURL('image/png')
}

// ---------------------------------------------------------------------------
// Distribution data: synthetically generated for every integer lat/lon cell
// ---------------------------------------------------------------------------

/**
 * Generate synthetic distribution data for a single cell.
 * Values are loosely plausible for each variable.
 *
 * @param {string} variable
 * @param {number} lat  Integer latitude  (-90..90)
 * @param {number} lon  Integer longitude (-180..180)
 * @returns {{ percentiles, bins, bin_edges, n }}
 */
function mockCellDistribution(variable, lat, lon) {
  const varDef = VARIABLES[variable]
  const min = varDef.min
  const max = varDef.max
  const range = max - min

  // Spatially vary the mean to give the map visual structure
  const spatialFactor = (Math.sin(lat * Math.PI / 90) * 0.4 + Math.cos(lon * Math.PI / 180) * 0.2 + 1) / 2

  const center = min + range * (0.2 + spatialFactor * 0.6)
  const spread = range * 0.12

  // Generate percentile values from a roughly normal distribution centred on 'center'
  const zScores = { p5: -1.645, p10: -1.282, p25: -0.674, p50: 0, p75: 0.674, p90: 1.282, p95: 1.645 }
  const percentiles = {}
  for (const [key, z] of Object.entries(zScores)) {
    percentiles[key] = Math.max(min, Math.min(max, center + z * spread))
  }

  // 20 histogram bins
  const numBins = 20
  const binWidth = range / numBins
  const bin_edges = Array.from({ length: numBins + 1 }, (_, i) => min + i * binWidth)
  const n = 300

  // Gaussian-ish bin counts
  const bins = bin_edges.slice(0, numBins).map((edge) => {
    const binCenter = edge + binWidth / 2
    const z = (binCenter - center) / spread
    return Math.max(0, Math.round(n * Math.exp(-0.5 * z * z) * binWidth / (spread * 2.507)))
  })

  return { percentiles, bins, bin_edges, n }
}

// ---------------------------------------------------------------------------
// Wind field: synthetic U/V grid for particle animation in mock mode
// ---------------------------------------------------------------------------

/**
 * Build a synthetic 2° resolution U/V wind field.
 * Mimics prevailing westerlies at mid-latitudes and trade winds in tropics.
 * Returns the same shape as GET /api/wind/field/...
 */
export function buildMockWindField() {
  const latStep = 2, lonStep = 2
  const lats = Array.from({ length: 91 }, (_, i) => 90 - i * latStep)
  const lons = Array.from({ length: 181 }, (_, i) => -180 + i * lonStep)
  const u = [], v = []

  for (const lat of lats) {
    for (const lon of lons) {
      const φ = lat * Math.PI / 180
      const λ = lon * Math.PI / 180
      // Westerlies at mid-latitudes, trade winds (eastward flow) in tropics
      const uComp = -10 * Math.cos(2 * φ)               // zonal flow
                  + 3  * Math.sin(3 * λ) * Math.cos(φ)  // wave pattern
      const vComp =  2  * Math.sin(2 * φ) * Math.cos(2 * λ) // meridional
      u.push(Math.round(uComp * 100) / 100)
      v.push(Math.round(vComp * 100) / 100)
    }
  }

  return { lats, lons, u, v, width: lons.length, height: lats.length }
}

// ---------------------------------------------------------------------------
// Swell field: synthetic U/V swell propagation grid for wave_height animation
// ---------------------------------------------------------------------------

/**
 * Build a synthetic 2° resolution swell propagation field.
 * - Tropics (±30°): swell propagates westward (trade-wind swell from E)
 * - Mid-latitudes (30–60°): swell propagates eastward (westerly storm swell)
 * - Magnitude higher near storm-track latitudes (~50°N/S)
 * Returns the same shape as buildMockWindField().
 */
export function buildMockSwellField() {
  const latStep = 2, lonStep = 2
  const lats = Array.from({ length: 91 }, (_, i) => 90 - i * latStep)
  const lons = Array.from({ length: 181 }, (_, i) => -180 + i * lonStep)
  const u = [], v = []

  for (const lat of lats) {
    for (const lon of lons) {
      const φ = lat * Math.PI / 180
      const λ = lon * Math.PI / 180

      // Height proxy: higher near 50°N/S storm tracks, lower in tropics & poles
      const heightProxy = 1.5
        + 2.5 * Math.exp(-Math.pow((Math.abs(lat) - 50) / 15, 2))

      // Zonal direction: cos(2φ) > 0 at mid-latitudes → eastward
      //                  cos(2φ) < 0 in tropics     → westward
      const uComp = heightProxy * Math.cos(2 * φ) * 0.9
                  + 0.5 * Math.sin(2 * λ) * Math.cos(φ)  // subtle longitude variation
      // Small meridional component — swell fans out from storm centres
      const vComp = heightProxy * 0.25 * Math.sin(φ) * Math.cos(λ)

      u.push(Math.round(uComp * 100) / 100)
      v.push(Math.round(vComp * 100) / 100)
    }
  }

  return { lats, lons, u, v, width: lons.length, height: lats.length }
}

// ---------------------------------------------------------------------------
// Distribution data: full mock distribution dict
// ---------------------------------------------------------------------------

/**
 * Build a full mock distribution dict covering every integer-degree cell.
 * Returns the same shape as the real /api/distribution/... endpoint.
 *
 * @param {string} variable
 * @returns {{ [key: string]: { percentiles, bins, bin_edges, n } }}
 */
export function buildMockDistribution(variable) {
  const result = {}
  for (let lat = -90; lat <= 90; lat++) {
    for (let lon = -180; lon <= 180; lon++) {
      const key = `${lat}_${lon}`
      result[key] = mockCellDistribution(variable, lat, lon)
    }
  }
  return result
}

// ---------------------------------------------------------------------------
// Annotations: sparse grid of value labels for the map
// ---------------------------------------------------------------------------

const ANNOTATION_FORMATTERS = {
  wind_speed:  (v) => `${Math.round(v)} kts`,
  pressure:    (v) => `${Math.round(v)}`,
  wave_height: (v) => `${v.toFixed(1)} m`,
  precip:      (v) => `${v.toFixed(1)} mm`,
  temp:        (v) => `${Math.round(v)}°`,
}

/**
 * Build a GeoJSON FeatureCollection of value labels for the given variable.
 * Points are placed on a regular grid (every gridDeg degrees).
 * Each feature has a `label` property with the formatted median value.
 *
 * @param {string} variable
 * @param {number} gridDeg  Grid spacing in degrees (default 30)
 * @returns {GeoJSON.FeatureCollection}
 */
export function buildMockAnnotations(variable, gridDeg = 30) {
  const format = ANNOTATION_FORMATTERS[variable] ?? ((v) => `${Math.round(v)}`)
  const features = []

  for (let lat = -75; lat <= 75; lat += gridDeg) {
    for (let lon = -180; lon <= 150; lon += gridDeg) {
      const dist = mockCellDistribution(variable, lat, lon)
      const label = format(dist.percentiles.p50)
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon, lat] },
        properties: { label },
      })
    }
  }

  return { type: 'FeatureCollection', features }
}
