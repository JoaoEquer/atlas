// @ts-check
import starlight from '@astrojs/starlight'
import { defineConfig } from 'astro/config'

import { REPOSITORY, SIDEBAR, SITE_URL } from './site.config.mjs'

// The home, the news and the changelog are Astro pages of their own; the documentation is
// Starlight, under /docs/, styled to match them.
export default defineConfig({
  site: SITE_URL,
  // The loopback's IPv4 address, as `atlas serve` uses: left as "localhost", the server listens on
  // ::1 alone on some Windows machines, and http://127.0.0.1:4321 finds nothing there.
  server: { host: '127.0.0.1', port: 4321 },
  integrations: [
    starlight({
      title: 'Atlas',
      // English at /docs/, and Portuguese at /pt-br/docs/ for the pages translated so far
      // (src/translations/pt-br/); the others show there in English, with Starlight's notice.
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        'pt-br': { label: 'Português', lang: 'pt-BR' }
      },
      description:
        'The documentation of Atlas, the open-source desktop app that runs Claude Code, Codex, the Gemini CLI and DeepSeek on every account and machine.',
      logo: { src: './src/assets/atlas-mark.svg', alt: 'Atlas' },
      favicon: '/favicon.svg',
      social: [{ icon: 'github', label: 'GitHub', href: REPOSITORY }],
      // A page's file is ../src/atlas/docs/pages/..., relative to this folder.
      editLink: { baseUrl: `${REPOSITORY}/edit/main/site/` },
      lastUpdated: true,
      disable404Route: true,
      customCss: [
        '@fontsource-variable/mona-sans/wdth.css',
        '@fontsource-variable/martian-mono/wdth.css',
        './src/styles/docs.css'
      ],
      components: {
        Head: './src/components/docs/Head.astro',
        SiteTitle: './src/components/docs/SiteTitle.astro',
        SocialIcons: './src/components/docs/HeaderLinks.astro'
      },
      // Code in the site's own type and surfaces, with a quiet palette for the syntax.
      expressiveCode: {
        themes: ['vitesse-dark', 'vitesse-light'],
        styleOverrides: {
          borderRadius: '10px',
          borderColor: 'var(--sl-color-hairline-light)',
          codeFontFamily: 'var(--font-mono)',
          codeFontSize: '0.84rem',
          codeLineHeight: '1.75',
          uiFontFamily: 'var(--font-sans)',
          frames: {
            shadowColor: 'transparent',
            editorBackground: 'var(--sl-color-gray-6)',
            terminalBackground: 'var(--sl-color-gray-6)',
            terminalTitlebarBackground: 'var(--sl-color-gray-6)',
            terminalTitlebarBorderBottomColor: 'var(--sl-color-hairline)'
          }
        }
      },
      sidebar: SIDEBAR,
      // Each page's title tag in the words people search with (src/lib/seo-titles.ts).
      routeMiddleware: './src/routeData.ts'
    })
  ]
})
