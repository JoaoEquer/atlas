// The orrery on the home page: Atlas at the centre, the coding agents on its orbits, their
// accounts as moons, the route a session takes when a limit is hit, and the other machines.
// The page's text is the story; this draws it as the reader scrolls.
import { createLabels, place, project } from './labels'
import { clamp, type Mat4, multiply, placement, smooth, type Vec3 } from './math'
import {
  bodyAngle,
  executorPosition,
  executors,
  FRAME_RADII,
  moonPosition,
  moons,
  PORTUGUESE_LABELS,
  routeMoment,
  type RouteMoment,
  strandOfRank,
  STRANDS,
  systems
} from './model'
import { Batch, hex, Renderer, type RGB, SPRITE_FLOATS, type StrandSystem } from './renderer'
import { cameraFor, CHAPTERS, shotAt, storyPosition } from './story'

const STARLIGHT = hex('#e8ecf4')
const MOONLIGHT = hex('#b9c3da')
const BRASS = hex('#ff5a1f')
const SIGNAL = hex('#86b6ff')
const WARM = hex('#fff0d8')
const CORE: Vec3 = [0, 0, 0]
const IDENTITY = placement(CORE, 1)
const NO_TRAIL = new Float32Array(STRANDS).fill(-100)
const NARROW = '(max-width: 860px)'

const mixRGB = (a: RGB, b: RGB, t: number): RGB => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t
]

function midpoint(a: Vec3, b: Vec3, lift: number): Vec3 {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + lift, (a[2] + b[2]) / 2]
}

function bezier(a: Vec3, c: Vec3, b: Vec3, t: number): Vec3 {
  const s = 1 - t
  return [
    s * s * a[0] + 2 * s * t * c[0] + t * t * b[0],
    s * s * a[1] + 2 * s * t * c[1] + t * t * b[1],
    s * s * a[2] + 2 * s * t * c[2] + t * t * b[2]
  ]
}

