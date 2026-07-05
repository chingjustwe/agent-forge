import { useEffect, useState } from "react";
import { fetchTenants, fetchUsers, fetchAdminWorkspaces, fetchUsage, Tenant, AdminUser, AdminWorkspace, UsageData } from "../api";
import { SkeletonTable, SkeletonText } from "../components/Skeleton";

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

  if (loading) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Admin Dashboard</h1>
          <p className="page-subtitle">Platform-wide overview and management</p>
        </div>
        <div className="stat-grid">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="stat-card">
              <SkeletonText lines={2} />
            </div>
          ))}
        </div>
        <SkeletonTable rows={5} cols={4} />
      </div>
    );
  }

  const totalUsers = users.length;
  const totalWorkspaces = workspaces.length;
  const requestsToday = usage?.total_requests || 0;
  const activeSessions = users.filter((u) => u.last_login).length;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Admin Dashboard</h1>
        <p className="page-subtitle">Platform-wide overview and management</p>
      </div>

      <div className="stat-grid">
        <div className="stat-card stat-card-accent">
          <div className="stat-card-value">{totalUsers}</div>
          <div className="stat-card-label">Total Users</div>
        </div>
        <div className="stat-card stat-card-accent-success">
          <div className="stat-card-value">{totalWorkspaces}</div>
          <div className="stat-card-label">Workspaces</div>
        </div>
        <div className="stat-card stat-card-accent">
          <div className="stat-card-value">{requestsToday}</div>
          <div className="stat-card-label">Requests Today</div>
        </div>
        <div className="stat-card stat-card-accent-warning">
          <div className="stat-card-value">{activeSessions}</div>
          <div className="stat-card-label">Active Sessions</div>
        </div>
      </div>

      {tenants.length > 0 && (
        <div className="detail-section">
          <h2 className="detail-section-title">Tenants</h2>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Domain</th>
                  <th>Users</th>
                  <th>Workspaces</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{t.domain}</td>
                    <td>{t.user_count}</td>
                    <td>{t.workspace_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
