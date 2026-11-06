import { useEffect, useRef } from 'react'

/** The characters of the field around the word, and the densest ones the word is set in, so it
 * reads at a glance. */
const CHARS = '01{}[]<>/|=+*#%&$?:;.,~^'
const DENSE = '#%&$@8B0WM*+='
const FRAME = 1000 / 24
/** How long, in milliseconds, the word takes to assemble. */
const ASSEMBLE = 1400
/** How far, in pixels, the pointer lights the characters around it. */
const REACH = 90
/** The grain's brightness outside the letters, as a floor and a spread. */
const GRAIN: [number, number] = [0.03, 0.06]

type WordmarkProps = {
  /** Where the word sits in its field. */
  align?: 'center' | 'start'
  /** A cell's size in pixels: the smaller, the finer the word. */
  cellH?: number
  cellW?: number
  className?: string
  /** The field's height as a share of its width. */
  ratio?: number
  word: string
}

/**
 * A word set in characters, as the website's title: the word is drawn on a hidden canvas, read back
 * cell by cell, and its cells are set in dense characters that keep changing, over a faint grain.
 * It assembles from left to right when it first shows, a band of Atlas's orange sweeps across it
 * every few seconds, and the pointer lights the characters it passes. Only a picture: whatever it
 * stands on says its word in text too. With less motion asked for it is simply there, and in a
 * window that cannot follow its size (the tests') it draws nothing.
 */