/** A seeded generator, so the sky is the same on every visit. */
function random(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function makeStars(count: number): Float32Array {
  const rand = random(7)
  const data = new Float32Array(count * SPRITE_FLOATS)
  for (let k = 0; k < count; k++) {
    const u = rand() * 2 - 1
    const th = rand() * Math.PI * 2
    const r = 40 + rand() * 70
    const s = Math.sqrt(1 - u * u)
    const warm = rand()
    const color = warm > 0.8 ? WARM : warm < 0.25 ? SIGNAL : STARLIGHT
    data.set(
      [r * s * Math.cos(th), r * u, r * s * Math.sin(th) - 10, 0.8 + rand() * 1.5, ...color, 0.12 + rand() * 0.42, 2],
      k * SPRITE_FLOATS
    )
  }
  return data
}

interface Particle {
  r0: number
  th0: number
  y0: number
  speed: number
  offset: number
}

function makeParticles(count: number): Particle[] {
  const rand = random(19)
  return Array.from({ length: count }, () => ({
    r0: 1.3 + rand() * 1.8,
    th0: rand() * Math.PI * 2,
    y0: (rand() * 2 - 1) * 0.5,
    speed: 0.09 + rand() * 0.08,
    offset: rand()
  }))
}

const STATUS: Record<string, string> = { limit: 'limit reached', running: 'running', idle: '' }

export function startOrrery(root: HTMLElement): void {
  void begin(root)
}

async function begin(root: HTMLElement): Promise<void> {
  const canvas = root.querySelector<HTMLCanvasElement>('[data-orrery-canvas]')
  const stage = root.querySelector<HTMLElement>('[data-orrery-scene]')
  const layer = root.querySelector<HTMLElement>('[data-orrery-labels]')
  const chapterEls = CHAPTERS.map(id => root.querySelector<HTMLElement>(`[data-chapter="${id}"]`))
  const meters = Array.from(document.querySelectorAll<HTMLElement>('[data-meter]'))
  if (!canvas || !stage || !layer || chapterEls.some(el => !el)) {
    return
  }

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
  const narrowQuery = window.matchMedia(NARROW)
  const finePointer = window.matchMedia('(pointer: fine)')

  let renderer: Renderer
  try {
    renderer = await Renderer.create(canvas, makeStars(520))
  } catch {
    // The page reads the same without the scene: the drawn sphere stays, the meters still move.
    startMetersOnly(meters, reduce.matches)
    return
  }

  // The page's language names the labels: the Portuguese home draws them in Portuguese.
  const portuguese = document.documentElement.lang.toLowerCase().startsWith('pt')
  const said = (key: string, text: string) => (portuguese ? (PORTUGUESE_LABELS[key] ?? text) : text)
  const labels = createLabels(layer, [
    { key: 'atlas', text: 'Atlas', kind: 'core' },
    { key: 'laptop', text: said('laptop', 'This laptop'), kind: 'system' },
    ...executors.map(e => ({ key: e.key, text: e.label, kind: 'planet', status: e.key === 'codex' })),
    ...moons.map(m => ({ key: m.key, text: said(m.key, m.label), kind: 'moon', status: true })),
    ...systems.map(s => ({ key: s.key, text: said(s.key, s.label), kind: 'system' }))
  ])
  const particles = makeParticles(220)
  const batch = new Batch()
  const trail = new Float32Array(STRANDS)

  const textColumn = root.querySelector<HTMLElement>('[data-text-column]')
  let chapters: { top: number; height: number }[] = []
  let width = 1
  let height = 1
  let textRight = 0
  const measure = () => {
    const rect = stage.getBoundingClientRect()
    width = rect.width
    height = rect.height
    textRight = textColumn ? textColumn.getBoundingClientRect().right : width * 0.4
    const dpr = Math.min(window.devicePixelRatio || 1, narrowQuery.matches ? 1.75 : 2)
    renderer.resize(width, height, dpr)
    chapters = chapterEls.map(el => {
      const box = (el as HTMLElement).getBoundingClientRect()
      return { top: box.top + window.scrollY, height: box.height }
    })
  }
  measure()
  new ResizeObserver(measure).observe(root)
  window.addEventListener('resize', measure)
  void document.fonts.ready.then(measure)

  // The pointer, in CSS pixels over the stage, and how strongly it pulls.
  const pointer = { x: 0, y: 0, nx: 0, ny: 0, tx: 0, ty: 0, strength: 0, target: 0 }
  window.addEventListener(
    'pointermove',
    event => {
      if (event.pointerType !== 'mouse' && event.pointerType !== 'pen') {
        pointer.target = 0
        return
      }
      const box = stage.getBoundingClientRect()
      pointer.tx = event.clientX - box.left
      pointer.ty = event.clientY - box.top
      pointer.target = finePointer.matches ? 1 : 0
    },
    { passive: true }
  )
  document.addEventListener('pointerleave', () => {
    pointer.target = 0
  })

  let hovered = ''
  layer.addEventListener('pointerover', event => {
    const key = (event.target as HTMLElement).closest<HTMLElement>('.orrery-label')?.dataset['key']
    hovered = key ?? ''
  })
  layer.addEventListener('pointerout', () => {
    hovered = ''
  })

  let running = false
  let visible = true
  let frameRequest = 0
  let last = performance.now()
  let time = reduce.matches ? 16 : 0
  let revealStart = -1
  let lastSnap = -1
  let lost = false

  const schedule = () => {
    const shouldRun = visible && !document.hidden && !lost
    if (shouldRun && !running) {
      running = true
      last = performance.now()
      frameRequest = requestAnimationFrame(tick)
    } else if (!shouldRun && running) {
      running = false
      cancelAnimationFrame(frameRequest)
    }
  }
  new IntersectionObserver(entries => {
    visible = entries.some(entry => entry.isIntersecting)
    schedule()
  }).observe(root)
  document.addEventListener('visibilitychange', schedule)
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault()
    lost = true
    root.removeAttribute('data-orrery-live')
    schedule()
  })

  function tick(now: number) {
    if (!running) {
      return
    }
    const dt = Math.min((now - last) / 1000, 0.05)
    last = now
    if (!reduce.matches) {
      time += dt
    }
    if (revealStart < 0) {
      revealStart = now
    }
    const reveal = reduce.matches ? 1 : 1 - Math.pow(1 - clamp((now - revealStart) / 2000), 3)

    pointer.x += (pointer.tx - pointer.x) * 0.12
    pointer.y += (pointer.ty - pointer.y) * 0.12
    pointer.strength += (pointer.target - pointer.strength) * 0.06
    const nxTarget = width > 0 ? pointer.tx / width - 0.5 : 0
    const nyTarget = height > 0 ? pointer.ty / height - 0.5 : 0
    pointer.nx += (nxTarget * pointer.target - pointer.nx) * 0.05
    pointer.ny += (nyTarget * pointer.target - pointer.ny) * 0.05

    let position = storyPosition(window.scrollY + window.innerHeight * 0.5, chapters)
    if (reduce.matches) {
      // No camera flights: each chapter's shot, changed with a short fade.
      const snap = Math.round(position)
      if (snap !== lastSnap && lastSnap >= 0) {
        canvas?.animate([{ opacity: 0.2 }, { opacity: 1 }], { duration: 240 })
      }
      lastSnap = snap
      position = snap
    }

    const narrow = narrowQuery.matches
    const planned = shotAt(position, narrow)
    // Beside the text, the scene centres itself in the room the text column leaves, and shrinks
    // to fit it on smaller windows.
    const room = Math.max(width - textRight, width * 0.42)
    const side = narrow ? 0 : planned.side
    const fill =
      side > 0 ? planned.fill + (Math.min(planned.fill, (0.84 * room) / height) - planned.fill) * side : planned.fill
    const shot = { ...planned, fill }
    const aspect = width / height
    const cam = cameraFor(shot, aspect, [pointer.nx * 0.08 * shot.open, -pointer.ny * 0.05 * shot.open])
    const viewProj: Mat4 = multiply(cam.proj, cam.view)
    const shift: [number, number] = [side * (((textRight + room / 2) / width) * 2 - 1), shot.shiftY]
    const opened = smooth(0.55, 1, shot.open)
    const exA = shot.executors * opened
    const route = routeMoment(time)

    // Each orbit with a coding agent on it glows behind the agent.
    trail.fill(-100)
    for (const e of executors) {
      trail[strandOfRank(e.rank)] = bodyAngle(e, time)
    }

    const strandSystems: StrandSystem[] = [
      {
        model: IDENTITY,
        scale: 1,
        open: shot.open,
        alpha: 1,
        spin: 1,
        frameAlpha: 1,
        base: STARLIGHT,
        accent: BRASS,
        trail,
        trailGain: exA
      }
    ]
    if (shot.machines > 0.001) {
      for (const s of systems) {
        strandSystems.push({
          model: placement(s.offset, s.scale),
          scale: s.scale,
          open: 1,
          alpha: shot.machines * 0.85,
          spin: 1.5,
          frameAlpha: 0.8,
          base: mixRGB(STARLIGHT, SIGNAL, 0.55),
          accent: SIGNAL,
          trail: NO_TRAIL,
          trailGain: 0
        })
      }
    }

    batch.reset()
    drawFrameTicks(batch, opened * (1 - shot.machines * 0.55))
    const positions = new Map<string, Vec3>()
    for (const e of executors) {
      positions.set(e.key, executorPosition(e, time))
    }
    for (const m of moons) {
      positions.set(m.key, moonPosition(m, time))
    }

    for (const e of executors) {
      const p = positions.get(e.key) ?? CORE
      const onRoute = e.key === 'claude' || e.key === 'codex'
      const dim = 1 - shot.route * (onRoute ? 0.2 : 0.7)
      const boost = hovered === e.key ? 2.4 : 1
      batch.curve(CORE, midpoint(CORE, p, 0), p, STARLIGHT, 0.17 * exA * dim * boost, {
        core: 1,
        halo: 5,
        dash: 11,
        speed: 0.45
      })
    }
    for (const m of moons) {
      const host = positions.get(m.host) ?? CORE
      const p = positions.get(m.key) ?? CORE
      batch.curve(host, midpoint(host, p, 0), p, MOONLIGHT, 0.16 * exA, { core: 0.9, halo: 4 })
    }

    if (shot.route > 0.001) {
      drawRoute(batch, route, shot.route, positions)
    }

    if (shot.machines > 0.001) {
      for (const s of systems) {
        const control = midpoint(CORE, s.offset, 3.6)
        const head = (time * 0.15 + s.phase * 0.31) % 1
        batch.curve(CORE, control, s.offset, SIGNAL, 0.24 * shot.machines, { core: 1.1, halo: 6, head, tail: 0.09 })
        batch.sprite(bezier(CORE, control, s.offset, head), 5, SIGNAL, shot.machines, 0)
        batch.sprite(s.offset, 16, SIGNAL, shot.machines, 1)
        batch.sprite(s.offset, 40, SIGNAL, 0.45 * shot.machines, 3)
      }
      batch.sprite(CORE, 20, BRASS, shot.machines, 1)
    }

    if (shot.memory > 0.001) {
      for (const p of particles) {
        const life = (time * p.speed + p.offset) % 1
        const r = p.r0 * Math.pow(1 - life, 1.5) + 0.05
        const th = p.th0 + life * 3.4
        const fade = Math.sin(life * Math.PI)
        batch.sprite(
          [r * Math.cos(th), p.y0 * (1 - life), r * Math.sin(th)],
          2.6 + 2.2 * life,
          mixRGB(STARLIGHT, BRASS, Math.min(life * 1.3, 1)),
          shot.memory * fade,
          0
        )
      }
    }

    // The bodies, over their lines.
    const core = clamp(shot.core, 0, 1.4)
    batch.sprite(CORE, 46 * (0.6 + 0.4 * core), WARM, Math.min(core, 1.15), 3)
    for (const e of executors) {
      const boost = hovered === e.key ? 1.5 : 1
      batch.sprite(positions.get(e.key) ?? CORE, 8.5 * boost, STARLIGHT, exA, 0)
    }
    for (const m of moons) {
      batch.sprite(positions.get(m.key) ?? CORE, 5.5, MOONLIGHT, exA, 0)
    }

    renderer.draw({
      time,
      view: cam.view,
      proj: cam.proj,
      shift,
      pointer: [
        pointer.x * renderer.dpr,
        (height - pointer.y) * renderer.dpr,
        pointer.strength * (1 - shot.machines * 0.7)
      ],
      globe: [pointer.nx * 0.42 + Math.sin(time * 0.12) * 0.1, pointer.ny * 0.26],
      reveal,
      systems: strandSystems,
      curves: batch.curves,
      curveCount: batch.curveCount,
      sprites: batch.sprites,
      spriteCount: batch.spriteCount,
      stars: 0.85
    })
    if (!root.hasAttribute('data-orrery-live')) {
      root.setAttribute('data-orrery-live', '')
    }

    // Labels follow their bodies.
    const at = (p: Vec3) => project(p, viewProj, shift, width, height)
    const atlasLabel = labels.get('atlas')
    if (atlasLabel) {
      place(atlasLabel, at(CORE), opened * Math.min(core, 1) * (1 - shot.machines))
    }
    const laptop = labels.get('laptop')
    if (laptop) {
      place(laptop, at(CORE), shot.machines)
    }
    for (const e of executors) {
      const label = labels.get(e.key)
      if (label) {
        place(label, at(positions.get(e.key) ?? CORE), exA * (1 - shot.machines) * (1 - shot.memory * 0.85))
      }
    }
    // An account's name sits on the far side of its moon from the harness, clear of the harness's
    // own name above it.
    for (const m of moons) {
      const label = labels.get(m.key)
      const moonAt = at(positions.get(m.key) ?? CORE)
      const hostAt = at(positions.get(m.host) ?? CORE)
      if (!label) {
        continue
      }
      if (!moonAt || !hostAt) {
        place(label, null, 0)
        continue
      }
      let dx = moonAt[0] - hostAt[0]
      let dy = moonAt[1] - hostAt[1]
      const length = Math.hypot(dx, dy) || 1
      dx /= length
      dy /= length
      place(
        label,
        [moonAt[0] + dx * (14 + 26 * Math.abs(dx)), moonAt[1] + dy * (10 + 6 * Math.abs(dy))],
        exA * (1 - shot.machines) * (1 - shot.memory)
      )
    }
    for (const s of systems) {
      const label = labels.get(s.key)
      if (label) {
        place(label, at(s.offset), shot.machines)
      }
    }
    const stops = [labels.get('personal'), labels.get('company'), labels.get('codex')]
    stops.forEach((label, k) => {
      if (label?.status) {
        const state = route.states[k] ?? 'idle'
        const text = shot.route > 0.3 ? (STATUS[state] ?? '') : ''
        if (label.status.textContent !== text) {
          label.status.textContent = text
          label.element.dataset['state'] = shot.route > 0.3 ? state : 'idle'
        }
      }
    })
    updateMeters(meters, route)

    frameRequest = requestAnimationFrame(tick)
  }

  schedule()
}

