/**
 * v2/shared.tsx — Shared UI kit for the reimagined Equitable UI
 *
 * Minimal-fintech Linear/Stripe aesthetic. Zinc-neutral palette, 1px borders,
 * Inter with tabular numerics. Equitable green (#2e7d32) reserved for logo,
 * primary CTAs, positive deltas, ongoing-run state, deep-research accents.
 *
 * Exports: Icons, Leaf, Divider, ActionPill, GradeChip, Delta, Card
 */

import React, { useRef, useState } from 'react';

export const BRAND = '#2e7d32';

/* ───────── Icons ───────── */
const I = (p: React.SVGProps<SVGSVGElement>) => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p} />
);
export const Menu       = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></I>;
export const X          = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></I>;
export const Search     = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></I>;
export const Filter     = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></I>;
export const ArrowLeft  = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></I>;
export const ArrowUp    = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></I>;
export const ArrowDown  = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></I>;
export const Clock      = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></I>;
export const Sun        = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></I>;
export const Moon       = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></I>;
export const Monitor    = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></I>;
export const Zap        = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></I>;
export const LogOut     = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></I>;
export const ChevRight  = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polyline points="9 18 15 12 9 6"/></I>;
export const Check      = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polyline points="20 6 9 17 4 12"/></I>;
export const Sparkles   = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M12 2l1.9 5.7L19.6 9l-5.7 1.9L12 16l-1.9-5.7L4.4 9l5.7-1.3L12 2zM19 14l.7 2.1L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.9L19 14zM5 14l.7 2.1L8 17l-2.3.7L5 20l-.7-2.3L2 17l2.3-.9L5 14z"/></I>;
export const Scales     = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M6 3v18M18 3v18"/><path d="M3 9l3-6 3 6H3zM15 9l3-6 3 6h-6z"/><path d="M6 21h12"/></I>;
export const Shield     = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></I>;
export const Book       = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></I>;
export const ChevronDn  = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polyline points="6 9 12 15 18 9"/></I>;
export const Brain      = (p: React.SVGProps<SVGSVGElement>) => (
  <I {...p}>
    <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15A2.5 2.5 0 0 1 7 19.5a2.5 2.5 0 0 1-3-3 2.5 2.5 0 0 1-1-4.5 2.5 2.5 0 0 1 1-4.5 2.5 2.5 0 0 1 3-3A2.5 2.5 0 0 1 9.5 2z"/>
    <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 5 0 2.5 2.5 0 0 0 3-3 2.5 2.5 0 0 0 1-4.5 2.5 2.5 0 0 0-1-4.5 2.5 2.5 0 0 0-3-3A2.5 2.5 0 0 0 14.5 2z"/>
  </I>
);
export const Users      = (p: React.SVGProps<SVGSVGElement>) => (
  <I {...p}>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </I>
);
export const Star       = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></I>;
export const Plus       = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I>;
export const Bookmark   = (p: React.SVGProps<SVGSVGElement>) => <I {...p}><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></I>;

/* ───────── Brand ───────── */
export function Leaf({ size = 22 }: { size?: number }) {
  return (
    <div
      className="rounded-[6px] flex items-center justify-center text-white font-extrabold"
      style={{
        backgroundColor: BRAND,
        width: size,
        height: size,
        fontSize: size * 0.65,
        lineHeight: 1,
        letterSpacing: '-0.04em',
      }}
    >
      e
    </div>
  );
}

/* ───────── Primitives ───────── */
export const Divider = ({ className = '' }: { className?: string }) =>
  <div className={`h-px bg-zinc-100 dark:bg-zinc-800 ${className}`} />;

export const ACTION_STYLES: Record<string, string> = {
  BUY:   'text-[#2e7d32] dark:text-[#4ea354] bg-[#ecf5ed] dark:bg-[#2e7d32]/15 border-[#d0e7d2] dark:border-[#2e7d32]/40',
  SELL:  'text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-500/10 border-rose-100 dark:border-rose-500/20',
  SHORT: 'text-orange-700 dark:text-orange-400 bg-orange-50 dark:bg-orange-500/10 border-orange-100 dark:border-orange-500/20',
  HOLD:  'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 border-amber-100 dark:border-amber-500/20',
};

export function ActionPill({ action, size = 'sm' }: { action?: string | null; size?: 'sm' | 'lg' }) {
  const base = (action && ACTION_STYLES[action]) || 'text-zinc-700 dark:text-zinc-300 bg-zinc-50 dark:bg-zinc-800/60 border-zinc-200 dark:border-zinc-800';
  const sz = size === 'lg' ? 'text-[11px] px-2.5 py-1' : 'text-[10px] px-1.5 py-0.5';
  return <span className={`inline-flex items-center rounded-md border font-semibold tracking-wide ${sz} ${base}`}>{action || '—'}</span>;
}

