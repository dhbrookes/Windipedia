import { useState, useEffect, useRef, useCallback } from 'react'
import { buildMockDistribution } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

/**
 * Fetches and caches ERA5 per-cell value distribution data.
 *
 * The distribution endpoint returns percentiles + histogram bins for every
 * 1-degree grid cell. The full response is cached in-memory keyed by the
 * parameter set. Individual cell lookups snap lat/lon to the nearest integer
 * degree.
 *
 * @param {object} params - Current debounced params (variable, years, months, etc.)
 * @returns {{ distribution, loading, error, getCell }}
 */
export function useDistribution(params) {
  const [distribution, setDistribution] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // In-memory cache keyed by canonical param string: variable|years|months|hour|avg
  const cacheRef = useRef({})

  const { variable, startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params

  const cacheKey = [variable, startYear, endYear, startMonth, endMonth, hourUtc, averaging].join('|')

  useEffect(() => {
    if (!variable) return

    // Return from in-memory cache if available
    if (cacheRef.current[cacheKey]) {
      setDistribution(cacheRef.current[cacheKey])
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    async function fetchDistribution() {
      try {
        let data
        if (IS_MOCK) {
          // Simulate slight network delay for realism
          await new Promise(r => setTimeout(r, 150))
          data = buildMockDistribution(variable)
        } else {
          const hour = hourUtc === 'all' ? 'all' : String(hourUtc).padStart(2, '0')
          const url = `/api/distribution/${variable}/${startYear}/${endYear}/${startMonth}/${endMonth}/${hour}/${averaging}`
          const res = await fetch(url)
          if (!res.ok) throw new Error(`Distribution fetch failed: ${res.status}`)
          data = await res.json()
        }

        if (!cancelled) {
          cacheRef.current[cacheKey] = data
          setDistribution(data)
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchDistribution()
    return () => { cancelled = true }
  }, [cacheKey, variable, startYear, endYear, startMonth, endMonth, hourUtc, averaging])

  /**
   * Look up the distribution for the cell nearest to (lat, lon).
   * Snaps to the nearest integer degree and returns the cell object,
   * or null if the distribution hasn't loaded yet.
   *
   * @param {number} lat
   * @param {number} lon
   * @returns {{ percentiles, bins, bin_edges, n } | null}
   */
  const getCell = useCallback(
    (lat, lon) => {
      if (!distribution) return null
      const snappedLat = Math.round(lat)
      const snappedLon = Math.round(lon)
      const key = `${snappedLat}_${snappedLon}`
      return distribution[key] ?? null
    },
    [distribution],
  )

  return { distribution, loading, error, getCell }
}
