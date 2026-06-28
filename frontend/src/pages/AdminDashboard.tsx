import { useEffect, useState } from "react";
import { fetchTenants, fetchUsers, fetchAdminWorkspaces, fetchUsage, Tenant, AdminUser, AdminWorkspace, UsageData } from "../api";

export default function AdminDashboard() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchTenants().catch(() => []),
      fetchUsers().catch(() => []),
      fetchAdminWorkspaces().catch(() => []),
      fetchUsage().catch(() => null),
    ]).then(([t, u, w, us]) => {
      setTenants(t);
      setUsers(u);
      setWorkspaces(w);
      setUsage(us);
      setLoading(false);
    });
  }, []);

  if (loading) return <div>Loading...</div>;

  const totalUsers = users.length;
  const totalWorkspaces = workspaces.length;
  const requestsToday = usage?.total_requests || 0;
  const activeSessions = users.filter((u) => u.last_login).length;

  const cardStyle: React.CSSProperties = {
    background: "#f5f5f5",
    borderRadius: 8,
    padding: "20px 24px",
    flex: 1,
    minWidth: 200,
  };

  return (
    <div>
      <h1>Admin Dashboard</h1>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 32 }}>
        <div style={cardStyle}>
          <div style={{ fontSize: 28, fontWeight: "bold" }}>{totalUsers}</div>
          <div style={{ color: "#666" }}>Total Users</div>
        </div>
        <div style={cardStyle}>
          <div style={{ fontSize: 28, fontWeight: "bold" }}>{totalWorkspaces}</div>
          <div style={{ color: "#666" }}>Workspaces</div>
        </div>
        <div style={cardStyle}>
          <div style={{ fontSize: 28, fontWeight: "bold" }}>{requestsToday}</div>
          <div style={{ color: "#666" }}>Requests Today</div>
        </div>
        <div style={cardStyle}>
          <div style={{ fontSize: 28, fontWeight: "bold" }}>{activeSessions}</div>
          <div style={{ color: "#666" }}>Active Sessions</div>
        </div>
      </div>

      {tenants.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h2>Tenants</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", background: "#eee" }}>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Domain</th>
                <th style={thStyle}>Users</th>
                <th style={thStyle}>Workspaces</th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id}>
                  <td style={tdStyle}>{t.name}</td>
                  <td style={tdStyle}>{t.domain}</td>
                  <td style={tdStyle}>{t.user_count}</td>
                  <td style={tdStyle}>{t.workspace_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: "flex", gap: 16 }}>
        <a href="/admin/users" style={linkStyle}>Manage Users →</a>
        <a href="/admin/workspaces" style={linkStyle}>Manage Workspaces →</a>
        <a href="/admin/audit" style={linkStyle}>Audit Log →</a>
        <a href="/admin/usage" style={linkStyle}>Usage →</a>
      </div>
    </div>
  );
}

const thStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid #eee" };
const linkStyle: React.CSSProperties = {
  display: "block",
  padding: "12px 20px",
  background: "#1a1a2e",
  color: "#fff",
  textDecoration: "none",
  borderRadius: 6,
  fontWeight: 500,
};
