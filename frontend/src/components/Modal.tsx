import { useEffect, useRef, ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  width?: "sm" | "md" | "lg";
  children: ReactNode;
  footer?: ReactNode;
  closeOnBackdrop?: boolean;
  closeOnEsc?: boolean;
  hideHeader?: boolean;
}

const WIDTH_CLASSES: Record<string, string> = {
  sm: "modal-sm",
  md: "modal-md",
  lg: "modal-lg",
};

export function Modal({
  open,
  onClose,
  title,
  width = "md",
  children,
  footer,
  closeOnBackdrop = true,
  closeOnEsc = true,
  hideHeader = false,
}: ModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  // Lock body scroll when modal is open
  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        // Safety: always restore on cleanup
        document.body.style.overflow = prev || "";
      };
    }
  }, [open]);

  // ESC key listener
  useEffect(() => {
    if (!open || !closeOnEsc) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, closeOnEsc, onClose]);

  if (!open) return null;

  function handleBackdropClick(e: React.MouseEvent) {
    if (closeOnBackdrop && e.target === backdropRef.current) {
      onClose();
    }
  }

  return (
    <div
      className="modal-backdrop"
      ref={backdropRef}
      onClick={handleBackdropClick}
    >
      <div className={`modal-card ${WIDTH_CLASSES[width] || ""}`}>
        {!hideHeader && (
          <div className="modal-header">
            {title && <div className="modal-title">{title}</div>}
            <button className="modal-close" onClick={onClose} aria-label="Close">
              <svg
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <line x1="4" y1="4" x2="12" y2="12" />
                <line x1="12" y1="4" x2="4" y2="12" />
              </svg>
            </button>
          </div>
        )}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}
