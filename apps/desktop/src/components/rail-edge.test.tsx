import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import {
  $panelWidth,
  $sidebarWidth,
  boundWidth,
  RAIL_DEFAULT,
  RAIL_MAX,
  RAIL_MIN,
  railMax,
  resetRailWidth,
  setRailWidth,
  shownWidth,
  WIDE_MAX,
  widest
} from '@/store/layout'

import { RailEdge } from './rail-edge'

afterEach(() => {
  cleanup()
  resetRailWidth('sidebar')
  resetRailWidth('panel')
})

describe("the rails' widths", () => {
  it('open the same on both sides', () => {
    expect($sidebarWidth.get()).toBe(RAIL_DEFAULT)
    expect($panelWidth.get()).toBe(RAIL_DEFAULT)
  })

  it('stay within their bounds, and leave the conversation its minimum', () => {
    expect(boundWidth(10)).toBe(RAIL_MIN)
    expect(boundWidth(5000)).toBe(RAIL_MAX)
    expect(boundWidth(300.4)).toBe(300)
    // A 288px rail beside a 500px conversation gives 116px more at most.
    expect(widest(288, 500)).toBe(404)
    expect(widest(288, 300)).toBe(RAIL_MIN)
  })

  it('let the panel pass the bound on the Browser tab only, the conversation keeping its minimum', () => {
    expect(railMax('panel', true)).toBe(WIDE_MAX)
    expect(railMax('panel', false)).toBe(RAIL_MAX)
    expect(railMax('sidebar', true)).toBe(RAIL_MAX)
    // A 640px browser beside a 1000px conversation goes to 1256px, the conversation keeping 384px.
    expect(widest(640, 1000, WIDE_MAX)).toBe(1256)
    expect(widest(640, 1000)).toBe(RAIL_MAX)
    setRailWidth('panel', 1100, true, WIDE_MAX)
    expect($panelWidth.get()).toBe(1100)
    // Kept for the browser, shown within the bounds on another tab.
    expect(shownWidth(1100, true)).toBe(1100)
    expect(shownWidth(1100, false)).toBe(RAIL_MAX)
    expect(shownWidth(300, false)).toBe(300)
  })

  it('keep a width once a drag ends, and forget it on a reset', () => {
    setRailWidth('sidebar', 320)
    expect(localStorage.getItem('atlas.sidebarWidth')).toBeNull()
    setRailWidth('sidebar', 330, true)
    expect(localStorage.getItem('atlas.sidebarWidth')).toBe('330')
    resetRailWidth('sidebar')
    expect($sidebarWidth.get()).toBe(RAIL_DEFAULT)
    expect(localStorage.getItem('atlas.sidebarWidth')).toBeNull()
  })
})

describe("a rail's edge", () => {
  it('moves with the arrows, the panel the other way, and a double click puts it back', () => {
    render(
      <>
        <RailEdge rail="sidebar" />
        <RailEdge rail="panel" />
      </>
    )
    const sidebar = screen.getByTestId('sidebar-edge')
    const panel = screen.getByTestId('panel-edge')
    expect(sidebar.getAttribute('role')).toBe('separator')
    expect(sidebar.getAttribute('aria-valuenow')).toBe(String(RAIL_DEFAULT))

    // jsdom lays nothing out: the rail measures 0, so the step starts from the minimum, and with no
    // conversation on screen the widest is the minimum too.
    setRailWidth('sidebar', 300)
    fireEvent.keyDown(sidebar, { key: 'ArrowRight' })
    expect($sidebarWidth.get()).toBe(RAIL_MIN)
    fireEvent.keyDown(panel, { key: 'ArrowLeft' })
    expect($panelWidth.get()).toBe(RAIL_MIN)
    expect(localStorage.getItem('atlas.panelWidth')).toBe(String(RAIL_MIN))

    fireEvent.doubleClick(panel)
    expect($panelWidth.get()).toBe(RAIL_DEFAULT)
    expect(localStorage.getItem('atlas.panelWidth')).toBeNull()
  })
})
