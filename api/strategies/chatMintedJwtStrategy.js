const { Strategy: JwtStrategy, ExtractJwt } = require('passport-jwt');
const jwt = require('jsonwebtoken');
const { SystemRoles } = require('librechat-data-provider');
const { createUser, getUserById } = require('~/models');

/**
 * Authenticate the short-lived token minted by JuristAI's Django service.
 *
 * JuristAI owns signup and therefore a new user may not yet have a Mongo
 * User document in LibreChat. The token carries the stable Mongo-compatible
 * id and email; create that local identity on first use so the normal
 * LibreChat agent routes can resolve req.user.
 *
 * JuristAI signs the token with CHAT_SECRET, which is deployed as the same
 * value as JWT_SECRET. Falling back to JWT_SECRET keeps this strategy working
 * on task definitions that only carry JWT_SECRET, so provisioning does not
 * depend on a separate deploy-config change.
 */
// LibreChat's user schema requires `email` and validates it against
// /\S+@\S+\.\S+/. The brand-new signup this strategy exists to rescue is
// exactly the case where the token may NOT carry one: chat_proxy resolves the
// address from a UserTable GSI, and that index is eventually consistent, so
// seconds after signup the lookup misses and the token goes out with
// `email: null` and `username` set to the raw Cognito sub. Handing that to
// createUser throws a validation error, the strategy reports failure, and the
// user still gets a 401 — the very failure this provisioning path is meant to
// remove. Fall back to a deterministic address derived from the id so
// provisioning always succeeds; the real address replaces it the moment a token
// carrying one arrives.
const EMAIL_PATTERN = /\S+@\S+\.\S+/;
const PROVISIONAL_EMAIL_DOMAIN = 'chat.juristai.internal';

const resolveProvisioningEmail = (payload, id) => {
  const candidates = [payload?.email, payload?.username];

  for (const candidate of candidates) {
    const normalized = String(candidate ?? '')
      .trim()
      .toLowerCase();
    if (normalized && EMAIL_PATTERN.test(normalized)) {
      return normalized;
    }
  }

  return `${id.toLowerCase()}@${PROVISIONAL_EMAIL_DOMAIN}`;
};

const chatMintedJwtLogin = () =>
  new JwtStrategy(
    {
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      secretOrKeyProvider: (_request, rawToken, done) => {
        const secrets = [
          process.env.CHAT_SECRET,
          process.env.CHAT_SECRET_PREVIOUS,
          // JWT_SECRET is a legacy fallback only when the dedicated key ring
          // has not been provisioned. Keep the ordinary LibreChat session key
          // out of the chat-token ring after a dedicated CHAT_SECRET exists.
          process.env.CHAT_SECRET ? null : process.env.JWT_SECRET,
        ].filter((secret, index, values) => secret && values.indexOf(secret) === index);
        if (!secrets.length) {
          return done(new Error('Chat token signing secret is not configured'));
        }

        const decoded = jwt.decode(rawToken, { complete: true });
        if (!decoded || decoded.header?.alg !== 'HS256') {
          return done(new Error('Invalid chat token'));
        }
        for (const secret of secrets) {
          try {
            // Select the matching key here; Passport performs the full
            // issuer, expiry, and claim validation with the selected key.
            jwt.verify(rawToken, secret, {
              algorithms: ['HS256'],
              ignoreExpiration: true,
              ignoreNotBefore: true,
            });
            return done(null, secret);
          } catch (_error) {
            // Continue through the configured current/previous key ring.
          }
        }
        return done(new Error('Invalid chat token signature'));
      },
      algorithms: ['HS256'],
      // Both Django chat_proxy and Chatbot's ActionService mint the ordinary
      // user token with this issuer. Meeting-scoped assertions use a distinct
      // issuer and must never authenticate against general chat routes.
      issuer: 'librechat',
    },
    async (payload, done) => {
      try {
        // chat_proxy mints the Mongo-compatible user id as `id` and puts the
        // Cognito sub in `sub`; ActionService mints only `sub` (the Mongo id).
        // Prefer `id` so a chat_proxy token never provisions a user keyed by the
        // Cognito sub, and fall back to `sub` for ActionService tokens.
        const id = String(payload?.id ?? payload?.sub ?? '').trim();
        if (!id) {
          return done(null, false, { message: 'Invalid Vienna chat token' });
        }

        const email = resolveProvisioningEmail(payload, id);

        let user = await getUserById(id, '-password -__v -totpSecret -backupCodes');
        if (!user) {
          user = await createUser(
            {
              _id: id,
              email,
              username: email,
              name: email,
              provider: 'local',
              role: SystemRoles.USER,
              emailVerified: true,
            },
            undefined,
            true,
            true,
          );
        }

        user.id = user._id?.toString?.() ?? id;
        return done(null, user);
      } catch (error) {
        return done(error, false);
      }
    },
  );

module.exports = chatMintedJwtLogin;
