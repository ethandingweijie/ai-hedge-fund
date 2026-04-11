import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { Card } from '@/components/ui/card';
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  Radar,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import type { PowerLawAnalysis } from '@/lib/reportTypes';
import { NIL_TEXT } from './ChecksConcernsPanel';

interface PowerLawRadarProps {
  powerLaw?: PowerLawAnalysis;
  ticker: string;
}

// ── Dimension config ───────────────────────────────────────────────────────────
const DIMENSIONS = [
  {
    label:       'Scale Economies',
    scoreKey:    'scale_economies'          as keyof PowerLawAnalysis,
    noteKey:     'scale_economies_note'     as keyof PowerLawAnalysis,
    concernKey:  'scale_economies_concern'  as keyof PowerLawAnalysis,
    noteKw:      ['scale', 'cost', 'margin', 'unit', 'infrastructure', 'volume', 'cmr', 'gross'],
    concernKw:   ['compress', 'commodit', 'pressure', 'erode', 'rival', 'competition', 'loss'],
  },
  {
    label:       'Network Effects',
    scoreKey:    'network_effects'          as keyof PowerLawAnalysis,
    noteKey:     'network_effects_note'     as keyof PowerLawAnalysis,
    concernKey:  'network_effects_concern'  as keyof PowerLawAnalysis,
    noteKw:      ['network', 'user', 'buyer', 'seller', 'platform', 'marketplace', 'flywheel', 'active'],
    concernKw:   ['single', 'geographic', 'domestic', 'fragment', 'churn', 'limit', 'international'],
  },
  {
    label:       'Winner-Take-Most',
    scoreKey:    'winner_take_most'         as keyof PowerLawAnalysis,
    noteKey:     'winner_take_most_note'    as keyof PowerLawAnalysis,
    concernKey:  'winner_take_most_concern' as keyof PowerLawAnalysis,
    noteKw:      ['market share', 'dominant', 'gmv', 'concentration', 'leader', '%', 'top'],
    concernKw:   ['fragment', 'rival', 'share loss', 'competitor', 'entrant', 'displac', 'new player'],
  },
  {
    label:       'Switching Costs',
    scoreKey:    'switching_costs'          as keyof PowerLawAnalysis,
    noteKey:     'switching_costs_note'     as keyof PowerLawAnalysis,
    concernKey:  'switching_costs_concern'  as keyof PowerLawAnalysis,
    noteKw:      ['switching', 'lock', 'retention', 'churn', 'friction', 'integrat', 'non-portable', 'crm'],
    concernKw:   ['alternative', 'easy', 'substitute', 'commodit', 'replac', 'low friction', 'migrate'],
  },
  {
    label:       'Data / IP Moat',
    scoreKey:    'data_ip_moat'             as keyof PowerLawAnalysis,
    noteKey:     'data_ip_moat_note'        as keyof PowerLawAnalysis,
    concernKey:  'data_ip_moat_concern'     as keyof PowerLawAnalysis,
    noteKw:      ['data', 'ip', 'patent', 'proprietary', 'algorithm', 'model', 'ai', 'qwen', 'behavioural'],
    concernKw:   ['open source', 'replicate', 'commodit', 'public', 'regulation', 'privacy', 'partial'],
  },
] as const;

const NIL = NIL_TEXT;
type DimRow = { label: string; note: string; concern: string; score: number };

// Negative signal words used to locate risk/concern sentences in old interpretation blobs
const RISK_WORDS = [
  'however', 'risk', 'limit', 'challeng', 'concern', 'despite', 'although',
  'but ', 'weak', 'erode', 'compress', 'fragment', 'only', 'partial',
  'uncertain', 'threat', 'compet', 'decline', 'loss', 'gap', 'lag',
];

function scoreColor(s: number) {
  if (s >= 8) return 'text-green-500';
  if (s >= 5) return 'text-yellow-500';
  return 'text-red-500';
}

function trim(text: string, limit = 240): string {
  const t = text.trim();
  return t.length > limit ? t.slice(0, limit - 1) + '…' : t;
}

