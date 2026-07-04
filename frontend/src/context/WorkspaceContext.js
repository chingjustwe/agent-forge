import { jsx as _jsx } from "react/jsx-runtime";
import { createContext, useContext, useState, useEffect } from "react";
import { listMyWorkspaces, getToken } from "../api";
const WorkspaceContext = createContext(null);
const STORAGE_KEY = "agent_platform_workspace";
export function WorkspaceProvider({ children }) {
    const [workspaces, setWorkspaces] = useState([]);
    const [currentId, setCurrentId] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
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
            if (next)
                localStorage.setItem(STORAGE_KEY, next);
            setError(null);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load workspaces");
        }
        finally {
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
    const switchTo = (id) => {
        setCurrentId(id);
        localStorage.setItem(STORAGE_KEY, id);
    };
    const current = workspaces.find(w => w.id === currentId) || null;
    const value = {
        workspaces,
        currentWorkspace: current,
        currentWorkspaceId: currentId,
        currentRole: current?.role || null,
        switchTo,
        refresh,
        loading,
        error,
    };
    return _jsx(WorkspaceContext.Provider, { value: value, children: children });
}
export function useWorkspace() {
    const ctx = useContext(WorkspaceContext);
    if (!ctx)
        throw new Error("useWorkspace must be used within WorkspaceProvider");
    return ctx;
}
export function useCurrentWorkspace() {
    return useWorkspace().currentWorkspace;
}
