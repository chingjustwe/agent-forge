import { useState, useRef, useEffect, ReactNode, useCallback } from "react";
import { createPortal } from "react-dom";

interface DropdownItem {
  label: string;
  onClick: () => void;
  variant?: "default" | "danger";
  icon?: ReactNode;
  disabled?: boolean;
}

interface DropdownProps {
  trigger?: ReactNode;
  items: DropdownItem[];
  align?: "left" | "right";
}

const DEFAULT_TRIGGER = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
    <circle cx="8" cy="3" r="1.5" />
    <circle cx="8" cy="8" r="1.5" />
    <circle cx="8" cy="13" r="1.5" />
  </svg>
);

export function Dropdown({ trigger, items, align = "right" }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement>(null);

  const updatePosition = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const style: React.CSSProperties = {
      position: "fixed",
      top: rect.bottom + 4,
      zIndex: 2000,
    };
    if (align === "right") {
      style.right = window.innerWidth - rect.right;
    } else {
      style.left = rect.left;
    }
    setMenuStyle(style);
  }, [align]);

  useEffect(() => {
    if (open) {
      updatePosition();
      window.addEventListener("scroll", updatePosition, true);
      window.addEventListener("resize", updatePosition);
    }
    return () => {
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [open, updatePosition]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        // Don't close if clicking inside the portal menu
        const menuEl = document.getElementById("dropdown-portal-menu");
        if (menuEl && menuEl.contains(e.target as Node)) return;
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  function handleItemClick(item: DropdownItem) {
    setOpen(false);
    item.onClick();
  }

  return (
    <>
      <button
        ref={triggerRef}
        className="dropdown-trigger"
        onClick={() => { updatePosition(); setOpen((prev) => !prev); }}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {trigger ?? DEFAULT_TRIGGER}
      </button>
      {open &&
        createPortal(
          <div id="dropdown-portal-menu" className={`dropdown-menu ${align === "left" ? "dropdown-menu-left" : ""}`} style={menuStyle}>
            {items.map((item, idx) => (
              <button
                key={idx}
                className={`dropdown-item ${item.variant === "danger" ? "dropdown-item-danger" : ""} ${item.disabled ? "dropdown-item-disabled" : ""}`}
                onClick={() => !item.disabled && handleItemClick(item)}
                disabled={item.disabled}
              >
                {item.icon && <span className="dropdown-item-icon">{item.icon}</span>}
                {item.label}
              </button>
            ))}
          </div>,
          document.body
        )}
    </>
  );
}
