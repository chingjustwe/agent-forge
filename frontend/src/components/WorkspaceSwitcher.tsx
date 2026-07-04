import { useWorkspace } from "../context/WorkspaceContext";

export default function WorkspaceSwitcher() {
  const { workspaces, currentWorkspaceId, switchTo, loading } = useWorkspace();

  // 未登录或无 workspace 时完全不渲染（保持 sidebar 简洁）
  if (loading) {
    return (
      <div className="ws-switcher-block">
        <div className="ws-switcher-label">
          <span className="ws-switcher-label-icon" aria-hidden>🏢</span>
          <span>Workspace</span>
        </div>
        <span className="ws-switcher-loading">Loading…</span>
      </div>
    );
  }
  if (workspaces.length === 0) return null;

  const onlyOne = workspaces.length === 1;

  return (
    <div className="ws-switcher-block">
      <div className="ws-switcher-label">
        <span className="ws-switcher-label-icon" aria-hidden>🏢</span>
        <span>Workspace</span>
        <span className="ws-switcher-count">{workspaces.length}</span>
      </div>
      <div className="ws-switcher-select-wrap">
        <select
          className="ws-switcher"
          value={currentWorkspaceId}
          onChange={(e) => switchTo(e.target.value)}
          disabled={onlyOne}
          title={onlyOne ? "You are in the only workspace" : "Switch workspace"}
        >
          {workspaces.map(w => (
            <option key={w.id} value={w.id}>
              {w.name}
              {w.role === "workspace_owner" ? " · Owner" : w.role === "workspace_admin" ? " · Admin" : ""}
            </option>
          ))}
        </select>
        <span className="ws-switcher-chevron" aria-hidden>▾</span>
      </div>
      {onlyOne && (
        <div className="ws-switcher-hint">Only workspace — invite members to create more</div>
      )}
    </div>
  );
}
