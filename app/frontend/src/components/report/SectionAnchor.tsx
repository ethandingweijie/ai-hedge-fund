/**
 * Report section heading (Valuation, Decision, Risk, ...).
 *
 * Shared by both desktop report pages (ReportViewPage, ReportPage). Sentence
 * case, a real heading size and a hairline underneath -- the previous
 * uppercase, letter-spaced "── VALUATION ──" divider read as chrome rather
 * than as a heading. `badge` carries the live page's section-complete tick.
 */
export function SectionAnchor({ id, label, badge }: { id: string; label: string; badge?: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-20 pt-14">
      <h2 className="flex items-center gap-2 pb-3 mb-6 border-b border-border text-[22px] font-semibold tracking-tight text-foreground">
        {label}
        {badge}
      </h2>
    </div>
  );
}

/** Small sentence-case section title used inside the report hero. */
export function HeroSectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 pb-3 mb-5 border-b border-border">
      <h2 className="text-[22px] font-semibold tracking-tight text-foreground">{children}</h2>
      {right}
    </div>
  );
}
