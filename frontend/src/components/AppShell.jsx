/**
 * Application shell.
 *
 * The persistent frame around every authenticated view: a header with the
 * product mark, tabbed navigation, and the signed-in user's controls. The
 * routed view renders into the <Outlet/>.
 *
 * The "instrument panel" aesthetic from global.css is carried through here: a
 * thin amber index mark, monospace nav, and a calm dark frame.
 */
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

/** The three primary navigation destinations. */
const NAV_ITEMS = [
  { to: "/", label: "Ask", end: true },
  { to: "/indexing", label: "Index", end: false },
  { to: "/logs", label: "History", end: false },
];

export default function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {/* ---- Header -------------------------------------------------- */}
      <header
        style={{
          borderBottom: "1px solid var(--border)",
          background: "rgba(13, 14, 16, 0.85)",
          backdropFilter: "blur(8px)",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <div
          style={{
            maxWidth: "var(--maxw)",
            margin: "0 auto",
            padding: "0 24px",
            height: "62px",
            display: "flex",
            alignItems: "center",
            gap: "32px",
          }}
        >
          {/* Product mark — a blinking amber cursor + serif wordmark. */}
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span
              style={{
                width: "9px",
                height: "18px",
                background: "var(--accent)",
                display: "inline-block",
                animation: "cb-blink 1.2s steps(2) infinite",
              }}
            />
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "19px",
                fontWeight: 600,
                letterSpacing: "-0.01em",
              }}
            >
              CodeBase Brain
            </span>
          </div>

          {/* Primary navigation. */}
          <nav style={{ display: "flex", gap: "4px", flex: 1 }}>
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                style={({ isActive }) => ({
                  fontSize: "13px",
                  letterSpacing: "0.04em",
                  padding: "7px 14px",
                  borderRadius: "var(--radius-sm)",
                  color: isActive ? "var(--accent)" : "var(--text-dim)",
                  background: isActive ? "var(--accent-soft)" : "transparent",
                  textDecoration: "none",
                  transition: "color 0.15s ease, background 0.15s ease",
                })}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {/* User controls. */}
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <span style={{ color: "var(--text-faint)", fontSize: "13px" }}>
              {user?.username}
            </span>
            <button className="cb-button cb-button--ghost" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* ---- Routed view -------------------------------------------- */}
      <main style={{ flex: 1, width: "100%" }}>
        <div
          style={{
            maxWidth: "var(--maxw)",
            margin: "0 auto",
            padding: "32px 24px 64px",
          }}
        >
          <Outlet />
        </div>
      </main>

      {/* Header cursor blink keyframes. */}
      <style>{`
        @keyframes cb-blink {
          0%, 49% { opacity: 1; }
          50%, 100% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
