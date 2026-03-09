/**
 * Color scale legend shown in the bottom-left corner of the map.
 *
 * Renders the variable's colorStops as a CSS linear-gradient bar
 * with labeled tick marks at key values.
 *
 * Props:
 *   variable - A variable definition object from constants/variables.js
 */
export default function Legend({ variable }) {
  const { label, unit, colorStops, min, max } = variable

  // Build CSS gradient string from colorStops
  const gradientStops = colorStops.map(([value, color]) => {
    const pct = ((value - min) / (max - min)) * 100
    return `${color} ${pct.toFixed(1)}%`
  }).join(', ')

  const gradient = `linear-gradient(to right, ${gradientStops})`

  // Pick 5 evenly-spaced tick labels including min and max
  const ticks = Array.from({ length: 5 }, (_, i) => {
    const v = min + (i / 4) * (max - min)
    return Math.round(v)
  })

  return (
    <div className="bg-panel-bg/85 backdrop-blur border border-panel-border rounded-lg px-3 py-2 shadow-xl min-w-[200px]">
      <div className="text-xs font-semibold text-slate-300 mb-1.5">
        {label} <span className="font-normal text-slate-500">({unit})</span>
      </div>

      {/* Gradient bar */}
      <div
        className="h-3 rounded-sm w-full"
        style={{ background: gradient }}
      />

      {/* Tick marks */}
      <div className="flex justify-between mt-1">
        {ticks.map(tick => (
          <span key={tick} className="text-[10px] text-slate-400">
            {tick}
          </span>
        ))}
      </div>
    </div>
  )
}
