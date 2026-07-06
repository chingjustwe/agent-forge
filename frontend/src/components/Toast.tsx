import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  ReactNode,
} from "react";

interface ToastMessage {
  id: string;
  type: "success" | "error" | "info" | "warning";
  title: string;
  message?: string;
  duration?: number;
}

interface ToastContextValue {
  success: (title: string, message?: string) => void;
  error: (title: string, message?: string) => void;
  info: (title: string, message?: string) => void;
  warning: (title: string, message?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const MAX_TOASTS = 5;

function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

/* ── Inline SVG icons per type ──────────────────────────────────────────── */

function SuccessIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6.5" />
      <polyline points="5.5,8 7.25,10 10.5,6" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6.5" />
      <line x1="5.75" y1="5.75" x2="10.25" y2="10.25" />
      <line x1="10.25" y1="5.75" x2="5.75" y2="10.25" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="8" cy="8" r="6.5" />
      <line x1="8" y1="7" x2="8" y2="11.5" />
      <circle cx="8" cy="5" r="0.5" fill="currentColor" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2L1.5 13.5h13L8 2z" />
      <line x1="8" y1="6.5" x2="8" y2="9.5" />
      <circle cx="8" cy="11.5" r="0.5" fill="currentColor" />
    </svg>
  );
}

const ICONS: Record<ToastMessage["type"], () => JSX.Element> = {
  success: SuccessIcon,
  error: ErrorIcon,
  info: InfoIcon,
  warning: WarningIcon,
};

/* ── Single toast row ────────────────────────────────────────────────────── */

function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastMessage;
  onDismiss: (id: string) => void;
}) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startRef = useRef<number>(0);
  const remainingRef = useRef<number>(toast.duration ?? 4000);
  const duration = toast.duration ?? 4000;

  function startTimer() {
    startRef.current = Date.now();
    timerRef.current = setTimeout(() => {
      onDismiss(toast.id);
    }, remainingRef.current);
  }

  function pauseTimer() {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
      remainingRef.current -= Date.now() - startRef.current;
      if (remainingRef.current < 0) remainingRef.current = 0;
    }
  }

  // Mount / resume timer
  useEffect(() => {
    startTimer();
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const Icon = ICONS[toast.type];

  return (
    <div
      className={`toast toast-${toast.type}`}
      onMouseEnter={pauseTimer}
      onMouseLeave={startTimer}
    >
      <span className="toast-icon">
        <Icon />
      </span>
      <div className="toast-content">
        <div className="toast-title">{toast.title}</div>
        {toast.message && (
          <div className="toast-message">{toast.message}</div>
        )}
      </div>
      <button className="toast-close" onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="4" y1="4" x2="12" y2="12" />
          <line x1="12" y1="4" x2="4" y2="12" />
        </svg>
      </button>
      <div className="toast-progress" style={{ animationDuration: `${duration}ms` }} />
    </div>
  );
}

/* ── ToastProvider ───────────────────────────────────────────────────────── */

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (type: ToastMessage["type"], title: string, message?: string, duration?: number) => {
      const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
      setToasts((prev) => {
        const next = [...prev, { id, type, title, message, duration }];
        return next.length > MAX_TOASTS ? next.slice(-MAX_TOASTS) : next;
      });
    },
    [],
  );

  const success = useCallback((title: string, message?: string) => addToast("success", title, message), [addToast]);
  const error = useCallback((title: string, message?: string) => addToast("error", title, message), [addToast]);
  const info = useCallback((title: string, message?: string) => addToast("info", title, message), [addToast]);
  const warning = useCallback((title: string, message?: string) => addToast("warning", title, message), [addToast]);

  const ctx: ToastContextValue = { success, error, info, warning };

  return (
    <ToastContext.Provider value={ctx}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => (
          <Toast key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export { useToast };
