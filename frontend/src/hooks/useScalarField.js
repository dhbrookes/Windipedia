/**
 * Fetches (or generates in mock mode) a global 1° resolution scalar field
 * for the current variable and parameters.
 *
 * Returns { field, loading } where field.sample(lat, lon) returns the
 * median value at the nearest 1° grid point, or null for missing data.
 *
 * In mock mode: field is generated synchronously from synthetic data — no fetch.
 * In production: fetches GET /api/field/{variable}/{params} and gzip-decodes.
 */

import { useState, useEffect } from 'react'
import { getMockScalarField } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

/** Wrap raw field data with a .sample(lat, lon) accessor. */
function createFieldAccessor(data) {
  const { la1, lo1, resolution, width, height, values } = data
  return {
    ...data,
    sample(lat, lon) {
      const latIdx = Math.round((la1 - lat) / resolution)
      let   lonIdx = Math.round((lon - lo1) / resolution)

      if (latIdx < 0 || latIdx >= height) return null
      lonIdx = ((lonIdx % width) + width) % width  // wrap longitude

      const v = values[latIdx * width + lonIdx]
      return (v == null || isNaN(v)) ? null : v
    },
  }
}

export function useScalarField(variable, params) {
  const [state, setState] = useState({ field: null, loading: true })

  useEffect(() => {
    setState({ field: null, loading: true })

    if (IS_MOCK) {
      setState({ field: createFieldAccessor(getMockScalarField(variable)), loading: false })
      return
    }

    // Production: fetch from backend field endpoint
    const { startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params
    const hour = hourUtc === 'all' ? 'all' : String(hourUtc).padStart(2, '0')
    const url = `/api/field/${variable}/${startYear}/${endYear}/${startMonth}/${endMonth}/${hour}/${averaging}`

    let cancelled = false
    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`Field fetch failed: ${r.status}`)
        return r.json()
      })
      .then(data => {
        if (!cancelled) setState({ field: createFieldAccessor(data), loading: false })
      })
      .catch(() => {
        if (!cancelled) setState({ field: null, loading: false })
      })

    return () => { cancelled = true }
  }, [variable, params])

  return state
}
