/**
 * Root application component.
 *
 * Wires together routing, the auth provider, and the persistent app shell
 * (header + navigation). Routes split into two groups:
 *
 *   * Public  — the login/register screen.
 *   * Private — chat, indexing, and query-log views, all gated by <Protected/>
 *     which redirects unauthenticated visitors to the login screen.
 *
 * Using a hash-free BrowserRouter is fine here because Nginx (production) and
 * the Vite dev server are both configured to serve index.html for any path.
 */
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext.jsx";
import { Spinner } from "./components/Feedback.jsx";
import AppShell from "./components/AppShell.jsx";
import LoginView from "./views/LoginView.jsx";
import ChatView from "./views/ChatView.jsx";
import IndexingView from "./views/IndexingView.jsx";
import QueryLogsView from "./views/QueryLogsView.jsx";

/**
 * Route guard. Renders its children only for an authenticated user; otherwise
 * redirects to /login. While the initial session-restore probe is running it
 * shows a spinner so we never flash the login screen for a logged-in user.
 */
function Protected({ children }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spinner label="Restoring session" />
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

/** The route table, kept separate so it can sit inside <AuthProvider/>. */
function AppRoutes() {
  return (
    <Routes>
      {/* Public route. */}
      <Route path="/login" element={<LoginView />} />

      {/* Private routes — wrapped in the shell and the auth guard. */}
      <Route
        path="/"
        element={
          <Protected>
            <AppShell />
          </Protected>
        }
      >
        {/* Default landing view is the chat. */}
        <Route index element={<ChatView />} />
        <Route path="indexing" element={<IndexingView />} />
        <Route path="logs" element={<QueryLogsView />} />
      </Route>

      {/* Anything unknown falls back to the chat. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
