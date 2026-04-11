import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

export type CardPriority = 'high' | 'medium' | 'low';

const PRIORITY_BORDER: Record<CardPriority, string> = {
  high:   'border-l-[3px] border-l-green-500',
  medium: 'border-l-[3px] border-l-blue-500',
  low:    'border-l-[3px] border-l-border',
};

interface MobileContentCardProps {
  title: string;
  badge?: React.ReactNode;
  defaultExpanded?: boolean;
  priority?: CardPriority;
  children: React.ReactNode;
}

export function MobileContentCard({
  title,
  badge,
  defaultExpanded = false,
  priority = 'low',
  children,
}: MobileContentCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className={`bg-card border border-border rounded-xl overflow-hidden ${PRIORITY_BORDER[priority]}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 active:bg-muted/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{title}</span>
          {badge}
        </div>
        <ChevronDown
          size={16}
          className={`text-muted-foreground transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t border-border/50">
          {children}
        </div>
      )}
    </div>
  );
}
