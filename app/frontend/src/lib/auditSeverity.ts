/**
 * auditSeverity — single source of truth for Card QA audit severity.
 *
 * Three render paths (ReportPage, V2ReportView, ReportViewPage) share this
 * utility so a future severity-rule change propagates everywhere without
 * any of them drifting on color/iconography conventions.
 *
 * Mirrors the persistence schema in src/agents/audit/card_qa_agent.py.
 */
import type { DdCardAudit } from './reportTypes';

export type AuditSeverity = 'critical' | 'warning' | 'info' | 'ok';

export interface AuditSeverityVisuals {
  /** Tailwind background gradient class for the banner. */
  bgClass: string;
  /** Tailwind border class. */
  borderClass: string;
  /** Tailwind text-color class for the headline. */
  textClass: string;
  /** Tailwind text-color class for the icon glyph. */
  iconClass: string;
  /** Short uppercase label shown in the chip. */
  label: string;
}

export const SEVERITY_VISUALS: Record<AuditSeverity, AuditSeverityVisuals> = {
  critical: {
    bgClass:     'bg-red-50 dark:bg-red-950/30',
    borderClass: 'border-red-300 dark:border-red-800',
    textClass:   'text-red-900 dark:text-red-200',
    iconClass:   'text-red-600 dark:text-red-400',
    label:       'CRITICAL',
  },
  warning: {
    bgClass:     'bg-yellow-50 dark:bg-yellow-950/30',
    borderClass: 'border-yellow-300 dark:border-yellow-800',
    textClass:   'text-yellow-900 dark:text-yellow-200',
    iconClass:   'text-yellow-600 dark:text-yellow-400',
    label:       'WARNING',
  },
  info: {
    bgClass:     'bg-blue-50 dark:bg-blue-950/30',
    borderClass: 'border-blue-300 dark:border-blue-800',
    textClass:   'text-blue-900 dark:text-blue-200',
    iconClass:   'text-blue-600 dark:text-blue-400',
    label:       'INFO',
  },
  ok: {
    bgClass:     '',
    borderClass: '',
    textClass:   '',
    iconClass:   '',
    label:       'OK',
  },
};

/**
 * Compute the severity of a single ticker's QA audit dict.
 *
 *   - critical : meta_check.passed === false (classification is wrong;
 *                downstream cards are noise — DON'T trust them)
 *   - warning  : at least one card has missing_mandatory entries OR at
 *                least one human_review_flag is present
 *   - info     : qa_budget_hit === true and otherwise clean
 *   - ok       : everything passed
 *
 * If the audit is null/undefined (no QA ran on this run), returns 'ok'.
 */
export function auditSeverity(audit: DdCardAudit | null | undefined): AuditSeverity {
  if (!audit) return 'ok';

  // 1. Meta-Check failure dominates — everything downstream is suspect.
  if (audit.meta_check && audit.meta_check.passed === false) {
    return 'critical';
  }

  // 2. Any card with missing mandatory OR any flag = warning
  const hasMissingMandatory = (audit.cards_inspected || []).some(
    (c) => c.missing_mandatory && c.missing_mandatory.length > 0,
  );
  const hasFlags = (audit.human_review_flags || []).length > 0;
  if (hasMissingMandatory || hasFlags) {
    return 'warning';
  }

  // 3. Budget hit but otherwise clean — informational
  if (audit.qa_budget_hit) {
    return 'info';
  }

  return 'ok';
}

/**
 * Compute the WORST severity across an entire run (multi-ticker analyses).
 * The banner needs a single severity to render — for multi-ticker pages
 * we surface the most severe of any ticker.
 */
export function worstSeverity(
  audits: Record<string, DdCardAudit> | null | undefined,
): AuditSeverity {
  if (!audits) return 'ok';
  const order: AuditSeverity[] = ['critical', 'warning', 'info', 'ok'];
  let worstIdx = order.length - 1;
  for (const audit of Object.values(audits)) {
    const sev = auditSeverity(audit);
    const idx = order.indexOf(sev);
    if (idx >= 0 && idx < worstIdx) {
      worstIdx = idx;
    }
  }
  return order[worstIdx];
}

/**
 * Headline message + sub-message for the banner. Single source of truth
 * so all 3 render paths show identical text.
 */
export function severityMessage(
  audit: DdCardAudit | null | undefined,
): { headline: string; detail: string } {
  if (!audit) return { headline: '', detail: '' };

  const sev = auditSeverity(audit);

  if (sev === 'critical') {
    const suggested = audit.meta_check?.suggested_profile;
    return {
      headline: 'Profile classification may be wrong',
      detail: suggested
        ? `Suggested correct profile: ${suggested}. Downstream KPI cards in this report may show incorrect data until reclassification.`
        : 'Downstream KPI cards in this report may show incorrect data.',
    };
  }

  if (sev === 'warning') {
    const total = audit.cards_inspected.length;
    const flagged = audit.cards_inspected.filter(
      (c) => c.missing_mandatory && c.missing_mandatory.length > 0,
    ).length;
    const remediated = audit.auto_remediations.length;
    const needsReview = audit.human_review_flags.filter(
      (f) => f.reason !== 'genuinely_absent_per_judge',
    ).length;
    return {
      headline: `${flagged} of ${total} card(s) had data gaps`,
      detail: `${remediated} auto-resolved by the QA agent; ${needsReview} need human review.`,
    };
  }

  if (sev === 'info') {
    return {
      headline: 'QA budget cap reached on this run',
      detail: 'Some cards were not audited because the per-run $0.50 cap was hit. The data shown is from the original extractors (not QA-validated).',
    };
  }

  return { headline: '', detail: '' };
}
