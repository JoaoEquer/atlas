// The orrery's bodies and their motion, shared by the renderer, the shaders and the labels.
import type { Vec3 } from './math'

/** Strands of the sphere: each opens into one orbit. */
export const STRANDS = 15
/** The logo's double ring, which becomes the orrery's outer frame. */
export const FRAME_RINGS = 2
export const SEGMENTS = 160
export const CURVE_SEGMENTS = 48

export const SPHERE_RINGS: [number, number] = [1.07, 1.14]
export const FRAME_RADII: [number, number] = [2.84, 2.93]
export const ORBIT_OUTER = 2.55
export const ORBIT_INNER = 0.42
/** Radians per second at radius 1; farther orbits turn slower. */
export const SPIN = 0.07

const MID = (STRANDS - 1) / 2

/** The order of a strand's orbit from the outside in: the equator becomes the outermost orbit. */
export function rankOf(strand: number): number {
  const d = strand - MID
  if (Math.abs(d) < 0.5) {
    return 0
  }
  return d < 0 ? -2 * d - 1 : 2 * d
}

export function strandOfRank(rank: number): number {
  if (rank === 0) {
    return MID
  }
  return rank % 2 === 1 ? MID - (rank + 1) / 2 : MID + rank / 2
}

export function orbitRadius(rank: number): number {
  return ORBIT_OUTER - ((ORBIT_OUTER - ORBIT_INNER) * rank) / (STRANDS - 1)
}

export function angularSpeed(radius: number): number {
  return SPIN * Math.pow(radius, -1.25)
}

export interface Executor {
  key: string
  label: string
  rank: number
  phase: number
}

export const executors: Executor[] = [
  { key: 'claude', label: 'Claude Code', rank: 7, phase: 2.35 },
  { key: 'codex', label: 'Codex', rank: 3, phase: 0.55 },
  { key: 'gemini', label: 'Gemini CLI', rank: 11, phase: 4.6 },
  { key: 'deepseek', label: 'DeepSeek', rank: 1, phase: 3.85 }
]

export interface Moon {
  key: string
  label: string
  host: string
  radius: number
  phase: number
}

/** Accounts circle the harness they belong to. */
export const moons: Moon[] = [
  { key: 'personal', label: 'personal', host: 'claude', radius: 0.34, phase: 0.4 },
  { key: 'company', label: 'company', host: 'claude', radius: 0.34, phase: 0.4 + Math.PI }
]
const MOON_SPEED = 0.42

export function bodyAngle(executor: Executor, time: number): number {
  return executor.phase + time * angularSpeed(orbitRadius(executor.rank))
}

export function executorPosition(executor: Executor, time: number): Vec3 {
  const a = orbitRadius(executor.rank)
  const th = bodyAngle(executor, time)
  return [a * Math.cos(th), 0, a * Math.sin(th)]
}

export function moonPosition(moon: Moon, time: number): Vec3 {
  const host = executors.find(executor => executor.key === moon.host)
  const center = host ? executorPosition(host, time) : ([0, 0, 0] as Vec3)
  const phi = moon.phase + time * MOON_SPEED
  return [
    center[0] + moon.radius * Math.cos(phi),
    center[1] + moon.radius * 0.42 * Math.sin(phi),
    center[2] + moon.radius * Math.sin(phi)
  ]
}

export interface RemoteSystem {
  key: string
  label: string
  offset: Vec3
  scale: number
  phase: number
}

/** Other machines, each running its own Atlas. */
export const systems: RemoteSystem[] = [
  { key: 'homelab', label: 'Home lab', offset: [-8.6, 0.9, -5.2], scale: 0.52, phase: 1.1 },
  { key: 'vps', label: 'VPS', offset: [8.2, -0.5, -7.4], scale: 0.48, phase: 2.7 },
  { key: 'office', label: 'Office PC', offset: [0.8, 1.7, -14.5], scale: 0.5, phase: 4.3 }
]

/** The labels the scene draws, in Portuguese, by their key; the names of products stay as they are. */
export const PORTUGUESE_LABELS: Record<string, string> = {
  laptop: 'Este notebook',
  personal: 'pessoal',
  company: 'empresa',
  homelab: 'Homelab',
  office: 'PC do escritório'
}

export type StopState = 'idle' | 'running' | 'limit'

export interface RouteMoment {
  /** Which hop the session is travelling, 0 or 1, or -1 while it stays put. */
  hop: number
  /** How far along that hop, 0 to 1. */
  along: number
  /** Each stop's five-hour window, 0 to 1. */
  meters: [number, number, number]
  states: [StopState, StopState, StopState]
}

export const ROUTE_PERIOD = 9

/** The route's loop: personal fills up, the session hops to company, then to Codex. */
export function routeMoment(time: number): RouteMoment {
  const t = ((time % ROUTE_PERIOD) + ROUTE_PERIOD) % ROUTE_PERIOD
  const ease = (v: number) => v * v * (3 - 2 * v)
  const span = (a: number, b: number) => Math.min(Math.max((t - a) / (b - a), 0), 1)
  if (t < 1.4) {
    return { hop: -1, along: 0, meters: [0.84 + 0.16 * span(0, 1.4), 0.31, 0.12], states: ['running', 'idle', 'idle'] }
  }
  if (t < 3.2) {
    return { hop: 0, along: ease(span(1.4, 3.2)), meters: [1, 0.31, 0.12], states: ['limit', 'idle', 'idle'] }
  }
  if (t < 4.8) {
    return {
      hop: -1,
      along: 0,
      meters: [1, 0.31 + 0.69 * span(3.2, 4.8), 0.12],
      states: ['limit', 'running', 'idle']
    }
  }
  if (t < 6.6) {
    return { hop: 1, along: ease(span(4.8, 6.6)), meters: [1, 1, 0.12], states: ['limit', 'limit', 'idle'] }
  }
  return {
    hop: -1,
    along: 0,
    meters: [1, 1, 0.12 + 0.1 * span(6.6, ROUTE_PERIOD)],
    states: ['limit', 'limit', 'running']
  }
}
