const jwt = require('jsonwebtoken');

// Keep this request-shaping unit lane independent of provider/cache startup.
// The real ActionService remains under test; only unrelated integration
// modules imported at file load are replaced.
jest.mock('@librechat/agents', () => ({ GraphEvents: {}, sleep: jest.fn() }));
jest.mock('@librechat/agents/langchain/tools', () => ({
  tool: jest.fn((call) => ({ _call: call })),
}));
jest.mock('@librechat/data-schemas', () => ({
  logger: { debug: jest.fn(), error: jest.fn(), warn: jest.fn() },
  encryptV2: jest.fn(async (value) => value),
  decryptV2: jest.fn(async (value) => value),
}));
jest.mock('@librechat/api', () => ({
  sendEvent: jest.fn(),
  isAbortError: jest.fn(() => false),
  logAxiosError: jest.fn(({ error }) => `error:${error?.message || 'unknown'}`),
  refreshAccessToken: jest.fn(),
  GenerationJobManager: { emitChunk: jest.fn() },
  createSSRFSafeAgents: jest.fn(),
  validateActionOAuthMetadata: jest.fn(),
}));
jest.mock('librechat-data-provider', () => ({
  Time: { TWO_MINUTES: 120000 },
  CacheKeys: { ENCODED_DOMAINS: 'encoded-domains', FLOWS: 'flows' },
  StepTypes: { TOOL_CALLS: 'tool_calls' },
  Constants: {},
  AuthTypeEnum: { None: 'none', OAuth: 'oauth' },
  actionDelimiter: '---',
  isImageVisionTool: jest.fn(() => false),
  actionDomainSeparator: '---',
}));
jest.mock('~/models', () => ({
  findToken: jest.fn(),
  updateToken: jest.fn(),
  createToken: jest.fn(),
  getActions: jest.fn(),
  deleteActions: jest.fn(),
  deleteAssistant: jest.fn(),
}));
jest.mock('~/config', () => ({
  getFlowStateManager: jest.fn(),
  getActionFlowStateManager: jest.fn(),
}));
jest.mock('~/cache', () => ({ getLogStores: jest.fn(() => ({})) }));
const { getActionFlowStateManager } = require('~/config');
const { findToken } = require('~/models');
const { sendEvent } = require('@librechat/api');
const {
  createActionTool,
  isChatMintedActionDomain,
  generateChatMintedToken,
} = require('../ActionService');
const { getChatOAuthOrigin } = require('~/server/utils/chatOAuthOrigin');

/**
 * Real-logic tests for per-user chat-minted JWT injection in createActionTool._call.
 * Only the request executor boundary is faked so we can capture the exact
 * auth headers handed to the OpenAPI request executor.
 */
const makeRequestBuilder = () => {
  const executor = {
    authHeaders: {},
    setParams(params) {
      this.capturedParams = params;
      return this;
    },
    async setAuth() {
      return this;
    },
    execute: jest.fn(async () => ({ data: { ok: true } })),
  };
  const requestBuilder = {
    createExecutor: () => executor,
  };
  return { requestBuilder, executor };
};

const juristaiAction = { metadata: { domain: 'https://api-dev.juristai.org' } };
const externalAction = { metadata: { domain: 'https://api.example.com' } };
const chatUser = { id: '665f1c0ffee1c0ffee1c0ffe', email: 'user@juristai.org' };

