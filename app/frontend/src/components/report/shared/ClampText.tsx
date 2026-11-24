/**
 * ClampText — long explanatory text clamped to N lines; the whole paragraph is the
 * tap target (so it clears the 44px rule without a tall button under the text) and
 * a small "More" / "Less" cue appears only when the text actually overflows.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { useI18n } from '@/i18n';

const CLAMP: Record<number, string> = { 1: 'line-clamp-1', 2: 'line-clamp-2', 3: 'line-clamp-3' };

export function ClampText({ text, lines = 2, className = '' }: { text: string; lines?: 1 | 2 | 3; className?: string }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const check = () => setOverflows(el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, open]);

  const interactive = overflows || open;
  const toggle = () => setOpen((o) => !o);

  return (
    <div
      className={`${className} ${interactive ? 'cursor-pointer rounded-sm' : ''}`}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-expanded={interactive ? open : undefined}
      onClick={interactive ? toggle : undefined}
      onKeyDown={interactive ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } } : undefined}
    >
      <div ref={ref} className={open ? '' : CLAMP[lines]}>{text}</div>
      {interactive && (
        <span className="mt-0.5 inline-block text-xs font-medium not-italic text-foreground/80">
          {open ? t('Less') : t('More')}
        </span>
      )}
    </div>
  );
}

export default ClampText;
