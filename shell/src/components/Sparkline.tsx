interface Props {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
  strokeWidth?: number;
  // "auto" picks green/red based on first vs last value
  tone?: "auto" | "neutral";
  /**
   * Optional reference value rendered as a dashed horizontal line.
   * Used on Dashboard cards to draw the previous close so the sparkline
   * reads as "above/below prev close" at a glance. When outside the
   * [min, max] range we widen the range to keep the line visible.
   */
  referenceValue?: number | null;
}

export function Sparkline({
  values,
  width = 120,
  height = 32,
  className,
  strokeWidth = 1.5,
  tone = "auto",
  referenceValue = null,
}: Props) {
  if (values.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        className={className}
        aria-hidden="true"
      />
    );
  }

  let min = Math.min(...values);
  let max = Math.max(...values);
  const showRef =
    referenceValue !== null &&
    referenceValue !== undefined &&
    Number.isFinite(referenceValue);
  if (showRef) {
    if (referenceValue! < min) min = referenceValue!;
    if (referenceValue! > max) max = referenceValue!;
  }
  const range = max - min || 1;
  const stepX = width / (values.length - 1);

  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const up = values[values.length - 1] >= values[0];
  const stroke =
    tone === "neutral"
      ? "currentColor"
      : up
        ? "rgb(16 185 129)" // emerald-500
        : "rgb(244 63 94)"; // rose-500

  const refY = showRef
    ? height - ((referenceValue! - min) / range) * height
    : null;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
    >
      {refY !== null && (
        <line
          x1={0}
          x2={width}
          y1={refY}
          y2={refY}
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray="2 2"
          className="text-zinc-300 dark:text-zinc-600"
          opacity={0.9}
        />
      )}
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}
