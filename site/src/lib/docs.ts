// The documentation's pages as the files for agents need them: id, header and Markdown body.
import { getCollection } from 'astro:content'

import type { DocPage } from './agents'

/**
 * The pages in English, as `llms.txt` lists them; with `translated`, the translations too
 * (`pt-br/docs/install`), each of which gets its Markdown twin.
 */
export async function docPages(translated = false): Promise<DocPage[]> {
  return (await getCollection('docs'))
    .filter(page => translated || page.id === 'docs' || page.id.startsWith('docs/'))
    .map(page => ({
      id: page.id,
      title: page.data.title,
      description: page.data.description ?? '',
      body: page.body ?? ''
    }))
}
