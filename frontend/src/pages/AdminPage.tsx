import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { listWorkspaces, listAdminUsers, Workspace, User } from "../api";

function UsersIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function WorkspacesIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="2" width="16" height="20" rx="2" />
      <path d="M9 22v-4h6v4" />
      <path d="M8 6h.01" />
      <path d="M16 6h.01" />
      <path d="M12 6h.01" />
      <path d="M12 10h.01" />
      <path d="M12 14h.01" />
      <path d="M16 10h.01" />
      <path d="M16 14h.01" />
      <path d="M8 10h.01" />
      <path d="M8 14h.01" />
    </svg>
  );
}

function AuditLogIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

function UsageIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  );
}

interface QuickLinkCard {
  path: string;
  title: string;
  description: string;
  icon: JSX.Element;
  accentClass: string;
}

const QUICK_LINKS: QuickLinkCard[] = [
  {
    path: "/admin/users",
    title: "Users",
    description: "Manage users, roles, and invitations",
    icon: <UsersIcon />,
    accentClass: "stat-card-accent",
  },
  {
    path: "/admin/workspaces",
    title: "Workspaces",
    description: "Manage workspaces and quotas",
    icon: <WorkspacesIcon />,
    accentClass: "stat-card-accent-success",
  },
  {
    path: "/admin/audit",
    title: "Audit Log",
    description: "View administrative actions",
    icon: <AuditLogIcon />,
    accentClass: "stat-card-accent-warning",
  },
  {
    path: "/admin/usage",
    title: "Usage",
    description: "View platform usage statistics",
    icon: <UsageIcon />,
    accentClass: "stat-card-accent-error",
  },
];

export default function AdminPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([listWorkspaces(), listAdminUsers()])
      .then(([ws, us]) => { setWorkspaces(ws); setUsers(us); })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load data");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Admin Overview</h1>
        <p className="page-subtitle">Manage workspaces, users, and platform settings</p>
      </div>

      <div className="stat-grid">
        <div className="stat-card stat-card-accent">
          <div className="stat-card-value">{loading ? "-" : workspaces.length}</div>
          <div className="stat-card-label">Workspaces</div>
        </div>
        <div className="stat-card stat-card-accent-success">
          <div className="stat-card-value">{loading ? "-" : users.length}</div>
          <div className="stat-card-label">Users</div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Quick Links */}
      <div style={{ marginBottom: 32 }}>
        <h2 style={{ fontSize: "1rem", marginBottom: 12, color: "var(--text-secondary)" }}>Quick Links</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
          {QUICK_LINKS.map(link => (
            <Link
              key={link.path}
              to={link.path}
              style={{ textDecoration: "none" }}
            >
              <div className={`stat-card ${link.accentClass}`} style={{ cursor: "pointer" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
                  <div style={{ color: "var(--text-secondary)" }}>{link.icon}</div>
                  <div className="stat-card-value" style={{ fontSize: "1.1rem" }}>{link.title}</div>
                </div>
                <div className="stat-card-label">{link.description}</div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
