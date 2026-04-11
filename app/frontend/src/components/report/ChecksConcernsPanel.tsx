/**
 * ChecksConcernsPanel
 * ─────────────────────────────────────────────────────────────────────────────
 * Reusable two-column bullet layout used by any analysis panel that produces
 * a set of positive evidence points (✓) and a set of concerns (?).
 *
 * Usage:
 *   <ChecksConcernsPanel
 *     checks={[{ label: 'Scale Economies', text: 'CMR margins 75–85%…' }]}
 *     concerns={[{ label: 'Scale Economies', text: 'Rival compression risk…' }]}
 *   />
 *
 * - Pass `text: NIL_TEXT` (or empty string) to render a dimmed "NIL" placeholder.
 * - Column headers default to "What Checks Off" / "Concerns" but can be
 *   overridden via `checkHeader` / `concernHeader`.
 */

export const NIL_TEXT = 'NIL' as const;

export interface BulletItem {
  /** Bold label shown before the em-dash */
  label: string;
  /** Evidence or concern text. Pass NIL_TEXT or '' to show NIL placeholder. */
  text: string;
}

interface ChecksConcernsPanelProps {
  checks: BulletItem[];
  concerns: BulletItem[];
  checkHeader?: string;
  concernHeader?: string;
  /** Extra Tailwind classes applied to the outer wrapper div */
  className?: string;
}

function BulletList({
  items,
  icon,
  iconClass,
  header,
  headerClass,
}: {
  items: BulletItem[];
  icon: string;
  iconClass: string;
  header: string;
  headerClass: string;
}) {
  return (
    <div>
      <p className={`text-[10px] font-bold uppercase tracking-widest mb-2 ${headerClass}`}>
        {header}
      </p>
      <ul className="space-y-2">
        {items.map((item, i) => {
          const isEmpty = !item.text || item.text === NIL_TEXT;
          return (
            <li key={`${item.label}-${i}`} className="flex gap-2 text-xs leading-relaxed">
              <span className={`shrink-0 font-bold mt-0.5 ${iconClass}`}>{icon}</span>
              <span className="text-foreground/80">
                <span className="font-semibold text-foreground">{item.label}</span>
                {' — '}
                {isEmpty
                  ? <span className="text-muted-foreground/50 italic">NIL</span>
                  : item.text
                }
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ChecksConcernsPanel({
  checks,
  concerns,
  checkHeader   = '😊 What Checks Off',
  concernHeader = '😐 Concerns',
  className = '',
}: ChecksConcernsPanelProps) {
  return (
    <div className={`grid grid-cols-2 gap-x-4 border-t pt-3 ${className}`}>
      <BulletList
        items={checks}
        icon="✓"
        iconClass="text-green-500"
        header={checkHeader}
        headerClass="text-green-600 dark:text-green-400"
      />
      <BulletList
        items={concerns}
        icon="?"
        iconClass="text-yellow-500"
        header={concernHeader}
        headerClass="text-amber-500 dark:text-amber-400"
      />
    </div>
  );
}
