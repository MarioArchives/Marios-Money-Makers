// Dev/test default to the local uvicorn; production builds default to the
// same origin ('' -> fetch('/api/...')) because Caddy proxies /api to the
// backend. Either can be overridden at build time via VITE_API_BASE_URL.
const BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.PROD ? '' : 'http://localhost:8000')

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) {
    throw new Error(`API error ${res.status} for ${path}`)
  }
  return res.json() as Promise<T>
}
