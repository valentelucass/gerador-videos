import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // OneDrive can miss native filesystem events; polling keeps HMR reliable.
  server: {
    host: '127.0.0.1',
    port: 5173,
    watch: { usePolling: true, interval: 300 },
  },
})
