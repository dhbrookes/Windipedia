/**
 * Sparse grid of numeric value labels overlaid on the Mapbox map.
 *
 * In mock mode, labels are generated from synthetic distribution data
 * (median value at each grid point). In production mode, this hook would
 * fetch from a backend annotation endpoint — left as a TODO.
 *
 * Labels appear at zoom ≥ 1 and are auto-hidden by Mapbox when they overlap.
 */

import { useEffect, useRef } from 'react'
import { buildMockAnnotations } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

const SOURCE_ID = 'value-annotations'
const LAYER_ID  = 'value-labels'

export function useValueAnnotations(map, params) {
  const currentVarRef = useRef(null)

  useEffect(() => {
    if (!map) return

    // Non-mock mode: no backend endpoint yet — skip
    // TODO: fetch from /api/annotations/{variable}/... when implemented
    if (!IS_MOCK) return

    const { variable } = params

    // Rebuild only when the variable changes (values don't depend on year/month in mock mode)
    if (variable === currentVarRef.current) return
    currentVarRef.current = variable

    function addLayer() {
      if (map.getLayer(LAYER_ID))  map.removeLayer(LAYER_ID)
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)

      map.addSource(SOURCE_ID, {
        type: 'geojson',
        data: buildMockAnnotations(variable),
      })

      map.addLayer(
        {
          id: LAYER_ID,
          type: 'symbol',
          source: SOURCE_ID,
          minzoom: 1,
          layout: {
            'text-field': ['get', 'label'],
            'text-size': 11,
            'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Regular'],
            'text-allow-overlap': false,
            'text-ignore-placement': false,
          },
          paint: {
            'text-color': '#e2e8f0',
            'text-halo-color': '#0f172a',
            'text-halo-width': 1.5,
          },
        },
        'waterway-label',
      )
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
        if (map.getLayer(LAYER_ID))  map.removeLayer(LAYER_ID)
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)
      } catch (_) { /* map may already be removed */ }
    }
  }, [map])
}
