import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { useHistoryRange } from './useHistoryRange'

function createWrapper(initialEntry: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter initialEntries={[initialEntry]}>{children}</MemoryRouter>
  }
}

describe('useHistoryRange', () => {
  it('defaults to 1d when the URL has no range param', () => {
    const { result } = renderHook(() => useHistoryRange(), {
      wrapper: createWrapper('/stock/AAPL'),
    })

    expect(result.current[0]).toBe('1d')
  })

  it('reads a valid range from the URL', () => {
    const { result } = renderHook(() => useHistoryRange(), {
      wrapper: createWrapper('/stock/AAPL?range=30d'),
    })

    expect(result.current[0]).toBe('30d')
  })

  it('falls back to 1d for an unknown range value', () => {
    const { result } = renderHook(() => useHistoryRange(), {
      wrapper: createWrapper('/stock/AAPL?range=5y'),
    })

    expect(result.current[0]).toBe('1d')
  })

  it('writes the chosen range to the URL and removes it again for the default', () => {
    const { result } = renderHook(() => ({ hook: useHistoryRange(), location: useLocation() }), {
      wrapper: createWrapper('/stock/AAPL'),
    })

    act(() => result.current.hook[1]('all'))
    expect(result.current.hook[0]).toBe('all')
    expect(result.current.location.search).toBe('?range=all')

    act(() => result.current.hook[1]('1d'))
    expect(result.current.hook[0]).toBe('1d')
    expect(result.current.location.search).toBe('')
  })
})