function drawFrameTicks(batch: Batch, alpha: number) {
  if (alpha <= 0.01) {
    return
  }
  const r0 = FRAME_RADII[1] + 0.04
  for (let k = 0; k < 72; k++) {
    const th = (k / 72) * Math.PI * 2
    const major = k % 6 === 0
    const r1 = r0 + (major ? 0.17 : 0.07)
    const c = Math.cos(th)
    const s = Math.sin(th)
    const a: Vec3 = [r0 * c, 0, r0 * s]
    const b: Vec3 = [r1 * c, 0, r1 * s]
    batch.curve(a, midpoint(a, b, 0), b, STARLIGHT, alpha * (major ? 0.42 : 0.24), { core: 1, halo: 2.5 })
  }
}

function drawRoute(batch: Batch, route: RouteMoment, strength: number, positions: Map<string, Vec3>) {
  const a = positions.get('personal') ?? CORE
  const b = positions.get('company') ?? CORE
  const c = positions.get('codex') ?? CORE
  const hops: [Vec3, Vec3, Vec3][] = [
    [a, midpoint(a, b, 0.62), b],
    [b, midpoint(b, c, 1.05), c]
  ]
  hops.forEach(([from, control, to], k) => {
    batch.curve(from, control, to, BRASS, 0.2 * strength, { core: 1, halo: 5, dash: 18, speed: 0.35 })
    if (route.hop === k) {
      batch.curve(from, control, to, BRASS, 0.03 * strength, { core: 1.8, halo: 9, head: route.along, tail: 0.3 })
      batch.sprite(bezier(from, control, to, route.along), 9, BRASS, strength, 0)
    }
  })
  const running = route.states.indexOf('running')
  const stop = [a, b, c][running]
  if (stop) {
    batch.sprite(stop, 15, BRASS, 0.9 * strength, 0)
  }
}

function updateMeters(meters: HTMLElement[], route: RouteMoment) {
  meters.forEach((meter, k) => {
    const fill = route.meters[k] ?? 0
    meter.style.setProperty('--fill', fill.toFixed(3))
    const state = route.states[k] ?? 'idle'
    if (meter.dataset['state'] !== state) {
      meter.dataset['state'] = state
    }
  })
}

/** Without WebGL the meters still tell the route's story. */
function startMetersOnly(meters: HTMLElement[], still: boolean) {
  if (meters.length === 0) {
    return
  }
  if (still) {
    updateMeters(meters, routeMoment(8))
    return
  }
  const start = performance.now()
  const step = (now: number) => {
    updateMeters(meters, routeMoment((now - start) / 1000))
    requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}
