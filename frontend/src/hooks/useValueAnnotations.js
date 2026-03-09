/**
 * Staggered grid of numeric value labels overlaid on the Mapbox map.
 *
 * - For vector variables (wind_speed, wave_height): shows a rotated arrow icon
 *   pointing in the propagation direction, with the magnitude label below it.
 * - For scalar variables: label only, no icon.
 *
 * Grid is staggered (hexagonal-ish) at 15° spacing to avoid the rigid military
 * grid look. Labels auto-hide on collision via Mapbox placement rules.
 *
 * Mock mode only for now — production would need a backend annotation endpoint.
 */

import { useEffect, useRef } from 'react'
import { buildMockAnnotations } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

const SOURCE_ID = 'value-annotations'
const LAYER_ID  = 'value-labels'
const ARROW_IMG = 'direction-arrow'

const VECTOR_VARIABLES = new Set(['wind_speed', 'wave_height'])

// ---------------------------------------------------------------------------
// Arrow icon: a simple upward-pointing arrow rendered onto a canvas, then
// registered as a Mapbox image.  icon-rotate in the symbol layer rotates it
// to match the field direction.
// ---------------------------------------------------------------------------

function makeArrowImage() {
  const SIZE = 24
  const canvas = document.createElement('canvas')
  canvas.width = SIZE
  canvas.height = SIZE
  const ctx = canvas.getContext('2d')

  const cx = SIZE / 2
  const tip = 3        // y-coord of arrowhead tip
  const tail = SIZE - 4 // y-coord of tail

  ctx.strokeStyle = 'rgba(255,255,255,0.9)'
  ctx.fillStyle   = 'rgba(255,255,255,0.9)'
  ctx.lineWidth   = 1.5
  ctx.lineCap     = 'round'
  ctx.lineJoin    = 'round'

  // Shaft
  ctx.beginPath()
  ctx.moveTo(cx, tip + 6)
  ctx.lineTo(cx, tail)
  ctx.stroke()

  // Arrowhead (filled triangle)
  ctx.beginPath()
  ctx.moveTo(cx, tip)
  ctx.lineTo(cx - 5, tip + 9)
  ctx.lineTo(cx + 5, tip + 9)
  ctx.closePath()
  ctx.fill()

  return ctx.getImageData(0, 0, SIZE, SIZE)
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useValueAnnotations(map, params) {
  const currentVarRef = useRef(null)

  useEffect(() => {
    if (!map) return
    // TODO: in production mode, fetch from /api/annotations/{variable}/...
    if (!IS_MOCK) return

    const { variable } = params
    if (variable === currentVarRef.current) return
    currentVarRef.current = variable

    const isVector = VECTOR_VARIABLES.has(variable)

    function addLayer() {
      // Remove stale layers/sources
      if (map.getLayer(LAYER_ID))   map.removeLayer(LAYER_ID)
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)

      // Register arrow image if needed (idempotent)
      if (isVector && !map.hasImage(ARROW_IMG)) {
        map.addImage(ARROW_IMG, makeArrowImage(), { pixelRatio: 2 })
      }

      map.addSource(SOURCE_ID, {
        type: 'geojson',
        data: buildMockAnnotations(variable),
      })

      const layerSpec = {
        id: LAYER_ID,
        type: 'symbol',
        source: SOURCE_ID,
        minzoom: 0.5,
        layout: {
          'text-field': ['get', 'label'],
          'text-size': 11,
          'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Regular'],
          'text-allow-overlap': false,
          'text-ignore-placement': false,
          ...(isVector
            ? {
                // Place arrow icon above label; rotate arrow to field direction
                'icon-image': ARROW_IMG,
                'icon-rotate': ['coalesce', ['get', 'rotation'], 0],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': false,
                'icon-size': 1,
                'text-offset': [0, 1.6],  // push label below the 24px icon
                'symbol-placement': 'point',
              }
            : {
                'text-offset': [0, 0],
              }),
        },
        paint: {
          'text-color': '#e2e8f0',
          'text-halo-color': '#0f172a',
          'text-halo-width': 1.5,
        },
      }

      map.addLayer(layerSpec, 'waterway-label')
    }

    if (map.isStyleLoaded()) {
      addLayer()
    } else {
      map.once('styledata', addLayer)
    }
  }, [map, params])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (!map) return
      try {
        if (map.getLayer(LAYER_ID))   map.removeLayer(LAYER_ID)
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)
      } catch (_) { /* map may already be removed */ }
    }
  }, [map])
}
