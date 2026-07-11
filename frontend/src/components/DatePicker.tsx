import { useState, useEffect, useRef, useMemo } from "react";

interface DatePickerProps {
  value: string; // ISO date: "2026-07-11"
  onChange: (value: string) => void;
  placeholder?: string;
  max?: string; // ISO date, restrict selection
  min?: string;
}

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function toISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseISO(s: string): Date | null {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

export function DatePicker({ value, onChange, placeholder = "Select date", max, min }: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const [viewDate, setViewDate] = useState(() => {
    const d = parseISO(value);
    return d || new Date();
  });
  const ref = useRef<HTMLDivElement>(null);

  // Sync viewDate when value changes externally
  useEffect(() => {
    const d = parseISO(value);
    if (d) setViewDate(d);
  }, [value]);

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

  const selected = parseISO(value);
  const today = new Date();
  const todayISO = toISO(today);

  const days = useMemo(() => {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const startWeekday = firstDay.getDay();
    const totalDays = lastDay.getDate();

    const cells: (Date | null)[] = [];
    // Leading blanks
    for (let i = 0; i < startWeekday; i++) cells.push(null);
    for (let d = 1; d <= totalDays; d++) cells.push(new Date(year, month, d));
    // Trailing blanks to fill the last row
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }, [viewDate]);

  const prevMonth = () => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1));
  const nextMonth = () => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1));

  const isDisabled = (d: Date): boolean => {
    const iso = toISO(d);
    if (min && iso < min) return true;
    if (max && iso > max) return true;
    return false;
  };

  return (
    <div ref={ref} className="date-picker">
      <div className="date-picker-trigger" onClick={() => setOpen(!open)}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ flexShrink: 0, color: "var(--text-muted)" }}>
          <rect x="1.5" y="3" width="11" height="9.5" rx="1.5" stroke="currentColor" strokeWidth="1.2" fill="none" />
          <path d="M1.5 6h11" stroke="currentColor" strokeWidth="1.2" />
          <path d="M4 1.5v3M10 1.5v3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
        <span className={value ? "" : "date-picker-placeholder"}>
          {value || placeholder}
        </span>
      </div>
      {open && (
        <div className="date-picker-dropdown">
          <div className="date-picker-header">
            <button className="date-picker-nav" onClick={prevMonth} type="button">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M8 2L3 6l5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
              </svg>
            </button>
            <span className="date-picker-title">
              {MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}
            </span>
            <button className="date-picker-nav" onClick={nextMonth} type="button">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M4 2l5 4-5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
              </svg>
            </button>
          </div>
          <div className="date-picker-weekdays">
            {WEEKDAYS.map(wd => (
              <div key={wd} className="date-picker-weekday">{wd}</div>
            ))}
          </div>
          <div className="date-picker-grid">
            {days.map((d, i) => {
              if (!d) return <div key={i} className="date-picker-cell empty" />;
              const iso = toISO(d);
              const isSelected = selected && toISO(selected) === iso;
              const isToday = iso === todayISO;
              const disabled = isDisabled(d);
              return (
                <button
                  key={i}
                  type="button"
                  className={`date-picker-cell${isSelected ? " selected" : ""}${isToday ? " today" : ""}${disabled ? " disabled" : ""}`}
                  disabled={disabled}
                  onClick={() => {
                    onChange(iso);
                    setOpen(false);
                  }}
                >
                  {d.getDate()}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
