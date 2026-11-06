// What the home tells the site's Worker a visit does next: the install line copied, by its button
// or by hand, and GitHub opened, in this tab or another (worker/home.ts). A beacon carries only the
// action; the Worker reads the copy seen from its cookie. The repository's address comes from the
// page: site.config.mjs reads the disk when it loads, and in the browser that stops every script on
// the home, the orrery's too.
const REPOSITORY = document.querySelector<HTMLElement>('[data-repository]')?.dataset['repository'] ?? ''

function hit(action: 'copy' | 'github'): void {
  navigator.sendBeacon(`/hit?a=${action}`)
}

function opened(event: MouseEvent): void {
  const target = event.target instanceof Element ? event.target : null
  if (target?.closest('[data-copy]')) {
    hit('copy')
  } else if (REPOSITORY && target?.closest<HTMLAnchorElement>('a[href]')?.href.startsWith(REPOSITORY)) {
    hit('github')
  }
}

document.addEventListener('click', opened)
document.addEventListener('auxclick', opened)
// The button writes to the clipboard itself and fires no copy event, so the two never count twice.
document.addEventListener('copy', () => {
  const node = document.getSelection()?.anchorNode
  const element = node instanceof Element ? node : node?.parentElement
  if (element?.closest('[data-install-tabs] pre')) {
    hit('copy')
  }
})
