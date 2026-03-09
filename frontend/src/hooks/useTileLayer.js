import { useEffect, useRef } from 'react'
import { getMockTileUrl } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

const TILE_SOURCE_ID = 'era5-tiles'
const TILE_LAYER_ID = 'era5-overlay'

/**
 * Manages the ERA5 raster tile overlay on a Mapbox GL map.
 *
 * When params change the hook swaps in a new tile source URL. Mapbox GL
 * requires removing and re-adding the layer/source when the URL changes
 * because raster sources are not dynamically updatable.
 *
 * @param {mapboxgl.Map | null} map - The Mapbox GL map instance
 * @param {object} params - Query params (variable, years, months, hourUtc, averaging)
 * @param {'average'|'exceedance'} mapMode - Which tile endpoint to use
 * @param {number} threshold - Exceedance threshold (used only when mapMode='exceedance')
 */
export function useTileLayer(map, params, mapMode = 'average', threshold = 0) {
  const currentUrlRef = useRef(null)

  useEffect(() => {
    if (!map) return

    const { variable, startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params
    const hour = hourUtc === 'all' ? 'all' : String(hourUtc).padStart(2, '0')

    let tileUrl

    if (IS_MOCK) {
      // In mock mode use a single-image data URI — Mapbox accepts data URIs as tile URLs
      // by treating it as a single tile at z=0.
      // Instead, use a template that always returns the same data URI regardless of z/x/y.
      // We achieve this by using the image URL directly as the sole tile in the source.
      tileUrl = `mock:${variable}:${mapMode}:${threshold}`
    } else if (mapMode === 'exceedance') {
      // /api/tiles/exceedance/{variable}/{threshold}/{start_year}/{end_year}/.../{z}/{x}/{y}.png
      tileUrl = [
        '/api/tiles/exceedance',
        variable,
        threshold.toFixed(2),
        startYear,
        endYear,
        startMonth,
        endMonth,
        hour,
        averaging,
        '{z}/{x}/{y}.png',
      ].join('/')
    } else {
      // /tiles/{variable}/{start_year}/{end_year}/{start_month}/{end_month}/{hour}/{averaging}/{z}/{x}/{y}.png
      tileUrl = [
        '/tiles',
        variable,
        startYear,
        endYear,
        startMonth,
        endMonth,
        hour,
        averaging,
        '{z}/{x}/{y}.png',
      ].join('/')
    }

    if (tileUrl === currentUrlRef.current) return
    currentUrlRef.current = tileUrl

    function addLayer() {
      if (map.getLayer(TILE_LAYER_ID)) map.removeLayer(TILE_LAYER_ID)
      if (map.getSource(TILE_SOURCE_ID)) map.removeSource(TILE_SOURCE_ID)

      let sourceSpec

      if (IS_MOCK) {
        // Mock mode: render a colored canvas image as a static raster overlay
        const dataUrl = getMockTileUrl(variable)
        sourceSpec = {
          type: 'image',
          url: dataUrl,
          coordinates: [
            [-180, 85.05], [180, 85.05],
            [180, -85.05], [-180, -85.05],
          ],
        }
        map.addSource(TILE_SOURCE_ID, sourceSpec)
        map.addLayer(
          {
            id: TILE_LAYER_ID,
            type: 'raster',
            source: TILE_SOURCE_ID,
            paint: { 'raster-opacity': 0.5, 'raster-fade-duration': 300 },
          },
          'waterway-label',
        )
      } else {
        sourceSpec = {
          type: 'raster',
          tiles: [tileUrl],
          tileSize: 256,
          bounds: [-180, -90, 180, 90],
          attribution: 'ERA5 © ECMWF / Copernicus Climate Change Service',
        }
        map.addSource(TILE_SOURCE_ID, sourceSpec)
        map.addLayer(
          {
            id: TILE_LAYER_ID,
            type: 'raster',
            source: TILE_SOURCE_ID,
            paint: { 'raster-opacity': 0.75, 'raster-fade-duration': 300 },
          },
          'waterway-label',
        )
      }
    }

    if (map.isStyleLoaded()) {
      addLayer()
    } else {
      map.once('styledata', addLayer)
    }

    return () => {
      // Cleanup is handled at the start of the next effect run (URL change)
    }
  }, [map, params, mapMode, threshold])
}
