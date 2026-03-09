import { useState, useEffect, useRef } from 'react'
import Map from './components/Map'
import ControlPanel from './components/ControlPanel'
import Legend from './components/Legend'
import Tooltip from './components/Tooltip'
import HistogramPopup from './components/HistogramPopup'
import { VARIABLES } from './constants/variables'
import { useDistribution } from './hooks/useDistribution'

// Default query parameters shown on first load
const DEFAULT_PARAMS = {
  variable: 'wind_speed',
  startYear: 1991,
  endYear: 2020,   // Standard WMO climatological normal period
  startMonth: 1,
  endMonth: 12,
  hourUtc: 'all', // 'all' or a zero-padded string like '06', '15'
  averaging: 'monthly',
}

// Per-variable default thresholds for exceedance mode (display units)
const DEFAULT_THRESHOLDS = {
  wind_speed:  22,   // Beaufort 6 — strong breeze
  pressure:    1000,
  wave_height: 3,
  precip:      5,
  temp:        20,
}

const DEBOUNCE_MS = 500   // Wait this long after the last param change before firing tile requests
const SLOW_TILE_MS = 3000 // Show "Computing averages..." message after this many ms

export default function App() {
  const [params, setParams]                   = useState(DEFAULT_PARAMS)
  const [debouncedParams, setDebouncedParams] = useState(DEFAULT_PARAMS)
  const [tooltip, setTooltip]                 = useState(null)
  const [mapLoaded, setMapLoaded]             = useState(false)
  const [tileLoading, setTileLoading]         = useState(false)
  const [showSlowMsg, setShowSlowMsg]         = useState(false)

  // Exceedance / histogram state
  const [mapMode, setMapMode]         = useState('average')             // 'average' | 'exceedance'
  const [threshold, setThreshold]     = useState(DEFAULT_THRESHOLDS.wind_speed)
  const [histogramCell, setHistogramCell] = useState(null)              // { lat, lon, cell }

  const currentVariable = VARIABLES[debouncedParams.variable]
  const debounceTimer   = useRef(null)
  const slowTimer       = useRef(null)

  // Fetch distribution data for current params (used by histogram popup)
  const { getCell, loading: distLoading } = useDistribution(debouncedParams)

  // Debounce: apply params to the tile layer 500ms after the user stops adjusting controls.
  function updateParam(key, value) {
    setParams(prev => {
      const next = { ...prev, [key]: value }
      // When variable changes reset threshold to its default
      if (key === 'variable') {
        setThreshold(DEFAULT_THRESHOLDS[value] ?? 0)
        setHistogramCell(null)
      }
      return next
    })
  }

  useEffect(() => {
    clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      setDebouncedParams(params)
    }, DEBOUNCE_MS)
    return () => clearTimeout(debounceTimer.current)
  }, [params])

  // Show "Computing averages..." after SLOW_TILE_MS of continuous tile loading.
  useEffect(() => {
    clearTimeout(slowTimer.current)
    setShowSlowMsg(false)
    if (tileLoading) {
      slowTimer.current = setTimeout(() => setShowSlowMsg(true), SLOW_TILE_MS)
    }
    return () => clearTimeout(slowTimer.current)
  }, [tileLoading])

  // Detect when debounced params diverge from live params (user is still typing)
  const isPending = JSON.stringify(params) !== JSON.stringify(debouncedParams)

  // Map click: open histogram popup for the clicked cell
  function handleMapClick(lat, lon) {
    const cell = getCell(lat, lon)
    setHistogramCell(cell ? { lat, lon, cell } : null)
  }

  return (
    <div className="relative w-full h-full">
      {/* Full-screen map — receives debounced params so tile requests are batched */}
      <Map
        params={debouncedParams}
        mapMode={mapMode}
        threshold={threshold}
        onTooltipChange={setTooltip}
        onMapLoaded={() => setMapLoaded(true)}
        onTileLoadingChange={setTileLoading}
        onMapClick={handleMapClick}
      />

      {/* Left control panel — receives live params so controls feel instant */}
      <div className="absolute top-4 left-4 z-10 w-72">
        <div className="bg-panel-bg/90 backdrop-blur border border-panel-border rounded-xl p-4 shadow-2xl">
          <div className="flex items-center justify-between mb-1">
            <h1 className="text-lg font-bold text-white tracking-tight">Windipedia</h1>
            {/* Subtle dot while debounce is pending */}
            {isPending && (
              <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" title="Updating map..." />
            )}
          </div>
          <p className="text-xs text-slate-400 mb-4">Historical climatological averages</p>
          <ControlPanel
            params={params}
            onParamChange={updateParam}
            mapMode={mapMode}
            threshold={threshold}
            onMapModeChange={setMapMode}
            onThresholdChange={setThreshold}
          />
        </div>
      </div>

      {/* Loading badge — shown while Mapbox tiles are fetching from the backend */}
      {tileLoading && (
        <div className="absolute top-4 right-16 z-10 flex items-center gap-2 bg-panel-bg/90 backdrop-blur border border-panel-border rounded-lg px-3 py-2 shadow-xl">
          <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse flex-shrink-0" />
          <span className="text-xs text-slate-300 whitespace-nowrap">
            {showSlowMsg ? 'Computing averages\u2026' : 'Loading tiles\u2026'}
          </span>
        </div>
      )}

      {/* Distribution loading indicator */}
      {distLoading && (
        <div className="absolute top-14 right-16 z-10 flex items-center gap-2 bg-panel-bg/90 backdrop-blur border border-panel-border rounded-lg px-3 py-2 shadow-xl">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse flex-shrink-0" />
          <span className="text-xs text-slate-300 whitespace-nowrap">Loading distributions\u2026</span>
        </div>
      )}

      {/* Bottom-left legend */}
      {mapLoaded && currentVariable && (
        <div className="absolute bottom-8 left-4 z-10">
          <Legend variable={currentVariable} />
        </div>
      )}

      {/* Hover tooltip */}
      {tooltip && (
        <Tooltip tooltip={tooltip} variable={currentVariable} params={debouncedParams} />
      )}

      {/* Histogram popup — shown after clicking the map in exceedance mode
          (or any mode when distribution data is available) */}
      {histogramCell && currentVariable && (
        <HistogramPopup
          cell={histogramCell.cell}
          variable={currentVariable}
          threshold={threshold}
          lat={histogramCell.lat}
          lon={histogramCell.lon}
          onClose={() => setHistogramCell(null)}
          onThresholdChange={(val) => {
            setThreshold(val)
            // Dismiss popup so the map updates are immediately visible
          }}
        />
      )}
    </div>
  )
}
