import { useRef, useState, useEffect, useCallback } from 'react'

// Beaufort scale thresholds in knots (wind_speed only)
const BEAUFORT_MARKERS = [
  { kts: 22, label: 'B6' },
  { kts: 28, label: 'B7' },
  { kts: 34, label: 'B8' },
  { kts: 41, label: 'B9' },
  { kts: 48, label: 'B10' },
]

const SVG_W = 300
const SVG_H = 140
const MARGIN = { top: 8, right: 8, bottom: 30, left: 36 }
const CHART_W = SVG_W - MARGIN.left - MARGIN.right
const CHART_H = SVG_H - MARGIN.top - MARGIN.bottom

/**
 * Histogram popup shown when user clicks on the map.
 *
 * Props:
 *   cell        - { percentiles, bins, bin_edges, n } from useDistribution
 *   variable    - variable definition object from VARIABLES
 *   threshold   - current exceedance threshold value
 *   lat, lon    - clicked coordinates (for display)
 *   onClose     - called when user dismisses the popup
 *   onThresholdChange(value) - called when user drags the threshold line
 */
export default function HistogramPopup({
  cell,
  variable,
  threshold,
  lat,
  lon,
  onClose,
  onThresholdChange,
}) {
  const svgRef = useRef(null)
  const [dragging, setDragging] = useState(false)
  const [localThreshold, setLocalThreshold] = useState(threshold)

  // Keep local threshold in sync when the parent updates it (e.g. slider)
  useEffect(() => {
    setLocalThreshold(threshold)
  }, [threshold])

  if (!cell || !variable) return null

  const { bins, bin_edges, percentiles, n } = cell
  const xMin = bin_edges[0]
  const xMax = bin_edges[bin_edges.length - 1]
  const xRange = xMax - xMin
  const maxBin = Math.max(...bins, 1)

  // Map a data value to SVG x coordinate
  const toX = (v) => MARGIN.left + ((v - xMin) / xRange) * CHART_W

  // Map an SVG x coordinate back to a data value (clamped to [xMin, xMax])
  const toVal = (svgX) => {
    const v = xMin + ((svgX - MARGIN.left) / CHART_W) * xRange
    return Math.max(xMin, Math.min(xMax, v))
  }

  // Compute exceedance probability from bins for the current threshold
  const computeExceedance = (thresh) => {
    if (!bins || bins.length === 0) return null
    const total = bins.reduce((s, b) => s + b, 0)
    if (total === 0) return null
    let above = 0
    for (let i = 0; i < bins.length; i++) {
      const binCenter = (bin_edges[i] + bin_edges[i + 1]) / 2
      if (binCenter > thresh) above += bins[i]
    }
    return (above / total) * 100
  }

  const exceedancePct = computeExceedance(localThreshold)
  const thresholdX = toX(localThreshold)

  // --- Drag handling ---
  const startDrag = useCallback(
    (e) => {
      e.preventDefault()
      setDragging(true)

      const svg = svgRef.current
      if (!svg) return
      const rect = svg.getBoundingClientRect()
      const scaleX = SVG_W / rect.width

      function move(ev) {
        const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX
        const svgX = (clientX - rect.left) * scaleX
        const val = toVal(svgX)
        setLocalThreshold(val)
        onThresholdChange?.(val)
      }

      function stop() {
        setDragging(false)
        window.removeEventListener('mousemove', move)
        window.removeEventListener('mouseup', stop)
        window.removeEventListener('touchmove', move)
        window.removeEventListener('touchend', stop)
      }

      window.addEventListener('mousemove', move)
      window.addEventListener('mouseup', stop)
      window.addEventListener('touchmove', move, { passive: false })
      window.addEventListener('touchend', stop)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [onThresholdChange, xMin, xMax, xRange],
  )

  const isWind = variable.id === 'wind_speed'

  return (
    <div
      className="absolute bottom-16 left-1/2 -translate-x-1/2 z-20 w-80 bg-panel-bg/95 backdrop-blur border border-panel-border rounded-xl shadow-2xl p-4 text-slate-200"
      // Prevent map click events from bubbling through
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div>
          <span className="text-sm font-semibold">{variable.label}</span>
          <span className="text-xs text-slate-400 ml-2">
            {lat.toFixed(1)}°, {lon.toFixed(1)}°
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-white transition-colors text-lg leading-none"
          aria-label="Close"
        >
          &times;
        </button>
      </div>

      {/* Percentile summary row */}
      <div className="flex justify-between text-xs text-slate-400 mb-2">
        {['p10', 'p25', 'p50', 'p75', 'p90'].map((p) => (
          <div key={p} className="text-center">
            <div className="text-slate-500 uppercase">{p.replace('p', 'P')}</div>
            <div className="text-slate-200 font-medium">
              {percentiles?.[p] != null ? percentiles[p].toFixed(1) : '–'}
            </div>
          </div>
        ))}
      </div>

      {/* SVG Histogram */}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${SVG_W} ${SVG_H}`}
        className="w-full select-none"
        style={{ touchAction: 'none' }}
      >
        {/* Axis lines */}
        <line
          x1={MARGIN.left} y1={MARGIN.top}
          x2={MARGIN.left} y2={MARGIN.top + CHART_H}
          stroke="#475569" strokeWidth="1"
        />
        <line
          x1={MARGIN.left} y1={MARGIN.top + CHART_H}
          x2={MARGIN.left + CHART_W} y2={MARGIN.top + CHART_H}
          stroke="#475569" strokeWidth="1"
        />

        {/* Bars */}
        {bins.map((count, i) => {
          const x1 = toX(bin_edges[i])
          const x2 = toX(bin_edges[i + 1])
          const barW = Math.max(x2 - x1 - 1, 1)
          const barH = (count / maxBin) * CHART_H
          const y = MARGIN.top + CHART_H - barH
          const binCenter = (bin_edges[i] + bin_edges[i + 1]) / 2
          const isAbove = binCenter > localThreshold
          return (
            <rect
              key={i}
              x={x1}
              y={y}
              width={barW}
              height={barH}
              fill={isAbove ? '#f97316' : '#38bdf8'}
              opacity={0.75}
            />
          )
        })}

        {/* Percentile tick marks: P25, P50, P75 */}
        {['p25', 'p50', 'p75'].map((p) => {
          const val = percentiles?.[p]
          if (val == null) return null
          const x = toX(val)
          return (
            <g key={p}>
              <line
                x1={x} y1={MARGIN.top}
                x2={x} y2={MARGIN.top + CHART_H}
                stroke="#64748b" strokeWidth="1" strokeDasharray="3,3"
              />
              <text
                x={x} y={MARGIN.top - 2}
                textAnchor="middle" fontSize="7" fill="#64748b"
              >
                {p.replace('p', 'P')}
              </text>
            </g>
          )
        })}

        {/* Beaufort markers (wind only) */}
        {isWind && BEAUFORT_MARKERS.map(({ kts, label }) => {
          if (kts < xMin || kts > xMax) return null
          const x = toX(kts)
          return (
            <g key={label}>
              <line
                x1={x} y1={MARGIN.top + CHART_H}
                x2={x} y2={MARGIN.top + CHART_H + 5}
                stroke="#94a3b8" strokeWidth="1"
              />
              <text
                x={x} y={MARGIN.top + CHART_H + 14}
                textAnchor="middle" fontSize="7" fill="#94a3b8"
              >
                {label}
              </text>
            </g>
          )
        })}

        {/* X-axis labels */}
        {[xMin, (xMin + xMax) / 2, xMax].map((v, i) => (
          <text
            key={i}
            x={toX(v)}
            y={SVG_H - 2}
            textAnchor="middle"
            fontSize="8"
            fill="#94a3b8"
          >
            {v.toFixed(0)}
          </text>
        ))}

        {/* Draggable threshold line */}
        <g
          style={{ cursor: dragging ? 'ew-resize' : 'col-resize' }}
          onMouseDown={startDrag}
          onTouchStart={startDrag}
        >
          {/* Invisible wide hit target */}
          <line
            x1={thresholdX} y1={MARGIN.top - 4}
            x2={thresholdX} y2={MARGIN.top + CHART_H + 4}
            stroke="transparent" strokeWidth="16"
          />
          {/* Visible threshold line */}
          <line
            x1={thresholdX} y1={MARGIN.top - 4}
            x2={thresholdX} y2={MARGIN.top + CHART_H}
            stroke="#f59e0b" strokeWidth="2"
          />
          {/* Handle knob */}
          <circle
            cx={thresholdX}
            cy={MARGIN.top + CHART_H / 2}
            r={5}
            fill="#f59e0b"
            stroke="#1e293b"
            strokeWidth="1.5"
          />
          {/* Value label */}
          <rect
            x={thresholdX - 18}
            y={MARGIN.top - 16}
            width={36} height={13}
            rx={3}
            fill="#f59e0b"
          />
          <text
            x={thresholdX}
            y={MARGIN.top - 6}
            textAnchor="middle"
            fontSize="8"
            fontWeight="bold"
            fill="#1e293b"
          >
            {localThreshold.toFixed(1)}
          </text>
        </g>
      </svg>

      {/* Exceedance summary */}
      <div className="mt-2 flex items-center justify-between text-xs">
        <span className="text-slate-400">
          P(value &gt; {localThreshold.toFixed(1)} {variable.unit})
        </span>
        <span className="font-semibold text-orange-400">
          {exceedancePct != null ? `${exceedancePct.toFixed(1)}%` : '–'}
        </span>
      </div>

      {/* Low-N warning */}
      {n < 30 && (
        <p className="text-xs text-amber-400 mt-1">
          Warning: only {n} samples — distribution estimate may be unreliable.
        </p>
      )}
    </div>
  )
}
