// The site on Cloudflare: the built files, with this Worker in front of the pages. An agent that
// asks for Markdown (Accept: text/markdown) gets the page's Markdown twin; every page announces its
// twin, and the home what agents read first, in Link headers. The installers are served as text,
// and each download counted (installs.ts).
import { downloadPoint, installerOf } from './installs'
import { CONTENT_SIGNAL, pageLinks, twinPath, wantsMarkdown } from './routing'

interface Env {
  ASSETS: Fetcher
  /** Optional: without the binding the installers are still served, only not counted. */
  INSTALLS?: AnalyticsEngineDataset
}

function markdown(response: Response, head: boolean): Response {
  const headers = new Headers(response.headers)
  headers.set('Content-Type', 'text/markdown; charset=utf-8')
  headers.set('Vary', 'Accept')
  headers.set('Content-Signal', CONTENT_SIGNAL)
  return new Response(head ? null : response.body, { status: response.status, headers })
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url)
    const reading = request.method === 'GET' || request.method === 'HEAD'
    const installer = reading ? installerOf(url.pathname) : null
    if (installer) {
      const answer = await env.ASSETS.fetch(request)
      if (!answer.ok) {
        return answer
      }
      if (request.method === 'GET') {
        env.INSTALLS?.writeDataPoint(downloadPoint(installer, request.headers.get('User-Agent'), request.cf?.country))
      }
      // `irm` decodes by the charset it is told, and a script must never be cached stale.
      const headers = new Headers(answer.headers)
      headers.set('Content-Type', 'text/plain; charset=utf-8')
      headers.set('Cache-Control', 'no-store')
      return new Response(answer.body, { status: answer.status, headers })
    }
    if (reading && wantsMarkdown(request.headers.get('Accept'))) {
      const twin = twinPath(url.pathname)
      if (twin) {
        const answer = await env.ASSETS.fetch(new Request(new URL(twin, url), { method: request.method }))
        if (answer.ok) {
          return markdown(answer, request.method === 'HEAD')
        }
      }
    }

    const response = await env.ASSETS.fetch(request)
    if (url.pathname.endsWith('.md') && response.ok) {
      return markdown(response, request.method === 'HEAD')
    }
    if (!(response.headers.get('Content-Type') ?? '').startsWith('text/html')) {
      return response
    }
    const headers = new Headers(response.headers)
    headers.append('Vary', 'Accept')
    for (const link of pageLinks(url.pathname)) {
      headers.append('Link', link)
    }
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers })
  }
} satisfies ExportedHandler<Env>
