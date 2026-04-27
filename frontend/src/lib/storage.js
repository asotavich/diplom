/**
 * In-memory holder for the short-lived access token.
 *
 * Audit C-B: the JWT pair used to live in localStorage, where any XSS
 * (including a future supply-chain compromise of a transitive
 * dependency) could read both halves and silently impersonate the user
 * for the full refresh-token lifetime. The refresh token now lives in
 * an httpOnly cookie issued by the auth endpoints; the access token is
 * kept here, in module-scope memory only, so it disappears the moment
 * the SPA process is torn down.
 *
 * On a fresh page load there is no access token in memory — the SPA
 * boot path must POST /api/auth/refresh/ (which sends the cookie
 * automatically) to obtain one. See `AuthContext.bootstrap`.
 */

let accessToken = null;

export function getAccessToken() {
  return accessToken;
}

export function setAccessToken(token) {
  accessToken = token || null;
}

export function clearAccessToken() {
  accessToken = null;
}
