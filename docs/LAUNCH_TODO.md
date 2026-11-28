# Commercial launch — to-do

Owner decisions and inputs still needed before charging customers (status 2026-11-28).

## Owner inputs
- [ ] **Company registered name** (exactly as on ACRA BizFile) → `app/frontend/src/lib/legal.ts` `LEGAL.entity`
- [ ] **UEN** → `LEGAL.uen`
- [ ] **Privacy / refund contact email** (PDPA Data Protection Officer contact; shown publicly) → `LEGAL.email`
- [ ] Lawyer review of Terms / Privacy / Refunds and of the licensing position for selling ratings and price targets
      in Singapore (Financial Advisers Act), Hong Kong and the US
- [ ] Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_{STARTER,PROFESSIONAL}_{MONTHLY,ADDON}`,
      `APP_BASE_URL`, webhook endpoint → then a test-mode payment → then `BILLING_ENFORCED=true` (billing goes last)
- [ ] D4 decisions still open: (b) moat ROIC without goodwill, (c) growth-fade base rates

## Engineering (in progress)
- [x] D1–D8, blocker 1, D4(d) AAPL Shareholder Yield
- [x] Item 6: admin secret header-only, API docs off
- [x] Item 1: legal pages, account deletion, research notice (entity details above still to fill)
- [ ] Item 3: shared self-tuning Qwen limiter, global admission + fair queue, batch priority, diagnostics, load test
- [ ] Item 4: extractor evaluation set
- [ ] Item 5: pre-launch sweep + profile pin audit (top ~500 US / 100 HK / 50 SG) on the batch queue
