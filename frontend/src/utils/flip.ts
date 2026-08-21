/**
 * The "invert" step of a FLIP (First-Last-Invert-Play) reorder animation,
 * as pure arithmetic so it can be unit-tested without layout.
 *
 * Given each item's position before (`first`) and after (`last`) a DOM
 * commit — both measured in the *same* frame of reference, e.g. relative
 * to the list container so page scroll never counts as movement — return
 * the offset (`first - last`) that, applied as `translateY`, puts an item
 * visually back where it started. Items that did not move, or that exist
 * in only one of the two snapshots (entered/left), are omitted.
 */
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

/**
 * Rank change per item between two orderings, as `oldRank - newRank`:
 * positive = climbed that many places, negative = fell. Same arithmetic
 * as `computeFlipOffsets` (old minus new, unchanged/entered/left omitted),
 * applied to list indices rather than pixel positions.
 */
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
