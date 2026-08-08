import { MobileTopBar } from './MobileTopBar';
import { FloatingNavBar } from '@/components/layout/FloatingNavBar';

interface MobileLayoutProps {
  children: React.ReactNode;
}

export function MobileLayout({ children }: MobileLayoutProps) {
  return (
    <div className="min-h-screen bg-muted flex justify-center">
      {/* Phone frame — max 430px like iPhone Pro Max */}
      <div className="w-full max-w-[430px] min-h-screen bg-background relative shadow-2xl flex flex-col"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
        <MobileTopBar />
        {/* NOTE: no overflow-y-auto here — the frame is min-h-screen so this
            div never scrolls itself (the WINDOW scrolls). An overflow ancestor
            that doesn't scroll captures position:sticky and silently breaks
            every sticky header (TabHero, V2 ticker bar) on mobile. */}
        {/* pb-24 clears the floating bottom nav bar mounted below. */}
        <div className="flex-1 pb-24">
          {children}
        </div>
      </div>
      <FloatingNavBar />
    </div>
  );
}
