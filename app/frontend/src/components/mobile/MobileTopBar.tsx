/**
 * MobileTopBar.tsx
 * =================
 * Floating avatar button, top-right — the mobile shell's identity anchor.
 * Replaces the old top-left hamburger: tapping it opens the full-page menu
 * at #/menu (MenuPage) instead of a drawer overlay, so back navigation is
 * native route behaviour and there's no z-index fight with the toaster
 * (the hamburger used to sit at z-10000 purely to outrank long-running
 * toasts — the avatar keeps the same stacking for the same reason).
 */
import { User } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';

export function MobileTopBar() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const initial = (user?.name ?? user?.email ?? '?')[0]?.toUpperCase() ?? '?';

  return (
    <div
      className="fixed right-3 z-[10000]"
      style={{ top: 'calc(env(safe-area-inset-top, 0px) + 12px)' }}
    >
      <button
        onClick={() => navigate('/menu')}
        aria-label="Open menu"
        className="w-10 h-10 rounded-full flex items-center justify-center overflow-hidden shadow-md border border-border bg-primary/15 text-primary"
      >
        {user?.avatar_url ? (
          <img src={user.avatar_url} alt="" className="w-full h-full object-cover" />
        ) : user ? (
          <span className="text-sm font-bold">{initial}</span>
        ) : (
          <User size={18} className="text-muted-foreground" />
        )}
      </button>
    </div>
  );
}
