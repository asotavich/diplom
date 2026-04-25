/**
 * Central axios instance with JWT refresh handling.
 *
 * Request pipeline
 * ----------------
 * Every request gets `Authorization: Bearer <access>` attached if a
 * token is present in localStorage.
 *
 * Response pipeline
 * -----------------
 * On a 401 we try to swap the expired access token for a fresh pair
 * via `POST /api/auth/refresh/`. If that works we retry the original
 * request once. Concurrent in-flight requests that all 401 during the
 * same refresh window are queued so we don't trigger a refresh per
 * request — important while the dashboard fires several requests in
 * parallel on load.
 *
 * If the refresh itself fails we clear the tokens and emit a custom
 * event (`auth:logout`) that the AuthContext listens to so the SPA
 * can redirect to /login without a full reload.
 */

import axios from "axios";

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "../lib/storage";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

// A *bare* axios (no interceptors) used for the refresh call itself so we
// can't recursively trigger our own 401 handler.
const bareAxios = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

// ---------------------------------------------------------------------------
// Request interceptor — attach the access token
// ---------------------------------------------------------------------------
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ---------------------------------------------------------------------------
// Response interceptor — refresh on 401, retry original request once
// ---------------------------------------------------------------------------
let isRefreshing = false;
let pendingQueue = [];

/** Resolve / reject all waiting requests once the single refresh completes. */
function drainQueue(error, token) {
  pendingQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error);
    else resolve(token);
  });
  pendingQueue = [];
}

/** Fire a global event the AuthContext listens for. */
function dispatchLogout() {
  clearTokens();
  window.dispatchEvent(new CustomEvent("auth:logout"));
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;

    // Skip refresh flow if: no response (network error), not 401, already
    // retried, or the failing request *is* the refresh itself.
    const shouldRefresh =
      error.response?.status === 401 &&
      !original?._retry &&
      !original?.url?.includes("/auth/refresh/") &&
      !original?.url?.includes("/auth/login/");

    if (!shouldRefresh) {
      return Promise.reject(error);
    }

    // Already refreshing? Queue this request.
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        pendingQueue.push({ resolve, reject });
      }).then((token) => {
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      });
    }

    original._retry = true;
    isRefreshing = true;

    try {
      const refresh = getRefreshToken();
      if (!refresh) {
        throw new Error("No refresh token available.");
      }

      const { data } = await bareAxios.post("/auth/refresh/", { refresh });
      setTokens({ access: data.access, refresh: data.refresh });
      drainQueue(null, data.access);

      original.headers.Authorization = `Bearer ${data.access}`;
      return api(original);
    } catch (refreshError) {
      drainQueue(refreshError, null);
      dispatchLogout();
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  }
);

export default api;
