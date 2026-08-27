/**
 * TemplateCard.tsx
 * ==================
 * A single "Browse Strategies" template card — icon, risk-level pill, title,
 * description, tag chips, and a Stocks/Bonds/Horizon stat footer. Risk pill
 * color is the primary visual cue for how aggressive a strategy is: red for
 * aggressive, green for moderate, yellow/amber for conservative (NOT green —
 * conservative and moderate must read as visually distinct at a glance).
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

const RISK_PILL: Record<StrategyTemplate['riskLevel'], string> = {
  conservative: 'bg-amber-400/20 text-amber-700 dark:text-amber-300',
  moderate: 'bg-brand text-white',
  aggressive: 'bg-surface-2 text-content-high',
};

export function TemplateCard({ template, onSelect }: { template: StrategyTemplate; onSelect: () => void }) {
  const Icon = ICONS[template.icon] ?? BarChart3;
  const { stocks, bonds } = template.assetAllocation;

  return (
    <button
      type="button"
      onClick={onSelect}
      className="text-left rounded-xl border border-border bg-card p-6 hover:border-brand/40 hover:shadow-sm transition-all flex flex-col gap-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="w-12 h-12 rounded-xl bg-primary/15 flex items-center justify-center shrink-0">
          <Icon size={22} className="text-primary" />
        </div>
        <span className={`text-[12px] font-semibold px-3 py-1.5 rounded-full shrink-0 ${RISK_PILL[template.riskLevel]}`}>
          {RISK_LABEL[template.riskLevel]}
        </span>
      </div>

      <div>
        <div className="text-[20px] font-bold text-foreground">{template.name}</div>
        <p className="text-[13.5px] text-muted-foreground mt-1.5 leading-relaxed line-clamp-2">
          {template.description}
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {template.tags.map((tag) => (
          <span key={tag} className="text-[11px] px-2.5 py-1 rounded-full border border-border text-muted-foreground">
            {tag}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-2 bg-muted/60 rounded-lg py-3 mt-auto">
        <div className="text-center">
          <div className="text-[16px] font-bold font-mono text-foreground">{stocks}%</div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mt-0.5">Stocks</div>
        </div>
        <div className="text-center">
          <div className="text-[16px] font-bold font-mono text-foreground">{bonds}%</div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mt-0.5">Bonds</div>
        </div>
        <div className="text-center">
          <div className="text-[14px] font-semibold text-foreground">{template.timeHorizon}</div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mt-0.5">Horizon</div>
        </div>
      </div>
    </button>
  );
}
