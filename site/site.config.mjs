// Where the site lives and what it is built from, in one place for the pages, the feeds and the
// files for agents.
import { readdirSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

/**
 * The site's address. The domain is still open (ROADMAP, step 19): until it is chosen, the build
 * takes it from SITE_URL and falls back to a placeholder that no deploy should keep.
 */
export const SITE_URL = process.env.SITE_URL ?? 'https://atlasv4.com'

/** The public repository the site, the app and the installers come from. */
export const REPOSITORY = 'https://github.com/marqueskaio/atlas'

/** Where the installers are served from: the site itself, which counts each download. */
export const INSTALLERS = SITE_URL

/**
 * The documentation's pages, in the package, where `atlas docs` reads them too. Found from the
 * site's folder, where every build and test runs: this file is also bundled into the build, where
 * its own address is elsewhere.
 */
export const PAGES = new URL('../src/atlas/docs/pages/', pathToFileURL(`${process.cwd()}/`))

/** The installers, `install.ps1` and `install.sh`, which the build copies to the site's root. */
export const SCRIPTS = new URL('../scripts/', pathToFileURL(`${process.cwd()}/`))

const UNORDERED = 10_000

/**
 * A folder's pages as sidebar items, in the order `atlas docs` lists them: by the `sidebar.order`
 * of their header, then by name. Listed one by one, since Starlight makes a group from a folder
 * only under its own `src/content/docs`.
 */
function folder(name) {
  return readdirSync(new URL(`${name}/`, PAGES))
    .filter(file => file.endsWith('.md'))
    .map(file => {
      const text = readFileSync(new URL(`${name}/${file}`, PAGES), 'utf8')
      const header = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)?.[1] ?? ''
      const order = /^\s+order:\s*(-?\d+)\s*$/m.exec(header)?.[1]
      return { slug: `docs/${name}/${file.slice(0, -3)}`, order: order === undefined ? UNORDERED : Number(order) }
    })
    .sort((a, b) => a.order - b.order || a.slug.localeCompare(b.slug))
    .map(({ slug }) => ({ slug }))
}

/**
 * The documentation's sidebar: the pages at the top, then one group per folder, in the order of
 * `atlas.docs.GROUPS`. `llms.txt` and the full Markdown follow the same order.
 */
export const SIDEBAR = [
  {
    label: 'Start here',
    items: [
      { label: 'What Atlas is', slug: 'docs' },
      { label: 'Install', slug: 'docs/install' },
      { label: 'Troubleshooting', slug: 'docs/troubleshooting' },
      { label: 'Questions and answers', slug: 'docs/faq' }
    ]
  },
  { label: 'Guides', items: folder('guides') },
  { label: 'Reference', items: folder('reference') },
  { label: 'Extending Atlas', items: folder('extending') },
  {
    label: 'For contributors',
    items: [
      { label: 'How to work on Atlas', link: `${REPOSITORY}/blob/main/AGENTS.md` },
      { label: 'Architecture', link: `${REPOSITORY}/blob/main/docs/ARCHITECTURE.md` },
      { label: 'Glossary', link: `${REPOSITORY}/blob/main/docs/GLOSSARY.md` }
    ]
  }
]
