import { describe, expect, it } from 'vitest'

import { homeQuery, homeReport, wilson } from '../src/lib/home-report'
import {
  actionOf,
  answerHit,
  countVisit,
  drawVariant,
  type HomePoint,
  homePoint,
  isVisit,
  variantCookie,
  variantOf
} from '../worker/home'

const CHROME =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'

function navigation(extra: Record<string, string> = {}): Headers {
  return new Headers({ 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'User-Agent': CHROME, ...extra })
}

describe('isVisit', () => {
  it('counts a browser opening the page', () => {
    expect(isVisit(navigation())).toBe(true)
  })

  it('leaves out prefetches, crawlers, scripts and requests that are not a navigation', () => {
    expect(isVisit(navigation({ 'Sec-Purpose': 'prefetch' }))).toBe(false)
    expect(isVisit(navigation({ 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' }))).toBe(false)
    expect(isVisit(navigation({ 'User-Agent': 'Mozilla/5.0 HeadlessChrome/141.0' }))).toBe(false)
    expect(isVisit(new Headers({ 'User-Agent': CHROME }))).toBe(false)
    expect(
      isVisit(new Headers({ 'Sec-Fetch-Dest': 'iframe', 'Sec-Fetch-Mode': 'navigate', 'User-Agent': CHROME }))
    ).toBe(false)
  })
})

describe('the copy a visitor sees', () => {
  it('is read back from its cookie, among others', () => {
    expect(variantOf('theme=dark; atlas_v=a; other=1')).toBe('a')
    expect(variantOf(null)).toBeNull()
    expect(variantOf('atlas_v=z')).toBeNull()
  })

  it('is drawn among the copies running, and kept 30 days', () => {
    expect(drawVariant(() => 0)).toBe('a')
    expect(drawVariant(() => 0.999)).toBe('a')
    expect(variantCookie('a')).toBe('atlas_v=a; Max-Age=2592000; Path=/; Secure; SameSite=Lax')
  })
})

describe('homePoint', () => {
  it('holds the event, the copy, whether the visitor came before and the country, nothing else', () => {
    expect(homePoint('visit', 'a', false, 'BR')).toEqual({ blobs: ['visit', 'a', 'new', 'BR'], indexes: ['a'] })
    expect(homePoint('visit', 'a', true, undefined).blobs).toEqual(['visit', 'a', 'return', 'unknown'])
    expect(homePoint('copy', 'a', true, 'US').blobs).toEqual(['copy', 'a', '', 'US'])
  })

  it('takes only the actions the home sends', () => {
    expect(actionOf('copy')).toBe('copy')
    expect(actionOf('github')).toBe('github')
    expect(actionOf('anything')).toBeNull()
    expect(actionOf(null)).toBeNull()
  })
})

describe('homeReport', () => {
  it('asks for the days given', () => {
    expect(homeQuery(14)).toContain("INTERVAL '14' DAY")
    expect(homeQuery(14)).toContain('FROM atlas_home')
  })

  it('gives each copy its rates per visitor, with their intervals', () => {
    const report = homeReport(
      [
        { event: 'visit', variant: 'a', seen: 'new', count: '200' },
        { event: 'visit', variant: 'a', seen: 'return', count: 40 },
        { event: 'copy', variant: 'a', seen: '', count: '10' },
        { event: 'github', variant: 'a', seen: '', count: 4 }
      ],
      7
    )
    expect(report).toContain('The home in the last 7 days')
    expect(report).toMatch(/Visitors\s+200/)
    expect(report).toMatch(/Visits again\s+40/)
    expect(report).toMatch(/Install copied\s+10, 5\.0% \(2\.7% to 9\.0%\)/)
    expect(report).toMatch(/GitHub opened\s+4, 2\.0%/)
  })

  it('says none when nothing was counted', () => {
    expect(homeReport([], 30)).toBe('The home in the last 30 days\n\nNo visit counted yet.')
  })

  it('narrows the interval as the visitors grow', () => {
    const [low, high] = wilson(50, 1000)
    expect(low).toBeCloseTo(0.038, 3)
    expect(high).toBeCloseTo(0.065, 3)
    expect(wilson(0, 0)).toEqual([0, 0])
  })
})

describe('the Worker', () => {
  function visit(init: RequestInit, points: HomePoint[]): Headers {
    const headers = new Headers()
    countVisit(new Request('https://atlasv4.com/', init), headers, 'BR', point => points.push(point))
    return headers
  }

  it('counts a new visitor once, gives the copy a cookie, and counts it again as returning', () => {
    const points: HomePoint[] = []
    expect(visit({ headers: navigation() }, points).get('Set-Cookie')).toContain('atlas_v=a')
    expect(visit({ headers: navigation({ Cookie: 'atlas_v=a' }) }, points).get('Set-Cookie')).toBeNull()
    expect(points.map(point => point.blobs)).toEqual([
      ['visit', 'a', 'new', 'BR'],
      ['visit', 'a', 'return', 'BR']
    ])
  })

  it('leaves crawlers and a HEAD uncounted, and gives them no cookie', () => {
    const points: HomePoint[] = []
    expect(visit({ headers: { 'User-Agent': 'Googlebot/2.1' } }, points).get('Set-Cookie')).toBeNull()
    visit({ method: 'HEAD', headers: navigation() }, points)
    expect(points).toEqual([])
  })

  it('counts a hit only when the site itself posts it for a visitor it counted', () => {
    const points: HomePoint[] = []
    const hit = (action: string, init: RequestInit) =>
      answerHit(new Request(`https://atlasv4.com/hit?a=${action}`, init), 'BR', point => points.push(point)).status
    const own = { Cookie: 'atlas_v=a', 'Sec-Fetch-Site': 'same-origin' }
    expect(hit('copy', { method: 'POST', headers: own })).toBe(204)
    hit('github', { method: 'POST', headers: own })
    expect(hit('copy', { method: 'POST', headers: { 'Sec-Fetch-Site': 'same-origin' } })).toBe(204)
    hit('copy', { method: 'POST', headers: { ...own, 'Sec-Fetch-Site': 'cross-site' } })
    hit('copy', { headers: own })
    hit('else', { method: 'POST', headers: own })
    expect(points.map(point => point.blobs[0])).toEqual(['copy', 'github'])
  })
})
