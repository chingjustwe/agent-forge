import { Fragment, useEffect, useState } from "react";
import {
  checkMCPHealth,
  createMCPServer,
  deleteMCPServer,
  discoverMCPTools,
  fetchPermissions,
  getCurrentUser,
  listMCPServers,
  MCPServerInfo,
  MCPToolInfo,
  updateMCPServer,
  User,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { Select } from "../components/Select";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

/** Auto-detect MCP transport from endpoint URL: /sse → sse, else http. */
function detectTransport(endpoint: string): string {
  const trimmed = endpoint.trim().replace(/\/+$/, "");
  if (trimmed.endsWith("/sse")) return "sse";
  return "http";
}

export default function AdminMCP() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const toast = useToast();
  const [servers, setServers] = useState<MCPServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  const isWorkspaceAdmin =
    currentRole === "workspace_admin" || currentRole === "tenant_admin";

  /** Whether the current user may edit/delete a specific MCP server. */
  function canEditServer(server: MCPServerInfo): boolean {
    if (isWorkspaceAdmin) return true;
    return server.created_by === (user?.id || "");
  }

  // Tools drawer: server name -> tools (lazy loaded)
  const [toolsByServer, setToolsByServer] = useState<Record<string, MCPToolInfo[]>>({});
  const [toolsLoading, setToolsLoading] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  // Health state: server name -> { healthy, error }
  const [health, setHealth] = useState<Record<string, { healthy: boolean | null; error?: string | null }>>({});
  const [healthChecking, setHealthChecking] = useState<string | null>(null);

  // Create modal
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEndpoint, setNewEndpoint] = useState("");
  const [newTransport, setNewTransport] = useState("http");
  const [newToken, setNewToken] = useState("");

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<MCPServerInfo | null>(null);

  // Edit modal
  const [editTarget, setEditTarget] = useState<MCPServerInfo | null>(null);
  const [editEndpoint, setEditEndpoint] = useState("");
  const [editTransport, setEditTransport] = useState("http");
  const [editToken, setEditToken] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [savingEdit, setSavingEdit] = useState(false);

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listMCPServers(currentWorkspaceId);
      setServers(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load MCP servers");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  useEffect(() => {
    fetchPermissions()
      .then(p => setCanWrite(p.permissions.includes("*") || p.permissions.includes("mcp:write")))
      .catch(() => setCanWrite(false));
    getCurrentUser().then(setUser).catch(() => {});
  }, []);

  // Auto-switch transport to "sse" when the create-form endpoint ends with /sse
  useEffect(() => {
    if (newEndpoint.trim().replace(/\/+$/, "").endsWith("/sse")) {
      setNewTransport("sse");
    }
  }, [newEndpoint]);

  // Auto-switch transport to "sse" when the edit-form endpoint ends with /sse
  useEffect(() => {
    if (editEndpoint.trim().replace(/\/+$/, "").endsWith("/sse")) {
      setEditTransport("sse");
    }
  }, [editEndpoint]);

  function openCreate() {
    setNewName("");
    setNewEndpoint("");
    setNewTransport("http");
    setNewToken("");
    setCreateOpen(true);
  }

  async function handleCreate() {
    if (!currentWorkspaceId) {
      toast.error("No workspace selected");
      return;
    }
    if (!newName.trim() || !newEndpoint.trim()) {
      toast.error("Validation error", "Name and endpoint are required");
      return;
    }
    setCreating(true);
    try {
      await createMCPServer(currentWorkspaceId, {
        name: newName.trim(),
        endpoint: newEndpoint.trim(),
        transport: newTransport,
        auth_token: newToken.trim() || null,
      });
      toast.success("MCP server registered");
      setCreateOpen(false);
      refresh();
    } catch (e: unknown) {
      toast.error("Failed to register", e instanceof Error ? e.message : undefined);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget || !currentWorkspaceId) return;
    try {
      await deleteMCPServer(currentWorkspaceId, deleteTarget.name);
      toast.success("MCP server deleted", deleteTarget.name);
      setDeleteTarget(null);
      refresh();
    } catch (e: unknown) {
      toast.error("Failed to delete", e instanceof Error ? e.message : undefined);
    }
  }

  function openEdit(server: MCPServerInfo) {
    setEditTarget(server);
    setEditEndpoint(server.endpoint);
    setEditTransport(server.transport);
    setEditToken("");
    setEditEnabled(server.enabled);
    setSavingEdit(false);
  }

  async function handleEditSave() {
    if (!editTarget || !currentWorkspaceId) return;
    if (!editEndpoint.trim()) {
      toast.error("Validation error", "Endpoint is required");
      return;
    }
    setSavingEdit(true);
    try {
      await updateMCPServer(currentWorkspaceId, editTarget.name, {
        endpoint: editEndpoint.trim(),
        transport: editTransport,
        auth_token: editToken.trim() || null,
        enabled: editEnabled,
      });
      toast.success("MCP server updated", editTarget.name);
      setEditTarget(null);
      refresh();
    } catch (e: unknown) {
      toast.error("Failed to update", e instanceof Error ? e.message : undefined);
    } finally {
      setSavingEdit(false);
    }
  }

  async function toggleTools(server: MCPServerInfo) {
    if (expanded === server.name) {
      setExpanded(null);
      return;
    }
    setExpanded(server.name);
    if (toolsByServer[server.name]) return;
    if (!currentWorkspaceId) return;
    setToolsLoading(server.name);
    try {
      const tools = await discoverMCPTools(currentWorkspaceId, server.name);
      setToolsByServer(prev => ({ ...prev, [server.name]: tools }));
    } catch (e: unknown) {
      toast.error("Failed to list tools", e instanceof Error ? e.message : undefined);
      setToolsByServer(prev => ({ ...prev, [server.name]: [] }));
    } finally {
      setToolsLoading(null);
    }
  }

  async function runHealth(server: MCPServerInfo) {
    if (!currentWorkspaceId) return;
    setHealthChecking(server.name);
    try {
      const res = await checkMCPHealth(currentWorkspaceId, server.name);
      setHealth(prev => ({ ...prev, [server.name]: res }));
    } catch (e: unknown) {
      setHealth(prev => ({ ...prev, [server.name]: { healthy: false, error: e instanceof Error ? e.message : "request failed" } }));
    } finally {
      setHealthChecking(null);
    }
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">MCP Servers</h1>
          <p className="page-subtitle">Workspace-scoped Model Context Protocol servers</p>
        </div>
        {canWrite && (
          <button className="btn btn-primary" onClick={openCreate}>+ Register Server</button>
        )}
      </div>

      {error && <EmptyState title="Error loading MCP servers" description={error} />}
      {!error && loading && <SkeletonTable rows={4} cols={5} />}
      {!error && !loading && servers.length === 0 && (
        <EmptyState
          title="No MCP servers"
          description={canWrite ? "Register an MCP server to expose its tools to agents in this workspace." : "No MCP servers are registered."}
          action={canWrite ? { label: "+ Register Server", onClick: openCreate } : undefined}
        />
      )}

      {!error && !loading && servers.length > 0 && (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Transport</th>
                <th>Endpoint</th>
                <th>Status</th>
                <th style={{ width: 1 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {servers.map(server => {
                const isOpen = expanded === server.name;
                const healthState = health[server.name];
                const healthy = healthState?.healthy;
                return (
                  <Fragment key={server.name}>
                    <tr key={server.name}>
                      <td>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          {server.name}
                          {server.enabled
                            ? <span className="badge badge-success" style={{ fontSize: "0.7rem" }}>enabled</span>
                            : <span className="badge badge-muted" style={{ fontSize: "0.7rem" }}>disabled</span>}
                        </span>
                      </td>
                      <td><span className="badge badge-info">{server.transport}</span></td>
                      <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)", fontFamily: "var(--font-mono, monospace)" }}>
                        {server.endpoint}
                      </td>
                      <td>
                        {healthy === true && <span className="badge badge-success">healthy</span>}
                        {healthy === false && (
                          <span
                            className="badge badge-error"
                            title={healthState.error || "unreachable"}
                            style={{ cursor: "help" }}
                          >
                            unreachable
                          </span>
                        )}
                        {healthy === null && <span className="badge badge-muted">unknown</span>}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => toggleTools(server)}
                          >
                            {isOpen ? "Hide Tools" : "Tools"}
                          </button>
                          <button
                            className="btn btn-secondary btn-sm"
                            disabled={healthChecking === server.name}
                            onClick={() => runHealth(server)}
                          >
                            {healthChecking === server.name ? "Pinging..." : "Health"}
                          </button>
                          {canEditServer(server) && (
                            <>
                              <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => openEdit(server)}
                              >
                                Edit
                              </button>
                              <button
                                className="btn btn-danger btn-sm"
                                onClick={() => setDeleteTarget(server)}
                              >
                                Delete
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={`${server.name}-tools`}>
                        <td colSpan={5} style={{ background: "var(--bg-subtle)" }}>
                          {toolsLoading === server.name ? (
                            <span style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Loading tools...</span>
                          ) : (
                            <div className="mcp-tools">
                              {(toolsByServer[server.name] || []).length === 0 ? (
                                <span style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>No tools discovered.</span>
                              ) : (
                                (toolsByServer[server.name] || []).map(tool => (
                                  <div key={tool.name} className="mcp-tool-row">
                                    <span className="tool-name">{tool.name}</span>
                                    <span className="tool-desc">{tool.description || "—"}</span>
                                  </div>
                                ))
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Register MCP Server"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setCreateOpen(false)} disabled={creating}>Cancel</button>
            <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
              {creating ? "Registering..." : "Register"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Name *</label>
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="e.g. github-mcp"
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label">Endpoint *</label>
            <input
              value={newEndpoint}
              onChange={e => setNewEndpoint(e.target.value)}
              placeholder="https://... or command for stdio"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Transport</label>
            <Select
              value={newTransport}
              onChange={setNewTransport}
              options={[
                { value: "http", label: "http (Streamable HTTP)" },
                { value: "sse", label: "sse (MCP SSE)" },
                { value: "stdio", label: "stdio (subprocess)" },
              ]}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Auth Token (optional)</label>
            <input
              type="password"
              value={newToken}
              onChange={e => setNewToken(e.target.value)}
              placeholder="Bearer token for HTTP/SSE transport"
            />
          </div>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title={`Edit MCP Server: ${editTarget?.name ?? ""}`}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setEditTarget(null)} disabled={savingEdit}>Cancel</button>
            <button className="btn btn-primary" onClick={handleEditSave} disabled={savingEdit}>
              {savingEdit ? "Saving..." : "Save"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Endpoint *</label>
            <input
              value={editEndpoint}
              onChange={e => setEditEndpoint(e.target.value)}
              placeholder="https://... or command for stdio"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Transport</label>
            <Select
              value={editTransport}
              onChange={setEditTransport}
              options={[
                { value: "http", label: "http (Streamable HTTP)" },
                { value: "sse", label: "sse (MCP SSE)" },
                { value: "stdio", label: "stdio (subprocess)" },
              ]}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Auth Token (optional)</label>
            <input
              type="password"
              value={editToken}
              onChange={e => setEditToken(e.target.value)}
              placeholder="Leave blank to keep existing token"
            />
          </div>
          <div className="form-group">
            <label className="check-row" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={editEnabled}
                onChange={e => setEditEnabled(e.target.checked)}
              />
              <span className="check-meta">
                <span className="check-name">Enabled</span>
                <span className="check-desc">When disabled, agents cannot use this server's tools.</span>
              </span>
            </label>
          </div>
        </div>
      </Modal>

      {/* Delete ConfirmDialog */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete MCP Server"
        description={`Remove "${deleteTarget?.name}"? Its tools will no longer be available to agents.`}
        confirmText="Delete"
        variant="danger"
      />
    </div>
  );
}
