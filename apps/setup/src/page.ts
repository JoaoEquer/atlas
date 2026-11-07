/** The Setup's window: what it will do, the run, and how it ended. Text goes in as text, never as
 * markup. */
import type { Ended, SetupApi, SetupState } from './bridge'
import type { Line } from './install'
import { WORDS, type Words } from './words'

declare global {
  interface Window {
    setup: SetupApi
  }
}

const setup = window.setup

function element(id: string): HTMLElement {
  const found = document.getElementById(id)

  if (!found) {
    throw new Error(`The page has no #${id}.`)
  }

  return found
}

const title = element('title')
const text = element('text')
const where = element('where')
const progress = element('progress')
const step = element('step')
const warnings = element('warnings')
const warningsList = element('warnings-list')
const details = element('details') as HTMLDetailsElement
const log = element('log')
const actions = element('actions')

interface Action {
  label: string
  primary?: boolean
  run: () => void
}

function show(heading: string, body: string, buttons: Action[]): void {
  title.textContent = heading
  text.textContent = body
  actions.replaceChildren(
    ...buttons.map(({ label, primary, run }) => {
      const button = document.createElement('button')
      button.textContent = label
      button.className = primary ? 'primary' : ''
      button.addEventListener('click', run)

      return button
    })
  )
  actions.querySelector<HTMLButtonElement>('button.primary')?.focus()
}

function addLine({ mark, text: said }: Line): void {
  if (mark === 'step') {
    step.textContent = said
  }

  const row = document.createElement('span')
  row.className = mark
  row.textContent = `${said}\n`
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4
  log.append(row)

  if (atBottom) {
    log.scrollTop = log.scrollHeight
  }
}

function install(words: Words): void {
  document.body.classList.remove('failed')
  log.replaceChildren()
  warnings.hidden = true
  progress.hidden = false
  details.hidden = false
  step.textContent = words.starting
  show(words.runningTitle, '', [])
  setup.install()
}

function ended(words: Words, result: Ended): void {
  progress.hidden = true
  warningsList.replaceChildren(
    ...result.warnings.map(warning => {
      const item = document.createElement('li')
      item.textContent = warning

      return item
    })
  )
  warnings.hidden = result.warnings.length === 0

  if (result.code === 0) {
    show(words.doneTitle, words.doneText, [
      {
        label: words.close,
        primary: true,
        run: () => {
          setup.close()
        }
      }
    ])

    return
  }

  document.body.classList.add('failed')
  details.open = true
  show(words.failedTitle, `${result.error}\n\n${words.failedText}`, [
    {
      label: words.log,
      run: () => {
        setup.openLog()
      }
    },
    {
      label: words.close,
      run: () => {
        setup.close()
      }
    },
    {
      label: words.retry,
      primary: true,
      run: () => {
        install(words)
      }
    }
  ])
}

function start(state: SetupState): void {
  const words = WORDS[state.language]
  document.documentElement.lang = state.language
  element('warnings-title').textContent = words.warnings
  element('details-label').textContent = words.details
  where.textContent = state.root
  setup.onLine(addLine)
  setup.onEnd(result => {
    ended(words, result)
  })

  if (state.installed) {
    show(words.installedTitle, words.installedText, [
      {
        label: words.repair,
        run: () => {
          install(words)
        }
      },
      {
        label: words.open,
        primary: true,
        run: () => {
          show(words.installedTitle, words.opening, [])
          setup.open()
        }
      }
    ])

    return
  }

  show(words.introTitle, words.introText, [
    {
      label: words.install,
      primary: true,
      run: () => {
        install(words)
      }
    }
  ])
}

void setup.state().then(start)
