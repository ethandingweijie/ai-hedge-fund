/**
 * Markdown — the single renderer for LLM-authored prose in reports.
 *
 * Why this exists: several narrative cards used to dump raw model output into
 * a `whitespace-pre-wrap` div. That leaks the markup verbatim — readers saw
 * literal `##` headings, `**bold**` asterisks, and GFM tables as rows of
 * `| … | … |` pipes. One card "solved" it by regex-stripping the asterisks,
 * which removed the emphasis instead of rendering it and still left tables
 * broken.
 *
 * Everything model-written should render through this component so the report
 * has one consistent typographic voice. `remarkGfm` is what supplies tables,
 * strikethrough and task lists.
 *
 * Tables get an `overflow-x-auto` wrapper: financial tables are frequently
 * wider than the column they sit in, and without it they force the whole page
 * to scroll sideways.
 */
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

export const mdComponents: Components = {
  h1: ({ children }) => (
    <h3 className="text-heading-xs text-foreground mt-5 mb-2 first:mt-0">{children}</h3>
  ),
  h2: ({ children }) => (
    <h4 className="text-[15px] font-semibold text-foreground mt-5 mb-2 first:mt-0">{children}</h4>
  ),
  h3: ({ children }) => (
    <h5 className="text-sm font-semibold text-foreground mt-4 mb-1.5 first:mt-0">{children}</h5>
  ),
  h4: ({ children }) => (
    <h6 className="text-[13px] font-semibold uppercase tracking-wide text-muted-foreground mt-4 mb-1.5 first:mt-0">
      {children}
    </h6>
  ),
  p: ({ children }) => (
    <p className="text-sm text-foreground/85 leading-relaxed mb-3 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc pl-5 mb-3 last:mb-0 space-y-1.5 marker:text-muted-foreground">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-5 mb-3 last:mb-0 space-y-1.5 marker:text-muted-foreground">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="text-sm text-foreground/85 leading-relaxed pl-1">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-foreground">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-brand underline underline-offset-2 hover:opacity-80"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-[var(--hairline)] pl-3 my-3 text-sm text-muted-foreground italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-[var(--hairline)]" />,
  code: ({ children }) => (
    <code className="rounded-tag bg-surface-2 px-1.5 py-0.5 text-[12.5px] numeric">
      {children}
    </code>
  ),
  // Wide financial tables scroll inside their own container rather than
  // pushing the page sideways.
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-control border border-[var(--hairline)]">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface-2">{children}</thead>,
  th: ({ children }) => (
    <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground whitespace-nowrap border-b border-[var(--hairline)]">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-2 align-top text-foreground/85 border-b border-[var(--hairline)] last:border-b-0 numeric">
      {children}
    </td>
  ),
  tr: ({ children }) => <tr className="last:[&>td]:border-b-0">{children}</tr>,
};

export function Markdown({
  children,
  className = '',
}: {
  /** Optional so callers can pass a possibly-absent field directly; empty
      content renders nothing rather than an empty box. */
  children?: string | null;
  className?: string;
}) {
  if (!children || !children.trim()) return null;
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
