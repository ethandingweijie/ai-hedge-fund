/**
 * LandingGreeting — the two-line hero on the New Analysis landing page.
 *
 * Owner, 2026-11-24: keep the user's name, but stop saying the same
 * "Hello, Name / What ticker are we analysing?" every visit.
 *
 *   line 1  a greeting that follows the local time of day ("Good morning",
 *           "Evening", "Burning the midnight oil") or a time-free one
 *           ("Welcome back"), picked once per visit, then ", {name}"
 *   line 2  a prompt that cycles every few seconds with a soft fade
 *
 * The cycling stops while the user is typing (`paused`) so the headline never
 * moves under their eyes, and it does not cycle at all for users who ask for
 * reduced motion. The first prompt is random per visit, so a reload also feels
 * fresh. Lines are plain, short and never claim a result.
 */
import { useEffect, useMemo, useState } from 'react';

const PROMPTS = [
  'What ticker are we analysing?',
  'Which company is on your mind?',
  "What's on your radar today?",
  'Got a hunch? Let’s test it.',
  'Bull or bear? Let’s find out.',
  'Which stock should we take apart?',
  'Who looks mispriced today?',
  'Pick a name. We’ll do the homework.',
  'Which moat are we measuring?',
  'Ready to stress-test a thesis?',
  'What are we valuing next?',
  'Hunting for the next compounder?',
];

const ANYTIME = ['Hello', 'Welcome back', 'Hey there', 'Good to see you'];

function timeGreeting(hour: number): string {
  if (hour < 5) return 'Burning the midnight oil';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

const ROTATE_MS = 4500;
const FADE_MS = 300;

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function LandingGreeting({ name, paused = false }: { name: string; paused?: boolean }) {
  // Picked once per mount: half the time the greeting follows the clock.
  const greeting = useMemo(() => {
    const pool = [timeGreeting(new Date().getHours()), ...ANYTIME];
    return Math.random() < 0.5 ? pool[0] : pool[Math.floor(Math.random() * pool.length)];
  }, []);

  const [idx, setIdx] = useState(() => Math.floor(Math.random() * PROMPTS.length));
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (paused || prefersReducedMotion()) return;
    let fadeTimer: number | undefined;
    const timer = window.setInterval(() => {
      setVisible(false);
      fadeTimer = window.setTimeout(() => {
        setIdx((i) => (i + 1) % PROMPTS.length);
        setVisible(true);
      }, FADE_MS);
    }, ROTATE_MS);
    return () => {
      window.clearInterval(timer);
      if (fadeTimer) window.clearTimeout(fadeTimer);
      setVisible(true);
    };
  }, [paused]);

  return (
    <>
      {/* Same size/weight as the line below: the greeting reads as the first
          line of the headline, not a caption. */}
      <p className="text-[24px] leading-[1.15] font-semibold tracking-tight text-foreground">
        {greeting}, {name}
      </p>
      <h1
        aria-live="off"
        className="mt-1 text-[24px] leading-[1.15] font-semibold tracking-tight text-foreground transition-opacity ease-out"
        style={{ opacity: visible ? 1 : 0, transitionDuration: `${FADE_MS}ms` }}
      >
        {PROMPTS[idx]}
      </h1>
    </>
  );
}
