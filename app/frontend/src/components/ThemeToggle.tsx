/**
 * ThemeToggle.tsx
 * ===============
 * Light / Dark / Auto segmented control. Extracted from MobileTopBar so the
 * desktop sidebar reuses the exact same control (no drift).
 */
import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme, type Theme } from '@/contexts/theme-context';

const OPTIONS: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun,     label: 'Light' },
  { value: 'dark',  icon: Moon,    label: 'Dark'  },
  { value: 'auto',  icon: Monitor, label: 'Auto'  },
];

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="flex gap-1.5">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          className={`flex-1 flex flex-col items-center gap-1 py-2 rounded-md transition-colors text-[10px] font-medium
            ${theme === value ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  );
}
