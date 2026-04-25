/**
 * Auth API wrappers — thin, typed-ish surface over the DRF/SimpleJWT
 * endpoints. None of these functions touch localStorage; that's the
 * AuthContext's job.
 */

import api from "./client";

export async function login({ username, password }) {
  // Returns { access, refresh }
  const { data } = await api.post("/auth/login/", { username, password });
  return data;
}

export async function register({ username, email, password, passwordConfirm }) {
  // Returns { user, access, refresh }
  const { data } = await api.post("/auth/register/", {
    username,
    email,
    password,
    password_confirm: passwordConfirm,
  });
  return data;
}

export async function fetchProfile() {
  const { data } = await api.get("/auth/profile/");
  return data;
}

export async function updateProfile(patch) {
  const { data } = await api.patch("/auth/profile/", patch);
  return data;
}

export async function logout(refreshToken) {
  // Best-effort — server blacklists the refresh; if it fails we still
  // wipe the tokens client-side.
  try {
    await api.post("/auth/logout/", { refresh: refreshToken });
  } catch {
    /* ignore */
  }
}
