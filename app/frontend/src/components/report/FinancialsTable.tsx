import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { currencySymbol } from '@/lib/utils';

interface FinancialsTableProps {
  rawFinancials?: Record<string, unknown>;
  ticker: string;
}

// ── Formatting helpers ────────────────────────────────────────────────────────

function makeFmt(sym: string) {
  return (v: unknown): string => {
    if (v == null) return '—';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    if (Math.abs(n) >= 1e9) return `${sym}${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `${sym}${(n / 1e6).toFixed(2)}M`;
    if (Math.abs(n) >= 1e3) return `${sym}${(n / 1e3).toFixed(2)}K`;
    return n % 1 === 0 ? String(n) : n.toFixed(2);
  };
}
// Module-level default — overridden inside the component
function fmt(v: unknown): string { return makeFmt('$')(v); }

function fmtLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

// Detect a year-like key: "2020", "FY2020", "FY20", "2020-01-01", "TTM"
function isYearKey(k: string): boolean {
  return /^(FY)?\d{4}(-\d{2}-\d{2})?$|^TTM$|^LTM$/i.test(k);
}

// Preferred metric display order
const METRIC_ORDER = [
  'revenue', 'gross_profit', 'operating_income', 'ebitda', 'net_income',
  'eps', 'operating_cash_flow', 'free_cash_flow', 'capex',
  'total_assets', 'total_debt', 'net_debt', 'cash_and_equivalents',
  'total_equity', 'shares_outstanding',
  'gross_margin', 'operating_margin', 'net_margin', 'return_on_equity',
  'return_on_assets', 'return_on_invested_capital',
];

function sortMetricKeys(keys: string[]): string[] {
  const ordered = METRIC_ORDER.filter(k => keys.includes(k));
  const rest = keys.filter(k => !METRIC_ORDER.includes(k)).sort();
  return [...ordered, ...rest];
}

// ── Layout detection ──────────────────────────────────────────────────────────

type Layout =
  | { kind: 'by-year';   years: string[]; metrics: string[] }
  | { kind: 'by-metric'; years: string[]; metrics: string[] }
  | { kind: 'flat';      rows: { key: string; value: unknown }[] }
  | { kind: 'unknown' };

function detectLayout(raw: Record<string, unknown>): Layout {
  const keys = Object.keys(raw);
  if (keys.length === 0) return { kind: 'unknown' };

  // by-year: top-level keys are year-like, values are objects
  const yearKeys = keys.filter(isYearKey);
  if (yearKeys.length >= 2) {
    const firstYear = raw[yearKeys[0]];
    if (firstYear && typeof firstYear === 'object' && !Array.isArray(firstYear)) {
      const metrics = sortMetricKeys(Object.keys(firstYear as object));
      return { kind: 'by-year', years: yearKeys.sort(), metrics };
    }
  }

  // by-metric: top-level keys are metric names, values are objects keyed by year
  const metricKeys = keys.filter(k => !isYearKey(k));
  if (metricKeys.length >= 1) {
    const firstMetricVal = raw[metricKeys[0]];
    if (firstMetricVal && typeof firstMetricVal === 'object' && !Array.isArray(firstMetricVal)) {
      const innerKeys = Object.keys(firstMetricVal as object);
      const innerYears = innerKeys.filter(isYearKey);
      if (innerYears.length >= 1) {
        // Collect all years across all metrics
        const allYears = new Set<string>();
        for (const mk of metricKeys) {
          const mv = raw[mk];
          if (mv && typeof mv === 'object') {
            Object.keys(mv as object).filter(isYearKey).forEach(y => allYears.add(y));
          }
        }
        return {
          kind: 'by-metric',
          years: Array.from(allYears).sort(),
          metrics: sortMetricKeys(metricKeys),
        };
      }
    }

    // flat: metric keys → scalar values
    const isFlat = metricKeys.every(k => {
      const v = raw[k];
      return v == null || typeof v !== 'object' || Array.isArray(v);
    });
    if (isFlat) {
      return {
        kind: 'flat',
        rows: sortMetricKeys(metricKeys).map(k => ({ key: k, value: raw[k] })),
      };
    }
  }

  return { kind: 'unknown' };
}

// ── Sub-components ────────────────────────────────────────────────────────────

function MultiYearTable({
  years,
  metrics,
  getCell,
}: {
  years: string[];
  metrics: string[];
  getCell: (metric: string, year: string) => unknown;
}) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-44 text-xs">Metric</TableHead>
            {years.map(y => (
              <TableHead key={y} className="text-right text-xs whitespace-nowrap">{y}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {metrics.map(metric => (
            <TableRow key={metric}>
              <TableCell className="font-medium text-xs py-1.5">{fmtLabel(metric)}</TableCell>
              {years.map(y => (
                <TableCell key={y} className="text-right text-xs py-1.5 tabular-nums">
                  {fmt(getCell(metric, y))}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function FlatTable({ rows }: { rows: { key: string; value: unknown }[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs w-56">Metric</TableHead>
            <TableHead className="text-right text-xs">Value</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(({ key, value }) => (
            <TableRow key={key}>
              <TableCell className="font-medium text-xs py-1.5">{fmtLabel(key)}</TableCell>
              <TableCell className="text-right text-xs py-1.5 tabular-nums">{fmt(value)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function FinancialsTable({ rawFinancials, ticker }: FinancialsTableProps) {
  const fmt = makeFmt(currencySymbol(ticker));
  if (!rawFinancials || Object.keys(rawFinancials).length === 0) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Raw financials unavailable for {ticker}.</p>
      </Card>
    );
  }

  const layout = detectLayout(rawFinancials);

  return (
    <Card className="p-4">
      <h3 className="text-sm font-semibold mb-3">Financials — {ticker}</h3>

      {layout.kind === 'by-year' && (
        <MultiYearTable
          years={layout.years}
          metrics={layout.metrics}
          getCell={(metric, year) =>
            (rawFinancials[year] as Record<string, unknown>)?.[metric]
          }
        />
      )}

      {layout.kind === 'by-metric' && (
        <MultiYearTable
          years={layout.years}
          metrics={layout.metrics}
          getCell={(metric, year) =>
            (rawFinancials[metric] as Record<string, unknown>)?.[year]
          }
        />
      )}

      {layout.kind === 'flat' && <FlatTable rows={layout.rows} />}

      {layout.kind === 'unknown' && (
        <pre className="text-xs text-muted-foreground whitespace-pre-wrap overflow-auto max-h-60 bg-muted/30 p-3 rounded">
          {JSON.stringify(rawFinancials, null, 2)}
        </pre>
      )}
    </Card>
  );
}
