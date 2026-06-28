import { useState, useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
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
import Header from "./components/Header";
import { getToken, getCurrentUser, User } from "./api";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = getToken();
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function App() {
  const token = getToken();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (token) {
      getCurrentUser().then(setUser).catch(() => {});
    }
  }, [token]);

  const isAdmin = user?.role === "workspace_admin" || user?.role === "tenant_admin";
  const isTenantAdmin = user?.role === "tenant_admin";
  const isOwnerOrAdmin = user?.role === "tenant_admin" || user?.role === "workspace_owner";
  const wsId = user?.workspace_ids?.[0] || "";

  return (
    <div>
      {token && <Header />}
      <Routes>
        <Route path="/login" element={token ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute><AdminPage /></ProtectedRoute>} />
        <Route path="/admin/dashboard" element={<ProtectedRoute><AdminDashboard /></ProtectedRoute>} />
        <Route path="/admin/users" element={<ProtectedRoute><AdminUsers /></ProtectedRoute>} />
        <Route path="/admin/workspaces" element={<ProtectedRoute><AdminWorkspaces /></ProtectedRoute>} />
        <Route path="/admin/audit" element={<ProtectedRoute><AdminAuditLog /></ProtectedRoute>} />
        <Route path="/admin/usage" element={<ProtectedRoute><AdminUsage /></ProtectedRoute>} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard wsId={wsId} /></ProtectedRoute>} />
        <Route path="/requests" element={<ProtectedRoute><RequestList wsId={wsId} /></ProtectedRoute>} />
        <Route path="/requests/:traceId" element={<ProtectedRoute><RequestDetail wsId={wsId} /></ProtectedRoute>} />
        <Route path="/quota" element={<ProtectedRoute><QuotaPage wsId={wsId} isAdmin={isAdmin} /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings wsId={wsId} isAdmin={isAdmin} /></ProtectedRoute>} />
      </Routes>
    </div>
  );
}

export default App;
