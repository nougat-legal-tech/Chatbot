import config from './playwright.config.mock';

const webServers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];
const baseURL = config.use?.baseURL;

for (const server of webServers) {
  if (server) {
    server.reuseExistingServer = true;
  }
}

config.globalSetup = require.resolve('./setup/authenticate-only');

export default config;
