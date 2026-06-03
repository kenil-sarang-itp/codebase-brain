/**
 * Indexing view — the "Index" screen.
 *
 * Two responsibilities:
 *   1. Start indexing — submit a repository and queue it as a backend job.
 *   2. Watch progress — poll `/index-status` and render live pipeline progress.
 *
 * Session state is persisted to localStorage so navigating away and back does
 * NOT reset the view — the progress dashboard resumes exactly where it was.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client.js";
import { Banner, Spinner } from "../components/Feedback.jsx";

// localStorage key — persists the active session across navigation + refresh.
const SESSION_STORAGE_KEY = "codebase_brain_index_session";

/** Save session info to localStorage. */
function saveSession(sessionId, repo) {
  try {
    localStorage.setItem(
      SESSION_STORAGE_KEY,
      JSON.stringify({ sessionId, repo }),
    );
  } catch {}
}

/** Load persisted session from localStorage. Returns { sessionId, repo } or null. */
function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Clear any persisted session (called when user wants to start fresh). */
function clearSession() {
  try {
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {}
}

/** The pipeline phases, in order, with human labels. */
const PHASES = [
  { key: "discovering",    label: "Discovery" },
  { key: "analysing",      label: "Static analysis" },
  { key: "generating_l3",  label: "L3 · Architecture docs" },
  { key: "generating_l2",  label: "L2 · Module docs" },
  { key: "generating_l1",  label: "L1 · Function docs" },
  { key: "complete",       label: "Complete" },
];

function phaseIndex(status) {
  const i = PHASES.findIndex((p) => p.key === status);
  return i === -1 ? -1 : i;
}

function ProgressBar({ percent }) {
  return (
    <div style={{ height: "8px", background: "var(--bg-inset)", borderRadius: "20px",
                  overflow: "hidden", border: "1px solid var(--border)" }}>
      <div style={{ height: "100%", width: `${Math.min(100, Math.max(0, percent))}%`,
                    background: "var(--accent)", transition: "width 0.5s ease" }} />
    </div>
  );
}

function PhaseStepper({ status }) {
  const active = phaseIndex(status);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {PHASES.map((phase, i) => {
        const done = active > i || status === "complete";
        const current = active === i && status !== "complete";
        return (
          <div key={phase.key} style={{ display: "flex", alignItems: "center", gap: "11px" }}>
            <span style={{
              width: "10px", height: "10px", borderRadius: "50%", flexShrink: 0,
              background: done ? "var(--ok)" : current ? "var(--accent)" : "var(--border-bright)",
              boxShadow: current ? "0 0 0 4px var(--accent-soft)" : "none",
            }} />
            <span style={{ fontSize: "13px",
                           color: done || current ? "var(--text)" : "var(--text-faint)" }}>
              {phase.label}
            </span>
            {current && <span style={{ marginLeft: "auto" }}><Spinner label="" /></span>}
          </div>
        );
      })}
    </div>
  );
}

function Counter({ label, done, total }) {
  return (
    <div className="cb-card" style={{ flex: 1, padding: "16px" }}>
      <div className="cb-label">{label}</div>
      <div style={{ fontFamily: "var(--font-display)", fontSize: "26px", color: "var(--text)" }}>
        {done}
        <span style={{ color: "var(--text-faint)", fontSize: "16px" }}> / {total}</span>
      </div>
    </div>
  );
}

