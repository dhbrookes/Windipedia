/**
 * Animated swell particle overlay for the wave_height variable.
 *
 * Shows small filled dots drifting in the swell propagation direction.
 * Dot radius and opacity are keyed to local wave height (swell magnitude).
 *
 * Only active when params.variable === 'wave_height'.
 */

import { useEffect, useRef } from 'react'
import { buildMockSwellField } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

const NUM_PARTICLES = 800
const SPEED_SCALE   = 0.06   // much slower than wind — swell moves steadily
const FADE_ALPHA    = 0.06   // moderately fast fade → short dotted trails
const MAX_AGE_BASE  = 180    // longer lifetime than wind particles

// ---------------------------------------------------------------------------
// Swell field bilinear sampling (same algorithm as wind particles)
// ---------------------------------------------------------------------------

function sampleUV(field, lat, lon) {
  const { lats, lons, u, v, width, height } = field

  lon = ((lon + 180) % 360 + 360) % 360 - 180

  const latMax = lats[0]
  const latMin = lats[height - 1]
  const lonMin = lons[0]
  const lonMax = lons[width - 1]

  const latRange = latMax - latMin
  const lonRange = lonMax - lonMin
  const latIdx = (latMax - Math.max(latMin, Math.min(latMax, lat))) / latRange * (height - 1)
  const lonIdx = (Math.max(lonMin, Math.min(lonMax, lon)) - lonMin) / lonRange * (width - 1)

  const i0 = Math.min(height - 2, Math.floor(latIdx))
  const j0 = Math.min(width - 2, Math.floor(lonIdx))
  const i1 = i0 + 1
  const j1 = j0 + 1
  const dlat = latIdx - i0
  const dlon = lonIdx - j0

  const idx = (r, c) => r * width + c
  const interp = (arr) =>
    arr[idx(i0, j0)] * (1 - dlat) * (1 - dlon) +
    arr[idx(i0, j1)] * (1 - dlat) * dlon +
    arr[idx(i1, j0)] * dlat * (1 - dlon) +
    arr[idx(i1, j1)] * dlat * dlon

  return [interp(u), interp(v)]
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSwellParticles(map, params) {
  const enabled = params.variable === 'wave_height'

  const fieldRef     = useRef(null)
  const particlesRef = useRef([])
  const animRef      = useRef(null)
  const canvasRef    = useRef(null)

  // --- Fetch swell field whenever params change ---
  useEffect(() => {
    if (!enabled) return

    fieldRef.current = null

    const { startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params
    const hour = hourUtc === 'all' ? 'all' : String(hourUtc).padStart(2, '0')

    let cancelled = false

    async function fetchField() {
      try {
        let field
        if (IS_MOCK) {
          field = buildMockSwellField()
        } else {
          const url = `/api/swell/field/${startYear}/${endYear}/${startMonth}/${endMonth}/${hour}/${averaging}`
          const res = await fetch(url)
          if (!res.ok) return
          field = await res.json()
        }
        if (!cancelled) fieldRef.current = field
      } catch (_) {
        // silently fail — canvas just fades without new particles
      }
    }

    fetchField()
    return () => { cancelled = true }
  }, [enabled, params])

  // --- Set up canvas + animation loop ---
  useEffect(() => {
    if (!map) return

    if (!enabled) {
      cancelAnimationFrame(animRef.current)
      if (canvasRef.current) {
        canvasRef.current.remove()
        canvasRef.current = null
      }
      return
    }

    const container = map.getContainer()

    const canvas = document.createElement('canvas')
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:1;'
    container.appendChild(canvas)
    canvasRef.current = canvas

    function resize() {
      canvas.width  = container.clientWidth
      canvas.height = container.clientHeight
    }
    resize()

    const ro = new ResizeObserver(resize)
    ro.observe(container)

    function randomParticle(W, H) {
      return {
        x:      Math.random() * W,
        y:      Math.random() * H,
        age:    Math.floor(Math.random() * MAX_AGE_BASE),
        maxAge: MAX_AGE_BASE * (0.5 + Math.random() * 0.8),
      }
    }

    particlesRef.current = Array.from(
      { length: NUM_PARTICLES },
      () => randomParticle(canvas.width, canvas.height),
    )

    function onMoveStart() {
      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, canvas.width, canvas.height)
    }
    map.on('movestart', onMoveStart)

    let running = true

    function animate() {
      if (!running) return

      const ctx   = canvas.getContext('2d')
      const W     = canvas.width
      const H     = canvas.height
      const field = fieldRef.current

      // Fade previous frame
      ctx.fillStyle = `rgba(0,0,0,${FADE_ALPHA})`
      ctx.fillRect(0, 0, W, H)

      if (field && map) {
        ctx.save()
        particlesRef.current.forEach(p => {
          const lngLat = map.unproject([p.x, p.y])
          const [u, v] = sampleUV(field, lngLat.lat, lngLat.lng)
          const magnitude = Math.sqrt(u * u + v * v)

          if (magnitude < 0.1) {
            p.age++
          } else {
            const nx = p.x + u * SPEED_SCALE
            const ny = p.y - v * SPEED_SCALE  // screen Y inverted

            // Height-keyed color: calm → pale blue, high → deep teal
            const t      = Math.min(1, magnitude / 4)
            const radius = 1.5 + t * 2.5          // 1.5–4 px
            const alpha  = 0.25 + t * 0.45         // 0.25–0.70

            const r = Math.round(14  + t * 30)     // 14–44
            const g = Math.round(165 + t * 90)     // 165–255
            const b = Math.round(233 - t * 40)     // 233–193 (bright → deeper teal)

            ctx.beginPath()
            ctx.arc(nx, ny, radius, 0, Math.PI * 2)
            ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`
            ctx.fill()

            p.x = nx
            p.y = ny
            p.age++
          }

          if (p.age > p.maxAge || p.x < -4 || p.x > W + 4 || p.y < -4 || p.y > H + 4) {
            Object.assign(p, randomParticle(W, H))
          }
        })
        ctx.restore()
      }

      animRef.current = requestAnimationFrame(animate)
    }

    animate()

    return () => {
      running = false
      cancelAnimationFrame(animRef.current)
      ro.disconnect()
      map.off('movestart', onMoveStart)
      canvas.remove()
      canvasRef.current = null
    }
  }, [map, enabled])
}
