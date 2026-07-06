import { useState, useEffect, useRef } from "react";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  className?: string;
}

export function Select({ value, onChange, options, placeholder, className = "" }: SelectProps) {
  const selected = options.find(o => o.value === value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Click outside to close
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} className={`custom-select${open ? " open" : ""} ${className}`} onClick={() => setOpen(!open)}>
      <div className="custom-select-value">
        {selected ? selected.label : (placeholder || "Select...")}
      </div>
      <svg className="custom-select-arrow" width="12" height="12" viewBox="0 0 12 12" fill="none">
        <path d="M6 8L1 3h10z" fill="currentColor" />
      </svg>
      {open && (
        <div className="custom-select-dropdown">
          {options.map(opt => (
            <div
              key={opt.value}
              className={`custom-select-option${opt.value === value ? " selected" : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                onChange(opt.value);
                setOpen(false);
              }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
