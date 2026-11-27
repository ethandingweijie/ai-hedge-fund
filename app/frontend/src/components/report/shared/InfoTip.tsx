/**
 * InfoTip — a small "?" beside a term that opens one plain sentence.
 *
 * Click/tap (not hover), so it works on phones; the text comes from the
 * shared glossary (lib/glossary.ts) unless the caller passes its own.
 * Monochrome by design-system rule.
 */
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { glossaryFor } from '@/lib/glossary';
import { useI18n } from '@/i18n';

export function InfoTip({ term, text, className = '' }: {
  /** Glossary label ("Market cap", "P/E (TTM)"). */
  term?: string;
  /** Explicit text (already translated or an i18n key); wins over the glossary. */
  text?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const parts = text ? [text] : term ? glossaryFor(term) : null;
  if (!parts || parts.length === 0) return null;
  const body = parts.map((p) => t(p)).join(' ');
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={term ? t('What is {term}?', { term: t(term) }) : t('Explain')}
          // tap-compact: inline in a stat label; the global 48px mobile floor would break the row.
          className={`tap-compact inline-flex items-center justify-center h-[15px] w-[15px] rounded-full border border-border text-[10px] font-semibold leading-none text-muted-foreground hover:text-foreground hover:border-foreground/50 align-middle shrink-0 ${className}`}
          onClick={(e) => e.stopPropagation()}
        >
          ?
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-64 p-3 text-[13px] leading-relaxed text-foreground">
        {term && <p className="font-semibold mb-1">{t(term)}</p>}
        <p className="text-foreground/85">{body}</p>
      </PopoverContent>
    </Popover>
  );
}

/** A label followed by its "?" tip, for stat grids. */
export function TermLabel({ label, className = '' }: { label: string; className?: string }) {
  const { t } = useI18n();
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      {t(label)}
      <InfoTip term={label} />
    </span>
  );
}
