/**
 * Auth API wrappers — thin, typed-ish surface over the DRF/SimpleJWT
 * endpoints. Audit C-B: the refresh JWT is delivered as an httpOnly
 * cookie set by the server, so it never appears in the JSON these
 * functions return and is never readable from JavaScript.
 */

import api from "./client";

export async function login({ username, password }) {
  // Returns { access }; refresh JWT arrives as a Set-Cookie header.
  const { data } = await api.post("/auth/login/", { username, password });
  return data;
}

export async function register({ username, email, password, passwordConfirm }) {
  // Returns { user, access }; refresh JWT arrives as a Set-Cookie header.
  const { data } = await api.post("/auth/register/", {
    username,
    email,
    password,
    password_confirm: passwordConfirm,
  });
  return data;
}

export async function refresh() {
  // The refresh-token cookie is sent automatically; body stays empty.
  const { data } = await api.post("/auth/refresh/", {});
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

export async function logout() {
  // Best-effort — server blacklists the refresh + clears the cookie. We
  // swallow errors because the access-token wipe must always succeed.
  try {
    await api.post("/auth/logout/", {});
  } catch {
    /* ignore */
  }
}
