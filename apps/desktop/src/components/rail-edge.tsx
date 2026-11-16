import { useStore } from '@nanostores/react'
import { type KeyboardEvent, type PointerEvent, useRef, useState } from 'react'

import { t } from '@/i18n'
import {
  $panelWide,
  $panelWidth,
  $sidebarWidth,
  type Rail,
  RAIL_MIN,
  railMax,
  resetRailWidth,
  setRailWidth,
  shownWidth,
  widest
} from '@/store/layout'

/** A key's step, as a drag would move it. */
const STEP = 16

/**
 * The edge of the sidebar or of the side panel toward the conversation, which a drag widens or
 * narrows and a double click puts back (a hairline that shows on hover, a few pixels
 * to grab). The arrows move it too, a separator's keys. While it drags, the frames and the terminal
 * under the pointer do not take the pointer away (`rail-resizing`, styles.css).
 */
export function RailEdge({ rail }: { rail: Rail }) {
  const kept = useStore(rail === 'sidebar' ? $sidebarWidth : $panelWidth)
  // The Browser tab drags past the rails' bound (store/layout.ts).
  const wide = useStore($panelWide) && rail === 'panel'
  const max = railMax(rail, wide)
  const width = shownWidth(kept, wide)
  const [dragging, setDragging] = useState(false)
  const drag = useRef<null | { max: number; start: number; x: number }>(null)
  // The sidebar's edge is on its right and the panel's on its left: a drag right widens one and
  // narrows the other.
  const sign = rail === 'sidebar' ? 1 : -1
  const label = rail === 'sidebar' ? t.layout.resizeSidebar : t.layout.resizePanel

  /** Its rail's width on screen and how far it may go, the conversation keeping its minimum. */
  const measure = (edge: HTMLElement) => {
    const current = edge.parentElement?.getBoundingClientRect().width ?? width
    const main = document.querySelector('.main-pane')?.getBoundingClientRect().width ?? 0

    return { current, max: widest(current, main, max) }
  }

  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) {
      return
    }

    drag.current = null
    setDragging(false)
    document.documentElement.classList.remove('rail-resizing')
    setRailWidth(rail, (rail === 'sidebar' ? $sidebarWidth : $panelWidth).get(), true, max)

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowRight' ? STEP : event.key === 'ArrowLeft' ? -STEP : 0

    if (step === 0) {
      return
    }

    event.preventDefault()
    const { current, max: room } = measure(event.currentTarget)
    setRailWidth(rail, Math.min(room, current + sign * step), true, max)
  }

  return (
    <div
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemax={max}
      aria-valuemin={RAIL_MIN}
      aria-valuenow={width}
      className={`group/edge absolute inset-y-0 z-10 w-1.5 cursor-col-resize outline-none ${
        rail === 'sidebar' ? 'right-0 translate-x-1/2' : 'left-0 -translate-x-1/2'
      }`}
      data-testid={`${rail}-edge`}
      onDoubleClick={() => {
        resetRailWidth(rail)
      }}
      onKeyDown={onKey}
      onLostPointerCapture={finish}
      onPointerCancel={finish}
      onPointerDown={event => {
        if (event.button !== 0) {
          return
        }

        event.preventDefault()
        const { current, max: room } = measure(event.currentTarget)
        drag.current = { max: room, start: current, x: event.clientX }
        event.currentTarget.setPointerCapture(event.pointerId)
        document.documentElement.classList.add('rail-resizing')
        setDragging(true)
      }}
      onPointerMove={event => {
        const from = drag.current

        if (from) {
          setRailWidth(rail, Math.min(from.max, from.start + sign * (event.clientX - from.x)), false, max)
        }
      }}
      onPointerUp={finish}
      role="separator"
      tabIndex={0}
      title={label}
    >
      <div
        className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${
          dragging ? 'bg-faint' : 'group-hover/edge:bg-faint group-focus-visible/edge:bg-faint'
        }`}
      />
    </div>
  )
}
