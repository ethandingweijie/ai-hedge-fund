import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { ReportPage } from './pages/ReportPage';
import { ReportViewPage } from './pages/ReportViewPage';
import { HistoryPage } from './pages/HistoryPage';
import { ScreenerPage } from './pages/ScreenerPage';
import { WatchlistPage } from './pages/WatchlistPage';
import { ResearchIdeasPage } from './pages/ResearchIdeasPage';
import { SW46Page } from './pages/SW46Page';
import { ComplacencyPage } from './pages/ComplacencyPage';
import { PricingPage } from './pages/PricingPage';
import { LoginPage } from './pages/LoginPage';
import { DDAlertsPage } from './pages/DDAlertsPage';
import { ActiveRunProvider } from './contexts/active-run-context';
import { ThemeProvider } from './contexts/theme-context';
import { AuthProvider, useAuth } from './contexts/auth-context';
import { MobileLayout } from './components/mobile/MobileLayout';

/** Redirect to /login if not authenticated */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null; // wait for auth check
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <ThemeProvider>
    <AuthProvider>
    <ActiveRunProvider>
    <HashRouter>
      {/* Global toast mount point — one per app, so toast.success()/.error()
          from any page (Screener, History, Report, etc.) renders consistently.
          Previously only ReportPage mounted a Toaster, so toasts from other
          pages silently no-oped. */}
      {/* offset respects iOS safe-area-inset-top so toasts don't sit on the
          notch / status bar (clock, signal). +32px adds breathing room
          below the inset on both PWA and Safari standalone. */}
      <Toaster
        position="top-right"
        richColors
        closeButton
        expand
        visibleToasts={6}
        offset="calc(env(safe-area-inset-top, 0px) + 32px)"
      />
      <MobileLayout>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          {/* All routes require authentication */}
          <Route path="/report" element={<RequireAuth><ReportPage /></RequireAuth>} />
          <Route path="/report/:runId" element={<RequireAuth><ReportViewPage /></RequireAuth>} />
          <Route path="/history" element={<RequireAuth><HistoryPage /></RequireAuth>} />
          <Route path="/screener" element={<RequireAuth><ScreenerPage /></RequireAuth>} />
          <Route path="/watchlist" element={<RequireAuth><WatchlistPage /></RequireAuth>} />
          <Route path="/dd-alerts" element={<RequireAuth><DDAlertsPage /></RequireAuth>} />
          <Route path="/research-ideas" element={<RequireAuth><ResearchIdeasPage /></RequireAuth>} />
          <Route path="/research-ideas/sw46" element={<RequireAuth><SW46Page /></RequireAuth>} />
          <Route path="/research-ideas/complacency" element={<RequireAuth><ComplacencyPage /></RequireAuth>} />
          <Route path="/pricing" element={<PricingPage />} />

          {/* Default: redirect to login (will redirect to /report after auth) */}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </MobileLayout>
    </HashRouter>
    </ActiveRunProvider>
    </AuthProvider>
    </ThemeProvider>
  );
}
