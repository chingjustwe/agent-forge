import { useState, useEffect } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { getCurrentUser, clearToken, User, fetchPermissions, createSession } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import WorkspaceSwitcher from "./WorkspaceSwitcher";

// ── SVG Icon Components ────────────────────────────────────────────────────

function SessionsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="12" width="4" height="9" rx="1" />
      <rect x="10" y="7" width="4" height="14" rx="1" />
      <rect x="17" y="3" width="4" height="18" rx="1" />
    </svg>
  );
}

function RequestsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  );
}

function QuotaIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" />
      <path d="M12 6v6l4 2" />
    </svg>
  );
}

function AgentsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <circle cx="12" cy="5" r="2" />
      <path d="M12 7v4" />
      <line x1="8" y1="16" x2="8" y2="16.01" />
      <line x1="16" y1="16" x2="16" y2="16.01" />
    </svg>
  );
}

function ApiKeysIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  );
}

function AdminOverviewIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function UsersIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function WorkspacesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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

function ObservabilityIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}

function AuditLogIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

function UsageIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function SidebarCollapseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 3v18" />
    </svg>
  );
}

function SidebarExpandIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M15 3v18" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function SignOutIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}

// ── Nav item type ─────────────────────────────────────────────────────────

interface NavItem {
  path: string;
  label: string;
  icon: JSX.Element;
}

const SESSION_ITEMS: NavItem[] = [
  { path: "/sessions", label: "Sessions", icon: <SessionsIcon /> },
];

const TOOL_ITEMS: NavItem[] = [
  { path: "/dashboard", label: "Dashboard", icon: <DashboardIcon /> },
  { path: "/requests", label: "Requests", icon: <RequestsIcon /> },
  { path: "/agents", label: "Agents", icon: <AgentsIcon /> },
  { path: "/api-keys", label: "API Keys", icon: <ApiKeysIcon /> },
  { path: "/quota", label: "Quota", icon: <QuotaIcon /> },
];

const ADMIN_ITEMS: NavItem[] = [
  { path: "/admin", label: "Overview", icon: <AdminOverviewIcon /> },
  { path: "/admin/users", label: "Users", icon: <UsersIcon /> },
  { path: "/admin/workspaces", label: "Workspaces", icon: <WorkspacesIcon /> },
  { path: "/admin/observability", label: "Observability", icon: <ObservabilityIcon /> },
  { path: "/admin/audit", label: "Audit Log", icon: <AuditLogIcon /> },
  { path: "/admin/usage", label: "Usage", icon: <UsageIcon /> },
];

// ── Layout Component ─────────────────────────────────────────────────────