/**
 * Strip LLM meta-scoring preambles that narrate the score rather than stating
 * a real business fact or risk.
 * e.g. "Winner-take-most earns only a partial score: "  → removed
 *      "Scale economies score 2/2: "                    → removed
 *      "Network effects score the maximum: "            → removed
 *      "Switching costs are scored at 1: "              → removed
 */
const META_PATTERNS = [
  /^[\w\s/\-]+(?:earns?|scores?|receives?|rated?|earn only|score only)[^:]*:\s*/i,
  /^[\w\s/\-]+(?:score[sd]?)\s+(?:the\s+)?(?:maximum|partially|partial|fully|a\s+\d|at\s+\d|\d\s*\/\s*2)[^:]*:\s*/i,
  /^the\s+[\w\s/\-]+(?:dimension|score|category)[^:]*:\s*/i,
];

function stripMeta(text: string): string {
  let t = text.trim();
  for (const pat of META_PATTERNS) {
    t = t.replace(pat, '');
  }
  // Capitalise first letter after stripping
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** Split a blob into sentences. */
function sentences(blob: string): string[] {
  return blob.split(/(?<=[.!?])\s+/).map(s => s.trim()).filter(Boolean);
}

/** Score a sentence against a keyword list. */
function kwScore(sentence: string, keywords: readonly string[]): number {
  const lower = sentence.toLowerCase();
  return keywords.filter(k => lower.includes(k)).length;
}

/**
 * Pick the best-matching sentence for `keywords`.
 * `used` tracks globally-assigned sentences — never reuse one.
 * Returns '' if no unused match with score > 0 exists.
 */
function extractBest(
  parts: string[],
  keywords: readonly string[],
  used: Set<string>,
): string {
  let best = '';
  let bestScore = 0;
  for (const s of parts) {
    if (used.has(s)) continue;
    const hits = kwScore(s, keywords);
    if (hits > bestScore) { bestScore = hits; best = s; }
  }
  return best; // '' when nothing unused qualifies
}

/**
 * For concerns, prefer sentences containing RISK_WORDS × dimension keywords.
 * Never reuse a sentence already in `used`.
 */
function extractConcern(
  parts: string[],
  dimKw: readonly string[],
  used: Set<string>,
): string {
  let best = '';
  let bestScore = 0;
  for (const s of parts) {
    if (used.has(s)) continue;
    const lower = s.toLowerCase();
    const riskHits = RISK_WORDS.filter(w => lower.includes(w)).length;
    const dimHits  = dimKw.filter(k => lower.includes(k)).length;
    const total = riskHits * 2 + dimHits;
    if (total > bestScore) { bestScore = total; best = s; }
  }
  if (best) return best;
  // Second pass: any unused sentence containing a risk word
  for (const s of parts) {
    if (used.has(s)) continue;
    if (RISK_WORDS.some(w => s.toLowerCase().includes(w))) return s;
  }
  return '';
}

export function PowerLawRadar({ powerLaw, ticker }: PowerLawRadarProps) {
  if (!powerLaw) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Power law data unavailable.</p>
      </Card>
    );
  }

  const radarData = [
    { subject: 'Scale Eco.', value: (powerLaw.scale_economies ?? 0) * 5 },
    { subject: 'Network Fx', value: (powerLaw.network_effects ?? 0) * 5 },
    { subject: 'Winner-All', value: (powerLaw.winner_take_most ?? 0) * 5 },
    { subject: 'Switch Cost', value: (powerLaw.switching_costs ?? 0) * 5 },
    { subject: 'Data/IP',    value: (powerLaw.data_ip_moat ?? 0) * 5 },
  ];

  const totalScore  = powerLaw.total_score ?? powerLaw.score ?? 0;
  const interpParts = sentences(powerLaw.interpretation ?? '');

  // Global deduplication — every sentence may appear in at most one cell
  const used = new Set<string>();

  function resolveField(
    structured: string,
    fallbackFn: () => string,
  ): string {
    // 1. Use structured LLM field if present and not a filler phrase
    const clean = stripMeta(structured.trim());
    const isBlank =
      !clean ||
      /^(insufficient|no positive|no specific|no risk)/i.test(clean);

    let result = isBlank ? '' : clean;

    // 2. Fallback to extraction if blank
    if (!result) {
      result = stripMeta(fallbackFn());
    }

    // 3. If result already used by another cell → NIL
    if (!result || used.has(result)) return NIL;

    used.add(result);
    return result;
  }

  // ── Build one row per dimension ────────────────────────────────────────────
  const rows: DimRow[] = DIMENSIONS.map(dim => {
    const score = (powerLaw[dim.scoreKey] as number | undefined) ?? 0;

    const note = resolveField(
      (powerLaw[dim.noteKey] as string | undefined) ?? '',
      () => extractBest(interpParts, dim.noteKw, used),
    );

    const concern = resolveField(
      (powerLaw[dim.concernKey] as string | undefined) ?? '',
      () => extractConcern(interpParts, dim.concernKw, used),
    );

    return {
      label:   dim.label,
      note:    note    === NIL ? NIL : trim(note),
      concern: concern === NIL ? NIL : trim(concern),
      score,
    };
  });

  // Truncate multiple_implication to one sentence ≤ 200 chars
  const implication = (() => {
    const raw = (powerLaw.multiple_implication ?? '').trim();
    if (!raw) return '';
    const first = raw.split(/[.!?]/)[0].trim();
    return first.length > 200 ? first.slice(0, 197) + '…' : first + '.';
  })();

  // Per-dimension accordion state: null = all closed
  const [openDim, setOpenDim] = useState<string | null>(null);

  return (
    <Card className="p-4 space-y-4">

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Power Law Score — {ticker}</h3>
        <span className={`text-2xl font-bold ${scoreColor(totalScore)}`}>
          {totalScore.toFixed(1)}
          <span className="text-sm font-normal text-muted-foreground">/10</span>
        </span>
      </div>

      {/* ── Radar chart ─────────────────────────────────────────────────────── */}
      <ResponsiveContainer width="100%" height={190}>
        <RadarChart data={radarData}>
          <PolarGrid />
          <PolarAngleAxis dataKey="subject" tick={{ fontSize: 11 }} />
          <Radar
            dataKey="value"
            stroke="#3b82f6"
            fill="#3b82f6"
            fillOpacity={0.35}
          />
          <Tooltip formatter={(v) => [(Number(v) / 5).toFixed(1) + ' / 2', 'Score']} />
        </RadarChart>
      </ResponsiveContainer>

      {/* ── Per-dimension accordion ──────────────────────────────────────────── */}
      <div className="border-t divide-y divide-border/50">
        {rows.map(({ label, note, concern, score }) => {
          const isOpen   = openDim === label;
          const hasNote    = note    !== NIL && note.trim()    !== '';
          const hasConcern = concern !== NIL && concern.trim() !== '';

          return (
            <div key={label}>
              {/* Row trigger */}
              <button
                onClick={() => setOpenDim(isOpen ? null : label)}
                className="w-full flex items-center gap-2.5 px-1 py-2 text-left transition-colors rounded-sm hover:bg-muted/40"
              >
                {/* Score badge */}
                <span className={`shrink-0 text-[11px] font-bold tabular-nums w-8 text-right ${scoreColor(score * 5)}`}>
                  {score}/2
                </span>

                {/* Label */}
                <span className="flex-1 text-xs font-medium text-foreground">
                  {label}
                </span>

                {/* Chevron */}
                <ChevronDown
                  size={13}
                  className={`shrink-0 text-muted-foreground transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
                />
              </button>

              {/* Collapsible detail */}
              {isOpen && (hasNote || hasConcern) && (
                <div className="px-5 pb-3 pt-0.5 space-y-2">
                  {hasNote && (
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-green-600 dark:text-green-400 mb-0.5">
                        ✓ What Checks Off
                      </p>
                      <p className="text-[11px] leading-relaxed text-muted-foreground">{note}</p>
                    </div>
                  )}
                  {hasConcern && (
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-amber-500 dark:text-amber-400 mb-0.5">
                        ? Concerns
                      </p>
                      <p className="text-[11px] leading-relaxed text-muted-foreground">{concern}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Valuation implication ────────────────────────────────────────────── */}
      {implication && (
        <p className="text-[11px] text-muted-foreground border-t pt-2 leading-relaxed">
          {implication}
        </p>
      )}

    </Card>
  );
}
