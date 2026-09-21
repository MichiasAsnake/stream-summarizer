import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The app calls a relative /api/v1; in dev, forward it to the backend.
// Set VITE_API_PROXY to point elsewhere (e.g. http://192.168.1.20:8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': process.env.VITE_API_PROXY || 'http://localhost:8000' },
  },
});
