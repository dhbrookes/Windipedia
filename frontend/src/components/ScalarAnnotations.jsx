/**
 * Canvas overlay showing a staggered grid of scalar value labels.
 *
 * - Zoom-adaptive grid spacing (coarser at low zoom, finer when zoomed in)
 * - Staggered rows (odd rows offset by half the grid spacing) for a
 *   diamond/hexagonal pattern that avoids the rigid military-grid look
 * - Dark rounded-rect (pill) background for legibility over any map colour
 * - Pressure variable: H / L prefixes at local extrema (Azores High etc.)
 * - Fades out on zoom-start, redraws on zoom-end for a clean transition
 * - z-index 1 (sits above raster tile, below wind/swell particle canvas)
 *
 * Props:
 *   map      – mapboxgl.Map instance (null while map is initialising)
 *   field    – field accessor from useScalarField (has .sample(lat, lon))
 *   loading  – true while field is being fetched
 *   variable – current variable ID (determines label formatting + H/L logic)
 */

import { useEffect, useRef } from 'react'

// ---------------------------------------------------------------------------
// Grid spacing by floor(zoom)
// ---------------------------------------------------------------------------
const GRID_SPACING_BY_ZOOM = [
  30,   // zoom 0
  20,   // zoom 1
  10,   // zoom 2  ← ~40–50 labels at default view
   7,   // zoom 3
   5,   // zoom 4
   3,   // zoom 5
   1.5, // zoom 6
   0.5, // zoom 7+
]

function getGridSpacing(zoom) {
  const z = Math.min(Math.floor(zoom), GRID_SPACING_BY_ZOOM.length - 1)
  return GRID_SPACING_BY_ZOOM[z]
}

// ---------------------------------------------------------------------------
// Label formatting per variable
// ---------------------------------------------------------------------------
const FORMATTERS = {
  wind_speed:  v => `${Math.round(v)}`,
  pressure:    v => `${Math.round(v)}`,
  wave_height: v => `${v.toFixed(1)}`,
  precip:      v => `${Math.round(v)}`,
  temp:        v => `${Math.round(v)}`,
}

function formatValue(v, variable) {
  return (FORMATTERS[variable] ?? (v => String(Math.round(v))))(v)
}

// ---------------------------------------------------------------------------
// Pressure H / L detection
// ---------------------------------------------------------------------------
function getPressurePrefix(field, lat, lon, radius) {
  const center = field.sample(lat, lon)
  if (center == null) return ''

  // Sample 8 cardinal/diagonal neighbours at `radius` distance
  const offsets = [
    [radius, 0], [-radius, 0], [0, radius], [0, -radius],
    [radius, radius], [radius, -radius], [-radius, radius], [-radius, -radius],
  ]
  const neighbours = offsets.map(([dLat, dLon]) => field.sample(lat + dLat, lon + dLon))
  const valid = neighbours.filter(v => v != null)
  if (valid.length < 4) return ''

  if (valid.every(v => center > v)) return 'H'
  if (valid.every(v => center < v)) return 'L'
  return ''
}

// ---------------------------------------------------------------------------
// Canvas drawing
// ---------------------------------------------------------------------------
function drawLabel(ctx, x, y, text, prefix) {
  const label = prefix ? `${prefix} ${text}` : text
  ctx.fillStyle = '#111111'
  ctx.fillText(label, x, y)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function ScalarAnnotations({ map, field, loading, variable }) {
  const canvasRef  = useRef(null)
  const fieldRef   = useRef(field)
  const varRef     = useRef(variable)
  const drawRef    = useRef(null)    // so event listeners always call the latest draw()

  // Keep refs in sync so event listeners always see the latest values
  useEffect(() => { fieldRef.current = field },    [field])
  useEffect(() => { varRef.current   = variable }, [variable])

  // --- Set up canvas once ---
  useEffect(() => {
    if (!map) return

    const container = map.getContainer()
    const canvas    = document.createElement('canvas')
    canvas.style.cssText = [
      'position:absolute', 'top:0', 'left:0',
      'pointer-events:none', 'z-index:1',
      'transition:opacity 200ms ease',
    ].join(';')
    container.appendChild(canvas)
    canvasRef.current = canvas

    function resize() {
      canvas.width  = container.clientWidth
      canvas.height = container.clientHeight
      drawRef.current?.()
    }
    resize()

    const ro = new ResizeObserver(resize)
    ro.observe(container)

    // ---------------------------------------------------------------------------
    // Core draw function — recomputes the full annotation grid for the current
    // viewport, zoom level, and scalar field.
    // ---------------------------------------------------------------------------
    function draw() {
      const currentField    = fieldRef.current
      const currentVariable = varRef.current

      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, canvas.width, canvas.height)

      if (!currentField) return

      const W    = canvas.width
      const H    = canvas.height
      const zoom = map.getZoom()
      const spacing = getGridSpacing(zoom)
      const bounds  = map.getBounds()

      // Expand bounds by one grid step to avoid labels popping at edges
      const latMin = Math.max(-85, bounds.getSouth() - spacing)
      const latMax = Math.min( 85, bounds.getNorth() + spacing)
      const lonMin = bounds.getWest()  - spacing
      const lonMax = bounds.getEast()  + spacing

      // Determine iteration start aligned to grid
      const latStart = Math.ceil(latMin / spacing) * spacing
      const lonStart = Math.ceil(lonMin / spacing) * spacing

      ctx.font         = '11px "DIN Offc Pro","Arial",monospace'
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'middle'

      const isPressure = currentVariable === 'pressure'
      const extRadius  = spacing * 2   // extremum search radius for H/L

      for (let lat = latStart; lat <= latMax; lat += spacing) {
        // Stagger odd rows by half a grid step — consistent via absolute row number
        const rowNum    = Math.round(Math.abs(lat) / spacing)
        const lonOffset = (rowNum % 2 === 1) ? spacing / 2 : 0

        for (let lon = lonStart + lonOffset; lon <= lonMax; lon += spacing) {
          const val = currentField.sample(lat, lon)
          if (val == null) continue

          const pt = map.project([lon, lat])
          // Skip points outside the visible canvas (with small margin for pill overflow)
          if (pt.x < -30 || pt.x > W + 30 || pt.y < -30 || pt.y > H + 30) continue

          const prefix = isPressure ? getPressurePrefix(currentField, lat, lon, extRadius) : ''
          drawLabel(ctx, pt.x, pt.y, formatValue(val, currentVariable), prefix)
        }
      }
    }

    drawRef.current = draw

    // Fade out while zooming (labels are in wrong screen positions mid-zoom)
    function onZoomStart() { canvas.style.opacity = '0.15' }
    function onZoomEnd()   { draw(); canvas.style.opacity = '1' }
    function onMoveEnd()   { draw() }

    map.on('zoomstart', onZoomStart)
    map.on('zoomend',   onZoomEnd)
    map.on('moveend',   onMoveEnd)

    return () => {
      map.off('zoomstart', onZoomStart)
      map.off('zoomend',   onZoomEnd)
      map.off('moveend',   onMoveEnd)
      ro.disconnect()
      canvas.remove()
      canvasRef.current = null
      drawRef.current   = null
    }
  }, [map])   // re-run only if the map instance changes

  // --- Trigger redraw when field or variable changes ---
  useEffect(() => {
    drawRef.current?.()
  }, [field, variable])

  // --- Show loading state ---
  useEffect(() => {
    if (canvasRef.current) {
      canvasRef.current.style.opacity = loading ? '0.4' : '1'
    }
  }, [loading])

  return null   // canvas is appended directly to the map container
}
