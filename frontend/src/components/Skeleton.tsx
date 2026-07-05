interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  className?: string;
}

export function Skeleton({ width, height, className }: SkeletonProps) {
  const style: React.CSSProperties = {};
  if (width !== undefined) style.width = typeof width === "number" ? `${width}px` : width;
  if (height !== undefined) style.height = typeof height === "number" ? `${height}px` : height;

  return (
    <div
      className={`skeleton ${className ?? ""}`}
      style={style}
    />
  );
}

interface SkeletonTextProps {
  lines?: number;
  gap?: number;
}

export function SkeletonText({ lines = 3, gap = 8 }: SkeletonTextProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: `${gap}px` }}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton
          key={i}
          className="skeleton-line"
          width={i === lines - 1 ? "60%" : "100%"}
          height="14px"
        />
      ))}
    </div>
  );
}

interface SkeletonTableProps {
  rows?: number;
  cols?: number;
}

export function SkeletonTable({ rows = 5, cols = 4 }: SkeletonTableProps) {
  return (
    <div className="skeleton-table">
      {/* Header */}
      <div className="skeleton-table-header">
        {Array.from({ length: cols }, (_, i) => (
          <Skeleton key={i} height="14px" width="80px" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="skeleton-table-row">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} height="14px" />
          ))}
        </div>
      ))}
    </div>
  );
}
