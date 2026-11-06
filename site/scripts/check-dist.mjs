// Checks the built site the way an agent and a visitor would meet it: every page and file agents
// read is there, each documentation page has its Markdown twin, the skill's digest matches its
// bytes, the installers are the repository's, and no internal link leads nowhere. Runs after
// `astro build`; fails the build on a miss.
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const problems = []

function files(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })
}

const all = files(dist).map(path => relative(dist, path).split(sep).join('/'))
const has = path => all.includes(path)

const required = [
  'index.html',
  'index.md',
  '404.html',
  'robots.txt',
  'llms.txt',
  'llms-full.txt',
  'sitemap-index.xml',
  'news/index.html',
  'news.md',
  'news/rss.xml',
  'changelog/index.html',
  'changelog.md',
  'changelog/rss.xml',
  'docs/index.html',
  'docs.md',
  '.well-known/agent-skills/index.json',
  '.well-known/agent-skills/atlas/SKILL.md',
  'install.ps1',
  'install.sh'
]
for (const path of required) {
  if (!has(path)) {
    problems.push(`missing ${path}`)
  }
}

// Every documentation page has a Markdown twin: docs/install/index.html beside docs/install.md.
for (const path of all.filter(p => p.startsWith('docs/') && p.endsWith('/index.html'))) {
  const twin = `${path.slice(0, -'/index.html'.length)}.md`
  if (!has(twin)) {
    problems.push(`${path} has no Markdown twin (${twin})`)
  }
}

const robots = existsSync(join(dist, 'robots.txt')) ? readFileSync(join(dist, 'robots.txt'), 'utf8') : ''
for (const line of ['Content-Signal:', 'Sitemap:', 'User-agent: *']) {
  if (!robots.includes(line)) {
    problems.push(`robots.txt lacks "${line}"`)
  }
}

// The installers served are the repository's, byte for byte: the install page's line runs them.
for (const name of ['install.ps1', 'install.sh']) {
  const built = join(dist, name)
  const source = fileURLToPath(new URL(`../../scripts/${name}`, import.meta.url))
  if (existsSync(built) && !readFileSync(built).equals(readFileSync(source))) {
    problems.push(`${name} differs from scripts/${name}`)
  }
}

// The skill's digest is over the bytes served.
const indexPath = join(dist, '.well-known/agent-skills/index.json')
if (existsSync(indexPath)) {
  const index = JSON.parse(readFileSync(indexPath, 'utf8'))
  for (const skill of index.skills ?? []) {
    const path = join(dist, new URL(skill.url).pathname)
    if (!existsSync(path)) {
      problems.push(`skill ${skill.name} points at a missing file`)
      continue
    }
    const digest = `sha256:${createHash('sha256').update(readFileSync(path)).digest('hex')}`
    if (digest !== skill.digest) {
      problems.push(`skill ${skill.name}'s digest does not match its file`)
    }
  }
}

/** Whether a site path is served by a file in dist. */
function served(pathname) {
  const path = decodeURIComponent(pathname).replace(/^\//, '')
  if (path === '' || path.endsWith('/')) {
    return has(`${path}index.html`)
  }
  return has(path) || has(`${path}/index.html`) || has(`${path}.html`)
}

const llms = existsSync(join(dist, 'llms.txt')) ? readFileSync(join(dist, 'llms.txt'), 'utf8') : ''
const site = (llms.match(/\((https?:\/\/[^/)]+)\//) ?? [])[1]
for (const [, url] of llms.matchAll(/\]\((https?:\/\/[^)]+)\)/g)) {
  if (site && url.startsWith(site) && !served(new URL(url).pathname)) {
    problems.push(`llms.txt links to ${url}, which the site does not serve`)
  }
}

/** The page a site path serves, as a dist path, or null. */
function pageFile(pathname) {
  const path = decodeURIComponent(pathname).replace(/^\//, '')
  for (const candidate of [
    path.endsWith('/') || path === '' ? `${path}index.html` : path,
    `${path}/index.html`,
    `${path}.html`
  ]) {
    if (has(candidate) && candidate.endsWith('.html')) {
      return candidate
    }
  }
  return null
}

const ids = new Map()
/** The ids a page's elements carry: where its `#` links may land. */
function anchorsOf(file) {
  if (!ids.has(file)) {
    const html = readFileSync(join(dist, file), 'utf8')
    ids.set(file, new Set(Array.from(html.matchAll(/\sid="([^"]+)"/g), match => match[1])))
  }
  return ids.get(file)
}

// Internal links in every page, and the section each `#` names: the documentation's pages link to
// each other's sections, and a heading renamed in one must not leave a link in another pointing
// at nothing.
for (const path of all.filter(p => p.endsWith('.html'))) {
  const html = readFileSync(join(dist, path), 'utf8')
  for (const [, target, fragment] of html.matchAll(/\shref="((?:\/(?!\/)[^"#?]*)?)(?:\?[^"#]*)?(?:#([^"]*))?"/g)) {
    if (target === '' && fragment === undefined) {
      continue
    }
    if (target && !served(target)) {
      problems.push(`${path} links to ${target}, which leads nowhere`)
      continue
    }
    if (!fragment) {
      continue
    }
    const file = target ? pageFile(target) : path
    if (file && !anchorsOf(file).has(decodeURIComponent(fragment))) {
      problems.push(`${path} links to ${target}#${fragment}, a section that ${file} does not have`)
    }
  }
}

// The browser's scripts carry no Node: a module that reads the disk or the environment, imported
// by a page's script, throws as it loads and takes the page's other scripts with it.
for (const path of all.filter(p => p.startsWith('_astro/') && p.endsWith('.js'))) {
  const code = readFileSync(join(dist, path), 'utf8')
  const node = /\bprocess\.(cwd|env)\b|\b(readdirSync|readFileSync|execFileSync)\b/.exec(code)
  if (node) {
    problems.push(`${path}, a script for the browser, uses ${node[0]}, which only Node has`)
  }
}

if (problems.length > 0) {
  console.error(`The built site has ${problems.length} problem(s):\n${problems.map(p => `  - ${p}`).join('\n')}`)
  process.exit(1)
}
console.log(`The built site checks out: ${all.length} files, every page agents read in place.`)
