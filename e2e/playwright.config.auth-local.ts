import config from './playwright.config.mock';
import path from 'node:path';

const webServers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];
const originalBaseURL = config.use?.baseURL;
const localBaseURL = 'http://127.0.0.1:3080';
config.use = {
  ...config.use,
  baseURL: localBaseURL,
  storageState: path.resolve(__dirname, 'specs/.test-results/auth-local-storage-state.json'),
};

for (const server of webServers) {
  if (server) {
    server.reuseExistingServer = true;
    if (server.url === originalBaseURL) {
      server.url = localBaseURL;
      server.env = { ...server.env, E2E_USE_MEMORY_MONGO: 'false', SESSION_EXPIRY: '600000' };
    }
  }
}

config.globalSetup = require.resolve('./setup/authenticate-only');

export default config;
