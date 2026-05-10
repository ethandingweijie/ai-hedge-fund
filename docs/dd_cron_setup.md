# Phase 2B — Auto Due-D Cron Service Setup (Railway)

Phase 2B introduces the **`dd-dispatcher`** cron service. It's a separate Railway service that wakes up every 5 minutes during US market hours, batch-quotes the configured universe (default: PORTFOLIO_TICKERS env), detects ±10% breaches, and POSTs `/admin/dd-trigger` against the existing web service for each breach.

## Architecture

```
                    ┌──────────────────────┐
                    │  Railway Cron        │
                    │  (every 5 min)       │
                    └──────────┬───────────┘
                               │
                               ▼
                   python -m src.agents.dd.cron_dispatcher
                               │
            ┌──────────────────┼─────────────────┐
            ▼                  ▼                 ▼
     universe.build     batch_quote.fetch   POST /admin/dd-trigger
       (env-driven)        (FMP /quote)        (web service)
                                                    │
                                                    ▼
                                       (existing Phase 2A flow:
                                       cooldown → dd_agent →
                                       dd_reports + Slack)
```

The cron service does NOT share the SQLite DB with the web service. All state lives on the web side; cron is pure HTTP client.

## Setup steps (Railway dashboard)

1. **Create a new service** in your Railway project:
   - Click **+ New** → **GitHub Repo** → select the same `ai-hedge-fund` repo
   - Name it `dd-dispatcher` (or similar)

2. **Set the start command** under Settings → **Deploy** → **Custom Start Command**:
   ```
   python -m src.agents.dd.cron_dispatcher
   ```

3. **Set the cron schedule** under Settings → **Deploy** → **Cron Schedule**:
   ```
   */5 * * * *
   ```
   (Every 5 minutes — adjust as needed. Use `*/1 * * * *` for 1-minute cadence once you've verified safe operation.)

4. **Set environment variables** under Variables tab:

   **Required:**
   ```
   DD_DISPATCHER_BASE_URL    https://ai-hedge-fund-production-7131.up.railway.app
   DB_UPLOAD_SECRET          <same value as web service>
   PORTFOLIO_TICKERS         AAPL,MSFT,NVDA,GOOGL,...   (comma-separated)
   FINANCIAL_DATASETS_API_KEY <FMP API key>
   ```

   **Optional (sensible defaults shown):**
   ```
   DD_DISPATCH_THRESHOLD_PCT  0.10        # ±10% trigger
   DD_MAX_ALERTS_PER_TICK     10          # safety valve
   DD_DISPATCH_TIER           tier1_dispatch
   DD_DRY_RUN                 false       # set true to log breaches but skip POST
   DD_INCLUDE_ANALYZED        false       # opt-in: expand universe to last-90d-analyzed
   DD_INCLUDE_SP500           false       # opt-in: expand universe to S&P 500
   DD_FORCE_DISPATCH          false       # opt-in: bypass market-hours gate (testing)
   ```

5. **Verify** by viewing the cron service's logs after the first scheduled tick. You should see one of:
   - `dispatcher: skipping (weekend / pre-market / etc.)` — gate working
   - `dispatcher: no breaches in N quotes at ±10% threshold` — universe scanned cleanly
   - `dispatcher: AAPL FIRED  pct=-12.3%  reason=first_breach  run_id=abc12345` — alert fired

   And a JSON summary line for monitoring/grep:
   ```
   [dd_dispatcher_summary] {"timestamp_utc":"...", "decision":"market open ...",
    "universe_size":15, "breaches_found":1, "alerts_dispatched":1, ...}
   ```

## Cost ceiling

- FMP: ~12 calls/hr × 6.5 trading hours × 20 days = ~1,500/month, well under the 300/min limit
- Qwen: cooldown caps individual tickers at 1 alert/24h. Realistic worst case = ~3-5 alerts/day on a volatile market day = ~$1.50/day = ~$30/month
- Slack webhook posts: free

The `DD_MAX_ALERTS_PER_TICK` safety valve hard-caps a single tick at N alerts even if more breaches are detected (prevents runaway costs on a flash-crash day where dozens of names move ≥10% in the same minute).

## Daily retention

The dispatcher hits `/admin/dd-cleanup` once per UTC day automatically (via a marker file in `/tmp`). This deletes `dd_alerts` + `dd_reports` rows older than 7 days (configurable via the route's `?retention_days=N` query param). The Auto Due-D dashboard shows only **today's** alerts; the 7-day backstop is just the audit window for forensic purposes.

To wipe immediately: `curl -X POST "https://<host>/admin/dd-cleanup?secret=<secret>&retention_days=0"`

## Disabling

To pause auto-fire without removing the service: set `DD_FORCE_DISPATCH=false` (default) AND temporarily change the schedule to something far-future, OR delete the cron schedule from the Settings page. Existing dd_alerts data is preserved.
