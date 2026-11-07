import { docsSchema } from '@astrojs/starlight/schema'
import { glob } from 'astro/loaders'
import { z } from 'astro/zod'
import { defineCollection } from 'astro:content'

export const collections = {
  // The documentation's pages live in the package, src/atlas/docs/pages/, where every installed
  // Atlas reads its own version of them with `atlas docs`. The site shows the same files under
  // /docs/: index.md is /docs/, guides/memory.md is /docs/guides/memory/. The pages translated to
  // Portuguese live in the site, src/translations/pt-br/, and show under /pt-br/docs/; the rest
  // falls back to English there (tests/translations.test.ts keeps each one up with its English page).
  docs: defineCollection({
    loader: glob({
      pattern: ['src/atlas/docs/pages/**/*.md', 'site/src/translations/*/**/*.md'],
      base: '..',
      generateId: ({ entry }) => {
        const translated = /^site\/src\/translations\/([^/]+)\/(.*)$/.exec(entry)
        const [locale, file] = translated
          ? [translated[1], translated[2] ?? '']
          : ['', entry.replace(/^src\/atlas\/docs\/pages\//, '')]
        const path = file.replace(/\.md$/, '').replace(/(^|\/)index$/, '')
        const id = path ? `docs/${path}` : 'docs'
        return locale ? `${locale}/${id}` : id
      }
    }),
    schema: docsSchema()
  }),
  // A news item is a short summary in Atlas's own words of a story published elsewhere, with a
  // link to the original. The body is the summary; `atlas` says what the story changes for Atlas.
  news: defineCollection({
    loader: glob({ pattern: '**/[^_]*.md', base: './src/content/news' }),
    schema: z.object({
      title: z.string(),
      date: z.coerce.date(),
      source: z.string(),
      url: z.url(),
      // A story is news, a model or a repository.
      // The story's picture, as its publisher serves it.
      image: z.url().optional(),
      kind: z.enum(['news', 'model', 'repo']).default('news'),
      atlas: z.string().optional(),
      // What the story is about, for the tabs on the news page.
      topics: z.array(z.string()).default([])
    })
  })
}
