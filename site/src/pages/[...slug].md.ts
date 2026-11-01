// Each documentation page as Markdown, at its path with `.md` added: /docs/install.md.
import type { APIRoute, GetStaticPaths } from 'astro'

import { SITE_URL } from '../../site.config.mjs'
import { docMarkdown, type DocPage } from '../lib/agents'
import { docPages } from '../lib/docs'

export const getStaticPaths = (async () =>
  (await docPages(true)).map(page => ({ params: { slug: page.id }, props: { ...page } }))) satisfies GetStaticPaths

export const GET: APIRoute = ({ props, site }) =>
  new Response(docMarkdown(props as DocPage, site ?? new URL(SITE_URL)), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' }
  })
