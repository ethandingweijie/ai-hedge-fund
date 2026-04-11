import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';

interface IndustryBriefPanelProps {
  industryBrief?: string;
  sector?: string;
}

// ── Detect whether the brief uses the old ASCII-box format or markdown ────────

function isMarkdownFormat(text: string): boolean {
  return /^#{1,3}\s/m.test(text) || /\*\*.+\*\*/.test(text);
}

// ── Markdown component overrides — professional enterprise styling ─────────────

const mdComponents: Components = {
  // h1 — document title
  h1({ children }) {
    // Strip the "INDUSTRY INTELLIGENCE BRIEF — " prefix if present and render as header card
    const text = String(children);
    const m = text.match(/INDUSTRY INTELLIGENCE BRIEF\s*[—–-]\s*(.+)/i);
    if (m) {
      // Parse company | sector | date from "COMPANY (TICKER) | Sector | Date"
      const parts = m[1].split('|').map(s => s.trim());
      const company = parts[0] ?? text;
      const badges = parts.slice(1);
      return (
        <div className="mb-6 pb-5 border-b">
          <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
            Industry Intelligence Brief
          </p>
          <h2 className="text-base font-bold text-foreground leading-snug">{company}</h2>
          {badges.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {badges.map((b, i) => (
                <Badge key={i} variant="outline" className="text-[11px] font-normal px-2 py-0.5">
                  {b}
                </Badge>
              ))}
            </div>
          )}
        </div>
      );
    }
    return (
      <h1 className="text-lg font-bold text-foreground mt-8 mb-3 pb-2 border-b">{children}</h1>
    );
  },

  // h2 — major section
  h2({ children }) {
    return (
      <div className="mt-8 mb-3">
        <div className="flex items-center gap-3">
          <div className="h-px w-4 bg-border shrink-0" />
          <h2 className="text-[13px] font-bold uppercase tracking-[0.12em] text-foreground/55 whitespace-nowrap">
            {children}
          </h2>
          <div className="h-px flex-1 bg-border" />
        </div>
      </div>
    );
  },

  // h3 — sub-section
  h3({ children }) {
    return (
      <div className="mt-5 mb-2 border-l-[3px] border-primary/40 pl-3">
        <h3 className="text-[13px] font-bold uppercase tracking-wide text-foreground/70 leading-snug">
          {children}
        </h3>
      </div>
    );
  },

  // h4 — minor heading
  h4({ children }) {
    return (
      <h4 className="text-xs font-semibold text-foreground/80 mt-4 mb-1">{children}</h4>
    );
  },

  // Paragraph
  p({ children }) {
    return (
      <p className="text-sm text-foreground/80 leading-relaxed mb-3">{children}</p>
    );
  },

  // Blockquote — variant perception callout
  blockquote({ children }) {
    return (
      <div className="my-4 border-l-4 border-primary/50 bg-primary/5 rounded-r px-4 py-3">
        <div className="text-sm text-foreground/85 leading-relaxed [&>p]:mb-0">{children}</div>
      </div>
    );
  },

  // Strong
  strong({ children }) {
    return <strong className="font-semibold text-foreground">{children}</strong>;
  },

  // Horizontal rule
  hr() {
    return <hr className="my-6 border-border" />;
  },

  // Unordered list
  ul({ children }) {
    return <ul className="my-3 space-y-1.5 pl-1">{children}</ul>;
  },

  // Ordered list
  ol({ children }) {
    return <ol className="my-3 space-y-1.5 pl-1 list-none">{children}</ol>;
  },

  // List item
  li({ children }) {
    // Ordered list items get a number prefix via CSS counter — handled inline
    return (
      <li className="flex gap-2 text-sm text-foreground/80 leading-relaxed">
        <span className="text-primary/50 shrink-0 mt-0.5">›</span>
        <span className="flex-1">{children}</span>
      </li>
    );
  },

  // Table — professional data table
  table({ children }) {
    return (
      <div className="my-4 rounded border overflow-hidden overflow-x-auto">
        <table className="w-full text-sm border-collapse">{children}</table>
      </div>
    );
  },

  thead({ children }) {
    return <thead className="bg-muted/50">{children}</thead>;
  },

  tbody({ children }) {
    return <tbody className="divide-y divide-border">{children}</tbody>;
  },

  tr({ children }) {
    return <tr className="hover:bg-muted/20 transition-colors">{children}</tr>;
  },

  th({ children }) {
    return (
      <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-foreground/60 border-b border-border whitespace-nowrap">
        {children}
      </th>
    );
  },

  td({ children }) {
    return (
      <td className="px-3 py-2 text-xs text-foreground/80 leading-relaxed align-top">
        {children}
      </td>
    );
  },

  // Inline code (used for metrics/values)
  code({ children }) {
    return (
      <code className="px-1 py-0.5 rounded bg-muted text-xs font-mono text-foreground/90">
        {children}
      </code>
    );
  },

  // Code block
  pre({ children }) {
    return (
      <pre className="my-3 p-3 rounded bg-muted text-xs font-mono leading-relaxed overflow-x-auto">
        {children}
      </pre>
    );
  },
};