export default function Layout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { currentWorkspaceId } = useWorkspace();
  const [user, setUser] = useState<User | null>(null);
  const [visibleTabs, setVisibleTabs] = useState<Set<string>>(new Set());
  const [visibleAdminTabs, setVisibleAdminTabs] = useState<Set<string>>(new Set());
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set(["platform"]));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem("agent_platform_sidebar_collapsed") === "true";
    } catch {
      return false;
    }
  });
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      const stored = localStorage.getItem("agent_platform_theme");
      return (stored === "light" || stored === "dark") ? stored : "dark";
    } catch {
      return "dark";
    }
  });

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => {});
  }, []);

  // Fetch permissions from backend to determine tab visibility.
  useEffect(() => {
    fetchPermissions()
      .then((resp) => {
        const tabs = new Set<string>();
        const adminTabs = new Set<string>();
        for (const [path, required] of Object.entries(resp.frontend_tabs)) {
          if (required !== null) {
            if (path.startsWith("/admin")) {
              adminTabs.add(path);
            } else {
              tabs.add(path);
            }
          } else {
            tabs.add(path);
          }
        }
        setVisibleTabs(tabs);
        setVisibleAdminTabs(adminTabs);
      })
      .catch(() => {});
  }, []);

  // Sync theme to DOM
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("agent_platform_theme", theme);
    } catch {}
  }, [theme]);

  // Sync sidebar collapsed state to localStorage
  useEffect(() => {
    try {
      localStorage.setItem("agent_platform_sidebar_collapsed", String(sidebarCollapsed));
    } catch {}
  }, [sidebarCollapsed]);

  function handleLogout() {
    clearToken();
    navigate("/login");
  }

  function toggleSidebarCollapse() {
    setSidebarCollapsed((prev) => !prev);
  }

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  function toggleSection(sectionId: string) {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      return next;
    });
  }

  // Create a new session and navigate to it directly
  async function handleNewSession() {
    if (!currentWorkspaceId) {
      navigate("/sessions");
      return;
    }
    try {
      const session = await createSession(currentWorkspaceId, { title: "New Session" });
      navigate(`/sessions/${session.id}`);
    } catch {
      // If creation fails, fall back to sessions list
      navigate("/sessions");
    }
  }

  const initials = user?.name
    ? user.name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)
    : user?.email?.slice(0, 2).toUpperCase() || "??";

  const visibleSessionItems = SESSION_ITEMS.filter((item) => visibleTabs.has(item.path));
  const visibleToolItems = TOOL_ITEMS.filter((item) => visibleTabs.has(item.path));
  const visibleAdminItems = ADMIN_ITEMS.filter((item) => visibleAdminTabs.has(item.path));

  function isNavActive(item: NavItem) {
    if (item.path === "/admin") {
      return location.pathname === "/admin";
    }
    if (item.path === "/") {
      return location.pathname === "/";
    }
    return location.pathname.startsWith(item.path);
  }

  function renderNavLink(item: NavItem) {
    const isActive = isNavActive(item);
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
  }

  return (
    <div className="app-layout">
      <aside className={`sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        {/* ── Brand ──────────────────────────────────────────────────── */}
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <defs>
                <linearGradient id="brand-logo-grad" x1="0" y1="0" x2="32" y2="32">
                  <stop offset="0%" stopColor="#3a81f6"/>
                  <stop offset="100%" stopColor="#2563ef"/>
                </linearGradient>
              </defs>
              <line x1="16" y1="6" x2="6" y2="18" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <line x1="16" y1="6" x2="26" y2="18" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <line x1="6" y1="18" x2="26" y2="18" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <line x1="16" y1="6" x2="16" y2="26" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <line x1="6" y1="18" x2="16" y2="26" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <line x1="26" y1="18" x2="16" y2="26" stroke="#2563ef" strokeWidth="1.5" opacity="0.5"/>
              <circle cx="16" cy="6" r="4" fill="url(#brand-logo-grad)"/>
              <circle cx="6" cy="18" r="4" fill="url(#brand-logo-grad)"/>
              <circle cx="26" cy="18" r="4" fill="url(#brand-logo-grad)"/>
              <circle cx="16" cy="26" r="4" fill="url(#brand-logo-grad)"/>
              <circle cx="16" cy="16" r="3" fill="#2563ef" opacity="0.8"/>
            </svg>
          </div>
          {!sidebarCollapsed && (
            <span className="sidebar-brand-text">Agent Platform</span>
          )}
          <button
            className="sidebar-collapse-btn"
            onClick={toggleSidebarCollapse}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? <SidebarExpandIcon /> : <SidebarCollapseIcon />}
          </button>
        </div>

        {/* ── Workspace Switcher ────────────────────────────────────── */}
        <div className="sidebar-workspace">
          <WorkspaceSwitcher />
        </div>

        {/* ── Navigation ────────────────────────────────────────────── */}
        <nav className="sidebar-nav">
          {/* Sessions — always visible at top, with inline "+" action */}
          {visibleSessionItems.map(item => (
            <div key={item.path} className="sidebar-link-group">
              <Link
                to={item.path}
                className={`sidebar-link${isNavActive(item) ? " active" : ""}`}
              >
                <span className="sidebar-link-icon">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
              <button
                className="sidebar-link-action"
                onClick={handleNewSession}
                title="New Session"
              >
                <PlusIcon />
              </button>
            </div>
          ))}

          {/* Divider */}
          <div className="sidebar-divider"></div>

          {/* Tools */}
          {visibleToolItems.length > 0 && (
            <div className={`collapsible-section${collapsedSections.has("tools") ? " collapsed" : ""}`}>
              <button className="sidebar-section-toggle" onClick={() => toggleSection("tools")}>
                <span>Tools</span>
                <span className={`collapsible-chevron${collapsedSections.has("tools") ? " collapsed" : ""}`}>
                  <ChevronDownIcon />
                </span>
              </button>
              {!collapsedSections.has("tools") && (
                <div>
                  {visibleToolItems.map(item => renderNavLink(item))}
                </div>
              )}
            </div>
          )}

          {/* Platform (Admin) */}
          {visibleAdminItems.length > 0 && (
            <div className={`collapsible-section${collapsedSections.has("platform") ? " collapsed" : ""}`}>
              <button className="sidebar-section-toggle" onClick={() => toggleSection("platform")}>
                <span>Platform</span>
                <span className={`collapsible-chevron${collapsedSections.has("platform") ? " collapsed" : ""}`}>
                  <ChevronDownIcon />
                </span>
              </button>
              {!collapsedSections.has("platform") && (
                <div>
                  {visibleAdminItems.map(item => renderNavLink(item))}
                </div>
              )}
            </div>
          )}
        </nav>

        {/* ── Footer ────────────────────────────────────────────────── */}
        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="sidebar-user-avatar">{initials}</div>
            <div className="sidebar-user-info">
              <div className="sidebar-user-name">{user?.name || "User"}</div>
              <div className="sidebar-user-email">{user?.email || ""}</div>
            </div>
            <button
              className="theme-toggle"
              onClick={toggleTheme}
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              style={{ marginLeft: "auto" }}
            >
              {theme === "dark" ? <SunIcon /> : <MoonIcon />}
            </button>
          </div>
          <button className="sidebar-logout" onClick={handleLogout}>
            <SignOutIcon />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      <div className="main-area">
        <main className="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