export function Wordmark({
  align = 'center',
  cellH = 8,
  cellW = 5,
  className = '',
  ratio = 0.26,
  word
}: WordmarkProps) {
  const area = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const box = area.current
    const surface = canvas.current
    const context = box && surface && typeof ResizeObserver === 'function' ? surface.getContext('2d') : null

    if (!box || !surface || !context) {
      return
    }

    const still = globalThis.matchMedia('(prefers-reduced-motion: reduce)')
    const pointer = { x: -1e4, y: -1e4 }
    let cols = 0
    let rows = 0
    let mask = new Uint8Array(0)
    let seeds = new Float32Array(0)
    let frame = 0
    let last = 0
    let born = -1
    let visible = true

    const drawWord = () => {
      const sample = document.createElement('canvas')
      sample.width = cols
      sample.height = rows
      const draw = sample.getContext('2d', { willReadFrequently: true })

      if (!draw) {
        return
      }

      // A cell is taller than it is wide, so the word is drawn stretched sideways to look natural.
      const stretch = cellH / cellW
      const face = (px: number) => `800 ${String(px)}px system-ui, 'Segoe UI', sans-serif`
      draw.textAlign = align === 'start' ? 'left' : 'center'
      draw.textBaseline = 'middle'
      let px = rows * 0.88
      draw.font = face(px)
      const wide = draw.measureText(word).width * stretch
      px = Math.floor(px * Math.min((cols * 0.97) / wide, 1))
      draw.font = face(px)
      draw.scale(stretch, 1)
      draw.fillStyle = '#fff'
      draw.fillText(word, align === 'start' ? 0 : cols / stretch / 2, rows / 2 + px * 0.04)
      const pixels = draw.getImageData(0, 0, cols, rows).data

      for (let i = 0; i < mask.length; i++) {
        mask[i] = (pixels[i * 4 + 3] ?? 0) > 110 ? 1 : 0
      }
    }

    const measure = () => {
      const scale = Math.min(window.devicePixelRatio || 1, 2)
      const width = box.clientWidth
      const height = Math.max(Math.floor(width * ratio), cellH * 8)
      cols = Math.max(1, Math.ceil(width / cellW))
      rows = Math.ceil(height / cellH)
      surface.width = cols * cellW * scale
      surface.height = rows * cellH * scale
      surface.style.height = `${String(rows * cellH)}px`
      context.setTransform(scale, 0, 0, scale, 0, 0)
      mask = new Uint8Array(cols * rows)
      seeds = Float32Array.from({ length: cols * rows }, () => Math.random())
      drawWord()
    }

    const render = (time: number) => {
      const light = !document.documentElement.classList.contains('dark')
      const styles = getComputedStyle(surface)
      const ink = styles.getPropertyValue('--foreground').trim() || 'white'
      const accent = styles.getPropertyValue('--brand').trim() || 'orange'
      context.clearRect(0, 0, surface.width, surface.height)
      context.font = `${light ? '700' : '500'} ${String(cellH - 2)}px ui-monospace, 'Cascadia Mono', Consolas, monospace`
      context.textBaseline = 'top'
      const tick = still.matches ? 0 : Math.floor(time / 140)
      const elapsed = still.matches ? Infinity : born < 0 ? 0 : time - born
      const built = elapsed / ASSEMBLE
      const sweep = still.matches ? -1e4 : ((time / 16) % (cols + 120)) - 60

      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const i = y * cols + x
          const seed = seeds[i] ?? 0
          const letter = mask[i] === 1
          const inside = letter && x / cols + seed * 0.12 < built
          const lit = letter && Math.abs(x + y * 0.6 - sweep) < 9
          const away = Math.hypot(x * cellW - pointer.x, y * cellH - pointer.y)
          const glow = away < REACH ? (1 - away / REACH) ** 2 : 0
          const churn = (tick + Math.floor(seed * 97)) % (inside ? 5 : 23) === 0
          const set = inside ? DENSE : CHARS
          const char = set.charAt(Math.floor((seed * 1000 + (churn || glow > 0.05 ? tick : 0)) % set.length))
          const base = inside ? (light ? 1 : 0.82 + seed * 0.18) : (GRAIN[0] + seed * GRAIN[1]) * (light ? 0.7 : 1)
          const bright = glow > 0.25 || lit
          context.fillStyle = bright ? accent : ink
          context.globalAlpha = Math.min(1, base + glow * 0.7)
          context.fillText(char, x * cellW, y * cellH)
        }
      }

      context.globalAlpha = 1
    }

    const loop = (time: number) => {
      if (time - last >= FRAME) {
        last = time
        render(time)
      }

      frame = visible && !still.matches ? requestAnimationFrame(loop) : 0
    }

    const start = () => {
      if (!frame) {
        frame = requestAnimationFrame(loop)
      }
    }

    const onMove = (event: PointerEvent) => {
      const at = box.getBoundingClientRect()
      pointer.x = event.clientX - at.left
      pointer.y = event.clientY - at.top
    }

    const onLeave = () => {
      pointer.x = -1e4
      pointer.y = -1e4
    }

    const onMotion = () => {
      render(0)
      start()
    }

    measure()
    render(0)
    const resize = new ResizeObserver(() => {
      measure()
      render(performance.now())
    })
    resize.observe(box)
    // Off screen, behind another tab, it stops drawing; it assembles the first time it shows.
    const seen =
      typeof IntersectionObserver === 'function'
        ? new IntersectionObserver(entries => {
            visible = entries.some(entry => entry.isIntersecting)

            if (visible && born < 0) {
              born = performance.now()
            }

            if (visible) {
              start()
            }
          })
        : null
    seen?.observe(box)
    born = seen ? born : performance.now()
    box.addEventListener('pointermove', onMove)
    box.addEventListener('pointerleave', onLeave)
    still.addEventListener('change', onMotion)
    start()

    return () => {
      cancelAnimationFrame(frame)
      resize.disconnect()
      seen?.disconnect()
      box.removeEventListener('pointermove', onMove)
      box.removeEventListener('pointerleave', onLeave)
      still.removeEventListener('change', onMotion)
    }
  }, [align, cellH, cellW, ratio, word])

  return (
    <div aria-hidden className={`w-full ${className}`} data-testid="wordmark" data-word={word} ref={area}>
      <canvas className="block w-full" ref={canvas} />
    </div>
  )
}
