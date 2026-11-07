/**
 * A relay endpoint's address (step 20): where a home waits, as its connection code says, riding in
 * the URL the window's client is given (`atlas-relay:` and base64url JSON), so the main process,
 * which has no WebCrypto types, builds it with no tunnel code.
 */

/** Where a home waits at a relay, as its connection code says. */
export interface RelayAddress {
  key: string
  room: string
  url: string
}

/** What a relay endpoint's URL starts with: the address rides in it, base64url JSON. */
export const RELAY_SCHEME = 'atlas-relay:'

export function relayUrl(relay: RelayAddress): string {
  return RELAY_SCHEME + base64url(new TextEncoder().encode(JSON.stringify(relay)))
}

export function readRelayUrl(url: string): RelayAddress {
  return JSON.parse(new TextDecoder().decode(fromBase64url(url.slice(RELAY_SCHEME.length)))) as RelayAddress
}

function base64url(data: Uint8Array): string {
  return btoa(String.fromCharCode(...data))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

function fromBase64url(text: string): Uint8Array {
  const plain = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4))

  return Uint8Array.from(plain, char => char.charCodeAt(0))
}
