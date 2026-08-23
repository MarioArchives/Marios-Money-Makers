/** The "invert" step of a FLIP reorder animation, as pure arithmetic. Given each item's position before (`first`) and after (`last`) in the same frame of reference, returns the offset (`first - last`) that, applied as `translateY`, puts it back where it started; unmoved/one-sided items are omitted. */
export function computeFlipOffsets(
  first: ReadonlyMap<string, number>,
  last: ReadonlyMap<string, number>,
): Map<string, number> {
  const offsets = new Map<string, number>()
  last.forEach((lastTop, key) => {
    const firstTop = first.get(key)
    if (firstTop === undefined) {
      return
    }
    const dy = firstTop - lastTop
    if (dy !== 0) {
      offsets.set(key, dy)
    }
  })
  return offsets
}

/** Rank change per item, `oldRank - newRank` (positive = climbed, negative = fell); same arithmetic as `computeFlipOffsets`, applied to indices rather than pixel positions. */
export function computeRankDeltas(
  previousRanks: ReadonlyMap<string, number>,
  nextRanks: ReadonlyMap<string, number>,
): Map<string, number> {
  return computeFlipOffsets(previousRanks, nextRanks)
}

/** Index each key by its position in `order`, for `computeRankDeltas`. */
export function rankOf(order: readonly string[]): Map<string, number> {
  return new Map(order.map((key, index) => [key, index]))
}
