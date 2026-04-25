/**
 * AuthContext — single source of truth for the logged-in user.
 *
 * On mount:
 *   1. If an access token is in storage, fetch the profile.
 *   2. If profile fetch fails with 401 the axios interceptor will try
 *      to refresh once and then fire `auth:logout`; this context
 *      listens for that event and clears its own state.
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
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "../lib/storage";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);

  // --- initial bootstrap ---------------------------------------------------
  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      setIsBootstrapping(false);
      return;
    }

    authApi
      .fetchProfile()
      .then(setUser)
      .catch(() => {
        // Interceptor already tried a refresh; if we're here, it failed.
        clearTokens();
        setUser(null);
      })
      .finally(() => setIsBootstrapping(false));
  }, []);

  // --- interceptor → context bridge ---------------------------------------
  useEffect(() => {
    const onForcedLogout = () => setUser(null);
    window.addEventListener("auth:logout", onForcedLogout);
    return () => window.removeEventListener("auth:logout", onForcedLogout);
  }, []);

  // --- actions -------------------------------------------------------------
  const login = useCallback(async ({ username, password }) => {
    const tokens = await authApi.login({ username, password });
    setTokens(tokens);
    const profile = await authApi.fetchProfile();
    setUser(profile);
  }, []);

  const register = useCallback(async (payload) => {
    const data = await authApi.register(payload);
    setTokens({ access: data.access, refresh: data.refresh });
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    const refresh = getRefreshToken();
    await authApi.logout(refresh);
    clearTokens();
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
