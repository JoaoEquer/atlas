import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { checkSshHost, type Cipher, decodeCode, MachineError, Machines } from './machines'

const made: string[] = []
const TOKEN = 'a-token-of-the-other-machine'

afterEach(() => {
  for (const folder of made.splice(0)) {
    rmSync(folder, { force: true, recursive: true })
  }
})

/** A code as `atlas server code` writes it. */
function code(fields: Record<string, unknown>): string {
  return `atlas1.${Buffer.from(JSON.stringify(fields)).toString('base64url')}`
}

const SHOP = code({ home: 'default', port: 18800, tailnet: '100.101.102.103', token: TOKEN })

/** The system's encryption, faked: reversible, and visibly not the plain text. */
function cipher(available = true): Cipher {
  return {
    available: () => available,
    decrypt: sealed =>
      Buffer.from(sealed.toString('utf8'), 'base64')
        .toString('utf8')
        .replace(/^sealed:/, ''),
    encrypt: text => Buffer.from(Buffer.from(`sealed:${text}`).toString('base64'))
  }
}

function folder(): string {
  const created = mkdtempSync(path.join(tmpdir(), 'atlas-machines-'))
  made.push(created)

  return created
}

describe('the connection code', () => {
  it('reads what atlas server code writes', () => {
    expect(decodeCode(`  ${SHOP}\n`)).toEqual({
      home: 'default',
      port: 18800,
      relay: null,
      tailnet: '100.101.102.103',
      token: TOKEN
    })
    expect(decodeCode(code({ home: 'work', port: 1, token: TOKEN })).tailnet).toBeNull()
  })

  it('says why not, and never says the code', () => {
    const cases: [string, RegExp][] = [
      ['hello', /not an Atlas connection code/],
      ['atlas2.e30', /newer Atlas/],
      ['atlas1.%%%', /cut or damaged/],
      [code({ home: 'default', port: 0, token: TOKEN }), /cut or damaged/],
      [code({ home: 'default', port: 70000, token: TOKEN }), /cut or damaged/],
      [code({ home: 'default', port: 1, token: 'short' }), /cut or damaged/],
      [code({ home: '', port: 1, token: TOKEN }), /cut or damaged/],
      [code({ home: 'default', port: 1, tailnet: 'a b', token: TOKEN }), /cut or damaged/],
      ['atlas1.' + Buffer.from('[1]').toString('base64url'), /cut or damaged/]
    ]

    for (const [text, said] of cases) {
      expect(() => decodeCode(text)).toThrow(said)

      try {
        decodeCode(text)
      } catch (error) {
        expect(error).toBeInstanceOf(MachineError)
        expect((error as Error).message).not.toContain(TOKEN)
      }
    }
  })
})

describe('the SSH host', () => {
  it('is only a destination', () => {
    expect(checkSshHost(' user@shop ')).toBe('user@shop')
    expect(checkSshHost('homelab')).toBe('homelab')

    for (const host of ['', '-oProxyCommand=calc', 'ssh shop', 'shop\n-v', 'a\u0000b']) {
      expect(() => checkSshHost(host)).toThrow(MachineError)
    }
  })
})

