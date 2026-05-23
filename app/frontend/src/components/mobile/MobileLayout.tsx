import { useLocation, useNavigate } from 'react-router-dom';
import { Lightbulb } from 'lucide-react';
import { MobileTopBar } from './MobileTopBar';

interface MobileLayoutProps {
  children: React.ReactNode;
}

/**
 * Side tab — always visible vertical ribbon on the right edge of the phone
 * frame. One-tap shortcut to the Research Ideas hub.
 *
 * Hidden when already on a /research-ideas* route (to avoid redundant nav).
 */
function ResearchIdeasSideTab() {
  const navigate = useNavigate();
  const location = useLocation();
  if (location.pathname.startsWith('/research-ideas')) return null;
  if (location.pathname === '/login') return null;

  return (
    <button
      onClick={() => navigate('/research-ideas')}
      aria-label="Open Research Ideas"
      className="absolute z-[55] right-0 top-1/2 -translate-y-1/2 flex flex-col items-center gap-1
                 bg-amber-500 hover:bg-amber-400 text-black
                 py-2.5 px-1.5 rounded-l-md shadow-lg
                 transition-all duration-150"
      style={{ writingMode: 'vertical-rl' }}
    >
      <Lightbulb size={13} className="-rotate-90" />
      <span className="text-[10px] font-bold uppercase tracking-wider">
        Research Ideas
      </span>
    </button>
  );
}

export function MobileLayout({ children }: MobileLayoutProps) {
  return (
    <div className="min-h-screen bg-neutral-200 dark:bg-neutral-900 flex justify-center">
      {/* Phone frame — max 430px like iPhone Pro Max */}
      <div className="w-full max-w-[430px] min-h-screen bg-background relative shadow-2xl flex flex-col"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
        <MobileTopBar />
        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
        <ResearchIdeasSideTab />
      </div>
    </div>
  );
}
