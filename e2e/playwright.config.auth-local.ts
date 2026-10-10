import config from './playwright.config.mock';
import fs from 'node:fs';
import path from 'node:path';

const webServers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];
const originalBaseURL = config.use?.baseURL;
const localBaseURL = 'http://127.0.0.1:3080';
const authStateDirectory = path.resolve(__dirname, '.auth-local');
fs.mkdirSync(authStateDirectory, { recursive: true });
config.use = {
  ...config.use,
  baseURL: localBaseURL,
  storageState: path.join(authStateDirectory, 'storageState.json'),
};

for (const server of webServers) {
  if (server) {
    server.reuseExistingServer = true;
    if (server.url === originalBaseURL) {
      server.url = localBaseURL;
      server.env = {
        ...server.env,
        HOST: '127.0.0.1',
        E2E_USE_MEMORY_MONGO: process.env.E2E_USE_MEMORY_MONGO ?? 'false',
        SESSION_EXPIRY: '600000',
      };
    }
  }
}

// The auth spec needs only the app server; keep the mock model and MCP
// fixtures from the full suite out of this auth-only run.
config.webServer = webServers.filter((server) => server?.url === localBaseURL);

config.globalSetup = require.resolve('./setup/authenticate-only');

export default config;
