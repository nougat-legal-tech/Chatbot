const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

describe('JuristAI Django MCP configuration', () => {
  it('uses the shared-secret gate without triggering OAuth discovery', () => {
    const configPath = path.resolve(__dirname, '../../../../librechat.yaml');
    const config = yaml.load(fs.readFileSync(configPath, 'utf8'));
    const server = config?.mcpServers?.['juristai-django'];

    expect(server).toEqual(
      expect.objectContaining({
        type: 'streamable-http',
        url: 'http://127.0.0.1:8001/mcp',
        requiresOAuth: false,
        headers: { 'x-mcp-secret': '${MCP_SERVER_SECRET}' },
      }),
    );
  });
});
