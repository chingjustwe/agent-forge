import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import InviteRegister from "./pages/InviteRegister";
import Sessions from "./pages/Sessions";
import AdminPage from "./pages/AdminPage";
import AdminDashboard from "./pages/AdminDashboard";
import AdminUsers from "./pages/AdminUsers";
import AdminWorkspaces from "./pages/AdminWorkspaces";
import Audit from "./pages/Audit";
import Analytics from "./pages/Analytics";
import RequestDetail from "./pages/RequestDetail";
import Settings from "./pages/Settings";
import WorkspaceInvitations from "./pages/WorkspaceInvitations";
import Agents from "./pages/Agents";
import ApiKeys from "./pages/ApiKeys";
import AdminMCP from "./pages/AdminMCP";
import AdminSkills from "./pages/AdminSkills";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { WorkspaceProvider } from "./context/WorkspaceContext";
import { getToken, getCurrentUser } from "./api";

const SESSION_CHECK_INTERVAL_MS = 60_000; // check session every 60s

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = getToken();
  if (!token) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

function App() {
  // Use state for token so it stays reactive — when clearToken() fires
  // the auth:token-changed event, this state updates and the login route
  // renders correctly instead of showing the old stale token.
  const [token, setToken] = useState<string | null>(getToken);

  // Listen for auth:token-changed events so the token state stays in sync.
  useEffect(() => {
    const handler = () => setToken(getToken());
    window.addEventListener("auth:token-changed", handler);
    return () => window.removeEventListener("auth:token-changed", handler);
  }, []);

  // Initialize theme from localStorage before first render
  useEffect(() => {
    try {
      const stored = localStorage.getItem("agent_platform_theme");
      if (stored === "light" || stored === "dark") {
        document.documentElement.dataset.theme = stored;
      } else {
        document.documentElement.dataset.theme = "dark";
      }
    } catch {
      document.documentElement.dataset.theme = "dark";
    }
  }, []);

  // Initial session check (passive: apiFetch redirects to /login on 401)
  useEffect(() => {
    if (token) {
      getCurrentUser().catch(() => {});
    }
  }, [token]);

  // Proactive session check: periodically verify the token is still valid
  useEffect(() => {
    if (!token) return;
    const intervalId = setInterval(async () => {
      try {
        await getCurrentUser();
      } catch {
        // apiFetch handles 401 by redirecting to /login;
        // other errors are silently ignored
      }
    }, SESSION_CHECK_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [token]);

  return (
    <ToastProvider>
      <WorkspaceProvider>
        <Routes>
          <Route path="/invite" element={<InviteRegister />} />
          <Route path="/login" element={token ? <Navigate to="/" replace /> : <LoginPage />} />
          <Route path="/" element={<Navigate to="/sessions" replace />} />
          <Route path="/sessions" element={<ProtectedRoute><Sessions /></ProtectedRoute>} />
          <Route path="/sessions/:sessionId" element={<ProtectedRoute><Sessions /></ProtectedRoute>} />
          <Route path="/admin" element={<ProtectedRoute><AdminPage /></ProtectedRoute>} />
          <Route path="/admin/dashboard" element={<ProtectedRoute><AdminDashboard /></ProtectedRoute>} />
          <Route path="/admin/users" element={<ProtectedRoute><AdminUsers /></ProtectedRoute>} />
          <Route path="/admin/workspaces" element={<ProtectedRoute><AdminWorkspaces /></ProtectedRoute>} />
          <Route path="/admin/audit" element={<ProtectedRoute><Audit /></ProtectedRoute>} />
          <Route path="/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
          <Route path="/usage" element={<Navigate to="/analytics" replace />} />
          <Route path="/dashboard" element={<Navigate to="/analytics" replace />} />
          <Route path="/quota" element={<Navigate to="/analytics" replace />} />
          <Route path="/requests" element={<Navigate to="/admin/audit" replace />} />
          <Route path="/requests/:traceId" element={<ProtectedRoute><RequestDetail /></ProtectedRoute>} />
          <Route path="/settings" element={<Navigate to="/admin/observability" replace />} />
          <Route path="/admin/observability" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
          <Route path="/invitations" element={<ProtectedRoute><WorkspaceInvitations /></ProtectedRoute>} />
          <Route path="/invitations/:token" element={<WorkspaceInvitations />} />
          <Route path="/agents" element={<ProtectedRoute><Agents /></ProtectedRoute>} />
          <Route path="/api-keys" element={<ProtectedRoute><ApiKeys /></ProtectedRoute>} />
          <Route path="/mcp" element={<ProtectedRoute><AdminMCP /></ProtectedRoute>} />
          <Route path="/skills" element={<ProtectedRoute><AdminSkills /></ProtectedRoute>} />
        </Routes>
      </WorkspaceProvider>
    </ToastProvider>
  );
}

export default App;
