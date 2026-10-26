import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Channel reaches the Bank under /bank and the Agent under /agent, on its own origin.
// In Docker Compose nginx does the same (nginx.conf); here the dev server proxies.
const BANK_URL = process.env.BANK_URL ?? 'http://localhost:8000'
const AGENT_URL = process.env.AGENT_URL ?? 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/bank': { target: BANK_URL, rewrite: (path) => path.replace(/^\/bank/, '') },
      '/agent': { target: AGENT_URL, rewrite: (path) => path.replace(/^\/agent/, '') },
    },
  },
})
