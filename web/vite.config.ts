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
})
