/**
 * TemplateCard.tsx
 * ==================
 * One "Browse strategies" template, drawn as a list row (owner, 2026-11-25:
 * align Robo with the flat, list-first design language — no boxed cards).
 *
 *   [icon]  Name  [risk pill]                    [stocks|bonds bar]   >
 *           One-line description                  70% stocks · 25% bonds
 *           Tag · Tag · Tag                       7-15 years
 *
 * Risk pill follows the app's pill scale rather than traffic-light colours
 * (green/red stay reserved for price change): Moderate = accent fill,
 * Aggressive = inverted fill, Conservative = outline.
 */
import {
  BarChart3, Banknote, Cpu, Shield, HeartPulse, Leaf, Globe, DollarSign, Bot, Earth, ChevronRight,
  type LucideIcon,
} from 'lucide-react';
import type { StrategyTemplate } from '@/data/roboStrategyTemplates';

const ICONS: Record<string, LucideIcon> = {
  BarChart3, Banknote, Cpu, Shield, HeartPulse, Leaf, Globe, DollarSign, Bot, Earth,
};

const RISK_LABEL: Record<StrategyTemplate['riskLevel'], string> = {
  conservative: 'Conservative',
  moderate: 'Moderate',
  aggressive: 'Aggressive',
};

const RISK_PILL: Record<StrategyTemplate['riskLevel'], string> = {
  conservative: 'border border-border text-foreground',
  moderate: 'bg-brand text-brand-foreground',
  aggressive: 'bg-foreground text-background',
};

function AllocationBar({ stocks, bonds }: { stocks: number; bonds: number }) {
  const other = Math.max(0, 100 - stocks - bonds);
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
      <div className="bg-foreground" style={{ width: `${stocks}%` }} />
      <div className="bg-muted-foreground/50" style={{ width: `${bonds}%` }} />
      {other > 0 && <div className="bg-transparent" style={{ width: `${other}%` }} />}
    </div>
  );
}

export function TemplateCard({ template, onSelect }: { template: StrategyTemplate; onSelect: () => void }) {
  const Icon = ICONS[template.icon] ?? BarChart3;
  const { stocks, bonds } = template.assetAllocation;
  const mix = `${stocks}% stocks · ${bonds}% bonds`;

  return (
    <button
      type="button"
      onClick={onSelect}
      className="group w-full text-left py-5 -mx-3 px-3 md:-mx-4 md:px-4 rounded-xl [@media(hover:hover)]:hover:bg-muted/40 transition-colors flex items-center gap-4"
    >
      <div className="w-11 h-11 rounded-full bg-muted flex items-center justify-center shrink-0 self-start">
        <Icon size={19} className="text-foreground" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[17px] md:text-[19px] font-semibold text-foreground">{template.name}</span>
          <span className={`inline-flex items-center h-6 px-2.5 rounded-full text-xs font-semibold ${RISK_PILL[template.riskLevel]}`}>
            {RISK_LABEL[template.riskLevel]}
          </span>
        </div>
        <p className="mt-1 text-[14px] text-muted-foreground leading-relaxed line-clamp-2 md:line-clamp-1">
          {template.description}
        </p>
        <p className="mt-1 text-[13px] text-muted-foreground">{template.tags.join(' · ')}</p>
        {/* Phone: the allocation sits under the text instead of in a right column. */}
        <div className="mt-3 md:hidden">
          <AllocationBar stocks={stocks} bonds={bonds} />
          <p className="mt-1.5 text-[13px] text-foreground tabular-nums">
            {mix} <span className="text-muted-foreground">· {template.timeHorizon}</span>
          </p>
        </div>
      </div>

      <div className="hidden md:block w-52 shrink-0">
        <AllocationBar stocks={stocks} bonds={bonds} />
        <p className="mt-2 text-[14px] font-medium text-foreground tabular-nums">{mix}</p>
        <p className="text-[13px] text-muted-foreground">{template.timeHorizon}</p>
      </div>

      <ChevronRight size={18} className="shrink-0 text-muted-foreground group-hover:text-foreground" />
    </button>
  );
}
