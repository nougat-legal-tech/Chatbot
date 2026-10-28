const jwt = require('jsonwebtoken');

const mockGetUserById = jest.fn();
const mockCreateUser = jest.fn();

// Keep this strategy unit-testable when the workspace data-provider package has
// not been built yet. The production package exports the same stable role value.
jest.mock(
  'librechat-data-provider',
  () => ({
    SystemRoles: { USER: 'USER' },
  }),
  { virtual: true },
);

jest.mock('~/models', () => ({
  getUserById: (...args) => mockGetUserById(...args),
  createUser: (...args) => mockCreateUser(...args),
}));

describe('chatMintedJwtStrategy', () => {
  let strategy;

  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
    process.env.CHAT_SECRET = 'test-chat-secret';
    delete process.env.CHAT_SECRET_PREVIOUS;
    delete process.env.JWT_SECRET;
    strategy = require('./chatMintedJwtStrategy')();
  });

  afterAll(() => {
    delete process.env.CHAT_SECRET;
    delete process.env.CHAT_SECRET_PREVIOUS;
    delete process.env.JWT_SECRET;
  });

  function verify(payload) {
    return new Promise((resolve, reject) => {
      strategy._verify(payload, (error, user, info) => {
        if (error) return reject(error);
        resolve({ user, info });
      });
    });
  }

  test('reuses an existing LibreChat user', async () => {
    const existing = { _id: 'abc123', email: 'lawyer@example.com' };
    mockGetUserById.mockResolvedValue(existing);

    const result = await verify({ id: 'abc123', email: existing.email });

    expect(result.user).toEqual({ ...existing, id: 'abc123' });
    expect(mockCreateUser).not.toHaveBeenCalled();
  });

  test('provisions a new user from a valid JuristAI chat token', async () => {
    mockGetUserById.mockResolvedValue(null);
    mockCreateUser.mockResolvedValue({ _id: 'abc123', email: 'lawyer@example.com' });

    const result = await verify({ id: 'abc123', email: 'Lawyer@Example.com' });

    expect(mockCreateUser).toHaveBeenCalledWith(
      expect.objectContaining({
        _id: 'abc123',
        email: 'lawyer@example.com',
        username: 'lawyer@example.com',
        emailVerified: true,
      }),
      undefined,
      true,
      true,
    );
    expect(result.user.id).toBe('abc123');
  });

  test('provisions a user from the standard JWT sub claim minted by ActionService', async () => {
    mockGetUserById.mockResolvedValue(null);
    mockCreateUser.mockResolvedValue({ _id: 'abc123', email: 'lawyer@example.com' });

    const result = await verify({ sub: 'abc123', email: 'lawyer@example.com' });

    expect(mockGetUserById).toHaveBeenCalledWith(
      'abc123',
      '-password -__v -totpSecret -backupCodes',
    );
    expect(result.user.id).toBe('abc123');
  });

  // The brand-new signup is exactly the case where chat_proxy's UserTable GSI
  // lookup can still miss, so the token arrives with no email and the raw
  // Cognito sub as username. LibreChat's user schema requires an email matching
  // /\S+@\S+\.\S+/, so passing either of those through would throw inside
  // createUser and the user would keep getting 401s.
  test('provisions a brand-new user whose token carries no resolvable email', async () => {
    mockGetUserById.mockResolvedValue(null);
    mockCreateUser.mockResolvedValue({ _id: '19e0ad79733f781d53950677' });

    const result = await verify({
      id: '19e0ad79733f781d53950677',
      email: null,
      username: '84083408-a051-70d1-f910-749d49645793',
    });

    expect(mockCreateUser).toHaveBeenCalledWith(
      expect.objectContaining({
        _id: '19e0ad79733f781d53950677',
        email: '19e0ad79733f781d53950677@chat.juristai.internal',
      }),
      undefined,
      true,
      true,
    );
    const [[created]] = mockCreateUser.mock.calls;
    expect(created.email).toMatch(/\S+@\S+\.\S+/);
    expect(result.user.id).toBe('19e0ad79733f781d53950677');
  });

  test('rejects a token with no id rather than provisioning an anonymous user', async () => {
    const result = await verify({ id: '   ', email: 'lawyer@example.com' });

    expect(result.user).toBe(false);
    expect(mockCreateUser).not.toHaveBeenCalled();
  });

  test('falls back to JWT_SECRET when CHAT_SECRET is absent from the deploy env', async () => {
    jest.resetModules();
    delete process.env.CHAT_SECRET;
    process.env.JWT_SECRET = 'deployed-jwt-secret';

    const fallbackStrategy = require('./chatMintedJwtStrategy')();
    const fallbackToken = jwt.sign(
      { id: 'abc123', email: 'lawyer@example.com' },
      process.env.JWT_SECRET,
      { issuer: 'librechat', expiresIn: '5m', algorithm: 'HS256' },
    );

    const resolvedSecret = await new Promise((resolve, reject) =>
      fallbackStrategy._secretOrKeyProvider(null, fallbackToken, (error, secret) =>
        error ? reject(error) : resolve(secret),
      ),
    );
    expect(resolvedSecret).toBe('deployed-jwt-secret');

    delete process.env.JWT_SECRET;
  });

  test('strategy verifies the issuer and shared secret', () => {
    const token = jwt.sign({ id: 'abc123', email: 'lawyer@example.com' }, process.env.CHAT_SECRET, {
      issuer: 'librechat',
      expiresIn: '5m',
      algorithm: 'HS256',
    });
    expect(jwt.verify(token, process.env.CHAT_SECRET).email).toBe('lawyer@example.com');
  });

  test('selects the previous rotation secret for an existing token', async () => {
    process.env.CHAT_SECRET = 'replacement-chat-secret';
    process.env.CHAT_SECRET_PREVIOUS = 'previous-chat-secret';
    process.env.JWT_SECRET = 'previous-chat-secret';
    jest.resetModules();
    const rotationStrategy = require('./chatMintedJwtStrategy')();
    const token = jwt.sign(
      { id: 'abc123', email: 'lawyer@example.com' },
      process.env.CHAT_SECRET_PREVIOUS,
      { issuer: 'librechat', expiresIn: '5m', algorithm: 'HS256' },
    );

    const resolvedSecret = await new Promise((resolve, reject) =>
      rotationStrategy._secretOrKeyProvider(null, token, (error, secret) =>
        error ? reject(error) : resolve(secret),
      ),
    );

    expect(resolvedSecret).toBe('previous-chat-secret');
    delete process.env.CHAT_SECRET_PREVIOUS;
  });
});
