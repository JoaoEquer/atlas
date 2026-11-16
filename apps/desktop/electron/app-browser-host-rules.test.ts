import { describe, expect, it } from 'vitest'

import { blockedRequest, cdpAllowed, firstAgentPage, parseHostRequest, partitionOf } from './app-browser-rules'

/** The same table is in the server's `tests/browser/test_model.py`: the two lists must not drift. */
const CDP_CASES: [string, boolean][] = [
  ['Page.navigate', true],
  ['Page.captureScreenshot', true],
  ['Runtime.evaluate', true],
  ['DOM.getDocument', true],
  ['Input.dispatchMouseEvent', true],
  ['Network.enable', true],
  ['Accessibility.getFullAXTree', true],
  ['Emulation.setDeviceMetricsOverride', true],
  ['Emulation.clearDeviceMetricsOverride', true],
  ['Emulation.setFocusEmulationEnabled', true],
  ['Inspector.enable', true],
  ['Security.enable', true],
  ['Network.setCacheDisabled', true],
  ['Target.getTargets', false],
  ['Target.createTarget', false],
  ['Browser.close', false],
  ['Browser.getVersion', false],
  ['Page.setDownloadBehavior', false],
  ['Page.close', false],
  ['IO.read', false],
  ['Storage.getCookies', false],
  ['Network.getAllCookies', false],
  ['Network.getCookies', false],
  ['Network.setCookie', false],
  ['Network.deleteCookies', false],
  ['Network.clearBrowserCookies', false],
  ['Network.clearBrowserCache', false],
  ['DOMStorage.getDOMStorageItems', false],
  ['IndexedDB.requestDatabaseNames', false],
  ['CacheStorage.requestCacheNames', false],
  ['Security.setIgnoreCertificateErrors', false],
  ['SystemInfo.getInfo', false],
  ['Tracing.start', false],
  ['page.navigate', false],
  ['Navigate', false],
  ['', false]
]

describe("what a page's DevTools commands may reach", () => {
  it.each(CDP_CASES)('%s: %s', (method, allowed) => {
    expect(cdpAllowed(method)).toBe(allowed)
  })
})

describe('the requests to cloud metadata addresses', () => {
  it('are refused, by name and by number', () => {
    for (const url of [
      'http://169.254.169.254/latest',
      'http://169.254.170.2/',
      'http://[fd00:ec2::254]/',
      'http://metadata.google.internal/',
      'http://METADATA.GOOGLE.INTERNAL./',
      'http://metadata.goog/',
      'http://100.100.100.200/',
      'http://[::ffff:169.254.169.254]/',
      'http://2852039166/'
    ]) {
      expect(blockedRequest(url), url).toBe(true)
    }
  })

  it('leave every other address alone', () => {
    for (const url of ['http://192.168.0.1/', 'https://example.com/', 'http://127.0.0.1:3000/', 'not a url']) {
      expect(blockedRequest(url), url).toBe(false)
    }
  })
})

describe("a home's partition", () => {
  it('is stable, differs by home and machine, and holds no name', () => {
    const work = partitionOf('local', 'work')

    expect(partitionOf('local', 'work')).toBe(work)
    expect(partitionOf('local', 'personal')).not.toBe(work)
    expect(partitionOf('abc123', 'work')).not.toBe(work)
    expect(work).toMatch(/^persist:[a-z0-9-]+$/)
    expect(work).not.toContain('work')
    expect(partitionOf('local', '../../etc/Passwd')).toMatch(/^persist:atlas-browser-[0-9a-f]{16}$/)
  })
})

describe("the server's requests", () => {
  it('are read with their arguments', () => {
    expect(parseHostRequest('browser.tabs', {})).toEqual({ kind: 'tabs' })
    expect(parseHostRequest('browser.close', { id: 't1' })).toEqual({ id: 't1', kind: 'close' })
    expect(parseHostRequest('browser.attach', { id: 't1' })).toEqual({ id: 't1', kind: 'attach' })
    expect(parseHostRequest('browser.open', { background: false, owner: 'c-1', url: 'about:blank' })).toEqual({
      background: false,
      kind: 'open',
      owner: 'c-1',
      url: 'about:blank'
    })
    expect(parseHostRequest('browser.open', { owner: '', url: 'https://example.com' })).toEqual({
      background: true,
      kind: 'open',
      owner: '',
      url: 'https://example.com'
    })
    expect(parseHostRequest('browser.cdp', { id: 't1', method: 'Page.enable', params: { a: 1 } })).toEqual({
      id: 't1',
      kind: 'cdp',
      method: 'Page.enable',
      params: { a: 1 }
    })
    expect(parseHostRequest('browser.cdp', { id: 't1', method: 'Page.enable' })).toEqual({
      id: 't1',
      kind: 'cdp',
      method: 'Page.enable',
      params: {}
    })
  })

  it('are refused when shaped otherwise', () => {
    const cases: [string, unknown][] = [
      ['browser.close', {}],
      ['browser.close', { id: '' }],
      ['browser.show', { id: 3 }],
      ['browser.open', { owner: 3, url: 'x' }],
      ['browser.open', { owner: '', url: 3 }],
      ['browser.open', { owner: '', url: 'x'.repeat(5000) }],
      ['browser.cdp', { id: 't1', params: {} }],
      ['browser.cdp', { id: 't1', method: 'Page.enable', params: [] }],
      ['browser.cdp', { id: 't1', method: 'Page.enable', params: 'x' }],
      ['browser.eval', { id: 't1' }],
      ['conversation.open', {}]
    ]

    for (const [method, value] of cases) {
      expect(parseHostRequest(method, value), `${method} ${JSON.stringify(value)}`).toBeNull()
    }
  })
})

describe("the agent's first page in a conversation", () => {
  it('counts once for each conversation and home', () => {
    const seen = new Set<string>()

    expect(firstAgentPage(seen, 'work', 'c-1')).toBe(true)
    expect(firstAgentPage(seen, 'work', 'c-1')).toBe(false)
    expect(firstAgentPage(seen, 'work', 'c-2')).toBe(true)
    expect(firstAgentPage(seen, 'personal', 'c-1')).toBe(true)
  })
})
