/**
 * TemplateCard.tsx
 * ==================
 * A single "Browse Strategies" template card. Deliberately minimal — one
 * icon, one title, one line of description, one line of plain-text
 * metadata. No badges, no boxed footer, no stacked chrome: those read as
 * clutter at a glance across a 10-card grid, so risk/stocks/bonds/horizon
 * collapse into a single muted text line instead of separate pills/boxes.
 */
import {
  BarChart3, Banknote, Cpu, Shield, HeartPulse, Leaf, Globe, DollarSign, Bot, Earth,
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

export function TemplateCard({ template, onSelect }: { template: StrategyTemplate; onSelect: () => void }) {
  const Icon = ICONS[template.icon] ?? BarChart3;
  const { stocks, bonds } = template.assetAllocation;

  return (
    <button
      type="button"
      onClick={onSelect}
      className="text-left rounded-lg border border-border bg-card p-4 hover:border-brand/40 transition-colors flex flex-col gap-2.5"
    >
      <div className="w-9 h-9 rounded-lg bg-primary/15 flex items-center justify-center shrink-0">
        <Icon size={17} className="text-primary" />
      </div>

      <div className="text-[14px] font-semibold text-foreground">{template.name}</div>

      <p className="text-[12px] text-muted-foreground leading-relaxed line-clamp-1">
        {template.description}
      </p>

      <div className="text-[11px] text-muted-foreground/70">
        {RISK_LABEL[template.riskLevel]} · {stocks}/{bonds} stocks/bonds · {template.timeHorizon}
      </div>
    </button>
  );
}
