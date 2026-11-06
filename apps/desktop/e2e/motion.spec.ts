import { expect, type Page, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

/**
 * The app's movement (step 9d, the UI review of 25/10/2026): what comes onto the thread while it is
 * watched rises into place, a conversation that opens is there at once, nothing moves with "reduce
 * motion" on in the system, and a streamed reply never holds the window still.
 */

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** From now, whether each part that comes onto the thread moved as it came: `window.rose`. */
async function watchArrivals(page: Page): Promise<void> {
  await page.evaluate(() => {
    const rose: boolean[] = []
    Object.assign(window, { rose })
    const thread = document.querySelector('[data-testid="thread"]')
    if (!thread) {
      throw new Error('no thread on screen')
    }
    new MutationObserver(records => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node instanceof HTMLElement) {
            for (const part of [node, ...node.querySelectorAll<HTMLElement>('.enter')]) {
              // Asking for its animations computes its first style, where a rise starts.
              if (part.classList.contains('enter')) {
                rose.push(part.getAnimations().length > 0)
              }
            }
          }
        }
      }
    }).observe(thread, { childList: true, subtree: true })
  })
}

async function arrivals(page: Page): Promise<boolean[]> {
  return page.evaluate(() => (window as unknown as { rose: boolean[] }).rose)
}

/** The movements under way that last: a pulse, a rise (a "reduced" one lasts a hundredth of a ms). */
async function moving(page: Page): Promise<number> {
  return page.evaluate(
    () =>
      document
        .getAnimations()
        .filter(animation => animation.playState === 'running')
        .filter(animation => Number(animation.effect?.getComputedTiming().duration ?? 0) > 1).length
  )
}

test('what comes onto the thread rises into place, and a conversation that opens is there at once', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'The first answer.' }, { text: 'The second answer.' }, { text: 'Another answer.' })
  await say(page, 'A first conversation')
  await expect(page.getByTestId('assistant-message').last()).toContainText('The first answer.')

  await watchArrivals(page)
  await say(page, 'Once more')
  await expect(page.getByTestId('assistant-message').last()).toContainText('The second answer.')
  // The message on its way and the reply's parts rose.
  expect((await arrivals(page)).filter(Boolean).length).toBeGreaterThanOrEqual(2)

  await page.getByRole('button', { name: 'New conversation' }).click()
  await say(page, 'A second conversation')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Another answer.')

  await watchArrivals(page)
  await page.getByTestId('conversation-row').filter({ hasText: 'A first conversation' }).click()
  await expect(page.getByTestId('assistant-message').last()).toContainText('The second answer.')
  // Its tab kept it drawn: shown again, nothing on it rises.
  expect((await arrivals(page)).filter(Boolean)).toEqual([])
})

test('with "reduce motion" on in the system, nothing moves', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'The first answer.', wait_before: 4 }, { text: 'The second answer.', wait_before: 4 })

  // The turn at work pulses, as the proof that the movement is seen here at all.
  await say(page, 'A turn at work')
  await expect(page.getByRole('status', { name: 'Working' })).toBeVisible()
  expect(await moving(page)).toBeGreaterThan(0)
  await expect(page.getByTestId('assistant-message').last()).toContainText('The first answer.', { timeout: 15_000 })

  await page.emulateMedia({ reducedMotion: 'reduce' })
  // The window takes the emulated setting a moment later: measured before it did, on a busy
  // runner, two of the first turn's animations still ran (the CI's Windows, 03/11/2026).
  await expect.poll(() => page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)
  await expect.poll(() => moving(page)).toBe(0)
  await say(page, 'Another turn at work')
  await expect(page.getByRole('status', { name: 'Working' })).toBeVisible()
  expect(await moving(page)).toBe(0)
  await expect(page.getByTestId('assistant-message').last()).toContainText('The second answer.', { timeout: 15_000 })
  expect(await moving(page)).toBe(0)
})

test('a streamed reply never holds the window still', async () => {
  const { model, page } = desktop
  const words = Array.from({ length: 120 }, (_, index) => `word${String(index)}`).join(' ')
  await model.reply({ text: `A long answer:\n\n${words}\n\n- one\n- two\n\n**Done.**`, pause: 0.01 })
  // The window can tell a long frame, or the test would prove nothing.
  expect(await page.evaluate(() => PerformanceObserver.supportedEntryTypes.includes('long-animation-frame'))).toBe(true)
  await page.evaluate(() => {
    const long: number[] = []
    Object.assign(window, { long })
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        long.push(entry.duration)
      }
    }).observe({ type: 'long-animation-frame' })
  })

  await say(page, 'Stream a long answer')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Done.', { timeout: 30_000 })

  // A frame the eye sees as a stall, with room for a slow machine: here the streaming, drawn piece
  // by piece, held none past 50 ms (26/10/2026).
  const long = await page.evaluate(() => (window as unknown as { long: number[] }).long)
  expect(Math.max(0, ...long)).toBeLessThan(250)
})
