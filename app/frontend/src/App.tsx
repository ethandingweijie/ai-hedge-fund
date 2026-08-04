import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { ReportPage } from './pages/ReportPage';
import { ReportViewPage } from './pages/ReportViewPage';
import { HistoryPage } from './pages/HistoryPage';
import { ScreenerPage } from './pages/ScreenerPage';
import { WatchlistPage } from './pages/WatchlistPage';
import { DiscussPage } from './pages/DiscussPage';
import { TickerChatPage } from './pages/TickerChatPage';
import { ResearchIdeasPage } from './pages/ResearchIdeasPage';
import { SW46Page } from './pages/SW46Page';
import { HK50Page } from './pages/HK50Page';
import { ComplacencyPage } from './pages/ComplacencyPage';
import { MomentumPage } from './pages/MomentumPage';
import { FundFlowPage } from './pages/FundFlowPage';
import { IdeaOfTheDayPage } from './pages/IdeaOfTheDayPage';
import { ShortlistedIdeasPage } from './pages/ShortlistedIdeasPage';
import { PricingPage } from './pages/PricingPage';
import { LoginPage } from './pages/LoginPage';
import { DDAlertsPage } from './pages/DDAlertsPage';
import { RoboStrategyPage } from './pages/RoboStrategyPage';
import { ActiveRunProvider } from './contexts/active-run-context';
import { ThemeProvider } from './contexts/theme-context';
import { AuthProvider, useAuth } from './contexts/auth-context';
import { LayoutModeProvider } from './contexts/layout-mode-context';
import { AppShell } from './components/AppShell';

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
    <LayoutModeProvider>
    <HashRouter>
      {/* Global toast mount point — one per app, so toast.success()/.error()
          from any page (Screener, History, Report, etc.) renders consistently.
          Previously only ReportPage mounted a Toaster, so toasts from other
          pages silently no-oped. */}
      {/* Offset must clear the iOS time/signal/battery bar in PWA mode.
          Previous value (safe-area-inset-top + 32px) wasn't enough on iPhones
          with Dynamic Island (54-59px inset) AND in Safari-tab mode where
          env() returns 0. Bumped to 80px which gives:
            • Safari tab (env=0):           80px from top — well clear of 20px status bar
            • PWA on notched iPhone:        ~124px from top — clear of 44px notch
            • PWA on Dynamic Island device: ~134px from top — clear of 54-59px island
          Slightly far down for desktop browsers, acceptable tradeoff for
          mobile-first audience. */}
      <Toaster
        position="top-right"
        richColors
        closeButton
        expand
        visibleToasts={6}
        offset="calc(env(safe-area-inset-top, 0px) + 80px)"
      />
      <AppShell>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          {/* All routes require authentication */}
          <Route path="/report" element={<RequireAuth><ReportPage /></RequireAuth>} />
          <Route path="/report/:runId" element={<RequireAuth><ReportViewPage /></RequireAuth>} />
          <Route path="/history" element={<RequireAuth><HistoryPage /></RequireAuth>} />
          <Route path="/screener" element={<RequireAuth><ScreenerPage /></RequireAuth>} />
          <Route path="/watchlist" element={<RequireAuth><WatchlistPage /></RequireAuth>} />
          <Route path="/discuss" element={<RequireAuth><DiscussPage /></RequireAuth>} />
          <Route path="/discuss/:ticker" element={<RequireAuth><TickerChatPage /></RequireAuth>} />
          <Route path="/robo-strategy" element={<RequireAuth><RoboStrategyPage /></RequireAuth>} />
          <Route path="/dd-alerts" element={<RequireAuth><DDAlertsPage /></RequireAuth>} />
          <Route path="/research-ideas" element={<RequireAuth><ResearchIdeasPage /></RequireAuth>} />
          <Route path="/research-ideas/sw46" element={<RequireAuth><SW46Page /></RequireAuth>} />
          <Route path="/research-ideas/hk50" element={<RequireAuth><HK50Page /></RequireAuth>} />
          <Route path="/research-ideas/complacency" element={<RequireAuth><ComplacencyPage /></RequireAuth>} />
          <Route path="/research-ideas/momentum" element={<RequireAuth><MomentumPage /></RequireAuth>} />
          <Route path="/research-ideas/fundflow" element={<RequireAuth><FundFlowPage /></RequireAuth>} />
          <Route path="/research-ideas/idea-of-the-day/:ideaId" element={<RequireAuth><IdeaOfTheDayPage /></RequireAuth>} />
          <Route path="/research-ideas/shortlist" element={<RequireAuth><ShortlistedIdeasPage /></RequireAuth>} />
          <Route path="/pricing" element={<PricingPage />} />

          {/* Default: redirect to login (will redirect to /report after auth) */}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </AppShell>
    </HashRouter>
    </LayoutModeProvider>
    </ActiveRunProvider>
    </AuthProvider>
    </ThemeProvider>
  );
}
