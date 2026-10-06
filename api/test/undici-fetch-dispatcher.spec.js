const http = require('node:http');
const { Agent } = require('undici');

describe('Node fetch and npm undici dispatcher compatibility', () => {
  it('uses the pinned npm undici Agent with Node built-in fetch', async () => {
    const npmUndiciVersion = require('undici/package.json').version;
    const builtinUndiciMajor = Number(process.versions.undici.split('.')[0]);
    const npmUndiciMajor = Number(npmUndiciVersion.split('.')[0]);

    expect(npmUndiciMajor).toBe(builtinUndiciMajor);

    const server = http.createServer((_request, response) => {
      response.end('dispatcher-ok');
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });

    const agent = new Agent();
    try {
      const response = await fetch(`http://127.0.0.1:${server.address().port}`, {
        dispatcher: agent,
      });

      expect(response.status).toBe(200);
      await expect(response.text()).resolves.toBe('dispatcher-ok');
    } finally {
      await agent.close();
      await new Promise((resolve) => server.close(resolve));
    }
  });
});
