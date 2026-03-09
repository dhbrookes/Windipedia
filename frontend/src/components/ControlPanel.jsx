import { VARIABLE_LIST, MONTH_LABELS, HOUR_OPTIONS, VARIABLES } from '../constants/variables'

// Beaufort scale reference markers for the wind threshold slider
const BEAUFORT_MARKS = [
  { kts: 22, label: 'B6' },
  { kts: 28, label: 'B7' },
  { kts: 34, label: 'B8' },
  { kts: 41, label: 'B9' },
  { kts: 48, label: 'B10' },
]

// Per-variable threshold defaults and ranges (in display units)
const THRESHOLD_CONFIG = {
  wind_speed:  { min: 0,    max: 64,   step: 0.5,  default: 22 },
  wind_dir:    { min: 0,    max: 360,  step: 5,    default: 180 },
  pressure:    { min: 960,  max: 1040, step: 0.5,  default: 1000 },
  wave_height: { min: 0,    max: 12,   step: 0.1,  default: 3 },
  precip:      { min: 0,    max: 20,   step: 0.1,  default: 5 },
  temp:        { min: -40,  max: 45,   step: 0.5,  default: 20 },
}

/**
 * Left-side control panel for selecting ERA5 query parameters.
 *
 * Props:
 *   params              - Current query params object
 *   onParamChange(key, value) - Callback to update a single param
 *   mapMode             - 'average' | 'exceedance'
 *   threshold           - Current exceedance threshold (in variable's display unit)
 *   onMapModeChange(mode)
 *   onThresholdChange(value)
 */
export default function ControlPanel({
  params,
  onParamChange,
  mapMode = 'average',
  threshold,
  onMapModeChange,
  onThresholdChange,
}) {
  const { variable, startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params
  const threshCfg = THRESHOLD_CONFIG[variable] ?? THRESHOLD_CONFIG.wind_speed
  const currentThreshold = threshold ?? threshCfg.default
  const varDef = VARIABLES[variable]

  return (
    <div className="space-y-4 text-sm text-slate-200">

      {/* Variable selector */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Variable
        </label>
        <div className="grid grid-cols-2 gap-1">
          {VARIABLE_LIST.map(v => (
            <button
              key={v.id}
              onClick={() => onParamChange('variable', v.id)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors ${
                variable === v.id
                  ? 'bg-sky-600 text-white'
                  : 'bg-panel-hover text-slate-300 hover:bg-slate-700'
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </section>

      {/* Map mode toggle */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Map mode
        </label>
        <div className="flex gap-1">
          {[
            { id: 'average',    label: 'Average' },
            { id: 'exceedance', label: 'Exceedance' },
          ].map(m => (
            <button
              key={m.id}
              onClick={() => onMapModeChange?.(m.id)}
              className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                mapMode === m.id
                  ? 'bg-sky-600 text-white'
                  : 'bg-panel-hover text-slate-300 hover:bg-slate-700'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </section>

      {/* Threshold slider — only shown in exceedance mode */}
      {mapMode === 'exceedance' && (
        <section>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Threshold
            </label>
            <span className="text-xs font-medium text-amber-400">
              {currentThreshold.toFixed(1)} {varDef?.unit}
            </span>
          </div>

          <div className="relative">
            <input
              type="range"
              min={threshCfg.min}
              max={threshCfg.max}
              step={threshCfg.step}
              value={currentThreshold}
              onChange={e => onThresholdChange?.(parseFloat(e.target.value))}
              className="w-full accent-amber-400"
            />

            {/* Beaufort markers (wind only) */}
            {variable === 'wind_speed' && (
              <div className="relative mt-1 h-4">
                {BEAUFORT_MARKS.map(({ kts, label }) => {
                  const pct = ((kts - threshCfg.min) / (threshCfg.max - threshCfg.min)) * 100
                  return (
                    <div
                      key={label}
                      className="absolute flex flex-col items-center"
                      style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}
                    >
                      <div className="w-px h-1.5 bg-slate-500" />
                      <span className="text-[9px] text-slate-500 leading-none">{label}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <p className="text-xs text-slate-500 mt-1">
            Map shows P(value &gt; threshold) per cell. Click a cell for histogram.
          </p>
        </section>
      )}

      {/* Year range */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Year range
        </label>
        <div className="flex items-center gap-2">
          <YearInput
            value={startYear}
            min={1940}
            max={endYear}
            onChange={v => onParamChange('startYear', v)}
          />
          <span className="text-slate-500">–</span>
          <YearInput
            value={endYear}
            min={startYear}
            max={2024}
            onChange={v => onParamChange('endYear', v)}
          />
        </div>
      </section>

      {/* Month range */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Months
        </label>
        <div className="flex items-center gap-2">
          <MonthSelect
            value={startMonth}
            onChange={v => onParamChange('startMonth', v)}
          />
          <span className="text-slate-500">–</span>
          <MonthSelect
            value={endMonth}
            onChange={v => onParamChange('endMonth', v)}
          />
        </div>
        {startMonth > endMonth && (
          <p className="text-xs text-amber-400 mt-1">
            Wraps year boundary (e.g. Oct–Mar = austral summer)
          </p>
        )}
      </section>

      {/* Time of day */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Time of day (UTC)
        </label>
        <select
          value={hourUtc}
          onChange={e => onParamChange('hourUtc', e.target.value)}
          className="w-full bg-slate-800 border border-panel-border rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
        >
          {HOUR_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {hourUtc !== 'all' && (
          <p className="text-xs text-slate-500 mt-1">
            Only ERA5 timesteps at {hourUtc}:00 UTC are averaged.
            Solar noon varies ±8 h by longitude.
          </p>
        )}
      </section>

      {/* Averaging period */}
      <section>
        <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
          Averaging period
        </label>
        <div className="flex gap-1">
          {['daily', 'weekly', 'monthly'].map(a => (
            <button
              key={a}
              onClick={() => onParamChange('averaging', a)}
              className={`flex-1 py-1.5 rounded text-xs font-medium capitalize transition-colors ${
                averaging === a
                  ? 'bg-sky-600 text-white'
                  : 'bg-panel-hover text-slate-300 hover:bg-slate-700'
              }`}
            >
              {a}
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}

function YearInput({ value, min, max, onChange }) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      onChange={e => {
        const v = parseInt(e.target.value, 10)
        if (!isNaN(v) && v >= min && v <= max) onChange(v)
      }}
      className="w-full bg-slate-800 border border-panel-border rounded px-2 py-1.5 text-xs text-center text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
    />
  )
}

function MonthSelect({ value, onChange }) {
  return (
    <select
      value={value}
      onChange={e => onChange(parseInt(e.target.value, 10))}
      className="flex-1 bg-slate-800 border border-panel-border rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
    >
      {MONTH_LABELS.map((label, i) => (
        <option key={i + 1} value={i + 1}>{label}</option>
      ))}
    </select>
  )
}
