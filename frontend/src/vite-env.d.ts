/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  /** Optional override for the USD->GBP rate endpoint (see `api/fx.ts`). */
  readonly VITE_FX_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
