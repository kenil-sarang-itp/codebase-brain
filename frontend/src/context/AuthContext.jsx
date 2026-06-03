/**
 * Authentication context.
 *
 * Provides the signed-in user and auth actions (login, register, logout) to the
 * whole component tree. On mount it tries to restore a session from a persisted
 * token by calling `/auth/me`, so a page refresh keeps the user logged in.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "../api/client.js";

const AuthContext = createContext(null);

/** Provider component — wrap the app in this. */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // `loading` is true only during the initial token-restore probe.
  const [loading, setLoading] = useState(true);

  // On first mount, restore the session if a token is already stored.
  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await api.me();
        if (!cancelled) setUser(me);
      } catch {
        // Token is stale/invalid — clear it.
        setToken(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    restore();
    return () => {
      cancelled = true;
    };
  }, []);

  /** Authenticate and store the resulting token + user. */
  async function login(username, password) {
    const result = await api.login(username, password);
    setToken(result.access_token);
    const me = await api.me();
    setUser(me);
  }

  /** Register a new account (the backend logs the user straight in). */
  async function register(username, email, password) {
    const result = await api.register(username, email, password);
    setToken(result.access_token);
    const me = await api.me();
    setUser(me);
  }

  /** Log out — best-effort server call, then clear local state. */
  async function logout() {
    try {
      await api.logout();
    } catch {
      // Ignore — logout is client-side token disposal regardless.
    }
    setToken(null);
    setUser(null);
  }

  const value = { user, loading, login, register, logout };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Hook for consuming the auth context. */
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return ctx;
}
