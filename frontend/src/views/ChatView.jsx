/**
 * Chat view — the primary "Ask" screen.
 *
 * A developer types a plain-English question about the codebase and receives a
 * grounded, cited answer. The view keeps a running transcript of the session
 * (so follow-up questions have visible context) and threads the server-issued
 * `session_id` through every request, which is what gives the backend its
 * short-term conversational memory.
 */
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client.js";
import { Banner, Spinner } from "../components/Feedback.jsx";

/** A single answer's citation list, rendered as monospace source chips. */
function Citations({ citations }) {
  if (!citations || citations.length === 0) return null;
  return (
    <div style={{ marginTop: "14px" }}>
      <div className="cb-label">Sources</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
        {citations.map((c, i) => (
          <span
            key={i}
            style={{
              fontSize: "11px",
              color: "var(--text-dim)",
              background: "var(--bg-inset)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              padding: "3px 8px",
            }}
          >
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * Render a markdown-like answer string as formatted HTML.
 * Handles: **bold**, `code`, bullet lists, numbered lists, headers, code blocks.
 */
function AnswerBody({ text }) {
  if (!text) return null;

  // Process the text line by line into React elements.
  const lines = text.split("\n");
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block: ```lang ... ```
    if (line.trimStart().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      elements.push(
        <pre key={i} style={{
          background: "var(--bg-inset)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)", padding: "12px 14px",
          overflowX: "auto", fontSize: "12px", margin: "10px 0",
          color: "var(--text)",
        }}>
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      i++;
      continue;
    }

    // Headers: ## or ###
    if (line.startsWith("### ")) {
      elements.push(<h3 key={i} style={{ fontSize: "14px", margin: "14px 0 6px", color: "var(--text)" }}>{inlineFormat(line.slice(4))}</h3>);
    } else if (line.startsWith("## ")) {
      elements.push(<h2 key={i} style={{ fontSize: "16px", margin: "18px 0 8px", color: "var(--text)", fontFamily: "var(--font-display)" }}>{inlineFormat(line.slice(3))}</h2>);
    } else if (line.startsWith("# ")) {
      elements.push(<h2 key={i} style={{ fontSize: "18px", margin: "18px 0 8px", color: "var(--text)", fontFamily: "var(--font-display)" }}>{inlineFormat(line.slice(2))}</h2>);
    }
    // Bullet list items: - or *
    else if (line.match(/^\s*[-*]\s/)) {
      elements.push(
        <div key={i} style={{ display: "flex", gap: "8px", margin: "3px 0", paddingLeft: "4px" }}>
          <span style={{ color: "var(--accent)", flexShrink: 0, marginTop: "1px" }}>▸</span>
          <span>{inlineFormat(line.replace(/^\s*[-*]\s/, ""))}</span>
        </div>
      );
    }
    // Numbered list: 1. 2. etc
    else if (line.match(/^\s*\d+\.\s/)) {
      const num = line.match(/^\s*(\d+)\.\s/)[1];
      elements.push(
        <div key={i} style={{ display: "flex", gap: "10px", margin: "3px 0", paddingLeft: "4px" }}>
          <span style={{ color: "var(--accent)", flexShrink: 0, minWidth: "16px" }}>{num}.</span>
          <span>{inlineFormat(line.replace(/^\s*\d+\.\s/, ""))}</span>
        </div>
      );
    }
    // Empty line = spacer
    else if (line.trim() === "") {
      elements.push(<div key={i} style={{ height: "6px" }} />);
    }
    // Regular paragraph
    else {
      elements.push(
        <p key={i} style={{ margin: "4px 0", lineHeight: "1.7" }}>
          {inlineFormat(line)}
        </p>
      );
    }
    i++;
  }

  return <div style={{ fontSize: "13.5px", color: "var(--text)" }}>{elements}</div>;
}

/**
 * Format inline markdown: **bold**, `code`, and citation references.
 * Returns an array of React nodes.
 */
function inlineFormat(text) {
  if (!text) return text;
  const parts = [];
  // Split on **bold**, `code`, or citation (file::fn, L1-L2)
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\([^)]+::[^)]+,\s*L\d+[^)]*\))/g;
  let last = 0;
  let match;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    if (match[1]) {
      // Inline code
      parts.push(
        <code key={key++} style={{
          background: "var(--bg-inset)", border: "1px solid var(--border)",
          borderRadius: "3px", padding: "1px 5px", fontSize: "12px",
          color: "var(--accent)", fontFamily: "var(--font-mono)",
        }}>
          {match[1].slice(1, -1)}
        </code>
      );
    } else if (match[2]) {
      // Bold
      parts.push(<strong key={key++} style={{ color: "var(--text)", fontWeight: "600" }}>{match[2].slice(2, -2)}</strong>);
    } else if (match[3]) {
      // Citation reference
      parts.push(
        <span key={key++} style={{
          fontSize: "11px", background: "var(--accent-soft)",
          color: "var(--accent)", borderRadius: "3px",
          padding: "1px 5px", fontFamily: "var(--font-mono)",
        }}>
          {match[3]}
        </span>
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 && typeof parts[0] === "string" ? parts[0] : parts;
}

/** One turn in the transcript — either the developer's question or an answer. */
function Turn({ turn }) {
  const isUser = turn.role === "user";
  return (
    <div className="cb-rise" style={{ marginBottom: "20px" }}>
      <div style={{
        fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase",
        color: isUser ? "var(--text-faint)" : "var(--accent)", marginBottom: "6px",
      }}>
        {isUser ? "You asked" : "CodeBase Brain"}
        {!isUser && turn.queryType && (
          <span
            className={"cb-pill " + (turn.queryType === "validation" ? "cb-pill--info" : "cb-pill--ok")}
            style={{ marginLeft: "10px" }}
          >
            {turn.queryType}
          </span>
        )}
      </div>
      <div
        className="cb-card"
        style={{
          background: isUser ? "var(--bg-inset)" : "var(--bg-raised)",
          borderColor: isUser ? "var(--border)" : "var(--border-bright)",
        }}
      >
        {isUser
          ? <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{turn.text}</p>
          : <AnswerBody text={turn.text} />
        }
        {!isUser && <Citations citations={turn.citations} />}
        {!isUser && turn.latencyMs != null && (
          <div style={{ marginTop: "12px", fontSize: "11px", color: "var(--text-faint)" }}>
            answered in {turn.latencyMs} ms
          </div>
        )}
      </div>
    </div>
  );
}

export default function ChatView() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The server-issued session id; null until the first answer comes back.
  const [sessionId, setSessionId] = useState(null);

  // Keep the newest turn scrolled into view.
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  async function handleAsk() {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setError("");
    setBusy(true);
    // Optimistically show the question immediately.
    setTurns((prev) => [...prev, { role: "user", text: trimmed }]);
    setQuestion("");

    try {
      const result = await api.chat(trimmed, sessionId);
      // Persist the session id so follow-ups continue the same conversation.
      setSessionId(result.session_id);
      setTurns((prev) => [
        ...prev,
        {
          role: "assistant",
          text: result.answer,
          citations: result.citations,
          queryType: result.query_type,
          latencyMs: result.latency_ms,
        },
      ]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : "Something went wrong while answering.";
      setError(String(message));
    } finally {
      setBusy(false);
    }
  }

  // Submit on Enter, allow newlines with Shift+Enter.
  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  }

  return (
    <div className="cb-rise">
      <h1 style={{ marginBottom: "6px" }}>Ask the codebase</h1>
      <p style={{ color: "var(--text-dim)", marginBottom: "26px" }}>
        Ask a question in plain English. Answers cite the exact files and lines
        they draw from. To validate a test, describe what it expects.
      </p>

      {/* Transcript. */}
      {turns.length === 0 && !busy && (
        <div
          className="cb-card"
          style={{
            textAlign: "center",
            color: "var(--text-faint)",
            padding: "40px 20px",
          }}
        >
          No questions yet. Try: “How does authentication work?” or “Where are
          webhook signatures verified?”
        </div>
      )}
      {turns.map((turn, i) => (
        <Turn key={i} turn={turn} />
      ))}
      {busy && (
        <div style={{ marginBottom: "20px" }}>
          <Spinner label="Searching the knowledge base" />
        </div>
      )}
      <div ref={bottomRef} />

      {error && (
        <div style={{ marginBottom: "16px" }}>
          <Banner kind="error">{error}</Banner>
        </div>
      )}

      {/* Composer. */}
      <div
        style={{
          position: "sticky",
          bottom: "0",
          paddingTop: "12px",
          background:
            "linear-gradient(to top, var(--bg) 70%, transparent)",
        }}
      >
        <textarea
          className="cb-textarea"
          placeholder="Ask about the codebase…  (Enter to send, Shift+Enter for a newline)"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          disabled={busy}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: "10px",
          }}
        >
          <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>
            {sessionId
              ? `session ${sessionId.slice(0, 16)}…`
              : "new session"}
          </span>
          <button
            className="cb-button"
            onClick={handleAsk}
            disabled={busy || !question.trim()}
          >
            {busy ? "Thinking…" : "Ask"}
          </button>
        </div>
      </div>
    </div>
  );
}
