import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import InviteRegister from "./pages/InviteRegister";
import Sessions from "./pages/Sessions";
import AdminPage from "./pages/AdminPage";
import AdminDashboard from "./pages/AdminDashboard";
import AdminUsers from "./pages/AdminUsers";
import AdminWorkspaces from "./pages/AdminWorkspaces";
import AdminAuditLog from "./pages/AdminAuditLog";
import AdminUsage from "./pages/AdminUsage";
import Dashboard from "./pages/Dashboard";
import RequestList from "./pages/RequestList";
import RequestDetail from "./pages/RequestDetail";
import QuotaPage from "./pages/QuotaPage";
import Settings from "./pages/Settings";
import WorkspaceInvitations from "./pages/WorkspaceInvitations";
import Agents from "./pages/Agents";
import ApiKeys from "./pages/ApiKeys";
import Layout from "./components/Layout";
import { WorkspaceProvider } from "./context/WorkspaceContext";
import { getToken, getCurrentUser } from "./api";

const SESSION_CHECK_INTERVAL_MS = 60_000; // check session every 60s

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = getToken();
  if (!token) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

function App() {
  const token = getToken();

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
        <Route path="/admin/audit" element={<ProtectedRoute><AdminAuditLog /></ProtectedRoute>} />
        <Route path="/admin/usage" element={<ProtectedRoute><AdminUsage /></ProtectedRoute>} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/requests" element={<ProtectedRoute><RequestList /></ProtectedRoute>} />
        <Route path="/requests/:traceId" element={<ProtectedRoute><RequestDetail /></ProtectedRoute>} />
        <Route path="/quota" element={<ProtectedRoute><QuotaPage /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/invitations" element={<ProtectedRoute><WorkspaceInvitations /></ProtectedRoute>} />
        <Route path="/invitations/:token" element={<WorkspaceInvitations />} />
        <Route path="/agents" element={<ProtectedRoute><Agents /></ProtectedRoute>} />
        <Route path="/api-keys" element={<ProtectedRoute><ApiKeys /></ProtectedRoute>} />
      </Routes>
    </WorkspaceProvider>
  );
}

export default App;