describe('the machine list', () => {
  it('adds, renames and removes a machine, and keeps its token apart and sealed', () => {
    const at = folder()
    const machines = new Machines(at, cipher())
    const shop = machines.add({ code: SHOP, name: 'Shop', transport: 'tailscale' })

    expect(shop).toMatchObject({ home: 'default', host: '100.101.102.103', name: 'Shop', port: 18800, sshHost: null })
    expect(machines.list()).toEqual([shop])
    expect(machines.token(shop.id)).toBe(TOKEN)
    expect(readFileSync(path.join(at, 'machines.json'), 'utf8')).not.toContain(TOKEN)
    expect(readFileSync(path.join(at, 'machine-tokens.json'), 'utf8')).not.toContain(TOKEN)

    expect(machines.rename(shop.id, ' Store ').name).toBe('Store')
    expect(new Machines(at, cipher()).find(shop.id)?.name).toBe('Store')

    machines.remove(shop.id)
    expect(machines.list()).toEqual([])
    expect(() => machines.token(shop.id)).toThrow(/no token kept/)
  })

  it('adds a machine through a relay, its key kept sealed with the token (step 20)', () => {
    const at = folder()
    const machines = new Machines(at, cipher())
    const key = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8'
    const relay = { key, room: 'room-1', url: 'wss://relay.example.com' }
    const shop = machines.add({
      code: code({ home: 'default', port: 18800, relay, token: TOKEN }),
      name: 'Shop',
      transport: 'relay'
    })

    expect(shop).toMatchObject({ relay: { room: 'room-1', url: 'wss://relay.example.com' }, transport: 'relay' })
    expect(machines.relayKey(shop.id)).toBe(key)
    expect(readFileSync(path.join(at, 'machines.json'), 'utf8')).not.toContain(key)
    expect(() => machines.add({ code: SHOP, name: 'Other', transport: 'relay' })).toThrow(/has no relay/)
    expect(() =>
      machines.add({
        code: code({ home: 'x', port: 1, relay: { ...relay, key: 'short' }, token: TOKEN }),
        name: 'Bad',
        transport: 'relay'
      })
    ).toThrow(/damaged/)
    machines.remove(shop.id)
    expect(() => machines.relayKey(shop.id)).toThrow(/no token kept/)
  })

  it('keeps the token plain, for the user alone, where the system cannot seal it', () => {
    const at = folder()
    const machines = new Machines(at, cipher(false))
    const shop = machines.add({ code: SHOP, name: 'Shop', transport: 'tailscale' })

    expect(machines.token(shop.id)).toBe(TOKEN)
    expect(JSON.parse(readFileSync(path.join(at, 'machine-tokens.json'), 'utf8'))).toEqual({
      [shop.id]: { plain: TOKEN }
    })
  })

  it('adds an SSH machine by its host, and a code without a tailnet only so', () => {
    const machines = new Machines(folder(), cipher())
    const closed = code({ home: 'work', port: 18801, token: TOKEN })

    expect(() => machines.add({ code: closed, name: 'Lab', transport: 'tailscale' })).toThrow(/no Tailscale address/)
    expect(() => machines.add({ code: closed, name: 'Lab', transport: 'ssh' })).toThrow(/Say the SSH host/)
    const lab = machines.add({ code: closed, name: 'Lab', sshHost: 'me@lab', transport: 'ssh' })

    expect(lab).toMatchObject({ home: 'work', host: null, port: 18801, sshHost: 'me@lab', transport: 'ssh' })
  })

  it('keeps names unique and one machine once', () => {
    const machines = new Machines(folder(), cipher())
    const shop = machines.add({ code: SHOP, name: 'Shop', transport: 'tailscale' })

    expect(() => machines.add({ code: SHOP, name: 'Again', transport: 'tailscale' })).toThrow(
      /already in the list, as "Shop"/
    )
    const other = code({ home: 'work', port: 18800, tailnet: '100.101.102.103', token: TOKEN })

    for (const name of ['shop', 'This Computer', '', 'x'.repeat(65)]) {
      expect(() => machines.add({ code: other, name, transport: 'tailscale' })).toThrow(MachineError)
    }

    const work = machines.add({ code: other, name: 'Work', transport: 'tailscale' })
    expect(() => machines.rename(work.id, 'SHOP')).toThrow(/taken/)
    expect(machines.rename(shop.id, 'shop').name).toBe('shop')
    expect(() => machines.rename('gone', 'Gone')).toThrow(/no longer in the list/)
  })

  it('reads nothing it could not have written', () => {
    const at = folder()
    const machines = new Machines(at, cipher())

    for (const text of ['not json', '{}', '[{"id": 1}, {"id": "a", "name": "A", "home": "h", "port": 1}]']) {
      writeFileSync(path.join(at, 'machines.json'), text)
      expect(machines.list()).toEqual([])
    }
  })
})
