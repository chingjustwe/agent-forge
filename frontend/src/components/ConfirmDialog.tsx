import { useState } from "react";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  title: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  variant?: "danger" | "default";
  loading?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmText = "Confirm",
  cancelText = "Cancel",
  variant = "danger",
  loading: externalLoading,
}: ConfirmDialogProps) {
  const [internalLoading, setInternalLoading] = useState(false);
  const loading = externalLoading ?? internalLoading;

  async function handleConfirm() {
    if (loading) return;
    const result = onConfirm();
    if (result instanceof Promise) {
      setInternalLoading(true);
      try {
        await result;
        setInternalLoading(false);
        onClose();
      } catch {
        // Keep dialog open on rejection
        setInternalLoading(false);
      }
    } else {
      onClose();
    }
  }

  return (
    <Modal open={open} onClose={onClose} width="sm">
      <div className="confirm-dialog">
        <div className="confirm-dialog-icon">
          {variant === "danger" ? (
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="3,6 5,6 21,6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              <path d="M10 11v6" />
              <path d="M14 11v6" />
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            </svg>
          ) : (
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <circle cx="12" cy="16" r="0.5" fill="currentColor" />
            </svg>
          )}
        </div>
        <div className="confirm-dialog-title">{title}</div>
        <div className="confirm-dialog-description">{description}</div>
      </div>
      <div className="confirm-dialog-actions" style={{ display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "20px" }}>
        <button className="btn btn-secondary" onClick={onClose} disabled={loading}>
          {cancelText}
        </button>
        <button
          className={`btn ${variant === "danger" ? "btn-danger" : "btn-primary"}`}
          onClick={handleConfirm}
          disabled={loading}
        >
          {loading ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="spin">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28 12" strokeLinecap="round" />
              </svg>
              {confirmText}
            </span>
          ) : (
            confirmText
          )}
        </button>
      </div>
    </Modal>
  );
}
