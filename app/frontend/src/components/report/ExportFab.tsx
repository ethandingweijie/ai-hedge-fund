/**
 * Floating export button, bottom-right of a saved report.
 *
 * One tap opens a vertical column of exports: the report as a PDF and the
 * valuation model as an Excel workbook (live formulas, every figure traced).
 * Mounted by both report render paths (desktop ReportViewPage / ReportPage and
 * the mobile V2ReportView). On mobile it sits above the floating nav pill.
 * Monochrome: green/red are reserved for price change.
 */
import { useEffect, useRef, useState } from 'react';
import { Download, FileSpreadsheet, FileText, Loader2, X } from 'lucide-react';
import { downloadRunExport, type RunExportKind } from '@/lib/api';
import { useLayoutMode } from '@/contexts/layout-mode-context';

const ITEMS: { kind: RunExportKind; label: string; icon: typeof FileText }[] = [
  { kind: 'pdf',  label: 'Report (PDF)',  icon: FileText },
  { kind: 'xlsx', label: 'Model (XLSX)',  icon: FileSpreadsheet },
];

/** Clearance above the mobile floating nav pill (~64px tall, 6px off the edge). */
const MOBILE_BOTTOM = 'calc(env(safe-area-inset-bottom, 0px) + 84px)';
const DESKTOP_BOTTOM = '24px';

export function ExportFab({ runId, ticker }: { runId: string; ticker?: string }) {
  const { mode } = useLayoutMode();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<RunExportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const run = async (kind: RunExportKind) => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await downloadRunExport(runId, kind, ticker);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      ref={rootRef}
      className="fixed z-40 flex flex-col items-end gap-2"
      style={{ right: mode === 'mobile' ? '16px' : '24px',
               bottom: mode === 'mobile' ? MOBILE_BOTTOM : DESKTOP_BOTTOM }}
    >
      {open && (
        <div role="menu" aria-label="Export" className="flex flex-col items-end gap-2">
          {error && (
            <span className="max-w-[220px] truncate rounded-md border border-border bg-card px-2 py-1 text-[11px] text-muted-foreground shadow-sm"
                  title={error}>
              Export failed. Try again.
            </span>
          )}
          {ITEMS.map(({ kind, label, icon: Icon }) => (
            <button
              key={kind}
              type="button"
              role="menuitem"
              disabled={busy !== null}
              onClick={() => run(kind)}
              className="flex h-10 items-center gap-2 rounded-full border border-border bg-card pl-3 pr-4 text-sm font-medium text-foreground
                         shadow-[0_8px_24px_rgb(0_0_0/0.12)] transition-colors hover:bg-muted disabled:opacity-60"
            >
              {busy === kind ? <Loader2 size={16} className="animate-spin" /> : <Icon size={16} />}
              <span>{busy === kind ? 'Preparing…' : label}</span>
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        aria-label={open ? 'Close export menu' : 'Export report'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => { setOpen(o => !o); setError(null); }}
        className="flex h-12 w-12 items-center justify-center rounded-full bg-foreground text-background
                   shadow-[0_12px_32px_rgb(0_0_0/0.18),0_4px_10px_rgb(0_0_0/0.10)] transition-transform hover:scale-105 active:scale-95"
      >
        {open ? <X size={20} /> : <Download size={20} />}
      </button>
    </div>
  );
}
