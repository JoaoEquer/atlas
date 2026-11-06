// How the Worker counts the home: a visit by a person and what that visit does next, the install
// line copied or GitHub opened, each with the copy of the home it saw (docs/ARCHITECTURE.md, The
// home's count). A cookie keeps only that copy's letter, so a second visit is told from a first;
// nothing about the person is kept. `npm run home` reads the counts back.

/** One event as Analytics Engine stores it. */
export interface HomePoint {
  blobs: string[]
  indexes: string[]
}

/** Where the Worker writes an event: the dataset, or nothing without its binding. */
export type WritePoint = (point: HomePoint) => void

/** The copies of the home's opening words. One for now: the count first learns the home's rate. */
export const VARIANTS = ['a'] as const
export type Variant = (typeof VARIANTS)[number]

/** The cookie that remembers which copy a visitor saw, for 30 days. */
export const VARIANT_COOKIE = 'atlas_v'
const COOKIE_DAYS = 30

/** What a visit does that the home counts. */
export const ACTIONS = ['copy', 'github'] as const
export type Action = (typeof ACTIONS)[number]

/** Where the page sends what a visit does. */
export const HIT_PATH = '/hit'

const ROBOTS = /bot|crawl|spider|slurp|preview|fetch|curl|wget|python|node|go-http|headless|lighthouse|monitor/i

/**
 * Whether a request for the home is a person opening it: a browser's top-level navigation, not a
 * prefetch, a crawler or a script. Browsers have sent Sec-Fetch-Dest for years; robots seldom do.
 */
export function isVisit(headers: Headers): boolean {
  if (headers.get('Sec-Fetch-Dest') !== 'document' || headers.get('Sec-Fetch-Mode') !== 'navigate') {
    return false
  }
  if (headers.get('Sec-Purpose') || headers.get('Purpose')) {
    return false
  }
  return !ROBOTS.test(headers.get('User-Agent') ?? '')
}

function isVariant(value: string | undefined): value is Variant {
  return (VARIANTS as readonly string[]).includes(value ?? '')
}

/** The copy a cookie header names, or null when it names none still running. */
export function variantOf(cookie: string | null): Variant | null {
  for (const part of (cookie ?? '').split(';')) {
    const [name, value] = part.trim().split('=')
    if (name === VARIANT_COOKIE && isVariant(value)) {
      return value
    }
  }
  return null
}

/** A copy drawn at random, each as likely as the others. */
export function drawVariant(random: () => number = Math.random): Variant {
  return VARIANTS[Math.min(VARIANTS.length - 1, Math.floor(random() * VARIANTS.length))] ?? 'a'
}

/** The Set-Cookie value that remembers a copy. */
export function variantCookie(variant: Variant): string {
  return `${VARIANT_COOKIE}=${variant}; Max-Age=${COOKIE_DAYS * 86400}; Path=/; Secure; SameSite=Lax`
}

/** The action a hit names, or null for anything else. */
export function actionOf(value: string | null): Action | null {
  return (ACTIONS as readonly string[]).includes(value ?? '') ? (value as Action) : null
}

/**
 * One event as Analytics Engine stores it: blob1 what happened (`visit`, `copy` or `github`),
 * blob2 the copy seen, blob3 whether the visitor came before (`new` or `return`, only for a
 * visit), blob4 the country.
 */
export function homePoint(
  event: 'visit' | Action,
  variant: Variant,
  returning: boolean,
  country: string | undefined
): HomePoint {
  const seen = event === 'visit' ? (returning ? 'return' : 'new') : ''
  return { blobs: [event, variant, seen, country || 'unknown'], indexes: [variant] }
}

/** The answer to the page's beacon, counted only when the site itself posts it for a visitor it counted. */
export function answerHit(request: Request, country: string | undefined, write: WritePoint): Response {
  const action = actionOf(new URL(request.url).searchParams.get('a'))
  const variant = variantOf(request.headers.get('Cookie'))
  if (request.method === 'POST' && action && variant && request.headers.get('Sec-Fetch-Site') === 'same-origin') {
    write(homePoint(action, variant, true, country))
  }
  return new Response(null, { status: 204 })
}

/** Counts a person's visit to the home, and gives a new visitor its copy's cookie in `headers`. */
export function countVisit(request: Request, headers: Headers, country: string | undefined, write: WritePoint): void {
  const seen = variantOf(request.headers.get('Cookie'))
  if (request.method !== 'GET' || !isVisit(request.headers)) {
    return
  }
  const variant = seen ?? drawVariant()
  write(homePoint('visit', variant, seen !== null, country))
  if (!seen) {
    headers.append('Set-Cookie', variantCookie(variant))
  }
}
