import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { listMyWorkspaces, WorkspaceMembership, getToken } from "../api";

export type { WorkspaceMembership };

interface WorkspaceContextValue {
  workspaces: WorkspaceMembership[];
  currentWorkspace: WorkspaceMembership | null;
  currentWorkspaceId: string;
  currentRole: string | null;
  switchTo: (id: string) => void;
  refresh: () => Promise<void>;
  loading: boolean;
  error: string | null;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

const STORAGE_KEY = "agent_platform_workspace";

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [currentId, setCurrentId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    // 未登录时不发起请求，避免 401 触发 redirectToLogin 导致页面无限刷新
    if (!getToken()) {
      setWorkspaces([]);
      setCurrentId("");
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const list = await listMyWorkspaces();
      setWorkspaces(list);
      // Selection: stored id (if still in list) → first item → empty.
      const stored = localStorage.getItem(STORAGE_KEY) || "";
      const valid = list.find(w => w.id === stored);
      const next = valid ? stored : (list[0]?.id || "");
      setCurrentId(next);
      if (next) localStorage.setItem(STORAGE_KEY, next);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load workspaces");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // 登录/登出/token 失效时重新拉取（setToken/clearToken 会派发该事件）
    const handler = () => refresh();
    window.addEventListener("auth:token-changed", handler);
    return () => window.removeEventListener("auth:token-changed", handler);
  }, []);

  const switchTo = (id: string) => {
    setCurrentId(id);
    localStorage.setItem(STORAGE_KEY, id);
  };

  const current = workspaces.find(w => w.id === currentId) || null;

  const value: WorkspaceContextValue = {
    workspaces,
    currentWorkspace: current,
    currentWorkspaceId: currentId,
    currentRole: current?.role || null,
    switchTo,
    refresh,
    loading,
    error,
  };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}

export function useCurrentWorkspace(): WorkspaceMembership | null {
  return useWorkspace().currentWorkspace;
}
