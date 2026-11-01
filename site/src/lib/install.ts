// How Atlas is installed, for the home, the install page and their Markdown twins.
import { INSTALLERS, REPOSITORY } from '../../site.config.mjs'

export interface InstallWay {
  id: 'windows' | 'macos' | 'linux' | 'docker'
  label: string
  /** Where the lines are typed. */
  where: string
  lines: string[]
  /** What to do once they have run, when it is more than opening the app. */
  after?: string
  /** The Setup for this system, for whoever does not open a terminal (step 18b). */
  downloads?: Download[]
}

export interface Download {
  label: string
  href: string
}

/** The release the Setup's files are put in; its addresses never change (.github/workflows/setup.yml). */
const SETUP = `${REPOSITORY}/releases/download/setup`

export const installWays: InstallWay[] = [
  {
    id: 'windows',
    label: 'Windows',
    where: 'In PowerShell',
    lines: [`irm ${INSTALLERS}/install.ps1 | iex`],
    downloads: [{ label: 'Atlas-Setup.exe', href: `${SETUP}/Atlas-Setup.exe` }]
  },
  {
    id: 'macos',
    label: 'macOS',
    where: 'In a terminal',
    lines: [`curl -fsSL ${INSTALLERS}/install.sh | bash`],
    downloads: [
      { label: 'Apple Silicon', href: `${SETUP}/Atlas-Setup-arm64.dmg` },
      { label: 'Intel', href: `${SETUP}/Atlas-Setup-x64.dmg` }
    ]
  },
  {
    id: 'linux',
    label: 'Linux',
    where: 'In a terminal',
    lines: [`curl -fsSL ${INSTALLERS}/install.sh | bash`],
    downloads: [{ label: 'AppImage', href: `${SETUP}/Atlas-Setup-x86_64.AppImage` }]
  },
  {
    id: 'docker',
    label: 'Docker',
    where: 'In a terminal, with Docker running',
    lines: [
      `git clone ${REPOSITORY}.git && cd atlas`,
      'docker compose up -d --build',
      'docker compose exec atlas atlas secrets set OPENAI_API_KEY',
      'docker compose exec atlas atlas server token'
    ],
    after:
      'The last line prints the token the app needs. Open the app with ATLAS_DESKTOP_URL=ws://127.0.0.1:18790/ws and ATLAS_DESKTOP_TOKEN set to that token.'
  }
]

/** The ways shown beside the hero's line, before the full install section. */
export const quickWays = installWays.filter(way => way.id !== 'docker')