// ── Legacy ASCII-box format parser (kept for old CLI runs) ────────────────────

type LegacyBlock =
  | { type: 'header';        company: string; sectorLine: string; date: string }
  | { type: 'major-section'; text: string }
  | { type: 'minor-section'; text: string }
  | { type: 'insight';       label: string; title: string }
  | { type: 'body';          text: string }
  | { type: 'disclaimer';    text: string };

function parseLegacy(raw: string): LegacyBlock[] {
  const lines = raw.split('\n');
  const blocks: LegacyBlock[] = [];
  let i = 0;
  let bodyAccum: string[] = [];

  const flush = () => {
    const t = bodyAccum.join('\n').trim();
    if (t) blocks.push({ type: 'body', text: t });
    bodyAccum = [];
  };
  const nextContent = (from: number): [number, string] | null => {
    for (let j = from; j < lines.length; j++) {
      const t = lines[j].trim();
      if (t && !/^[━─=]{5,}$/.test(t)) return [j, t];
    }
    return null;
  };

  while (i < lines.length) {
    const line = lines[i], t = line.trim();

    if (/^={5,}$/.test(t)) {
      if (blocks.length === 0) {
        i++;
        const hls: string[] = [];
        while (i < lines.length) {
          const ht = lines[i].trim();
          if (/^[=━]{5,}$/.test(ht)) break;
          if (ht) hls.push(ht);
          i++;
        }
        const tl = hls.find(l => /INDUSTRY INTELLIGENCE BRIEF/i.test(l));
        if (tl) {
          const m = tl.match(/INDUSTRY INTELLIGENCE BRIEF\s*[—–-]\s*(.+)/i);
          const sl = hls.find(l => /^Sector:/i.test(l)) ?? '';
          const dl = hls.find(l => /^Brief Date:/i.test(l)) ?? '';
          blocks.push({ type: 'header', company: m ? m[1].trim() : tl, sectorLine: sl.replace(/^Sector:\s*/i, '').trim(), date: dl.replace(/^Brief Date:\s*/i, '').trim() });
        }
        i++; continue;
      }
      i++; continue;
    }

    if (/^━{5,}$/.test(t)) {
      const nc = nextContent(i + 1);
      if (nc) {
        const [ni, nt] = nc;
        let j = ni + 1;
        while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && /^━{5,}$/.test(lines[j].trim())) {
          flush(); blocks.push({ type: 'major-section', text: nt }); i = j + 1; continue;
        }
      }
      flush(); i++; continue;
    }

    if (/^─{5,}$/.test(t)) {
      const nc = nextContent(i + 1);
      if (nc) {
        const [ni, nt] = nc;
        let j = ni + 1;
        while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && /^─{5,}$/.test(lines[j].trim())) {
          flush(); blocks.push({ type: 'minor-section', text: nt }); i = j + 1; continue;
        }
      }
      i++; continue;
    }

    if (!t) { bodyAccum.push(''); i++; continue; }
    if (/^DISCLAIMER:/i.test(t)) { flush(); blocks.push({ type: 'disclaimer', text: t }); i++; continue; }

    const im = t.match(/^(INSIGHT\s+\d+)\s*[—–-]\s*(.+)/i);
    if (im) { flush(); blocks.push({ type: 'insight', label: im[1].toUpperCase(), title: im[2].trim() }); i++; continue; }

    bodyAccum.push(line); i++;
  }
  flush();
  return blocks;
}

function cites(text: string): React.ReactNode {
  return <>{text.split(/(\[\d+\])/g).map((p, i) => { const m = p.match(/^\[(\d+)\]$/); return m ? <sup key={i} className="text-[9px] font-mono text-sky-400 ml-[1px]">[{m[1]}]</sup> : <span key={i}>{p}</span>; })}</>;
}

function LegacyBodyBlock({ text }: { text: string }) {
  return (
    <div className="space-y-3">
      {text.split(/\n{2,}/).map(p => p.trim()).filter(Boolean).map((para, pi) => (
        <p key={pi} className="text-sm text-foreground/80 leading-relaxed">
          {cites(para.replace(/\n/g, ' '))}
        </p>
      ))}
    </div>
  );
}

