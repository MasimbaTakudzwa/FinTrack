interface Props {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
  strokeWidth?: number;
  // "auto" picks green/red based on first vs last value
  tone?: "auto" | "neutral";
}

export function Sparkline({
  values,
  width = 120,
  height = 32,
  className,
  strokeWidth = 1.5,
  tone = "auto",
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

  const min = Math.min(...values);
  const max = Math.max(...values);
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

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
    >
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