function gradeStyle(grade?: string | null) {
  if (!grade) return { text: 'text-zinc-400 dark:text-zinc-500', bg: 'bg-zinc-50 dark:bg-zinc-800/60' };
  const L = grade[0];
  const mod = grade.slice(1);
  if (L === 'A') {
    return {
      text: 'text-[#1b5e20] dark:text-[#9fd6a4]',
      bg: mod === '+' ? 'bg-[#2e7d32]/30 dark:bg-[#2e7d32]/40'
        : mod === '-' ? 'bg-[#2e7d32]/10 dark:bg-[#2e7d32]/20'
        :               'bg-[#2e7d32]/20 dark:bg-[#2e7d32]/30',
    };
  }
  if (L === 'B') {
    return {
      text: 'text-blue-700 dark:text-blue-300',
      bg: mod === '+' ? 'bg-blue-500/25 dark:bg-blue-500/30'
        : mod === '-' ? 'bg-blue-500/10 dark:bg-blue-500/15'
        :               'bg-blue-500/15 dark:bg-blue-500/20',
    };
  }
  if (L === 'C') {
    return {
      text: 'text-amber-700 dark:text-amber-300',
      bg: mod === '+' ? 'bg-amber-500/25 dark:bg-amber-500/30'
        : mod === '-' ? 'bg-amber-500/10 dark:bg-amber-500/15'
        :               'bg-amber-500/20 dark:bg-amber-500/25',
    };
  }
  return {
    text: 'text-rose-700 dark:text-rose-300',
    bg:   'bg-rose-500/20 dark:bg-rose-500/25',
  };
}

export function GradeChip({ grade, label }: { grade?: string | null; label?: string }) {
  const s = gradeStyle(grade);
  return (
    <div className="flex flex-col items-center gap-1 min-w-[28px]">
      {label && <span className="text-[9px] font-medium uppercase tracking-[0.08em] text-zinc-400 dark:text-zinc-500">{label}</span>}
      <span className={`inline-flex items-center justify-center min-w-[22px] h-[20px] px-1.5 rounded-md text-[11.5px] font-bold tabular-nums ${s.text} ${s.bg}`}>
        {grade || '—'}
      </span>
    </div>
  );
}

export function Delta({ v, unit = '%' }: { v: number | null | undefined; unit?: string }) {
  if (v == null) return <span className="text-zinc-400 dark:text-zinc-500">—</span>;
  const up = v >= 0;
  return (
    <span className={`inline-flex items-center gap-0.5 font-medium tabular-nums ${up ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
      {up ? <ArrowUp width={11} height={11} strokeWidth={2.2}/> : <ArrowDown width={11} height={11} strokeWidth={2.2}/>}
      {Math.abs(v).toFixed(1)}{unit}
    </span>
  );
}

export function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 ${className}`}>
      {children}
    </div>
  );
}

/* ───────── TopBar (hamburger only) ───────── */
export function TopBar({ onMenu }: { onMenu: () => void }) {
  return (
    <div className="sticky top-0 z-30 h-12 px-3 flex items-center bg-white/85 dark:bg-zinc-900/85 backdrop-blur border-b border-zinc-100 dark:border-zinc-800">
      <button
        onClick={onMenu}
        aria-label="Open menu"
        className="w-9 h-9 -ml-1 rounded-lg active:bg-zinc-100 dark:active:bg-zinc-800 flex items-center justify-center text-zinc-700 dark:text-zinc-300"
      >
        <Menu />
      </button>
    </div>
  );
}

/* ───────── SwipeRow (touch + mouse drag) ───────── */
export function SwipeRow({
  onClick,
  actions = [],
  children,
  className = '',
}: {
  onClick?: () => void;
  actions?: { icon: React.ReactNode; color: string; onClick?: () => void; label?: string }[];
  children: React.ReactNode;
  className?: string;
}) {
  const [dx, setDx] = useState(0);
  const [open, setOpen] = useState(false);
  const start = useRef<number | null>(null);
  const base = useRef(0);
  const moved = useRef(false);
  const downTarget = useRef<Element | null>(null);
  const actionsWidth = actions.length * 64;

  const onPointerDown = (e: React.PointerEvent) => {
    start.current = e.clientX;
    base.current = open ? -actionsWidth : 0;
    moved.current = false;
    downTarget.current = e.target as Element;
    try { (e.target as Element).setPointerCapture?.(e.pointerId); } catch { /* ignore */ }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (start.current == null) return;
    const delta = e.clientX - start.current;
    if (Math.abs(delta) > 4) moved.current = true;
    setDx(Math.max(-actionsWidth - 20, Math.min(0, base.current + delta)));
  };
  const onPointerEnd = () => {
    if (start.current == null) return;
    const wasMove = moved.current;
    start.current = null;
    const shouldOpen = Math.abs(dx) > actionsWidth * 0.4;
    setOpen(shouldOpen);
    setDx(shouldOpen ? -actionsWidth : 0);
    // Only fire onClick if the tap landed inside a data-tap="open" element.
    // This lets consumers restrict the click-through target (e.g. only the
    // ticker column, not the price / VGPM cells) while the swipe gesture
    // still works across the full row.
    const target = downTarget.current;
    downTarget.current = null;
    if (wasMove) return;
    if (target && target.closest('[data-tap="open"]')) {
      onClick?.();
    }
  };

  return (
    <div className={`relative overflow-hidden select-none ${className}`}>
      <div className="absolute right-0 top-0 bottom-0 flex">
        {actions.map((a, i) => (
          <button
            key={i}
            onClick={(e) => {
              e.stopPropagation();
              a.onClick?.();
              setDx(0);
              setOpen(false);
            }}
            style={{ backgroundColor: a.color, width: 64 }}
            className="flex flex-col items-center justify-center gap-0.5 text-white active:opacity-85 transition-opacity"
            aria-label={a.label}
          >
            {a.icon}
            {a.label && <span className="text-[9.5px] font-medium tracking-wide">{a.label}</span>}
          </button>
        ))}
      </div>
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        style={{
          transform: `translateX(${dx}px)`,
          transition: start.current == null ? 'transform 0.24s ease' : 'none',
          touchAction: 'pan-y',
        }}
        className="bg-white dark:bg-zinc-900 relative"
      >
        {children}
      </div>
    </div>
  );
}
