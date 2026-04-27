/**
 * AuthContext — single source of truth for the logged-in user.
 *
 * On mount:
 *   1. POST /api/auth/refresh/ — the httpOnly refresh cookie (audit C-B)
 *      either gives us back a fresh access token or 401s.
 *   2. On 200, fetch the profile and seed `user`.
 *   3. On any failure, leave `user` null — the app routes to /login.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import * as authApi from "../api/auth";
import {
  clearAccessToken,
  setAccessToken,
} from "../lib/storage";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);

  // --- initial bootstrap ---------------------------------------------------
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { access } = await authApi.refresh();
        if (cancelled) return;
        setAccessToken(access);
        const profile = await authApi.fetchProfile();
        if (cancelled) return;
        setUser(profile);
      } catch {
        clearAccessToken();
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsBootstrapping(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // --- interceptor → context bridge ---------------------------------------
  useEffect(() => {
    const onForcedLogout = () => setUser(null);
    window.addEventListener("auth:logout", onForcedLogout);
    return () => window.removeEventListener("auth:logout", onForcedLogout);
  }, []);

  // --- actions -------------------------------------------------------------
  const login = useCallback(async ({ username, password }) => {
    const { access } = await authApi.login({ username, password });
    setAccessToken(access);
    const profile = await authApi.fetchProfile();
    setUser(profile);
  }, []);

  const register = useCallback(async (payload) => {
    const data = await authApi.register(payload);
    setAccessToken(data.access);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    clearAccessToken();
    setUser(null);
  }, []);

  const value = {
    user,
    isAuthenticated: Boolean(user),
    isBootstrapping,
    login,
    register,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside an <AuthProvider>.");
  }
  return ctx;
}
