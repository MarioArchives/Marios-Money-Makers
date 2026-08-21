#!/usr/bin/env node
/**
 * Browser check for the leaderboard re-rank animation (not part of vitest:
 * jsdom has no layout). Loads the running app, intercepts the next
 * `/api/stocks` poll to shuffle `change_percent`, then samples the row
 * slots' transforms until the poll lands and reports how many rows slid,
 * for how long, and that the final DOM order matches the injected ranking.
 *
 * Requires the dev servers to be up and Playwright to be available:
 *   npx -y playwright@1 install chromium      # once
 *   node e2e/leaderboard-reorder.mjs [http://localhost:5173]
 */
const base = process.argv[2] ?? 'http://localhost:5173'

let chromium
try {
  ;({ chromium } = await import('playwright'))
} catch {
  console.error('playwright is not installed. Run: npm i --no-save playwright && npx playwright install chromium')
  process.exit(2)
}

const browser = await chromium.launch()
const page = await browser.newPage()

let injected = null
await page.route('**/api/stocks', async (route) => {
  const response = await route.fetch()
  const body = await response.json()
  // Deterministic shuffle of the ranking: alternate sign, scale by index.
  body.stocks = body.stocks.map((s, i) => ({ ...s, change_percent: (i % 2 ? 1 : -1) * (i + 0.5) }))
  injected = [...body.stocks]
    .sort((a, b) => b.change_percent - a.change_percent)
    .map((s) => s.ticker)
  await route.fulfill({ response, json: body })
})

await page.goto(base)
await page.waitForSelector('[data-testid="stock-slot"]')

const result = await page.evaluate(async () => {
  const slots = () => Array.from(document.querySelectorAll('[data-testid="stock-slot"]'))
  const before = slots().map((s) => s.dataset.ticker)
  const moving = []
  const start = performance.now()
  while (performance.now() - start < 25_000) {
    const n = slots().filter((s) => getComputedStyle(s).transform !== 'none').length
    if (n) moving.push({ t: Math.round(performance.now() - start), n })
    await new Promise((r) => setTimeout(r, 40))
  }
  return { before, after: slots().map((s) => s.dataset.ticker), moving }
})
await browser.close()

const slideMs = result.moving.length ? result.moving.at(-1).t - result.moving[0].t : 0
const ok = injected && JSON.stringify(result.after) === JSON.stringify(injected) && result.moving.length > 0
console.log(`rows that slid: ${Math.max(0, ...result.moving.map((m) => m.n))}, slide window ≈ ${slideMs} ms`)
console.log(`final order matches injected ranking: ${ok ? 'yes' : 'NO'}`)
process.exit(ok ? 0 : 1)
