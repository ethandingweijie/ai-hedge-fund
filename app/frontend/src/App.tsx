import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from './components/ui/sonner';
import { ReportPage } from './pages/ReportPage';
import { ReportViewPage } from './pages/ReportViewPage';
import { HistoryPage } from './pages/HistoryPage';
import { ScreenerPage } from './pages/ScreenerPage';
import { WatchlistPage } from './pages/WatchlistPage';
import { PricingPage } from './pages/PricingPage';
import { LoginPage } from './pages/LoginPage';
import { ActiveRunProvider } from './contexts/active-run-context';
import { ThemeProvider } from './contexts/theme-context';
import { AuthProvider } from './contexts/auth-context';
import { MobileLayout } from './components/mobile/MobileLayout';

export default function App() {
  return (
    <ThemeProvider>
    <AuthProvider>
    <ActiveRunProvider>
    <HashRouter>
      <MobileLayout>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          {/* Analysis routes — standalone pages without the graph IDE layout */}
          <Route path="/report" element={<ReportPage />} />
          <Route path="/report/:runId" element={<ReportViewPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/screener" element={<ScreenerPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/pricing" element={<PricingPage />} />

          {/* Default: redirect to Ticker Research */}
          <Route path="*" element={<Navigate to="/report" replace />} />
        </Routes>
      </MobileLayout>
    </HashRouter>
    </ActiveRunProvider>
    </AuthProvider>
    </ThemeProvider>
  );
}
