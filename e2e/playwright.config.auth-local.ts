import config from './playwright.config.mock';

const webServers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];
const originalBaseURL = config.use?.baseURL;
const localBaseURL = 'http://127.0.0.1:3080';
config.use = { ...config.use, baseURL: localBaseURL };

for (const server of webServers) {
  if (server) {
    server.reuseExistingServer = true;
    if (server.url === originalBaseURL) {
      server.url = localBaseURL;
    }
  }
}

config.globalSetup = require.resolve('./setup/authenticate-only');

export default config;
