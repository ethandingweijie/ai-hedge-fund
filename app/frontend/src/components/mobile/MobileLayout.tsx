import { MobileTopBar } from './MobileTopBar';

interface MobileLayoutProps {
  children: React.ReactNode;
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
      </div>
    </div>
  );
}
