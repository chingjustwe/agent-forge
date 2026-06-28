import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";
import { getCurrentUser, clearToken, User } from "../api";
import WorkspaceSwitcher from "./WorkspaceSwitcher";

export default function Header() {
  const navigate = useNavigate();
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

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 16px",
        borderBottom: "1px solid #ccc",
        background: "#f8f8f8",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <Link to="/" style={{ textDecoration: "none", color: "inherit", fontWeight: "bold" }}>
          Agent Platform
        </Link>
        <WorkspaceSwitcher />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Link to="/dashboard" style={{ padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }}>
          Dashboard
        </Link>
        <Link to="/requests" style={{ padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }}>
          Requests
        </Link>
        <Link to="/quota" style={{ padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }}>
          Quota
        </Link>
        <Link to="/settings" style={{ padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }}>
          Settings
        </Link>
        {user?.role === "tenant_admin" && (
          <Link to="/admin" style={{ padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }}>
            Admin
          </Link>
        )}
        <span style={{ fontSize: "0.9em", color: "#666" }}>
          {user?.email || ""}
        </span>
        <button onClick={handleLogout} style={{ padding: "4px 12px", cursor: "pointer" }}>
          Logout
        </button>
      </div>
    </header>
  );
}
