/**
 * EstimateWorkbench — iterate with the valuation agent on its estimates.
 *
 * Owner, 2026-10-03: "the whole thinking process should show on the frontend and for user to
 * query against it, and the change to be reflected on the go including frontend, pdf and excel".
 *
 * Three parts, all against one run:
 *   1. Edit — the estimates the agent built (FY+1/FY+2 growth, margins, EPS per scenario; the
 *      medium-term target; fade, tax, capex intensity, working capital, WACC, terminal growth).
 *      Only fields the user changes are sent.
 *   2. Recompute — POST /estimates/preview runs the SAME engine the pipeline ran (forecast → DCF
 *      leg → blend at the leg's own weight → target bridge → probability-weighted target) and
 *      shows the agent's figures beside the user's. Nothing is saved until "Save".
 *   3. Ask — a question to the agent about its reasoning; it answers from the run's trace and may
 *      propose an override, which the user can load into the form (never applied by itself).
 *
 * A saved override is applied on read by the server, so the page, the PDF and the workbook move
 * together; "Revert to the agent" deletes it and the stored run is untouched.
 * Monochrome by design-system rule: no green/red outside price change.
 */
import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { askEstimateAgent, clearEstimateOverride, previewEstimateOverride, regeneratePmText, saveEstimateOverride } from '@/lib/api';
import type {
  DcfRange, EstimateOverrides, EstimateRecompute, ForecastContext, GuidanceEstimates, GuidanceForecast, GuidanceScenarioRow, RunResult,
} from '@/lib/reportTypes';

const LABEL_CLS = 'text-[14px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70';
const SCENARIOS = ['bear', 'base', 'bull'] as const;
type Scenario = (typeof SCENARIOS)[number];
type ScenarioField = keyof GuidanceScenarioRow;
type SharedField = 'fade_years' | 'tax_rate' | 'capex_alpha' | 'nwc_intensity' | 'terminal_roic' | 'wacc' | 'tgr' | 'gross_margin' | 'sbc_pct' | 'interest_rate' | 'payout_ratio' | 'buyback_annual'
  | 'bank_loan_growth' | 'bank_nim' | 'bank_fee_growth' | 'bank_cost_to_income' | 'bank_credit_cost_bps' | 'bank_cet1_target' | 'ins_premium_growth' | 'ins_combined_ratio' | 'ins_investment_yield';

