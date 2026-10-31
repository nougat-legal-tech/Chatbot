/**
 * Regression for the production defect where a brand-new JuristAI signup could
 * never use chat: JuristAI owns signup, so the user has no LibreChat Mongo
 * document yet. `jwtLogin` verified the token signature fine and then failed
 * with "no user found", returning 401 to chat_proxy — which silenced both the
 * in-app assistant and every email-agent reply that routed through it.
 *
 * This drives the real `requireJwtAuth` middleware and the real
 * `chatMintedJwtStrategy` with a token shaped exactly like the one
 * `Lambda_chat_proxy.generate_chat_token` mints, and asserts the fallthrough:
 * `jwt` rejects the unknown user, `chatMintedJwt` provisions it, request
 * succeeds.
 *
 * The model layer and `@librechat/api` are stubbed only because requiring them
 * pulls in native image dependencies that are not needed for auth ordering.
 */
const jwt = require('jsonwebtoken');
const express = require('express');
const request = require('supertest');
const passport = require('passport');

const JWT_SECRET = 'deployed-jwt-secret';
// chat_proxy derives a stable 24-hex Mongo ObjectId from the Cognito sub.
const CHAT_PROXY_USER_ID = '19e0ad79733f781d53950677';
const EMAIL = 'agent+1786490504@juristai.org';

const mockUsers = new Map();

jest.mock('~/models', () => ({
  getUserById: async (id) => mockUsers.get(String(id)) || null,
  createUser: async (doc) => {
    const created = { ...doc };
    mockUsers.set(String(doc._id), created);
    return created;
  },
  updateUser: async () => {},
}));

jest.mock('@librechat/api', () => ({
  AGENT_TRIGGER_SCOPE: 'agent_trigger',
  isEnabled: () => false,
  tenantContextMiddleware: (_req, _res, next) => next(),
  maybeRefreshCloudFrontAuthCookiesMiddleware: (_req, _res, next) => next(),
  getValidOpenIdReuseUserId: () => null,
  getAuthFailureReasonCategory: () => 'unknown',
  buildSafeAuthLogContext: () => ({}),
  recordRumProxyRequest: () => {},
}));

const mintChatProxyToken = (secret) =>
  jwt.sign(
    {
      id: CHAT_PROXY_USER_ID,
      username: EMAIL,
      provider: 'cognito',
      email: EMAIL,
      sub: 'cognito-sub',
      iss: 'librechat',
    },
    secret,
    { algorithm: 'HS256', expiresIn: '1h' },
  );

describe('requireJwtAuth with JuristAI chat-minted tokens', () => {
  let app;

  beforeAll(() => {
    process.env.JWT_SECRET = JWT_SECRET;
    delete process.env.CHAT_SECRET;

    const jwtLogin = require('~/strategies/jwtStrategy');
    const chatMintedJwtLogin = require('~/strategies/chatMintedJwtStrategy');
    const requireJwtAuth = require('./requireJwtAuth');

    passport.use(jwtLogin());
    passport.use('chatMintedJwt', chatMintedJwtLogin());

    app = express();
    app.use(passport.initialize());
    app.get('/api/agents/chat', requireJwtAuth, (req, res) =>
      res.status(200).json({ userId: req.user.id, strategy: req.authStrategy }),
    );
  });

  afterAll(() => {
    delete process.env.JWT_SECRET;
  });

  it('provisions the LibreChat user on first request instead of 401ing', async () => {
    expect(mockUsers.has(CHAT_PROXY_USER_ID)).toBe(false);

    const response = await request(app)
      .get('/api/agents/chat')
      .set('authorization', `Bearer ${mintChatProxyToken(JWT_SECRET)}`);

    expect(response.status).toBe(200);
    expect(response.body.strategy).toBe('chatMintedJwt');
    expect(response.body.userId).toBe(CHAT_PROXY_USER_ID);
    expect(mockUsers.get(CHAT_PROXY_USER_ID).email).toBe(EMAIL);
  });

  it('reuses the provisioned user on the next request', async () => {
    const response = await request(app)
      .get('/api/agents/chat')
      .set('authorization', `Bearer ${mintChatProxyToken(JWT_SECRET)}`);

    expect(response.status).toBe(200);
    expect(response.body.userId).toBe(CHAT_PROXY_USER_ID);
  });

  it('still rejects a token signed with the wrong secret', async () => {
    const response = await request(app)
      .get('/api/agents/chat')
      .set('authorization', `Bearer ${mintChatProxyToken('not-the-deployed-secret')}`);

    expect(response.status).toBe(401);
  });

  it('rejects a shared-secret token from a different issuer', async () => {
    const meetingGrant = jwt.sign(
      {
        id: CHAT_PROXY_USER_ID,
        username: EMAIL,
        provider: 'cognito',
        email: EMAIL,
        sub: 'cognito-sub',
      },
      JWT_SECRET,
      { algorithm: 'HS256', issuer: 'vienna-meeting', expiresIn: '1m' },
    );

    const response = await request(app)
      .get('/api/agents/chat')
      .set('authorization', `Bearer ${meetingGrant}`);

    expect(response.status).toBe(401);
    expect(response.body.userId).toBeUndefined();
  });
});
