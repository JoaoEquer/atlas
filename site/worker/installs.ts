// The installers the site serves, and how the Worker counts each download: by system, client and
// country, never by address (docs/ARCHITECTURE.md, The installer). `npm run installs` reads the
// counts back.

/** The system an installer is for: `install.ps1` is Windows's, `install.sh` macOS's and Linux's. */
export type InstallerSystem = 'windows' | 'unix'

/** What asked for it: the line the install page gives, or anything else (a browser, a crawler). */
export type InstallerClient = 'powershell' | 'curl' | 'other'

const INSTALLERS: Record<string, InstallerSystem> = {
  '/install.ps1': 'windows',
  '/install.sh': 'unix'
}

/** The installer a path serves, or null. */
export function installerOf(pathname: string): InstallerSystem | null {
  return INSTALLERS[pathname] ?? null
}

/** Who asked, from the User-Agent: PowerShell's `irm` and curl name themselves. */
export function clientOf(userAgent: string | null): InstallerClient {
  if (!userAgent) {
    return 'other'
  }
  if (/PowerShell\//i.test(userAgent)) {
    return 'powershell'
  }
  if (/^curl\//i.test(userAgent)) {
    return 'curl'
  }
  return 'other'
}

/** One download as Analytics Engine stores it: blob1 the system, blob2 the client, blob3 the country. */
export function downloadPoint(
  system: InstallerSystem,
  userAgent: string | null,
  country: string | undefined
): { blobs: string[]; indexes: string[] } {
  return { blobs: [system, clientOf(userAgent), country || 'unknown'], indexes: [system] }
}
