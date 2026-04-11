/**
 * AgentOrbIcon — globe-with-robot SVG that mirrors the user's PNG asset.
 * Drop-in replacement until the real PNG is placed at /public/agent-orb.png.
 */
export function AgentOrbIcon({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="currentColor"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Outer circle */}
      <circle cx="50" cy="50" r="48" fill="none" stroke="currentColor" strokeWidth="4" />

      {/* Globe latitude lines */}
      <ellipse cx="50" cy="50" rx="48" ry="20" fill="none" stroke="currentColor" strokeWidth="3" />
      <ellipse cx="50" cy="50" rx="48" ry="38" fill="none" stroke="currentColor" strokeWidth="3" />

      {/* Globe longitude lines */}
      <ellipse cx="50" cy="50" rx="20" ry="48" fill="none" stroke="currentColor" strokeWidth="3" />
      <ellipse cx="50" cy="50" rx="38" ry="48" fill="none" stroke="currentColor" strokeWidth="3" />

      {/* Vertical and horizontal axes */}
      <line x1="50" y1="2" x2="50" y2="98" stroke="currentColor" strokeWidth="3" />
      <line x1="2" y1="50" x2="98" y2="50" stroke="currentColor" strokeWidth="3" />

      {/* Robot head — white cutout on a filled circle */}
      <circle cx="50" cy="72" r="18" fill="currentColor" />
      <rect x="40" y="64" width="20" height="14" rx="3" fill="white" />
      {/* Eyes */}
      <circle cx="45" cy="71" r="2.5" fill="currentColor" />
      <circle cx="55" cy="71" r="2.5" fill="currentColor" />
      {/* Antenna */}
      <line x1="50" y1="64" x2="50" y2="58" stroke="white" strokeWidth="2" strokeLinecap="round" />
      <circle cx="50" cy="56" r="2" fill="white" />
    </svg>
  );
}
