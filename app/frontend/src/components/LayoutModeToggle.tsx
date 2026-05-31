/**
 * LayoutModeToggle.tsx
 * ====================
 * Segmented Mobile / Desktop switch. Lives in BOTH the desktop sidebar and the
 * mobile hamburger drawer so a user can always get back to the other layout.
 * Styled to match the Theme segmented control in MobileTopBar.
 */
import { Smartphone, Monitor } from 'lucide-react';
import { useLayoutMode, type LayoutMode } from '@/contexts/layout-mode-context';

const OPTIONS: { value: LayoutMode; icon: typeof Smartphone; label: string }[] = [
  { value: 'mobile',  icon: Smartphone, label: 'Mobile'  },
  { value: 'desktop', icon: Monitor,    label: 'Desktop' },
];

export function LayoutModeToggle({ onChange }: { onChange?: () => void }) {
  const { mode, setMode } = useLayoutMode();
  return (
    <div className="flex gap-1.5">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          onClick={() => { setMode(value); onChange?.(); }}
          className={`flex-1 flex flex-col items-center gap-1 py-2 rounded-md transition-colors text-[10px] font-medium
            ${mode === value ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  );
}