const SCENARIO_FIELDS: { key: ScenarioField; label: (fy1: string, fy2: string) => string; kind: 'pct' | 'num' }[] = [
  { key: 'revenue_growth_fy1', label: (a) => `Revenue growth ${a}`, kind: 'pct' },
  { key: 'revenue_growth_fy2', label: (_a, b) => `Revenue growth ${b}`, kind: 'pct' },
  { key: 'ebitda_margin_fy1', label: (a) => `Operating margin ${a}`, kind: 'pct' },
  { key: 'ebitda_margin_fy2', label: (_a, b) => `Operating margin ${b}`, kind: 'pct' },
  { key: 'eps_fy1', label: (a) => `EPS ${a}`, kind: 'num' },
  { key: 'eps_fy2', label: (_a, b) => `EPS ${b}`, kind: 'num' },
];
const SHARED_FIELDS: { key: SharedField; label: string; kind: 'pct' | 'num' | 'int'; hint: string; family?: 'bank' | 'insurer' }[] = [
  { key: 'fade_years', label: 'Fade years after the target', kind: 'int', hint: 'growth decays linearly to the terminal rate over these years' },
  { key: 'tax_rate', label: 'Tax rate', kind: 'pct', hint: 'history median unless overridden' },
  { key: 'capex_alpha', label: 'Growth capex per unit of new revenue', kind: 'num', hint: 'capex = D&A + alpha × new revenue' },
  { key: 'nwc_intensity', label: 'Working capital per unit of new revenue', kind: 'num', hint: 'cash absorbed by growth' },
  { key: 'terminal_roic', label: 'Terminal ROIC', kind: 'pct', hint: 'reinvestment = terminal growth / ROIC; the engine floors it at WACC + 2pp' },
  { key: 'wacc', label: 'WACC', kind: 'pct', hint: 'the discount rate the leg used' },
  { key: 'tgr', label: 'Terminal growth', kind: 'pct', hint: 'the perpetuity rate' },
  // Owner, 2026-10-03: the three-statement model's assumptions beyond the forecast's operating lines.
  { key: 'gross_margin', label: 'Gross margin (3-statement model)', kind: 'pct', hint: 'COGS line of the income statement' },
  { key: 'sbc_pct', label: 'Stock-based compensation, % of revenue', kind: 'pct', hint: 'add-back in operating cash flow' },
  { key: 'interest_rate', label: 'Interest rate on debt', kind: 'pct', hint: 'interest expense on opening debt' },
  { key: 'payout_ratio', label: 'Dividend payout ratio', kind: 'pct', hint: 'dividends in financing cash flow' },
  { key: 'buyback_annual', label: 'Share repurchases per year (currency)', kind: 'num', hint: 'capped at cash above the minimum' },
  // Owner, 2026-10-03 (step three): the bank / insurer earnings-and-capital model's drivers; shown only where the run built one.
  { key: 'bank_loan_growth', label: 'Loan / asset growth (bank)', kind: 'pct', hint: 'earning assets and loans, both years', family: 'bank' },
  { key: 'bank_nim', label: 'Net interest margin (bank)', kind: 'pct', hint: 'NII = average earning assets × NIM', family: 'bank' },
  { key: 'bank_fee_growth', label: 'Non-interest income growth (bank)', kind: 'pct', hint: 'fees, trading and other income', family: 'bank' },
  { key: 'bank_cost_to_income', label: 'Cost-to-income (bank)', kind: 'pct', hint: 'operating expenses as a share of total income', family: 'bank' },
  { key: 'bank_credit_cost_bps', label: 'Credit cost, bps of loans (bank)', kind: 'num', hint: 'provisions = credit cost × average loans', family: 'bank' },
  { key: 'bank_cet1_target', label: 'CET1 target (bank)', kind: 'pct', hint: 'distributions are cut to hold it', family: 'bank' },
  { key: 'ins_premium_growth', label: 'Premium growth (insurer)', kind: 'pct', hint: 'net earned premiums, both years', family: 'insurer' },
  { key: 'ins_combined_ratio', label: 'Combined ratio (insurer)', kind: 'pct', hint: 'underwriting result = premiums × (1 − combined ratio)', family: 'insurer' },
  { key: 'ins_investment_yield', label: 'Investment yield (insurer)', kind: 'pct', hint: 'investment income = float × yield', family: 'insurer' },
];

type Form = {
  scenarios: Record<Scenario, Partial<Record<ScenarioField, string>>>;
  shared: Partial<Record<SharedField, string>>;
  mt: { metric: string; target_year: string; low: string; mid: string; high: string; enabled: boolean };
};

