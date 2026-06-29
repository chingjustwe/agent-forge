import { useState, useEffect } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { getCurrentUser, clearToken, User } from "../api";

const NAV_ITEMS = [
  { section: "Main", items: [
    { path: "/", label: "Chat", icon: "💬" },
    { path: "/dashboard", label: "Dashboard", icon: "📊" },
    { path: "/requests", label: "Requests", icon: "📋" },
    { path: "/quota", label: "Quota", icon: "📦" },
    { path: "/settings", label: "Settings", icon: "⚙️" },
  ]},
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => {});
  }, []);

  function handleLogout() {
    clearToken();
    navigate("/login");
  }

  const isAdmin = user?.role === "tenant_admin" || user?.role === "workspace_admin";
  const initials = user?.name
    ? user.name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)
    : user?.email?.slice(0, 2).toUpperCase() || "??";

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon">A</div>
          <span className="sidebar-brand-text">Agent Platform</span>
        </div>

        <nav className="sidebar-nav">
          {NAV_ITEMS.map((group) => (
            <div key={group.section}>
              <div className="sidebar-section-label">{group.section}</div>
              {group.items.map((item) => {
                const isActive = item.path === "/"
                  ? location.pathname === "/"
                  : location.pathname.startsWith(item.path);
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`sidebar-link${isActive ? " active" : ""}`}
                  >
                    <span className="sidebar-link-icon">{item.icon}</span>
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          ))}

          {isAdmin && (
            <div>
              <div className="sidebar-section-label">Admin</div>
              {[
                { path: "/admin", label: "Overview", icon: "🛡️" },
                { path: "/admin/users", label: "Users", icon: "👥" },
                { path: "/admin/workspaces", label: "Workspaces", icon: "🏢" },
                { path: "/admin/audit", label: "Audit Log", icon: "📝" },
                { path: "/admin/usage", label: "Usage", icon: "📈" },
              ].map((item) => {
                const isActive = location.pathname.startsWith(item.path);
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`sidebar-link${isActive ? " active" : ""}`}
                  >
                    <span className="sidebar-link-icon">{item.icon}</span>
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="sidebar-user-avatar">{initials}</div>
            <div className="sidebar-user-info">
              <div className="sidebar-user-name">{user?.name || "User"}</div>
              <div className="sidebar-user-email">{user?.email || ""}</div>
            </div>
          </div>
          <button className="sidebar-logout" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="main-content">
        {children}
      </main>
    </div>
  );
}