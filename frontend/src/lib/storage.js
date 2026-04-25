/**
 * Tiny wrapper around localStorage for the JWT pair.
 *
 * Centralised here so the axios interceptor and the auth context never
 * touch localStorage keys directly — one place to change if we ever
 * switch to an httpOnly cookie strategy.
 */

const ACCESS_KEY = "feanalyzer.access";
const REFRESH_KEY = "feanalyzer.refresh";

export function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens({ access, refresh }) {
  if (access) localStorage.setItem(ACCESS_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}
