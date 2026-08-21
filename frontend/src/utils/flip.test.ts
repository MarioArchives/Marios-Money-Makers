import { describe, expect, it } from 'vitest'
import { computeFlipOffsets, computeRankDeltas, rankOf } from './flip'

const snapshot = (entries: Array<[string, number]>): Map<string, number> => new Map(entries)

describe('computeFlipOffsets', () => {
  it('returns first - last for every item that moved', () => {
    const first = snapshot([['A', 0], ['B', 100], ['C', 200]])
    const last = snapshot([['C', 0], ['A', 100], ['B', 200]])

    expect(computeFlipOffsets(first, last)).toEqual(new Map([['A', -100], ['B', -100], ['C', 200]]))
  })

  it('omits items that did not move', () => {
    const first = snapshot([['A', 0], ['B', 100]])
    const last = snapshot([['A', 0], ['B', 100]])

    expect(computeFlipOffsets(first, last).size).toBe(0)
  })

  it('omits items that entered or left between the snapshots', () => {
    const first = snapshot([['A', 0], ['GONE', 100]])
    const last = snapshot([['NEW', 0], ['A', 100]])

    expect(computeFlipOffsets(first, last)).toEqual(new Map([['A', -100]]))
  })

  it('is unaffected by a uniform shift of both snapshots (e.g. page scroll handled by the caller)', () => {
    // Caller measures container-relative, so a scroll never reaches here;
    // but if both snapshots share a frame the maths is shift-invariant.
    const first = snapshot([['A', 300], ['B', 400]])
    const last = snapshot([['B', 300], ['A', 400]])

    expect(computeFlipOffsets(first, last)).toEqual(new Map([['A', -100], ['B', 100]]))
  })
})

describe('computeRankDeltas', () => {
  it('reports old rank minus new rank: positive for a climb, negative for a fall', () => {
    const previous = new Map([['A', 0], ['B', 1], ['C', 2]])
    const next = new Map([['C', 0], ['A', 1], ['B', 2]])

    expect(computeRankDeltas(previous, next)).toEqual(new Map([['C', 2], ['A', -1], ['B', -1]]))
  })

  it('omits rows that kept their rank or were not in both rankings', () => {
    const previous = new Map([['A', 0], ['GONE', 1], ['B', 2]])
    const next = new Map([['A', 0], ['B', 1], ['NEW', 2]])

    expect(computeRankDeltas(previous, next)).toEqual(new Map([['B', 1]]))
  })
})

describe('rankOf', () => {
  it('maps each key to its index', () => {
    expect(rankOf(['X', 'Y', 'Z'])).toEqual(new Map([['X', 0], ['Y', 1], ['Z', 2]]))
  })
})
