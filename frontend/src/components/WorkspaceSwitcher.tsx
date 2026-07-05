import { useState, useRef, useEffect } from "react";
import { useWorkspace } from "../context/WorkspaceContext";

function BuildingIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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

export default function WorkspaceSwitcher() {
  const { workspaces, currentWorkspaceId, switchTo, loading } = useWorkspace();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  if (loading) {
    return (
      <div className="ws-switcher-block">
        <div className="ws-switcher-label">
          <span className="ws-switcher-label-icon" aria-hidden><BuildingIcon /></span>
          <span>Workspace</span>
        </div>
        <span className="ws-switcher-loading">Loading…</span>
      </div>
    );
  }
  if (workspaces.length === 0) return null;

  const current = workspaces.find(w => w.id === currentWorkspaceId);
  const onlyOne = workspaces.length === 1;

  function renderIcon(icon?: string) {
    if (!icon) return null;
    if (/^https?:\/\//.test(icon)) {
      return <img src={icon} alt="" style={{ width: 16, height: 16, objectFit: "contain", flexShrink: 0 }} />;
    }
    return <span style={{ fontSize: "1rem", flexShrink: 0 }}>{icon}</span>;
  }

  return (
    <div className="ws-switcher-block" ref={ref}>
      <div className="ws-switcher-label">
        <span className="ws-switcher-label-icon" aria-hidden><BuildingIcon /></span>
        <span>Workspace</span>
        <span className="ws-switcher-count">{workspaces.length}</span>
      </div>
      <div
        className="ws-switcher-select-wrap"
        onClick={() => !onlyOne && setOpen(!open)}
        style={{ cursor: onlyOne ? "default" : "pointer" }}
        title={onlyOne ? "You are in the only workspace" : "Switch workspace"}
      >
        <div className="ws-switcher-trigger">
          {renderIcon(current?.icon)}
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {current?.name || ""}
          </span>
          {current?.role === "workspace_admin" && (
            <span style={{ color: "var(--text-muted)", fontSize: "0.75rem", flexShrink: 0 }}>Admin</span>
          )}
          {!onlyOne && <span className="ws-switcher-chevron" aria-hidden>▾</span>}
        </div>

        {open && !onlyOne && (
          <div className="ws-switcher-dropdown">
            {workspaces.map(w => (
              <div
                key={w.id}
                className={`ws-switcher-option${w.id === currentWorkspaceId ? " active" : ""}`}
                onClick={(e) => { e.stopPropagation(); switchTo(w.id); setOpen(false); }}
              >
                {renderIcon(w.icon)}
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {w.name}
                </span>
                {w.role === "workspace_admin" && (
                  <span style={{ color: "var(--text-muted)", fontSize: "0.75rem", flexShrink: 0 }}>Admin</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      {onlyOne && (
        <div className="ws-switcher-hint">Only workspace — invite members to create more</div>
      )}
    </div>
  );
}