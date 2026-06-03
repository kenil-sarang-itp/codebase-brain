/**
 * API client.
 *
 * A single typed wrapper around `fetch` for every backend endpoint. Centralis-
 * ing HTTP here means components never touch `fetch` directly — they call
 * intention-revealing methods (`api.login`, `api.chat`, ...) and receive parsed
 * JSON or a thrown `ApiError`.
 *
 * The JWT access token is held in memory and attached as a bearer header. It
 * is also mirrored to localStorage so a page refresh keeps the user signed in.
 */

const TOKEN_KEY = "codebase_brain_token";

/** Error type carrying the HTTP status and the backend's error detail. */
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** In-memory token cache, seeded from localStorage on load. */
let authToken = localStorage.getItem(TOKEN_KEY) || null;

/** Store (or clear) the auth token in memory and localStorage. */
export function setToken(token) {
  authToken = token;
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

/** Return the current token, or null if the user is not signed in. */
export function getToken() {
  return authToken;
}

/**
 * Core request helper. Attaches JSON headers + bearer auth, parses the
 * response, and converts any non-2xx into a thrown ApiError.
 */
async function request(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }

  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    // The backend is unreachable (container down, network error, ...).
    throw new ApiError(
      "Could not reach the server. Is the backend running?",
      0,
      String(networkErr),
    );
  }

  // 204 No Content — nothing to parse.
  if (response.status === 204) return null;

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      (payload && (payload.detail || payload.error)) ||
      `Request failed with status ${response.status}`;
    throw new ApiError(detail, response.status, payload);
  }
  return payload;
}

/* ------------------------------------------------------------------------ */
/* Public API surface                                                       */
/* ------------------------------------------------------------------------ */
export const api = {
  // --- Auth ---------------------------------------------------------------
  register: (username, email, password) =>
    request("/auth/register", {
      method: "POST",
      body: { username, email, password },
    }),

  login: (username, password) =>
    request("/auth/login", {
      method: "POST",
      body: { username, password },
    }),

  logout: () => request("/auth/logout", { method: "POST" }),

  me: () => request("/auth/me"),

  // --- Chat ---------------------------------------------------------------
  chat: (question, sessionId) =>
    request("/chat", {
      method: "POST",
      body: { question, session_id: sessionId || null },
    }),

  // --- Indexing -----------------------------------------------------------
  startIndexing: (repo, ref = "main") =>
    request("/index", { method: "POST", body: { repo, ref } }),

  indexStatus: (sessionId) => request(`/index-status/${sessionId}`),

  // --- Query logs ---------------------------------------------------------
  queryLogs: (limit = 50) => request(`/query-logs?limit=${limit}`),
};
