const DEFAULT_CHAT_ACTION_OAUTH_ORIGINS =
  'https://chat.juristai.org,https://chat.nougat.law';

/**
 * Select the public origin for chat OAuth URLs without trusting arbitrary Host
 * or Origin headers. The configured allowlist is exact because OAuth callbacks
 * must return to the same host that issued the host-scoped CSRF/session cookies.
 * @param {import('express').Request | undefined} req
 * @returns {string}
 */
function getChatOAuthOrigin(req) {
  const allowedOrigins = (
    process.env.CHAT_ACTION_OAUTH_ORIGINS ?? DEFAULT_CHAT_ACTION_OAUTH_ORIGINS
  )
    .split(',')
    .map((value) => {
      try {
        return new URL(value.trim()).origin;
      } catch {
        return null;
      }
    })
    .filter(Boolean);

  const requestHost = String(req?.headers?.host || req?.get?.('host') || '').toLowerCase();
  const hostOrigin = allowedOrigins.find((origin) => {
    try {
      return new URL(origin).host.toLowerCase() === requestHost;
    } catch {
      return false;
    }
  });
  if (hostOrigin) {
    return hostOrigin;
  }

  const requestOrigin = req?.headers?.origin || req?.get?.('origin');
  if (requestOrigin) {
    try {
      const parsedOrigin = new URL(requestOrigin);
      if (parsedOrigin.origin === requestOrigin && allowedOrigins.includes(parsedOrigin.origin)) {
        return parsedOrigin.origin;
      }
    } catch {
      // Ignore malformed Origin values and use the configured fallback.
    }
  }

  const configuredOrigin = process.env.DOMAIN_SERVER || process.env.DOMAIN_CLIENT;
  if (configuredOrigin) {
    try {
      return new URL(configuredOrigin).origin;
    } catch {
      // Keep local development usable when an optional value is malformed.
    }
  }
  return 'http://localhost:3080';
}

module.exports = { getChatOAuthOrigin };
