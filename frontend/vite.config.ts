import http from 'node:http'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Media range requests must reuse sockets. Without a keep-alive agent the dev
// proxy answers every <video> range with `Connection: close`, so the player can
// only fetch small chunks per handshake and stalls repeatedly while seeking.
const mediaAgent = new http.Agent({ keepAlive: true, maxSockets: 64 })

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        agent: mediaAgent,
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Let the browser keep the connection instead of inheriting the
            // upstream's close hint (which breaks video streaming).
            delete proxyRes.headers.connection
          })
        },
      },
      '/ws': {
        target: 'ws://127.0.0.1:8001',
        ws: true,
      },
    },
  },
})
