import config from './playwright.config.auth-local';
const servers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];
for (const server of servers) {
  if (server) server.timeout = 600_000;
}
export default config;
