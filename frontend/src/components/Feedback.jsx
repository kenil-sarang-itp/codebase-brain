/**
 * Small shared presentational components used across views.
 */

/**
 * Banner — an inline status message (error / success / info).
 *
 * Props:
 *   kind: "error" | "success" | "info"
 *   children: message content
 */
export function Banner({ kind = "info", children }) {
  if (!children) return null;
  const colour = {
    error: "var(--err)",
    success: "var(--ok)",
    info: "var(--info)",
  }[kind];
  return (
    <div
      style={{
        borderLeft: `3px solid ${colour}`,
        background: "var(--bg-inset)",
        color: "var(--text-dim)",
        padding: "10px 14px",
        borderRadius: "var(--radius-sm)",
        fontSize: "13px",
      }}
    >
      {children}
    </div>
  );
}

/**
 * Spinner — a minimal inline loading indicator.
 *
 * Props:
 *   label: optional text shown next to the animated marker.
 */
export function Spinner({ label = "Working" }) {
  return (
    <span style={{ color: "var(--text-dim)", fontSize: "13px" }}>
      <span
        style={{
          display: "inline-block",
          width: "8px",
          height: "8px",
          marginRight: "8px",
          borderRadius: "50%",
          background: "var(--accent)",
          animation: "cb-pulse 1s ease-in-out infinite",
        }}
      />
      {label}…
      {/* Local keyframes so this component is fully self-contained. */}
      <style>{`
        @keyframes cb-pulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50% { opacity: 1; transform: scale(1.1); }
        }
      `}</style>
    </span>
  );
}
