/**
 * PageHeader.tsx
 * ==============
 * Consistent page title band for top-level pages. Replaces the ad-hoc,
 * per-page title markup so every page gets the same heading hierarchy,
 * spacing, and an optional right-aligned actions slot.
 *
 * Desktop pages previously started abruptly with no heading at all
 * (Watchlist / History dropped straight into a search box) — this gives them
 * proper chrome while still reading correctly inside the mobile frame.
 */
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  icon?: LucideIcon;
  /** Extra classes for the icon (e.g. a brand colour like `text-amber-500`). */
  iconClassName?: string;
  /** Optional content (e.g. a back button) rendered before the title. */
  leading?: ReactNode;
  /** Right-aligned actions (refresh button, filters, etc.). */
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  subtitle,
  icon: Icon,
  iconClassName,
  leading,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <div className={cn('mb-5 flex items-start justify-between gap-4', className)}>
      <div className="flex min-w-0 items-start gap-2">
        {leading}
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-foreground md:text-2xl">
            {Icon && <Icon className={cn('h-5 w-5 shrink-0 md:h-6 md:w-6', iconClassName)} />}
            <span className="truncate">{title}</span>
          </h1>
          {subtitle && (
            <p className="mt-1 text-xs text-muted-foreground md:text-sm">{subtitle}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
