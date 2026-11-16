import { describe, expect, it } from 'vitest'

import { normalizeAddress, pageAllowed, parseCommand, permissionGranted } from './app-browser-rules'

describe("the app browser's address bar", () => {
  it('keeps a web address and gives a bare host https', () => {
    expect(normalizeAddress('https://example.com/a?b=1#c')).toBe('https://example.com/a?b=1#c')
    expect(normalizeAddress('  http://example.com  ')).toBe('http://example.com/')
    expect(normalizeAddress('example.com')).toBe('https://example.com/')
    expect(normalizeAddress('docs.example.co.uk/path')).toBe('https://docs.example.co.uk/path')
    expect(normalizeAddress('example.com:8443/x')).toBe('https://example.com:8443/x')
  })

  it("gives this machine's own addresses http, where a dev server has no certificate", () => {
    expect(normalizeAddress('localhost:3000')).toBe('http://localhost:3000/')
    expect(normalizeAddress('127.0.0.1:8000/app')).toBe('http://127.0.0.1:8000/app')
    expect(normalizeAddress('192.168.0.10')).toBe('http://192.168.0.10/')
  })

  it('refuses what is not a web address, and every scheme but http and https', () => {
    for (const typed of [
      '',
      '   ',
      'hello world',
      'hello',
      'javascript:alert(1)',
      'file:///C:/secret.txt',
      'data:text/html,<p>x</p>',
      'atlas-preview://page/1',
      'devtools://devtools/bundled/inspector.html',
      'about:blank',
      'ftp://example.com',
      'http://',
      'https://exa mple.com'
    ]) {
      expect(normalizeAddress(typed), typed).toBeNull()
    }
  })
})

describe('the pages a browser tab may load', () => {
  it('allows web pages and the blank start, nothing else', () => {
    expect(pageAllowed('https://example.com/')).toBe(true)
    expect(pageAllowed('http://localhost:5173/')).toBe(true)
    expect(pageAllowed('about:blank')).toBe(true)

    for (const url of [
      'file:///etc/passwd',
      'javascript:1',
      'data:text/html,x',
      'atlas-preview://page/1',
      'nope',
      ''
    ]) {
      expect(pageAllowed(url), url).toBe(false)
    }
  })
})

describe('what a site may ask of the machine', () => {
  it('is refused, but for a copy to the clipboard', () => {
    expect(permissionGranted('clipboard-sanitized-write')).toBe(true)

    for (const permission of [
      'media',
      'geolocation',
      'notifications',
      'midi',
      'midiSysex',
      'clipboard-read',
      'fullscreen',
      'openExternal',
      'hid',
      'usb',
      'serial',
      'display-capture'
    ]) {
      expect(permissionGranted(permission), permission).toBe(false)
    }
  })
})

describe("the window's commands", () => {
  it('reads each command with its arguments', () => {
    expect(parseCommand({ command: 'open', home: 'work', owner: 'c-1' })).toEqual({
      command: 'open',
      home: 'work',
      owner: 'c-1'
    })
    expect(parseCommand({ command: 'adopt', from: '', home: 'work', to: 'c-1' })).toEqual({
      command: 'adopt',
      from: '',
      home: 'work',
      to: 'c-1'
    })
    expect(parseCommand({ command: 'back', id: 'b1' })).toEqual({ command: 'back', id: 'b1' })
    expect(parseCommand({ command: 'go', home: 'work', id: null, owner: '', text: 'example.com' })).toEqual({
      command: 'go',
      home: 'work',
      id: null,
      owner: '',
      text: 'example.com'
    })
    expect(parseCommand({ command: 'go', home: 'work', owner: 'c-1', text: 'example.com' })).toEqual({
      command: 'go',
      home: 'work',
      id: null,
      owner: 'c-1',
      text: 'example.com'
    })
    expect(parseCommand({ command: 'show', rect: null })).toEqual({ command: 'show', rect: null })
    expect(parseCommand({ command: 'show', rect: { height: 5, width: 4, x: 3, y: 2 } })).toEqual({
      command: 'show',
      rect: { height: 5, width: 4, x: 3, y: 2 }
    })
  })

  it('refuses what is not shaped as one', () => {
    for (const value of [
      null,
      'back',
      42,
      {},
      { command: 'eval', code: 'process.exit()' },
      { command: 'back' },
      { command: 'close', id: 7 },
      { command: 'open' },
      { command: 'open', home: 'work' },
      { command: 'open', home: 'work', owner: 'x'.repeat(300) },
      { command: 'open', home: 'x'.repeat(300), owner: '' },
      { command: 'adopt', from: '', to: 'c-1' },
      { command: 'adopt', from: 3, home: 'work', to: 'c-1' },
      { command: 'go', home: 'work', owner: '', text: 7 },
      { command: 'go', home: 'work', id: 3, owner: '', text: 'x' },
      { command: 'go', home: 'work', owner: '', text: 'x'.repeat(5000) },
      { command: 'go', owner: '', text: 'x' },
      { command: 'go', home: 'work', text: 'x' },
      { command: 'show' },
      { command: 'show', rect: { height: 5, width: 4, x: 3 } },
      { command: 'show', rect: { height: 5, width: Number.NaN, x: 3, y: 2 } },
      { command: 'show', rect: { height: 5, width: 4, x: 1e9, y: 2 } }
    ]) {
      expect(parseCommand(value), JSON.stringify(value)).toBeNull()
    }
  })
})
