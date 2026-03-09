/**
 * Variable definitions for Windipedia.
 *
 * Each entry defines display metadata and the color scale used both in the
 * frontend Legend/Tooltip AND in the pipeline's color_scales.py. The two must
 * stay in sync — if you change a scale here, update color_scales.py as well.
 *
 * colorStops: array of [value, cssColor] pairs used for CSS gradient rendering.
 * The pipeline uses the same numeric stops to map data values → RGBA pixels.
 */

export const VARIABLES = {
  wind_speed: {
    id: 'wind_speed',
    label: 'Wind',
    unit: 'kts',
    description: '10 m wind speed with animated flow direction',
    // 0 kts (calm) → 64 kts (storm force)
    colorStops: [
      [0,  '#f0f9ff'],  // calm — near white
      [5,  '#7dd3fc'],  // light — light blue
      [10, '#22c55e'],  // gentle — green
      [20, '#facc15'],  // moderate — yellow
      [30, '#f97316'],  // fresh — orange
      [40, '#ef4444'],  // strong — red
      [55, '#7c3aed'],  // storm — purple
      [64, '#1e1b4b'],  // hurricane — very dark blue
    ],
    min: 0,
    max: 64,
  },

  pressure: {
    id: 'pressure',
    label: 'Air Pressure',
    unit: 'hPa',
    description: 'Mean sea level pressure',
    // Low (960 hPa) → neutral (1013 hPa) → high (1040 hPa)
    colorStops: [
      [960,  '#7c3aed'],  // deep low — purple
      [990,  '#a78bfa'],  // low — light purple
      [1013, '#f1f5f9'],  // neutral — near white
      [1025, '#fb923c'],  // high — orange
      [1040, '#92400e'],  // very high — dark orange/brown
    ],
    min: 960,
    max: 1040,
  },

  wave_height: {
    id: 'wave_height',
    label: 'Significant Wave Height',
    unit: 'm',
    description: 'Significant height of combined wind waves and swell',
    // 0 m (flat calm) → 12 m (extreme seas)
    colorStops: [
      [0,   '#f8fafc'],  // calm — near white
      [1,   '#bae6fd'],  // slight — pale blue
      [3,   '#38bdf8'],  // moderate — sky blue
      [6,   '#0284c7'],  // rough — medium blue
      [9,   '#1e3a8a'],  // very rough — dark blue
      [12,  '#0f0a1e'],  // extreme — near black
    ],
    min: 0,
    max: 12,
  },

  precip: {
    id: 'precip',
    label: 'Precipitation',
    unit: 'mm/day',
    description: 'Mean daily precipitation',
    // 0 → 20 mm/day
    colorStops: [
      [0,   '#f8fafc'],  // dry — near white
      [1,   '#dbeafe'],  // trace — very light blue
      [3,   '#93c5fd'],  // light — light blue
      [7,   '#3b82f6'],  // moderate — blue
      [12,  '#1d4ed8'],  // heavy — dark blue
      [20,  '#0f172a'],  // extreme — near black
    ],
    min: 0,
    max: 20,
  },

  temp: {
    id: 'temp',
    label: 'Temperature',
    unit: '°C',
    description: '2 m air temperature',
    // -40°C (polar) → 45°C (desert)
    colorStops: [
      [-40, '#0f172a'],  // extreme cold — near black blue
      [-20, '#3b82f6'],  // cold — blue
      [-5,  '#67e8f9'],  // cool — cyan
      [10,  '#4ade80'],  // mild — green
      [20,  '#facc15'],  // warm — yellow
      [30,  '#f97316'],  // hot — orange
      [40,  '#dc2626'],  // very hot — red
      [45,  '#7f1d1d'],  // extreme heat — dark red
    ],
    min: -40,
    max: 45,
  },
}

/** Ordered list for the variable selector UI */
export const VARIABLE_LIST = Object.values(VARIABLES)

/** Month labels for the month range picker */
export const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

/** Hour options for the time-of-day slider */
export const HOUR_OPTIONS = [
  { value: 'all', label: 'All hours (24 h avg)' },
  ...Array.from({ length: 24 }, (_, i) => ({
    value: String(i).padStart(2, '0'),
    label: `${String(i).padStart(2, '0')}:00 UTC`,
  })),
]