function fmtIn(v: number | null | undefined, kind: 'pct' | 'num' | 'int'): string {
  if (v == null || !isFinite(v)) return '';
  if (kind === 'pct') return (v * 100).toFixed(2).replace(/\.?0+$/, '');
  if (kind === 'int') return String(Math.round(v));
  return String(+v.toFixed(4));
}
function parseIn(s: string, kind: 'pct' | 'num' | 'int'): number | null {
  const t = s.trim();
  if (!t) return null;
  const n = Number(t.replace(/[%,]/g, ''));
  if (!isFinite(n)) return null;
  return kind === 'pct' ? n / 100 : n;
}
function money(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pctS(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${v >= 0 ? '+' : '−'}${(Math.abs(v) * 100).toFixed(1)}%`;
}

function initialForm(block: GuidanceEstimates | null | undefined, fc: GuidanceForecast | null | undefined, ctx?: ForecastContext | null): Form {
  const est = block?.estimates ?? {};
  const mt = (block as { medium_term_target?: { metric?: string; target_year?: string; low?: number | null; mid?: number | null; high?: number | null } } | null | undefined)?.medium_term_target
    ?? (fc?.target ? { metric: fc.target.metric, target_year: fc.target.target_year, mid: fc.target.value } : undefined);
  const scenarios = {} as Form['scenarios'];
  for (const s of SCENARIOS) {
    const row = est[s] ?? {};
    scenarios[s] = {};
    for (const f of SCENARIO_FIELDS) scenarios[s][f.key] = fmtIn(row[f.key], f.kind);
  }
  const h = (fc?.history ?? {}) as Record<string, number | null | undefined>;
  const inp = fc?.inputs ?? {};
  const saRaw = (ctx?.statement_assumptions ?? {}) as Record<string, { value?: number | null } | undefined>;
  const sa: Record<string, number | null | undefined> = Object.fromEntries(Object.entries(saRaw).map(([k, v]) => [k, v?.value]));
  const baRaw = ((ctx?.bank_model as { assumptions?: Record<string, { value?: number | null } | undefined> } | undefined)?.assumptions ?? {});
  const ba: Record<string, number | null | undefined> = Object.fromEntries(Object.entries(baRaw).map(([k, v]) => [k, typeof v?.value === 'number' ? v.value : undefined]));
  return {
    scenarios,
    shared: {
      fade_years: fmtIn(fc?.fade_years, 'int'), tax_rate: fmtIn(h.tax_rate as number, 'pct'), capex_alpha: fmtIn(h.capex_alpha as number, 'num'),
      nwc_intensity: fmtIn(h.nwc_intensity as number, 'num'), terminal_roic: fmtIn(fc?.terminal?.roic_terminal, 'pct'),
      wacc: fmtIn(inp.wacc, 'pct'), tgr: fmtIn(inp.tgr, 'pct'),
      gross_margin: fmtIn(sa.gross_margin, 'pct'), sbc_pct: fmtIn(sa.sbc_pct, 'pct'), interest_rate: fmtIn(sa.interest_rate, 'pct'),
      payout_ratio: fmtIn(sa.payout_ratio ?? ba.payout_ratio, 'pct'), buyback_annual: fmtIn(sa.buyback_annual ?? ba.buyback_annual, 'num'),
      bank_loan_growth: fmtIn(ba.asset_growth_fy1, 'pct'), bank_nim: fmtIn(ba.nim_fy1, 'pct'), bank_fee_growth: fmtIn(ba.fee_income_growth_fy1, 'pct'),
      bank_cost_to_income: fmtIn(ba.cost_to_income_fy1, 'pct'), bank_credit_cost_bps: fmtIn(ba.credit_cost_bps_fy1, 'num'), bank_cet1_target: fmtIn(ba.cet1_target, 'pct'),
      ins_premium_growth: fmtIn(ba.premium_growth_fy1, 'pct'), ins_combined_ratio: fmtIn(ba.combined_ratio_fy1, 'pct'), ins_investment_yield: fmtIn(ba.investment_yield_fy1, 'pct'),
    },
    mt: { metric: mt?.metric ?? 'eps', target_year: mt?.target_year ?? '', low: fmtIn(mt?.low, 'num'), mid: fmtIn(mt?.mid, 'num'), high: fmtIn(mt?.high, 'num'), enabled: !!mt },
  };
}

/** Only what the user changed, in the server's shape. */
function diffOverrides(form: Form, base: Form): EstimateOverrides {
  const out: EstimateOverrides = {};
  for (const s of SCENARIOS) {
    for (const f of SCENARIO_FIELDS) {
      const a = form.scenarios[s][f.key] ?? '', b = base.scenarios[s][f.key] ?? '';
      if (a.trim() !== b.trim()) {
        const v = parseIn(a, f.kind);
        if (v != null) { out.scenarios = out.scenarios ?? {}; out.scenarios[s] = { ...(out.scenarios[s] ?? {}), [f.key]: v }; }
      }
    }
  }
  for (const f of SHARED_FIELDS) {
    const a = form.shared[f.key] ?? '', b = base.shared[f.key] ?? '';
    if (a.trim() !== b.trim()) {
      const v = parseIn(a, f.kind);
      if (v != null) { out.shared = out.shared ?? {}; out.shared[f.key] = v; }
    }
  }
  const m = form.mt, bm = base.mt;
  if (m.enabled !== bm.enabled || m.metric !== bm.metric || m.target_year !== bm.target_year || m.low !== bm.low || m.mid !== bm.mid || m.high !== bm.high) {
    out.medium_term_target = m.enabled && m.target_year
      ? { metric: m.metric, target_year: m.target_year, low: parseIn(m.low, 'num'), mid: parseIn(m.mid, 'num'), high: parseIn(m.high, 'num') }
      : {};
  }
  return out;
}

function applyProposal(form: Form, p: EstimateOverrides): Form {
  const next: Form = { scenarios: { bear: { ...form.scenarios.bear }, base: { ...form.scenarios.base }, bull: { ...form.scenarios.bull } }, shared: { ...form.shared }, mt: { ...form.mt } };
  for (const s of SCENARIOS) {
    const row = p.scenarios?.[s];
    if (!row) continue;
    for (const f of SCENARIO_FIELDS) if (row[f.key] != null) next.scenarios[s][f.key] = fmtIn(row[f.key], f.kind);
  }
  for (const f of SHARED_FIELDS) {
    const v = p.shared?.[f.key];
    if (v != null) next.shared[f.key] = fmtIn(v, f.kind);
  }
  if (p.medium_term_target && 'metric' in p.medium_term_target) {
    const m = p.medium_term_target;
    next.mt = { metric: m.metric, target_year: m.target_year, low: fmtIn(m.low, 'num'), mid: fmtIn(m.mid, 'num'), high: fmtIn(m.high, 'num'), enabled: true };
  } else if (p.medium_term_target && Object.keys(p.medium_term_target).length === 0) {
    next.mt = { ...next.mt, enabled: false };
  }
  return next;
}

const INPUT_CLS = 'h-7 w-24 rounded border border-input bg-background px-2 text-right text-[16px] tabular-nums focus:outline-none focus:ring-1 focus:ring-ring';

export function EstimateWorkbench({ runId, ticker, block, forecast, dcfRange, onRunUpdated }: {
  runId: string; ticker: string; block: GuidanceEstimates | null | undefined; forecast: GuidanceForecast | null | undefined;
  dcfRange: DcfRange | undefined; onRunUpdated?: (r: RunResult) => void;
}) {
  const base = useMemo(() => initialForm(block, forecast, dcfRange?.forecast_context), [block, forecast, dcfRange]);
  const [form, setForm] = useState<Form>(base);
  const [scenario, setScenario] = useState<Scenario>('base');
  const [note, setNote] = useState('');
  const [preview, setPreview] = useState<EstimateRecompute | null>(null);
  const [busy, setBusy] = useState<'preview' | 'save' | 'revert' | 'ask' | 'pm' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [thread, setThread] = useState<{ role: 'user' | 'agent'; content: string; proposal?: EstimateOverrides | null; proposalError?: string | null }[]>([]);
  const override = dcfRange?.estimate_override ?? null;
  const modelKind = (dcfRange?.forecast_context as { bank_model?: { kind?: string } } | undefined)?.bank_model?.kind ?? null;
  const fy1 = block?.fiscal_year_1 ?? dcfRange?.forecast_context?.fiscal_year_1 ?? 'FY+1';
  const fy2 = block?.fiscal_year_2 ?? dcfRange?.forecast_context?.fiscal_year_2 ?? 'FY+2';
  const overrides = useMemo(() => diffOverrides(form, base), [form, base]);
  const nChanges = (Object.values(overrides.scenarios ?? {}).reduce((n, r) => n + Object.keys(r ?? {}).length, 0))
    + Object.keys(overrides.shared ?? {}).length + (overrides.medium_term_target !== undefined ? 1 : 0);

  const setSc = (f: ScenarioField, v: string) => setForm((p) => ({ ...p, scenarios: { ...p.scenarios, [scenario]: { ...p.scenarios[scenario], [f]: v } } }));
  const setSh = (f: SharedField, v: string) => setForm((p) => ({ ...p, shared: { ...p.shared, [f]: v } }));
  const setMt = (k: keyof Form['mt'], v: string | boolean) => setForm((p) => ({ ...p, mt: { ...p.mt, [k]: v } }));

  async function run(kind: 'preview' | 'save') {
    if (nChanges === 0) { setError('Change an estimate first.'); return; }
    setBusy(kind); setError(null);
    try {
      if (kind === 'preview') {
        setPreview(await previewEstimateOverride(runId, ticker, overrides));
      } else {
        const r = await saveEstimateOverride(runId, ticker, overrides, note || undefined);
        setPreview(r.result);
        onRunUpdated?.(r.run);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : String(e));
    } finally { setBusy(null); }
  }
  async function revert() {
    setBusy('revert'); setError(null);
    try {
      const r = await clearEstimateOverride(runId, ticker);
      setPreview(null);
      setForm(base);
      onRunUpdated?.(r.run);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  }
  async function regenerate() {
    setBusy('pm'); setError(null);
    try {
      const r = await regeneratePmText(runId, ticker);
      onRunUpdated?.(r.run);
    } catch (e) { setError(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : String(e)); } finally { setBusy(null); }
  }
  async function ask() {
    const q = question.trim();
    if (!q) return;
    setBusy('ask'); setError(null);
    const history = thread.map((m) => ({ role: m.role, content: m.content }));
    setThread((t) => [...t, { role: 'user', content: q }]);
    setQuestion('');
    try {
      const r = await askEstimateAgent(runId, ticker, q, history);
      setThread((t) => [...t, { role: 'agent', content: r.answer, proposal: r.proposal_valid ? r.proposal : null, proposalError: r.proposal_valid ? null : r.proposal_error }]);
    } catch (e) {
      setThread((t) => [...t, { role: 'agent', content: `The agent did not answer: ${e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : String(e)}` }]);
    } finally { setBusy(null); }
  }

  const sc = preview?.scenarios[scenario];
  const pf = sc?.forecast;

  return (
    <div className="mt-5 border-t border-border/60 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className={LABEL_CLS}>Work the estimate with the agent</span>
          {override ? <Badge variant="outline" className="text-[14px]">user override in force{override.created_at ? ` · ${override.created_at.slice(0, 10)}` : ''}</Badge> : null}
        </div>
        <div className="flex gap-1">
          {SCENARIOS.map((s) => (
            <button key={s} type="button" onClick={() => setScenario(s)}
              className={`rounded px-2 py-0.5 text-[15px] ${s === scenario ? 'bg-foreground text-background' : 'border border-border text-muted-foreground hover:text-foreground'}`}>
              {s}
            </button>
          ))}
        </div>
      </div>
      {override ? (
        <div className="mt-1 text-[15.5px] text-foreground/85">
          {override.fields?.join(', ')}{override.note ? ` — ${override.note}` : ''}. Agent's base IV {money(override.before?.intrinsic_value)} → {money(override.after?.intrinsic_value)}; 12-month target {money(override.before?.['12m_price_target'])} → {money(override.after?.['12m_price_target'])}. The PDF and the workbook carry the same figures. These estimates are carried into the next run of {ticker} until revoked on the Model Accuracy page.
        </div>
      ) : (
        <div className="mt-1 text-[15.5px] text-muted-foreground">
          Change what you disagree with, recompute, then save. Only the DCF leg moves — the same engine, the same weights, the same target bridge; peer-multiple legs stay on the cohort.
        </div>
      )}

      {/* 1. the estimates */}
      <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-1 text-[16px] sm:grid-cols-2">
        <div>
          <div className={LABEL_CLS}>{scenario} case estimates</div>
          {SCENARIO_FIELDS.map((f) => (
            <label key={f.key} className="mt-1 flex items-center justify-between gap-3">
              <span className="text-foreground/85">{f.label(fy1, fy2)}{f.kind === 'pct' ? ' (%)' : ''}</span>
              <input className={INPUT_CLS} value={form.scenarios[scenario][f.key] ?? ''} onChange={(e) => setSc(f.key, e.target.value)} placeholder="—" />
            </label>
          ))}
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className={LABEL_CLS}>Medium-term target</span>
            <label className="flex items-center gap-1 text-[15px] text-muted-foreground">
              <input type="checkbox" checked={form.mt.enabled} onChange={(e) => setMt('enabled', e.target.checked)} /> in use
            </label>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[15.5px]">
            <select className="h-7 rounded border border-input bg-background px-1 text-[16px]" value={form.mt.metric} onChange={(e) => setMt('metric', e.target.value)} disabled={!form.mt.enabled}>
              {['eps', 'revenue', 'revenue_growth', 'ebitda_margin'].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <input className="h-7 w-20 rounded border border-input bg-background px-2 text-[16px]" placeholder="FY2029" value={form.mt.target_year} onChange={(e) => setMt('target_year', e.target.value)} disabled={!form.mt.enabled} />
            {(['low', 'mid', 'high'] as const).map((k) => (
              <input key={k} className="h-7 w-16 rounded border border-input bg-background px-2 text-right text-[16px] tabular-nums" placeholder={k} value={form.mt[k]} onChange={(e) => setMt(k, e.target.value)} disabled={!form.mt.enabled} />
            ))}
          </div>
        </div>
        <div>
          <div className={LABEL_CLS}>Engine inputs (all scenarios)</div>
          {SHARED_FIELDS.filter((f) => !f.family || f.family === modelKind).map((f) => (
            <label key={f.key} className="mt-1 flex items-center justify-between gap-3" title={f.hint}>
              <span className="text-foreground/85">{f.label}{f.kind === 'pct' ? ' (%)' : ''}</span>
              <input className={INPUT_CLS} value={form.shared[f.key] ?? ''} onChange={(e) => setSh(f.key, e.target.value)} placeholder="—" />
            </label>
          ))}
        </div>
      </div>

      {/* 2. recompute, save, revert */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={busy !== null || nChanges === 0} onClick={() => run('preview')}>
          {busy === 'preview' ? 'Recomputing…' : `Recompute${nChanges ? ` (${nChanges} change${nChanges > 1 ? 's' : ''})` : ''}`}
        </Button>
        <input className="h-8 min-w-[12rem] flex-1 rounded border border-input bg-background px-2 text-[16px]" placeholder="Why (saved with the override)" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" disabled={busy !== null || nChanges === 0} onClick={() => run('save')}>{busy === 'save' ? 'Saving…' : 'Save override'}</Button>
        {override ? <Button size="sm" variant="ghost" disabled={busy !== null} onClick={revert}>{busy === 'revert' ? 'Reverting…' : 'Revert to the agent'}</Button> : null}
        {override ? (
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={regenerate} title="One model call: the PM rewrites its rationale and headline on your figures">
            {busy === 'pm' ? 'Rewriting…' : 'Rewrite PM rationale on my figures'}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => { setForm(base); setPreview(null); setError(null); }}>Reset fields</Button>
      </div>
      {error ? <div className="mt-2 text-[15.5px] font-medium text-foreground">{error}</div> : null}

      {preview ? (
        <div className="mt-3 rounded border border-border/60 p-3 text-[16px]">
          <div className={LABEL_CLS}>Agent vs you{preview.computed_at ? ` · ${preview.computed_at.slice(11, 19)} UTC` : ''}</div>
          <table className="mt-1 w-full tabular-nums">
            <thead><tr className="text-[14px] uppercase tracking-[0.08em] text-muted-foreground/70"><th className="text-left font-medium">Figure</th><th className="text-right font-medium">Agent</th><th className="text-right font-medium">You</th></tr></thead>
            <tbody>
              {([['DCF value per share (base)', 'dcf_value'], ['Blended intrinsic value (base)', 'intrinsic_value'], ['Base-case target', 'target'],
                ['12-month target (probability-weighted)', '12m_price_target'], ['Expected value', 'expected_value']] as const).map(([lab, k]) => (
                <tr key={k} className="border-t border-border/50">
                  <td className="py-1 text-foreground/85">{lab}</td>
                  <td className="py-1 text-right text-muted-foreground">{money(preview.before[k])}</td>
                  <td className="py-1 text-right font-medium">{money(preview.after[k])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sc?.skipped ? <div className="mt-2 text-[15.5px] text-foreground/85">{scenario}: {sc.skipped}</div> : null}
          {sc?.note ? <div className="mt-2 text-[15.5px] text-foreground/85">{sc.note}</div> : null}
          {sc && !sc.skipped ? (
            <div className="mt-1 text-[15px] text-muted-foreground">
              {scenario} DCF {money(sc.before.dcf_value)} → {money(sc.dcf?.value)} at {(sc.dcf_weight * 100).toFixed(0)}% weight; IV {money(sc.before.intrinsic_value)} → {money(sc.intrinsic_value)}; target {money(sc.before.target)} → {money(sc.target)}.
            </div>
          ) : null}
          {sc?.legs && Object.keys(sc.legs).length ? (
            <table className="mt-2 w-full tabular-nums text-[15px]">
              <thead><tr className="text-[14px] uppercase tracking-[0.08em] text-muted-foreground/70"><th className="text-left font-medium">Forward leg ({scenario})</th><th className="text-right font-medium">Metric agent → you</th><th className="text-right font-medium">Value agent → you</th></tr></thead>
              <tbody>
                {Object.entries(sc.legs).map(([name, lg]) => (
                  <tr key={name} className="border-t border-border/50">
                    <td className="py-1 text-foreground/85">{name} <span className="text-muted-foreground">({lg.basis})</span></td>
                    <td className="py-1 text-right">{lg.metric === 'eps' ? `${money(lg.metric_before)} → ${money(lg.metric_after)}` : `${(lg.metric_before / 1e9).toFixed(2)}bn → ${(lg.metric_after / 1e9).toFixed(2)}bn`}</td>
                    <td className="py-1 text-right">{money(lg.value_before)} → {money(lg.value_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {pf?.steps?.length ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[15px] text-muted-foreground">How your version was built ({pf.archetype_name}, {pf.horizon_years}-year path)</summary>
              <ol className="mt-1 space-y-1 text-[15px] text-foreground/85">
                {pf.steps.map((s) => <li key={s.n}><span className="font-medium">{s.n}. {s.title}.</span> {s.detail}</li>)}
              </ol>
              <div className="mt-1 flex flex-wrap gap-x-3 text-[15px] tabular-nums text-muted-foreground">
                {pf.rows.map((r) => <span key={r.year}>Y{r.year} {pctS(r.growth)} / {(r.ebit_margin * 100).toFixed(1)}%</span>)}
              </div>
              {(pf.invariants ?? []).filter((i) => i.ok === false).map((i) => <div key={i.id} className="mt-1 text-[15px] font-medium text-foreground">Check {i.id} {i.name} FAILS: {i.detail}</div>)}
            </details>
          ) : null}
          <div className="mt-2 text-[15px] text-muted-foreground">Preview only until saved. Saving applies to this run on the page, the PDF and the workbook; the written rationale and headline stay the agent's and say so.</div>
        </div>
      ) : null}

      {/* 3. ask the agent */}
      <div className="mt-4">
        <div className={LABEL_CLS}>Ask the agent</div>
        <div className="mt-1 space-y-2 text-[16px]">
          {thread.map((m, i) => (
            <div key={i} className={m.role === 'user' ? 'text-foreground' : 'rounded border border-border/60 p-2 text-foreground/85'}>
              <span className="mr-1 font-mono text-[14px] uppercase text-muted-foreground">{m.role}</span>{m.content}
              {m.proposal ? (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <span className="text-[15px] text-muted-foreground">Proposed: {describe(m.proposal)}{m.proposal.rationale ? ` — ${m.proposal.rationale}` : ''}</span>
                  <Button size="sm" variant="outline" onClick={() => { setForm((f) => applyProposal(f, m.proposal!)); setPreview(null); if (m.proposal?.rationale && !note) setNote(m.proposal.rationale); }}>Load into the form</Button>
                </div>
              ) : null}
              {m.proposalError ? <div className="mt-1 text-[15px] text-muted-foreground">The agent's proposal was not in the accepted shape ({m.proposalError}); nothing loaded.</div> : null}
            </div>
          ))}
        </div>
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
          <input className="h-8 flex-1 rounded border border-input bg-background px-2 text-[16px]" placeholder={`Why this margin path? What if ${fy2} growth is 4%?`} value={question} onChange={(e) => setQuestion(e.target.value)} disabled={busy === 'ask'} />
          <Button size="sm" variant="outline" type="submit" disabled={busy !== null || !question.trim()}>{busy === 'ask' ? 'Thinking…' : 'Ask'}</Button>
        </form>
        <div className="mt-1 text-[14.5px] text-muted-foreground">The agent answers from this run's trace and the brief's footnotes. It proposes; only you save.</div>
      </div>
    </div>
  );
}

function describe(p: EstimateOverrides): string {
  const parts: string[] = [];
  for (const s of SCENARIOS) {
    const row = p.scenarios?.[s];
    if (row) for (const [k, v] of Object.entries(row)) if (v != null) parts.push(`${s} ${k} ${k.startsWith('eps') ? money(v) : pctS(v)}`);
  }
  for (const [k, v] of Object.entries(p.shared ?? {})) if (v != null) parts.push(`${k} ${k === 'fade_years' || k === 'capex_alpha' || k === 'nwc_intensity' ? v : pctS(v)}`);
  if (p.medium_term_target && 'metric' in p.medium_term_target) parts.push(`${p.medium_term_target.metric} ${money(p.medium_term_target.mid)} by ${p.medium_term_target.target_year}`);
  else if (p.medium_term_target && Object.keys(p.medium_term_target).length === 0) parts.push('drop the medium-term target');
  return parts.join('; ');
}

export default EstimateWorkbench;
