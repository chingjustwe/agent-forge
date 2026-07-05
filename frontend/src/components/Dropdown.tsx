import { useState, useRef, useEffect, ReactNode } from "react";

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
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
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
    <div className="dropdown" ref={containerRef}>
      <button
        className="dropdown-trigger"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {trigger ?? DEFAULT_TRIGGER}
      </button>
      {open && (
        <div className={`dropdown-menu ${align === "left" ? "dropdown-menu-left" : ""}`}>
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
        </div>
      )}
    </div>
  );
}
