import { useState, useEffect } from "react";
import { listWorkspaces, Workspace } from "../api";

const SELECTED_WS_KEY = "agent_platform_workspace";

export function getSelectedWorkspace(): string {
  return localStorage.getItem(SELECTED_WS_KEY) || "";
}

export function setSelectedWorkspace(id: string): void {
  localStorage.setItem(SELECTED_WS_KEY, id);
}

export default function WorkspaceSwitcher() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState(getSelectedWorkspace);

  useEffect(() => {
    listWorkspaces()
      .then((ws) => {
        setWorkspaces(ws);
        if (!selected && ws.length > 0) {
          setSelected(ws[0].id);
          setSelectedWorkspace(ws[0].id);
        }
      })
      .catch(() => {});
  }, []);

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value;
    setSelected(id);
    setSelectedWorkspace(id);
  }

  if (workspaces.length === 0) return null;

  return (
    <select value={selected} onChange={handleChange} style={{ padding: "4px 8px" }}>
      {workspaces.map((ws) => (
        <option key={ws.id} value={ws.id}>
          {ws.name}
        </option>
      ))}
    </select>
  );
}
