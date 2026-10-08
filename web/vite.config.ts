/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

/**
 * The proxy is the whole point of this config (task P2-11): the app only ever
 * calls `/api/...` relative, so no environment-specific base URL exists in the
 * source at all and there is nothing to rebuild between dev and production.
 *
 * The target is a property of the developer's machine, not of the app. 8000 is
 * a natively-run `uvicorn`; the compose stack publishes the same API on
 * 127.0.0.1:21114 (scaffold §5), so run against the stack with
 * `VITE_API_PROXY=http://localhost:21114 npm run dev`.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 21115,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  // Coverage (`Q-01`): `npm run coverage`. Thresholds are the measured baseline, so a drop
  // fails and a target does not; see docs/guides/testing.md.
  test: {
    // Mutation tools leave copies of the tests behind (`mutants/`, `.stryker-tmp/`).
    exclude: ['**/node_modules/**', '**/mutants/**', '**/.stryker-tmp/**'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/**/*.d.ts'],
      reporter: ['text-summary', 'json-summary'],
    },
  },
})
