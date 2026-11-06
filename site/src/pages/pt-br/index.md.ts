import type { APIRoute } from 'astro'

import { SITE_URL } from '../../../site.config.mjs'
import { homeMarkdown } from '../../lib/agents'

export const GET: APIRoute = ({ site }) =>
  new Response(homeMarkdown(site ?? new URL(SITE_URL), 'pt-br'), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' }
  })
