import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig(({mode}) => {
  const envDir = fileURLToPath(new URL('../', import.meta.url));
  const env = loadEnv(mode, envDir, '');
  return {
    plugins: [react()], envDir,
    server: {proxy:{'/api':`http://127.0.0.1:${env.API_PORT || '8000'}`}},
  };
});