export default function IndexingView() {
  // Restore persisted session on mount so navigation doesn't reset the view.
  const persisted = loadSession();

  const [repo, setRepo]           = useState(persisted?.repo || "");
  const [ref, setRef]             = useState("main");
  const [sessionId, setSessionId] = useState(persisted?.sessionId || null);
  const [status, setStatus]       = useState(null);
  const [error, setError]         = useState("");
  const [starting, setStarting]   = useState(false);

  const pollRef = useRef(null);

  const poll = useCallback(async () => {
    if (!sessionId) return;
    try {
      const s = await api.indexStatus(sessionId);
      setStatus(s);
      if (s.status === "complete" || s.status === "failed") {
        clearInterval(pollRef.current);
        pollRef.current = null;
        // Keep the session persisted so it shows on return, but mark it done.
        saveSession(sessionId, repo);
      }
    } catch (err) {
      console.error("Status poll failed:", err);
    }
  }, [sessionId, repo]);

  // When sessionId is available (restored or freshly started), kick off polling.
  useEffect(() => {
    if (!sessionId) return;
    poll(); // immediate first fetch so UI fills in fast on navigation return
    pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [sessionId, poll]);

  async function handleStart() {
    const trimmed = repo.trim();
    if (!trimmed || starting) return;
    setError("");
    setStarting(true);
    setStatus(null);
    try {
      const result = await api.startIndexing(trimmed, ref.trim() || "main");
      setSessionId(result.session_id);
      // Persist immediately so a navigation away right after starting still works.
      saveSession(result.session_id, trimmed);
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : "Could not start indexing.";
      setError(String(message));
    } finally {
      setStarting(false);
    }
  }

  function handleReset() {
    // User wants to index a different repo — clear everything.
    clearInterval(pollRef.current);
    pollRef.current = null;
    clearSession();
    setSessionId(null);
    setStatus(null);
    setRepo("");
    setError("");
  }

  const isRunning = status && status.status !== "complete" && status.status !== "failed";
  const isDone    = status?.status === "complete" || status?.status === "failed";

  return (
    <div className="cb-rise">
      <div style={{ display: "flex", alignItems: "baseline",
                    justifyContent: "space-between", marginBottom: "6px" }}>
        <h1>Index a repository</h1>
        {/* Show a "Index another repo" button once a run is done or in progress */}
        {sessionId && (
          <button className="cb-button cb-button--ghost" onClick={handleReset}
                  style={{ fontSize: "12px" }}>
            ✕ Start over
          </button>
        )}
      </div>
      <p style={{ color: "var(--text-dim)", marginBottom: "26px" }}>
        Point CodeBase Brain at a repository. It builds a three-level documentation
        layer — architecture, modules, and functions — and makes it queryable.
      </p>

      {/* --- Start form — hidden while a session is active ---------------- */}
      {!sessionId && (
        <div className="cb-card" style={{ marginBottom: "26px" }}>
          <label className="cb-label">Repository</label>
          <input className="cb-input"
            placeholder="owner/name  (GitHub)  or  /repo  (local path)"
            value={repo} onChange={(e) => setRepo(e.target.value)}
            disabled={starting} />
          <div style={{ height: "12px" }} />
          <label className="cb-label">Git ref (GitHub only)</label>
          <input className="cb-input" placeholder="main"
            value={ref} onChange={(e) => setRef(e.target.value)}
            disabled={starting} />
          <div style={{ marginTop: "16px" }}>
            <button className="cb-button" onClick={handleStart}
                    disabled={starting || !repo.trim()}>
              {starting ? "Queuing…" : "Start indexing"}
            </button>
          </div>
          {error && <div style={{ marginTop: "14px" }}><Banner kind="error">{error}</Banner></div>}
        </div>
      )}

      {/* --- Resumed-session banner (shown when restoring from navigation) - */}
      {sessionId && !status && (
        <div className="cb-card" style={{ marginBottom: "20px", color: "var(--text-dim)",
                                          fontSize: "13px" }}>
          <Spinner label={`Fetching status for session ${sessionId.slice(0, 16)}…`} />
        </div>
      )}

      {/* --- Live progress dashboard --------------------------------------- */}
      {status && (
        <div className="cb-rise">
          <div style={{ display: "flex", alignItems: "baseline",
                        justifyContent: "space-between", marginBottom: "14px" }}>
            <h2>Progress</h2>
            <span className={
              "cb-pill " +
              (status.status === "complete" ? "cb-pill--ok" :
               status.status === "failed"   ? "cb-pill--err" : "cb-pill--info")
            }>
              {status.status}
            </span>
          </div>

          <div style={{ marginBottom: "8px" }}>
            <ProgressBar percent={status.progress_percent || 0} />
          </div>
          <div style={{ fontSize: "12px", color: "var(--text-faint)", marginBottom: "22px" }}>
            {status.progress_percent || 0}% · {status.repo_url}
          </div>

          <div style={{ display: "flex", gap: "14px", marginBottom: "22px" }}>
            <Counter label="Files"
              done={status.processed_files || 0} total={status.total_files || 0} />
            <Counter label="Functions"
              done={status.processed_functions || 0} total={status.total_functions || 0} />
          </div>

          <div className="cb-card">
            <div className="cb-label" style={{ marginBottom: "14px" }}>Pipeline phases</div>
            <PhaseStepper status={status.status} />
          </div>

          {status.error_message && (
            <div style={{ marginTop: "16px" }}>
              <Banner kind="error">{status.error_message}</Banner>
            </div>
          )}

          {status.status === "complete" && (
            <div style={{ marginTop: "16px" }}>
              <Banner kind="success">
                Indexing complete — go to the Ask tab to query your codebase.
              </Banner>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
