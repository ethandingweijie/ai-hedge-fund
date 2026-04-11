import { BarChart2, Filter, BookMarked, History } from 'lucide-react';
import { useLocation } from 'react-router-dom';

const TABS = [
  { label: 'Research',  icon: BarChart2,  path: '/report',    href: '#/report'    },
  { label: 'Screener',  icon: Filter,     path: '/screener',  href: '#/screener'  },
  { label: 'Watchlist',  icon: BookMarked, path: '/watchlist', href: '#/watchlist' },
  { label: 'History',    icon: History,    path: '/history',   href: '#/history'   },
] as const;

export function MobileBottomNav() {
  const location = useLocation();
  const currentPath = location.pathname;
  const active = TABS.find(t => currentPath.startsWith(t.path)) ?? TABS[0];

  return (
    <nav className="sticky bottom-0 z-50 bg-background border-t border-border safe-area-bottom">
      <div className="flex items-center justify-around h-14">
        {TABS.map(({ label, icon: Icon, href, path }) => {
          const isActive = active.path === path;
          return (
            <a
              key={href}
              href={href}
              className={`flex flex-col items-center justify-center gap-0.5 flex-1 h-full transition-colors
                ${isActive
                  ? 'text-blue-500'
                  : 'text-muted-foreground'
                }`}
            >
              <Icon size={20} strokeWidth={isActive ? 2.2 : 1.8} />
              <span className="text-[10px] font-medium leading-none">{label}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}
