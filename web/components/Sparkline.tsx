// Chat score vs. baseline and trigger threshold over the last few minutes.

interface Props {
  history: [number, number, number][]; // [time, score, baseline]
  ratio: number;                        // ratio_threshold: trigger line = baseline * ratio (approx.)
  height?: number;
}

export default function Sparkline({ history, ratio, height = 64 }: Props) {
  const W = 300;
  const H = height;
  if (history.length < 2) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Collecting chat data">
        <text x={W / 2} y={H / 2 + 4} textAnchor="middle" fontSize="11" fill="var(--faint)">collecting chat…</text>
      </svg>
    );
  }
  const t0 = history[0][0];
  const t1 = history[history.length - 1][0];
  const span = Math.max(1, t1 - t0);
  const maxY = Math.max(...history.map(([, s, b]) => Math.max(s, b * ratio))) * 1.1 || 1;
  const x = (t: number) => ((t - t0) / span) * W;
  const y = (v: number) => H - 2 - (v / maxY) * (H - 6);
  const path = (pick: (p: [number, number, number]) => number) =>
    history.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(pick(p)).toFixed(1)}`).join("");
  const area = `${path((p) => p[1])}L${W},${H}L0,${H}Z`;
  const last = history[history.length - 1];
  const hot = last[1] >= last[2] * ratio;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img"
         aria-label={`Chat score ${last[1].toFixed(0)}, trigger at about ${(last[2] * ratio).toFixed(0)}`}>
      <path d={area} fill={hot ? "var(--live-soft)" : "var(--accent-soft)"} />
      <path d={path((p) => p[2] * ratio)} fill="none" stroke="var(--live)" strokeWidth="1.2"
            strokeDasharray="4 3" vectorEffect="non-scaling-stroke" opacity="0.8" />
      <path d={path((p) => p[2])} fill="none" stroke="var(--faint)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <path d={path((p) => p[1])} fill="none" stroke={hot ? "var(--live)" : "var(--accent)"} strokeWidth="1.8"
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}
