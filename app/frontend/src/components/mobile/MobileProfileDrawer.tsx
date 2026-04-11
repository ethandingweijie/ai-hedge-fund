import { X, Sun, Moon, Monitor, Zap, LogOut } from 'lucide-react';
import { useTheme, type Theme } from '@/contexts/theme-context';
import { useAuth } from '@/contexts/auth-context';

interface MobileProfileDrawerProps {
  open: boolean;
  onClose: () => void;
}

const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun,     label: 'Light' },
  { value: 'dark',  icon: Moon,    label: 'Dark' },
  { value: 'auto',  icon: Monitor, label: 'Auto' },
];

export function MobileProfileDrawer({ open, onClose }: MobileProfileDrawerProps) {
  const { theme, setTheme } = useTheme();
  const { user, logout } = useAuth();

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/40 transition-opacity"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 bottom-0 z-50 w-72 bg-background border-l border-border transform transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-border">
          <span className="text-sm font-semibold">Settings</span>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-muted"
          >
            <X size={16} className="text-muted-foreground" />
          </button>
        </div>

        {/* User info */}
        {user && (
          <div className="px-4 py-4 border-b border-border">
            <div className="flex items-center gap-3">
              {user.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.name ?? user.email}
                  className="w-10 h-10 rounded-full object-cover ring-2 ring-border"
                />
              ) : (
                <div className="w-10 h-10 rounded-full bg-primary/15 flex items-center justify-center text-sm font-bold text-primary ring-2 ring-border">
                  {(user.name ?? user.email)[0].toUpperCase()}
                </div>
              )}
              <div className="min-w-0">
                <p className="text-sm font-semibold truncate">{user.name ?? ''}</p>
                <p className="text-xs text-muted-foreground truncate">{user.email}</p>
              </div>
            </div>
          </div>
        )}

        {/* Theme toggle */}
        <div className="px-4 py-4 border-b border-border">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Theme</p>
          <div className="flex gap-2">
            {THEMES.map(({ value, icon: Icon, label }) => (
              <button
                key={value}
                onClick={() => setTheme(value)}
                className={`flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-lg transition-colors
                  ${theme === value
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:text-foreground'
                  }`}
              >
                <Icon size={16} />
                <span className="text-[10px] font-medium">{label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Links */}
        <div className="px-4 py-2">
          <a
            href="#/pricing"
            onClick={onClose}
            className="flex items-center gap-3 px-3 py-3 rounded-lg hover:bg-muted transition-colors"
          >
            <Zap size={16} className="text-amber-500" />
            <span className="text-sm font-medium">Pricing</span>
          </a>
          {user && (
            <button
              onClick={() => { logout(); onClose(); }}
              className="w-full flex items-center gap-3 px-3 py-3 rounded-lg hover:bg-muted transition-colors text-left"
            >
              <LogOut size={16} className="text-muted-foreground" />
              <span className="text-sm font-medium text-muted-foreground">Sign out</span>
            </button>
          )}
        </div>
      </div>
    </>
  );
}
