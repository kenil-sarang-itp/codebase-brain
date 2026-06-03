/**
 * Login / register view.
 *
 * A single screen with a mode toggle: the same form serves both signing in and
 * creating an account, since they share most fields. On success the auth
 * context is updated and the user is redirected to wherever they were headed
 * (or the chat view by default).
 */
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { Banner, Spinner } from "../components/Feedback.jsx";

export default function LoginView() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // "login" or "register" — toggled by the link at the bottom of the card.
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Where to send the user after a successful auth.
  const redirectTo = location.state?.from?.pathname || "/";
  const isRegister = mode === "register";

  /** Submit the form — calls the matching auth action. */
  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (isRegister) {
        await register(username.trim(), email.trim(), password);
      } else {
        await login(username.trim(), password);
      }
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(err.message || "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "24px",
      }}
    >
      <div className="cb-rise" style={{ width: "100%", maxWidth: "390px" }}>
        {/* Wordmark above the card. */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "11px",
            marginBottom: "26px",
            justifyContent: "center",
          }}
        >
          <span
            style={{
              width: "11px",
              height: "22px",
              background: "var(--accent)",
              display: "inline-block",
            }}
          />
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "24px",
              fontWeight: 600,
            }}
          >
            CodeBase Brain
          </span>
        </div>

        <div className="cb-card">
          <h2 style={{ marginBottom: "4px" }}>
            {isRegister ? "Create your account" : "Sign in"}
          </h2>
          <p
            style={{
              color: "var(--text-faint)",
              fontSize: "13px",
              marginBottom: "22px",
            }}
          >
            {isRegister
              ? "Set up access to the codebase knowledge base."
              : "Query and index your codebase documentation."}
          </p>

          <form
            onSubmit={handleSubmit}
            style={{ display: "flex", flexDirection: "column", gap: "16px" }}
          >
            <div>
              <label className="cb-label" htmlFor="username">
                Username
              </label>
              <input
                id="username"
                className="cb-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                minLength={3}
              />
            </div>

            {/* Email is only needed when registering. */}
            {isRegister && (
              <div>
                <label className="cb-label" htmlFor="email">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  className="cb-input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
            )}

            <div>
              <label className="cb-label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                className="cb-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isRegister ? "new-password" : "current-password"}
                required
                minLength={8}
              />
            </div>

            {error && <Banner kind="error">{error}</Banner>}

            <button
              type="submit"
              className="cb-button"
              disabled={busy}
              style={{ marginTop: "4px" }}
            >
              {busy ? (
                <Spinner label={isRegister ? "Creating" : "Signing in"} />
              ) : isRegister ? (
                "Create account"
              ) : (
                "Sign in"
              )}
            </button>
          </form>

          {/* Mode toggle. */}
          <p
            style={{
              marginTop: "20px",
              fontSize: "13px",
              color: "var(--text-faint)",
              textAlign: "center",
            }}
          >
            {isRegister ? "Already have an account?" : "Need an account?"}{" "}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                setError("");
                setMode(isRegister ? "login" : "register");
              }}
            >
              {isRegister ? "Sign in" : "Register"}
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