function LegacyView({ blocks }: { blocks: LegacyBlock[] }) {
  return (
    <>
      {blocks.map((block, idx) => {
        switch (block.type) {
          case 'header':
            return (
              <div key={idx} className="px-6 py-5 border-b">
                <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Industry Intelligence Brief</p>
                <h2 className="text-base font-bold text-foreground">{block.company}</h2>
                {block.sectorLine && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {block.sectorLine.split('|').map((s, i) => <Badge key={i} variant="outline" className="text-[11px] font-normal">{s.trim()}</Badge>)}
                  </div>
                )}
                {block.date && <p className="text-xs text-muted-foreground mt-2">{block.date}</p>}
              </div>
            );
          case 'major-section':
            return (
              <div key={idx} className="px-6 pt-7 pb-2">
                <div className="flex items-center gap-3">
                  <div className="h-px w-4 bg-border shrink-0" />
                  <span className="text-[10px] font-bold uppercase tracking-[0.15em] text-foreground/50 whitespace-nowrap">{block.text}</span>
                  <div className="h-px flex-1 bg-border" />
                </div>
              </div>
            );
          case 'minor-section':
            return (
              <div key={idx} className="px-6 pt-5 pb-1">
                <div className="border-l-[3px] border-primary/40 pl-3">
                  <h4 className="text-[13px] font-bold uppercase tracking-wide text-foreground/70">{block.text}</h4>
                </div>
              </div>
            );
          case 'insight':
            return (
              <div key={idx} className="px-6 pt-5 pb-0">
                <div className="flex items-start gap-2.5 flex-wrap">
                  <span className="text-[9px] font-bold uppercase tracking-wider text-primary bg-primary/10 border border-primary/20 px-2 py-0.5 rounded-sm">{block.label}</span>
                  <span className="text-sm font-semibold text-foreground leading-snug">{block.title}</span>
                </div>
              </div>
            );
          case 'body':
            return <div key={idx} className="px-6 py-2.5"><LegacyBodyBlock text={block.text} /></div>;
          case 'disclaimer':
            return <div key={idx} className="px-6 py-4 mt-2 border-t bg-muted/15"><p className="text-[11px] text-muted-foreground italic">{block.text.replace(/^DISCLAIMER:\s*/i, '')}</p></div>;
          default: return null;
        }
      })}
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function IndustryBriefPanel({ industryBrief, sector: _sector }: IndustryBriefPanelProps) {
  const [tocOpen, setTocOpen] = useState(false);

  if (!industryBrief) {
    return (
      <Card className="p-6">
        <p className="text-muted-foreground text-sm">Industry brief unavailable.</p>
      </Card>
    );
  }

  const isMarkdown = isMarkdownFormat(industryBrief);

  // Build ToC from h2 headings for markdown, or major-section blocks for legacy
  const tocItems: { id: string; label: string }[] = [];
  if (isMarkdown) {
    for (const m of industryBrief.matchAll(/^##\s+(.+)$/gm)) {
      const label = m[1].trim();
      tocItems.push({ id: `toc-${label.replace(/\W+/g, '-').toLowerCase()}`, label });
    }
  }

  return (
    <Card className="overflow-hidden">
      {/* ToC bar */}
      {tocItems.length > 0 && (
        <div className="border-b bg-muted/20 px-6 py-2 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">{tocItems.length} sections</span>
          <button
            className="text-xs text-primary/70 hover:text-primary transition-colors"
            onClick={() => setTocOpen(o => !o)}
          >
            {tocOpen ? 'Hide contents ↑' : 'Show contents ↓'}
          </button>
        </div>
      )}

      {tocOpen && tocItems.length > 0 && (
        <div className="border-b bg-muted/10 px-6 py-3">
          <ol className="space-y-1">
            {tocItems.map((item, i) => (
              <li key={i}>
                <a
                  href={`#${item.id}`}
                  className="text-xs text-foreground/55 hover:text-foreground transition-colors flex gap-2"
                  onClick={() => setTocOpen(false)}
                >
                  <span className="font-mono text-primary/50 w-4 shrink-0">{i + 1}</span>
                  <span className="truncate">{item.label}</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="overflow-y-auto max-h-[75vh]">
        {isMarkdown ? (
          /* ── Markdown rendering ──────────────────────────────────────── */
          <div className="px-6 py-5">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={mdComponents}
            >
              {industryBrief}
            </ReactMarkdown>
          </div>
        ) : (
          /* ── Legacy ASCII-box rendering ──────────────────────────────── */
          <LegacyView blocks={parseLegacy(industryBrief)} />
        )}
      </div>
    </Card>
  );
}
