import { MONTH_LABELS } from '../constants/variables'

/**
 * Floating tooltip shown when the user hovers over the map.
 *
 * Displays lat/lon and the averaging parameters for the current view.
 * The actual data value is shown as '–' until a point-query API is
 * implemented (see TODO in Map.jsx).
 *
 * Props:
 *   tooltip  - { lat, lon, value, x, y } — pixel position + data
 *   variable - Variable definition from constants/variables.js
 *   params   - Current query params for the averaging period description
 */
export default function Tooltip({ tooltip, variable, params }) {
  const { lat, lon, value, x, y } = tooltip
  const { startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params

  const monthRange = startMonth === 1 && endMonth === 12
    ? 'All months'
    : `${MONTH_LABELS[startMonth - 1]}–${MONTH_LABELS[endMonth - 1]}`

  const hourLabel = hourUtc === 'all' ? 'All hours' : `${hourUtc}:00 UTC`

  // Keep tooltip inside viewport by flipping when near the right/bottom edge
  const style = {
    position: 'fixed',
    left: x + 12,
    top: y + 12,
    pointerEvents: 'none',
    zIndex: 20,
    // Snap left if near right edge — simple heuristic
    ...(x > window.innerWidth - 200 ? { left: 'auto', right: window.innerWidth - x + 4 } : {}),
  }

  return (
    <div style={style} className="bg-panel-bg/95 border border-panel-border rounded-lg px-3 py-2 shadow-2xl text-xs text-slate-200 space-y-1 min-w-[160px]">
      <div className="font-semibold text-white">
        {variable?.label ?? '—'}
      </div>

      <div className="text-slate-400 space-y-0.5">
        <div>
          <span className="text-slate-500">Lat </span>{lat}°
          <span className="text-slate-500"> / Lon </span>{lon}°
        </div>
        <div>
          <span className="text-slate-500">Period </span>
          {startYear}–{endYear}, {monthRange}
        </div>
        <div>
          <span className="text-slate-500">Hour </span>{hourLabel}
        </div>
        <div>
          <span className="text-slate-500">Avg </span>
          <span className="capitalize">{averaging}</span>
        </div>
      </div>

      <div className="border-t border-panel-border pt-1 flex items-baseline gap-1">
        <span className="text-lg font-bold text-white">
          {value !== null && value !== undefined ? value : '–'}
        </span>
        {value !== null && (
          <span className="text-slate-400">{variable?.unit}</span>
        )}
        {value === null && (
          <span className="text-xs text-slate-500">
            (point query not yet wired)
          </span>
        )}
      </div>
    </div>
  )
}
