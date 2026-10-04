import path from 'node:path';
import { defineConfig } from '@playwright/test';
import mockConfig from './playwright.config.mock';

const latencyHook = path.resolve(__dirname, 'benchmarks/mongoose-latency-hook.cjs');
const regressionHook = path.resolve(__dirname, 'lighthouse/regression.cjs');
const serverPath = path.resolve(__dirname, 'setup/start-server.js');
const servers = (Array.isArray(mockConfig.webServer) ? mockConfig.webServer : []).filter((server) =>
  server.command.includes(serverPath),
);
if (servers.length !== 1) {
  throw new Error('Lighthouse requires the isolated single-server harness (E2E_REPLICAS=1).');
}

export default defineConfig({
  ...mockConfig,
  testDir: 'lighthouse',
  outputDir: 'lighthouse/.test-results',
  timeout: 300_000,
  retries: 0,
  reporter: [['line']],
  webServer: servers.map((server) => ({
    ...server,
    timeout: 300_000,
    env: {
      ...server.env,
      CONFIG_PATH: path.resolve(__dirname, 'lighthouse/librechat.yaml'),
      ENDPOINTS: 'openAI',
      OPENAI_MODELS: 'gpt-4o-mini',
      ASSISTANTS_API_KEY: '',
      E2E_USE_MEMORY_MONGO: 'true',
      E2E_LATENCY_MONGO_DELAY_MS: '250',
      NODE_OPTIONS: [
        server.env.NODE_OPTIONS,
        `--require="${latencyHook.replaceAll('\\', '/')}"`,
        ...(process.env.LIGHTHOUSE_REGRESSION === 'serial-reads'
          ? [`--require="${regressionHook.replaceAll('\\', '/')}"`]
          : []),
      ].join(' '),
    },
  })),
});
