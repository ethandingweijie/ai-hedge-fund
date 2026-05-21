/**
 * CardAuditBanner
 * ─────────────────────────────────────────────────────────────────────────────
 * Surfaces Card QA Agent (Phase 10.5) findings at the top of a report page.
 *
 * Renders only when severity >= 'warning'. The same component is wired into
 * three render paths (ReportPage / V2ReportView / ReportViewPage) — all
 * share the auditSeverity utility so colors + iconography stay consistent.
 *
 * Click expands to a detail panel showing:
 *   * Meta-Check result (if applicable)
 *   * Per-card findings: which fields were missing, what the judge said,
 *     whether re-extraction succeeded
 *   * Cumulative QA cost for the run
 */
import { useState } from 'react';
import {
  AlertTriangle,
  AlertOctagon,
  Info,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type { DdCardAudit } from '../../lib/reportTypes';
import {
  auditSeverity,
  severityMessage,
  SEVERITY_VISUALS,
  type AuditSeverity,
} from '../../lib/auditSeverity';

interface CardAuditBannerProps {
  audit: DdCardAudit | null | undefined;
  /** Optional ticker shown in the headline (multi-ticker runs). */
  ticker?: string;
  /** Hide the banner entirely if severity is 'ok'. Defaults to true. */
  hideWhenOk?: boolean;
}

function IconForSeverity({ severity }: { severity: AuditSeverity }) {
  const cls = SEVERITY_VISUALS[severity].iconClass + ' h-5 w-5 shrink-0';
  if (severity === 'critical') return <AlertOctagon className={cls} />;
  if (severity === 'warning')  return <AlertTriangle className={cls} />;
  if (severity === 'info')     return <Info className={cls} />;
  return <Info className={cls} />;
}

export function CardAuditBanner({
  audit,
  ticker,
  hideWhenOk = true,
}: CardAuditBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const severity = auditSeverity(audit);

  if (!audit) return null;
  if (severity === 'ok' && hideWhenOk) return null;

  const visuals = SEVERITY_VISUALS[severity];
  const { headline, detail } = severityMessage(audit);

  const tickerSuffix = ticker ? ` · ${ticker}` : '';

  return (
    <div
      className={`mb-4 rounded-lg border ${visuals.bgClass} ${visuals.borderClass}`}
      data-testid={`card-audit-banner-${severity}`}
    >
      {/* Header — clickable to expand */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        aria-expanded={expanded}
      >
        <IconForSeverity severity={severity} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-bold uppercase tracking-widest ${visuals.iconClass}`}>
              {visuals.label}
            </span>
            <span className={`text-[10px] uppercase tracking-wider text-muted-foreground`}>
              Card QA{tickerSuffix}
            </span>
          </div>
          <div className={`text-sm font-medium ${visuals.textClass} truncate`}>
            {headline}
          </div>
          {detail && (
            <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
              {detail}
            </div>
          )}
        </div>
        {expanded
          ? <ChevronUp className={`h-4 w-4 shrink-0 ${visuals.iconClass}`} />
          : <ChevronDown className={`h-4 w-4 shrink-0 ${visuals.iconClass}`} />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-current/10">
          <AuditDetail audit={audit} />
        </div>
      )}
    </div>
  );
}

function AuditDetail({ audit }: { audit: DdCardAudit }) {
  return (
    <div className="space-y-3 text-xs">
      {/* Meta-Check */}
      {audit.meta_check && audit.meta_check.passed === false && (
        <div>
          <div className="font-semibold mb-1">Meta-Check Findings</div>
          <ul className="space-y-1 list-disc list-inside text-muted-foreground">
            {audit.meta_check.issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
            {audit.meta_check.suggested_profile && (
              <li>
                <span className="font-medium">Suggested profile:</span>{' '}
                {audit.meta_check.suggested_profile}
              </li>
            )}
          </ul>
        </div>
      )}

      {/* Cards with issues */}
      {audit.cards_inspected.filter((c) => c.missing_mandatory.length > 0).length > 0 && (
        <div>
          <div className="font-semibold mb-1">Cards with Data Gaps</div>
          <ul className="space-y-1.5">
            {audit.cards_inspected
              .filter((c) => c.missing_mandatory.length > 0)
              .map((card) => (
                <li key={card.card} className="text-muted-foreground">
                  <span className="font-mono font-medium text-foreground">{card.card}</span>
                  {' · '}
                  {card.judge_verdict || 'pending judge'}
                  {card.remediation_success === true && ' · auto-resolved'}
                  {card.remediation_success === false && ' · re-extract NOT_FOUND'}
                  {card.missing_mandatory.length > 0 && (
                    <div className="ml-3 text-[11px] opacity-70">
                      Missing: {card.missing_mandatory.join(', ')}
                    </div>
                  )}
                  {card.judge_reasoning && (
                    <div className="ml-3 text-[11px] italic opacity-70">
                      “{card.judge_reasoning.slice(0, 200)}”
                    </div>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}

      {/* Auto-remediations */}
      {audit.auto_remediations.length > 0 && (
        <div>
          <div className="font-semibold mb-1">
            Auto-Resolved ({audit.auto_remediations.length})
          </div>
          <ul className="space-y-0.5 text-muted-foreground">
            {audit.auto_remediations.map((rem, i) => (
              <li key={i}>
                <span className="font-mono text-foreground">{rem.field}</span>
                {' '}via {rem.method}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Cost + budget */}
      <div className="flex items-center justify-between text-[11px] text-muted-foreground pt-2 border-t border-current/10">
        <span>
          QA cost: ${audit.qa_cost_estimate_usd.toFixed(4)}
          {audit.qa_budget_hit && (
            <span className="ml-2 font-medium text-yellow-600 dark:text-yellow-400">
              · budget cap reached
            </span>
          )}
        </span>
        <span>
          model: {audit.qa_model}{' '}
          · run at {audit.qa_ran_at.slice(0, 19).replace('T', ' ')}
        </span>
      </div>
    </div>
  );
}
