import { useEffect, useRef, useState } from 'react'
import mapboxgl from 'mapbox-gl'
import { useTileLayer } from '../hooks/useTileLayer'
import { useWindParticles } from '../hooks/useWindParticles'
import { useSwellParticles } from '../hooks/useSwellParticles'
import { useScalarField } from '../hooks/useScalarField'
import ScalarAnnotations from './ScalarAnnotations'

// Mapbox token is injected via Vite's env system at build time.
// Set VITE_MAPBOX_TOKEN in your .env file (see .env.example).
mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN || ''

// Lat lines to draw as dashed overlays (equator + tropics)
const REFERENCE_LINES = [
  { lat: 0,     label: 'Equator',       color: '#94a3b8', width: 1.5 },
  { lat: 23.5,  label: 'Tropic of Cancer',   color: '#64748b', width: 1 },
  { lat: -23.5, label: 'Tropic of Capricorn', color: '#64748b', width: 1 },
]

/**
 * Full-screen Mapbox GL map with ERA5 raster overlay and reference lines.
 *
 * Props:
 *   params               - Current (debounced) query params from App state
 *   mapMode              - 'average' | 'exceedance'
 *   threshold            - Exceedance threshold value
 *   onTooltipChange      - Called when user hovers over the map
 *   onMapLoaded()        - Called once the map style has fully loaded
 *   onTileLoadingChange  - Called with true/false as tiles start/finish loading
 *   onMapClick(lat, lon) - Called when user clicks the map
 */
export default function Map({
  params,
  mapMode = 'average',
  threshold = 0,
  onTooltipChange,
  onMapLoaded,
  onTileLoadingChange,
  onMapClick,
}) {
  const containerRef = useRef(null)
  const [map, setMap] = useState(null)

  // Initialize map once
  useEffect(() => {
    if (!containerRef.current) return

    const m = new mapboxgl.Map({
      container: containerRef.current,
      // Dark ocean basemap — good contrast for colored overlays
      style: 'mapbox://styles/mapbox/navigation-night-v1',
      center: [0, 20],
      zoom: 2,
      projection: 'mercator', // keep flat for tile overlay alignment
      attributionControl: true,
    })

    m.addControl(new mapboxgl.NavigationControl(), 'top-right')
    m.addControl(new mapboxgl.ScaleControl({ unit: 'nautical' }), 'bottom-right')

    m.on('load', () => {
      addReferenceLines(m)
      onMapLoaded?.()
      setMap(m)
    })

    return () => m.remove()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Wire up ERA5 tile overlay — updates when params or mode/threshold change
  useTileLayer(map, params, mapMode, threshold)

  // Animated wind particle overlay (only active for wind_speed variable)
  useWindParticles(map, params)

  // Animated swell dot overlay (only active for wave_height variable)
  useSwellParticles(map, params)

  // Scalar field for annotations (1° resolution median values)
  const { field, loading: fieldLoading } = useScalarField(params.variable, params)

  // Tile loading state: Mapbox fires 'dataloading' when any source begins fetching
  // and 'idle' when all sources are done and the map has re-rendered.
  useEffect(() => {
    if (!map || !onTileLoadingChange) return

    function onDataLoading() { onTileLoadingChange(true) }
    function onIdle()        { onTileLoadingChange(false) }

    map.on('dataloading', onDataLoading)
    map.on('idle',        onIdle)
    return () => {
      map.off('dataloading', onDataLoading)
      map.off('idle',        onIdle)
    }
  }, [map, onTileLoadingChange])

  // Hover: extract lat/lon and forward to parent for the tooltip
  useEffect(() => {
    if (!map) return

    function onMouseMove(e) {
      const { lng, lat } = e.lngLat
      // Mapbox does not expose pixel values for raster layers via queryRenderedFeatures.
      // Actual data value requires a backend point-query endpoint.
      // TODO: add GET /api/point/{variable}?lat=&lon=&params=... to backend.
      onTooltipChange?.({
        lat: lat.toFixed(3),
        lon: lng.toFixed(3),
        value: null,
        x: e.point.x,
        y: e.point.y,
      })
    }

    function onMouseLeave() {
      onTooltipChange?.(null)
    }

    map.on('mousemove', onMouseMove)
    map.on('mouseout',  onMouseLeave)
    return () => {
      map.off('mousemove', onMouseMove)
      map.off('mouseout',  onMouseLeave)
    }
  }, [map, onTooltipChange])

  // Click: open histogram popup for the clicked cell
  useEffect(() => {
    if (!map || !onMapClick) return

    function onClick(e) {
      const { lng, lat } = e.lngLat
      onMapClick(lat, lng)
    }

    map.on('click', onClick)
    return () => map.off('click', onClick)
  }, [map, onMapClick])

  return (
    <div ref={containerRef} className="w-full h-full">
      <ScalarAnnotations map={map} field={field} loading={fieldLoading} variable={params.variable} />
    </div>
  )
}

/**
 * Draws equator and tropics as dashed GeoJSON line layers.
 * These are static reference lines baked in at map load time.
 */
function addReferenceLines(map) {
  REFERENCE_LINES.forEach(({ lat, label, color, width }) => {
    const id = `ref-line-${label.replace(/\s+/g, '-').toLowerCase()}`

    map.addSource(id, {
      type: 'geojson',
      data: {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          // Span full longitude range
          coordinates: Array.from({ length: 361 }, (_, i) => [i - 180, lat]),
        },
      },
    })

    map.addLayer({
      id,
      type: 'line',
      source: id,
      paint: {
        'line-color': color,
        'line-width': width,
        'line-dasharray': [4, 4],
        'line-opacity': 0.6,
      },
    })
  })
}
