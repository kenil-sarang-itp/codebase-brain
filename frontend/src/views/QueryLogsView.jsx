/**
 * Query-logs view — the "History" screen.
 *
 * Renders the developer's recent question history as an audit table: the
 * question asked, how it was classified, how many sources were cited, the
 * answer latency, and when it happened. Useful both as a personal history and
 * as a lightweight observability surface.
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client.js";
import { Banner, Spinner } from "../components/Feedback.jsx";

/** Format an ISO timestamp into a compact, readable local string. */
function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** One row of the audit table. */
function LogRow({ entry }) {
  return (
    <tr style={{ borderTop: "1px solid var(--border)" }}>
      <td style={{ padding: "12px 10px", color: "var(--text)" }}>
        {entry.question}
        {entry.answer_preview && (
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-faint)",
              marginTop: "4px",
              maxWidth: "440px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {entry.answer_preview}
          </div>
        )}
      </td>
      <td style={{ padding: "12px 10px" }}>
        <span
          className={
            "cb-pill " +
            (entry.query_type === "validation"
              ? "cb-pill--info"
              : "cb-pill--ok")
          }
        >
          {entry.query_type}
        </span>
      </td>
      <td
        style={{
          padding: "12px 10px",
          color: "var(--text-dim)",
          textAlign: "center",
        }}
      >
        {entry.num_sources}
      </td>
      <td
        style={{
          padding: "12px 10px",
          color: "var(--text-dim)",
          textAlign: "right",
        }}
      >
        {entry.latency_ms} ms
      </td>
      <td
        style={{
          padding: "12px 10px",
          color: "var(--text-faint)",
          textAlign: "right",
          whiteSpace: "nowrap",
        }}
      >
        {formatTime(entry.created_at)}
      </td>
    </tr>
  );
}

export default function QueryLogsView() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Load the log once on mount.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await api.queryLogs(100);
        if (!cancelled) setEntries(result.entries || []);
      } catch (err) {
        if (!cancelled) {
          const message =
            err instanceof ApiError
              ? err.detail
              : "Could not load the query log.";
          setError(String(message));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="cb-rise">
      <h1 style={{ marginBottom: "6px" }}>Query history</h1>
      <p style={{ color: "var(--text-dim)", marginBottom: "26px" }}>
        Every question you have asked, with how it was classified and answered.
      </p>

      {loading && <Spinner label="Loading history" />}

      {error && <Banner kind="error">{error}</Banner>}

      {!loading && !error && entries.length === 0 && (
        <div
          className="cb-card"
          style={{
            textAlign: "center",
            color: "var(--text-faint)",
            padding: "40px 20px",
          }}
        >
          No queries yet. Ask something on the “Ask” screen and it will appear
          here.
        </div>
      )}

      {!loading && entries.length > 0 && (
        <div className="cb-card" style={{ padding: "6px 10px" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "13px",
            }}
          >
            <thead>
              <tr>
                {["Question", "Type", "Sources", "Latency", "When"].map(
                  (h, i) => (
                    <th
                      key={h}
                      style={{
                        textAlign:
                          i === 0
                            ? "left"
                            : i === 2
                              ? "center"
                              : "right",
                        padding: "10px",
                        fontSize: "11px",
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                        color: "var(--text-faint)",
                        fontWeight: "500",
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <LogRow key={entry.id} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