describe('chat-minted action auth', () => {
  const OLD_ENV = process.env;

  beforeEach(() => {
    process.env = { ...OLD_ENV, CHAT_SECRET: 'test-chat-secret' };
    delete process.env.CHAT_MINTED_ACTION_DOMAINS;
    delete process.env.CHAT_ACTION_OAUTH_ORIGINS;
    delete process.env.DOMAIN_CLIENT;
    delete process.env.DOMAIN_SERVER;
  });

  afterAll(() => {
    process.env = OLD_ENV;
  });

  describe('isChatMintedActionDomain', () => {
    it('matches the default juristai.org domain and subdomains', () => {
      expect(isChatMintedActionDomain('https://juristai.org')).toBe(true);
      expect(isChatMintedActionDomain('https://api-dev.juristai.org')).toBe(true);
      expect(isChatMintedActionDomain('api-dev.juristai.org')).toBe(true);
    });

    it('matches nougat.law and its subdomains by default', () => {
      expect(isChatMintedActionDomain('https://nougat.law')).toBe(true);
      expect(isChatMintedActionDomain('https://api.nougat.law')).toBe(true);
      expect(isChatMintedActionDomain('api-dev.nougat.law')).toBe(true);
      expect(isChatMintedActionDomain('https://notnougat.law')).toBe(false);
    });

    it('rejects external and suffix-spoofed domains', () => {
      expect(isChatMintedActionDomain('https://api.example.com')).toBe(false);
      expect(isChatMintedActionDomain('https://notjuristai.org')).toBe(false);
      expect(isChatMintedActionDomain('')).toBe(false);
      expect(isChatMintedActionDomain(undefined)).toBe(false);
    });

    it('honors CHAT_MINTED_ACTION_DOMAINS overrides', () => {
      process.env.CHAT_MINTED_ACTION_DOMAINS = 'internal.example.com';
      expect(isChatMintedActionDomain('https://internal.example.com')).toBe(true);
      expect(isChatMintedActionDomain('https://api-dev.juristai.org')).toBe(false);
    });
  });

  describe('getChatOAuthOrigin', () => {
    it('uses the allowlisted Nougat origin that started the chat action flow', () => {
      expect(getChatOAuthOrigin({ headers: { host: 'chat.nougat.law' } })).toBe(
        'https://chat.nougat.law',
      );
    });

    it('keeps existing JuristAI chat flows on their original host', () => {
      expect(getChatOAuthOrigin({ headers: { host: 'chat.juristai.org' } })).toBe(
        'https://chat.juristai.org',
      );
    });

    it('ignores an untrusted browser origin and uses the configured fallback', () => {
      process.env.DOMAIN_SERVER = 'https://chat.juristai.org/';
      expect(getChatOAuthOrigin({ headers: { host: 'evil.example', origin: 'https://evil.example' } })).toBe(
        'https://chat.juristai.org',
      );
    });

    it('uses the local origin when no public origin or fallback is configured', () => {
      expect(getChatOAuthOrigin(undefined)).toBe('http://localhost:3080');
    });
  });

  describe('generateChatMintedToken', () => {
    it('mints an HS256 token django ChatMintedJWTAuthentication accepts', () => {
      const token = generateChatMintedToken(chatUser);
      const claims = jwt.verify(token, 'test-chat-secret', {
        algorithms: ['HS256'],
        issuer: 'librechat',
      });
      expect(claims.sub).toBe(chatUser.id);
      expect(claims.email).toBe(chatUser.email);
      expect(claims.exp).toBeGreaterThan(Math.floor(Date.now() / 1000));
    });

    it('returns null without a signing secret', () => {
      delete process.env.CHAT_SECRET;
      delete process.env.JWT_SECRET;
      expect(generateChatMintedToken(chatUser)).toBeNull();
    });

    it('returns null without a user email', () => {
      expect(generateChatMintedToken({ id: 'abc' })).toBeNull();
      expect(generateChatMintedToken(null)).toBeNull();
    });
  });

  describe('createActionTool._call injection', () => {
    it('injects a per-user bearer token for juristai domains with no auth configured', async () => {
      const { requestBuilder, executor } = makeRequestBuilder();
      const tool = await createActionTool({
        userId: chatUser.id,
        user: chatUser,
        action: juristaiAction,
        requestBuilder,
      });

      await tool._call({ caseId: 'case-1' });

      const header = executor.authHeaders.Authorization;
      expect(header).toMatch(/^Bearer /);
      const claims = jwt.verify(header.slice('Bearer '.length), 'test-chat-secret', {
        algorithms: ['HS256'],
        issuer: 'librechat',
      });
      expect(claims.email).toBe(chatUser.email);
    });

    it('does not inject for non-allowlisted domains', async () => {
      const { requestBuilder, executor } = makeRequestBuilder();
      const tool = await createActionTool({
        userId: chatUser.id,
        user: chatUser,
        action: externalAction,
        requestBuilder,
      });

      await tool._call({});

      expect(executor.authHeaders.Authorization).toBeUndefined();
    });

    it('does not override explicitly configured action auth', async () => {
      const { requestBuilder, executor } = makeRequestBuilder();
      const tool = await createActionTool({
        userId: chatUser.id,
        user: chatUser,
        action: {
          metadata: {
            domain: 'https://api-dev.juristai.org',
            auth: { type: 'service_http', authorization_type: 'bearer' },
            api_key: 'static-key',
          },
        },
        requestBuilder,
      });

      await tool._call({});

      expect(executor.authHeaders.Authorization).toBeUndefined();
    });

    it('fails closed when no user is available for a JuristAI action', async () => {
      const { requestBuilder, executor } = makeRequestBuilder();
      const tool = await createActionTool({
        userId: chatUser.id,
        action: juristaiAction,
        requestBuilder,
      });

      const result = await tool._call({});

      expect(executor.authHeaders.Authorization).toBeUndefined();
      expect(executor.execute).not.toHaveBeenCalled();
      expect(result).toBe('error:Chat authentication unavailable');
    });

    it.each(['https://chat.juristai.org', 'https://chat.nougat.law'])(
      'uses the initiating %s origin for both OAuth redirect URI stages',
      async (origin) => {
        const flowManager = {
          createFlowWithHandler: jest.fn(async (_identifier, _type, handler) => handler()),
          createFlow: jest.fn(async () => ({ access_token: 'access-token' })),
        };
        getActionFlowStateManager.mockReturnValue(flowManager);
        findToken.mockResolvedValue(null);

        const tool = await createActionTool({
          userId: chatUser.id,
          user: chatUser,
          action: {
            action_id: 'action-123',
            metadata: {
              domain: 'https://api.example.com',
              auth: {
                type: 'oauth',
                authorization_url: 'https://provider.example/oauth/authorize',
                scope: 'read',
                client_url: 'https://provider.example/oauth/token',
              },
            },
          },
          encrypted: { oauth_client_id: 'client-id', oauth_client_secret: 'client-secret' },
          res: { req: { headers: { host: new URL(origin).host, origin } } },
          requestBuilder: makeRequestBuilder().requestBuilder,
        });

        await tool._call({}, {
          toolCall: { stepId: 'step-1', id: 'call-1' },
          metadata: { thread_id: 'thread-1', run_id: 'run-1' },
        });

        const callbackUrl = `${origin}/api/actions/action-123/oauth/callback`;
        const sentEvent = sendEvent.mock.calls.at(-1)[1];
        const authorizationUrl = new URL(sentEvent.data.delta.auth);
        const flowMetadata = flowManager.createFlow.mock.calls[0][2];
        expect(authorizationUrl.searchParams.get('redirect_uri')).toBe(callbackUrl);
        expect(flowMetadata.redirect_uri).toBe(callbackUrl);
      },
    );
  });
});
