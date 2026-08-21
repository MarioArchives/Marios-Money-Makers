import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DEFAULT_HISTORY_RANGE, isHistoryRange, type HistoryRange } from '../api/types'

export const RANGE_SEARCH_PARAM = 'range'

/**
 * The stock detail page's history range, kept in the URL (`?range=30d`)
 * so it survives reloads, is shareable, and needs no page-level state.
 * Unknown or absent values resolve to the default (`1d`); setting the
 * default removes the param again to keep URLs clean.
 */
export function useHistoryRange(): [HistoryRange, (range: HistoryRange) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const raw = searchParams.get(RANGE_SEARCH_PARAM)
  const range: HistoryRange = isHistoryRange(raw) ? raw : DEFAULT_HISTORY_RANGE

  const setRange = useCallback(
    (next: HistoryRange) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          if (next === DEFAULT_HISTORY_RANGE) {
            params.delete(RANGE_SEARCH_PARAM)
          } else {
            params.set(RANGE_SEARCH_PARAM, next)
          }
          return params
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  return [range, setRange]
}
