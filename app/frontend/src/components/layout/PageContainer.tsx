/**
 * PageContainer.tsx
 * =================
 * Shared responsive content shell for top-level pages. Centralises two things
 * that were previously copy-pasted (inconsistently) into every page wrapper:
 *
 *   1. A tuned max-width per content type, so reading pages don't strand
 *      content in huge desktop gutters (e.g. the old `max-w-3xl` catalogue) and
 *      data pages don't stretch edge-to-edge.
 *
 *   2. A MODE-AWARE vertical chrome offset. Pages used to bake in `pt-16 pb-20`
 *      to clear the mobile floating avatar button (MobileTopBar) + leave bottom
 *      breathing room. But the desktop shell (DesktopSidebar) has neither, so
 *      that padding became dead space top and bottom on desktop. Here the
 *      offset is applied only in mobile mode; desktop gets a normal `py-8`.
 *
 * Width is capped, never expanded — inside the 430px MobileLayout frame the
 * `max-w-*` values are wider than the frame, so they no-op on mobile and the
 * content simply fills the phone width as before.
 */
import type { ReactNode } from 'react';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { cn } from '@/lib/utils';

export type ContainerSize = 'prose' | 'default' | 'wide' | 'full';

const MAX_W: Record<ContainerSize, string> = {
  prose: 'max-w-3xl',     // long-form text where reading measure matters
  default: 'max-w-5xl',   // card lists / standard single-column content
  wide: 'max-w-7xl',      // data tables / cohort grids
  full: 'max-w-none',     // full-bleed (e.g. Screener)
};

interface PageContainerProps {
  size?: ContainerSize;
  className?: string;
  /** When true, skip the built-in vertical chrome padding (caller controls it). */
  flush?: boolean;
  children: ReactNode;
}

export function PageContainer({ size = 'default', className, flush = false, children }: PageContainerProps) {
  const { mode } = useLayoutMode();
  return (
    <div className="min-h-full bg-background">
      <div
        className={cn(
          'mx-auto w-full px-4 md:px-8',
          MAX_W[size],
          !flush && (mode === 'mobile' ? 'pt-16 pb-6' : 'py-8 md:py-10'),
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
