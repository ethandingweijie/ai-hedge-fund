# Reimagined UI Migration — Progress Tracker

## Strategy (confirmed)
- **Progressive per-screen** porting (one screen per commit)
- **Translation layer in frontend** for data shape gaps (Power Law, Value Trap, Investor verdicts)

## Completed ✅

### 1. Login Screen (commit `6941691`)
- `app/frontend/src/pages/LoginPage.tsx` — reimagined sign-in
- Zinc-neutral palette, Leaf logo + "Equitable" wordmark
- Native Google GSI button + Apple OAuth button
- Real auth-context wired (loginWithGoogle, loginWithApple)
- "US · HK · SGX" market chip divider
- **Verified on Vercel**: https://ai-hedge-fund-ethandingweijies-projects.vercel.app/#/login
  - Google SDK loaded ✓
  - Apple SDK loaded ✓
  - GSI button renders "Continue as Ding" correctly
  - Backward compat: existing auth tokens still authenticate and redirect

### 2. Shared Component Kit (commit `739bcdb`)
- `app/frontend/src/components/v2/shared.tsx`
- 23 typed lucide-style icons (Menu, X, Search, Filter, Arrow*, Clock, Sun, Moon, Monitor, Zap, LogOut, ChevRight, Check, Sparkles, Scales, Shield, Book, ChevronDn, Brain, Users, Star, Plus, Bookmark)
- `Leaf` — brand "e" badge
- `Divider`, `ActionPill` (BUY/SELL/SHORT/HOLD), `GradeChip` (A+/A/A-/B+ etc with saturation gradient)
- `Delta` (up/down arrow + % with sign-aware color)
- `Card` (1px border surface)
- `TopBar` (sticky hamburger-only)
- `SwipeRow` (pointer-events based swipe-to-action for iOS + mouse)

## Remaining 🚧

Size estimates below are lines-of-source. Total remaining: ~2,200 LOC to adapt + wire to real data.

### 3. Home / Search Screen (~300 LOC)
Target: `app/frontend/src/pages/ReportPage.tsx` — replace the `!liveMode` form block (starts at line 890).

Prototype has:
- Greeting + hero heading
- Search input with ticker autocomplete
- Investor archetype bottom sheet (14 investors, 5 presets)
- QuickChips (Screener / Watchlist / History)
- Popular marquee tape (endless scroll)
- Ambient radial gradient + eq-marquee animation

Wire to:
- `startStream(ticker, model, agents)` from ActiveRunContext
- `getPopularTickers()` from lib/api
- `searchCompanies(q)` for autocomplete

Blockers: must preserve tier enforcement for Starter plan (profile locking), customAgents state.

### 4. Screener Screen (~600 LOC)
Target: `app/frontend/src/pages/ScreenerPage.tsx` — full replacement.

Prototype has:
- Market tabs (US/HK/SG)
- Sector filter + market cap filter + VGPM-only toggle
- Search input
- Sortable VGPM grades columns
- Stock cards with composite score + price refresh indicator

Wire to:
- `getScreenerStocks({ market, sector, capDef })`
- `getHkScreenerStocks()`, `getSgScreenerStocks()`
- `getScreenerPrices(symbols[])` (15s live refresh)
- `addToWatchlist(ticker)` / `removeFromWatchlist(ticker)` via SwipeRow

### 5. History Screen (~200 LOC)
Target: `app/frontend/src/pages/HistoryPage.tsx` — full replacement.

Prototype has:
- Filter/search row
- Past analysis cards with VGPM grades, action pill, upside %
- Group by date (Today / This Week / Earlier)

Wire to:
- `getHistory(params)` existing endpoint
- `getArchiveSummary()` for stats header
- Click row → `/report/:runId` (view existing result)

### 6. Report Analysis Tabs (~1000 LOC — biggest remaining)
Target: Extract from `app/frontend/src/pages/ReportPage.tsx` liveMode section.

Tabs: **Summary · Valuation · Investors · Risk · Research · Financials**

Data-shape translations needed:
- **Summary**: PM decision card, VGPM scorecard, Key stats grid
- **Valuation**: DCF ladder (bear/base/bull), probability distribution, WACC, scenario chart
  - Translator: `RunResult.data.dcf_range[ticker]` → `{ bear, base, bull, wacc }` for new card shape
- **Investors**: Agent verdict list with dynamic agent names (not hardcoded Buffett/Munger)
  - Translator: `RunResult.data.analyst_signals` → `[{ name, verdict, thesis }]`
- **Risk**: Power Law radar + Value Trap checklist
  - Translator: `RunResult.data.power_law_analysis` and `value_trap_analysis` → prototype shapes
- **Research**: 4-category LLM summary + industry brief + deep research text + citations
  - Translator: parse `deep_research_sections` 2A-2F into prototype's `{ label, bullets }[]`
- **Financials**: Revenue bar chart + price chart + key stats
  - Wire to `getFinancials(ticker, period)` and `getStockData(ticker)`

### 7. Settings Drawer + Pricing (~300 LOC)
Target: New `components/v2/Drawer.tsx` + update `App.tsx` to use it.

Prototype has:
- Theme toggle (light/dark/auto)
- Subscription tier chip
- Account section (name, email, plan)
- Logout button
- Pricing page with plan cards + comparison matrix

Wire to:
- `useAuth().logout()`
- `useAuth().user` for name/email
- localStorage for tier persistence (already exists as `ai_hf_tier`)

## Data Contract Notes

Pick-up points for the translator layer in `lib/api.ts`:

```typescript
// Add these translator functions when porting Report tabs:

export function toV2Summary(rr: RunResult, ticker: string): V2SummaryData { ... }
export function toV2Valuation(rr: RunResult, ticker: string): V2ValuationData { ... }
export function toV2Investors(rr: RunResult, ticker: string): V2Investor[] { ... }
export function toV2PowerLaw(rr: RunResult, ticker: string): V2PowerLawData { ... }
export function toV2ValueTrap(rr: RunResult, ticker: string): V2ValueTrapData { ... }
export function toV2Research(rr: RunResult, ticker: string): V2ResearchData { ... }
```

## Testing Approach (per screen)

1. Port screen component using prototype as design reference
2. Wire real data via `lib/api.ts` translators if needed
3. Local `npx tsc --noEmit` — no type errors on the new file
4. Local `npm run build` — production build succeeds
5. Commit and push to `main` → Vercel auto-deploys
6. Claude in Chrome smoke test on live Vercel URL:
   - Visual render matches prototype
   - Data loads from Railway backend
   - Interactions trigger expected API calls (network tab)
7. Move to next screen

## Prototype Source

`C:\Users\ethan\Documents\Projects\AI Hedge Fund\ui-reimagined\ReimaginedUI.jsx` (2701 LOC, single file with all screens + mock data)
