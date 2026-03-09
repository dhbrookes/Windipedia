/**
 * Animated wind particle overlay — Windy/Ventusky style flow lines.
 *
 * Always active regardless of the selected variable — provides persistent
 * background flow context. Line size scales with zoom so particles appear
 * geographically proportional (smaller at low zoom, larger when zoomed in).
 */

import { useEffect, useRef } from 'react'
import { buildMockWindField } from '../mocks/mockApi'

const IS_MOCK = import.meta.env.VITE_MOCK_API === 'true'

const NUM_PARTICLES = 1000
const SPEED_SCALE   = 0.35   // screen pixels per m/s per frame
const FADE_ALPHA    = 0.055  // how fast trails fade (lower = longer trails)
const MAX_AGE_BASE  = 120    // base particle lifetime in frames

// ---------------------------------------------------------------------------
// Wind field bilinear sampling
// ---------------------------------------------------------------------------

/**
 * Bilinearly interpolate U and V at (lat, lon) from the wind field grid.
 * field.lats is N→S (descending); field.lons is W→E (ascending).
 */
function sampleUV(field, lat, lon) {
  const { lats, lons, u, v, width, height } = field

  // Normalize longitude to [-180, 180]
  lon = ((lon + 180) % 360 + 360) % 360 - 180

  const latMax = lats[0]
  const latMin = lats[height - 1]
  const lonMin = lons[0]
  const lonMax = lons[width - 1]

  // latIdx increases going south (lats is N→S)
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

export function useWindParticles(map, params) {
  const fieldRef     = useRef(null)
  const particlesRef = useRef([])
  const animRef      = useRef(null)
  const canvasRef    = useRef(null)

  // --- Fetch wind field whenever params change ---
  useEffect(() => {
    fieldRef.current = null  // clear while fetching

    const { startYear, endYear, startMonth, endMonth, hourUtc, averaging } = params
    const hour = hourUtc === 'all' ? 'all' : String(hourUtc).padStart(2, '0')

    let cancelled = false

    async function fetchField() {
      try {
        let field
        if (IS_MOCK) {
          field = buildMockWindField()
        } else {
          const url = `/api/wind/field/${startYear}/${endYear}/${startMonth}/${endMonth}/${hour}/${averaging}`
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
  }, [params])

  // --- Set up canvas + animation loop ---
  useEffect(() => {
    if (!map) return

    const container = map.getContainer()

    // Create canvas overlay (pointer-events: none so map clicks pass through)
    const canvas = document.createElement('canvas')
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:2;'
    container.appendChild(canvas)
    canvasRef.current = canvas

    function resize() {
      canvas.width  = container.clientWidth
      canvas.height = container.clientHeight
    }
    resize()

    const ro = new ResizeObserver(resize)
    ro.observe(container)

    // Initialise particles at random screen positions, staggered ages
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

    // Clear stale trails whenever the map moves (pan/zoom)
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

      // Fade previous frame toward transparent (destination-out avoids black accumulation)
      ctx.globalCompositeOperation = 'destination-out'
      ctx.fillStyle = `rgba(0,0,0,${FADE_ALPHA})`
      ctx.fillRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'source-over'

      if (field && map) {
        ctx.save()
        particlesRef.current.forEach(p => {
          // Convert screen position → geographic coords to sample the wind field
          const lngLat = map.unproject([p.x, p.y])
          const [u, v] = sampleUV(field, lngLat.lat, lngLat.lng)
          const speed  = Math.sqrt(u * u + v * v)  // m/s

          if (speed < 0.2) {
            // Dead air: just age the particle
            p.age++
          } else {
            // Scale movement and line width with zoom so particles appear
            // geographically proportional (smaller at low zoom, larger when zoomed in)
            const zoomScale = Math.max(0.12, map.getZoom() / 3)
            const nx = p.x + u * SPEED_SCALE * zoomScale
            const ny = p.y - v * SPEED_SCALE * zoomScale  // screen Y inverted

            // Speed-keyed color: calm→blue-white, strong→bright white
            const t     = Math.min(1, speed / 18)
            const alpha = 0.18 + t * 0.27
            const r     = Math.round(180 + t * 75)   // 180–255
            const g     = Math.round(210 + t * 45)   // 210–255
            const b     = 255

            ctx.beginPath()
            ctx.moveTo(p.x, p.y)
            ctx.lineTo(nx, ny)
            ctx.strokeStyle = `rgba(${r},${g},${b},${alpha})`
            ctx.lineWidth   = (0.6 + t * 1.2) * zoomScale
            ctx.stroke()

            p.x = nx
            p.y = ny
            p.age++
          }

          // Reset particle when it expires or leaves the canvas
          if (p.age > p.maxAge || p.x < -2 || p.x > W + 2 || p.y < -2 || p.y > H + 2) {
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
  }, [map])  // re-run only when the map instance changes
}
