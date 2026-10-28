import type { APIRoute } from 'astro'

import { installerScript } from '../lib/installers'

export const GET: APIRoute = () => installerScript('install.ps1')
