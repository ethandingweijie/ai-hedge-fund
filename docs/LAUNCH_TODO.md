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
- [ ] China A-shares (docs/markets/CHINA.md): licensing questions for the lawyer -- questions only, no conclusions:
  - Does publishing ratings / 12-month targets on SSE/SZSE companies to subscribers OUTSIDE mainland China need any
    PRC licence or registration (CSRC securities investment consulting / 证券投资咨询 qualification), or only the
    licences of where the subscriber and the company (Singapore entity) are?
  - Can subscribers located IN mainland China be served at all, and does that change if the product is paid, in
    Chinese, or marketed there? Should the app geo-block mainland users or show a jurisdiction notice?
  - Singapore FAA: does analysis of foreign-listed (A-share) securities fall under the same "advising on
    investment products / issuing research analyses" treatment as SG/HK/US names; does a research-only exemption
    or the accredited-investor route apply?
  - Cross-border data: do the PRC Data Security Law / PIPL / rules on financial or "important" data restrict
    collecting and republishing cninfo, SSE/SZSE disclosures or Chinese-language press at scale, or storing
    mainland users' data in Singapore/US?
  - Reproduction rights: may report PDFs quote or link cninfo / exchange announcements and Chinese financial press
    (Caixin, Securities Times), and may stockanalysis.com (S&P Global) consensus targets be shown to paying users?
  - Market data licensing: does showing SSE/SZSE prices (FMP feed) to paying subscribers require an exchange
    (SSE Infonet / SZSE Information) display licence, and is the FMP plan's redistribution right sufficient?
  - Disclosures: is a specific risk statement needed for A-shares (capital controls, Stock Connect eligibility, A/H
    price difference, daily price limits, state ownership)?

## Engineering (in progress)
- [x] D1–D8, blocker 1, D4(d) AAPL Shareholder Yield
- [x] Item 6: admin secret header-only, API docs off
- [x] Item 1: legal pages, account deletion, research notice (entity details above still to fill)
- [ ] Item 3: shared self-tuning Qwen limiter, global admission + fair queue, batch priority, diagnostics, load test
- [ ] Item 4: extractor evaluation set
- [ ] Item 5: pre-launch sweep + profile pin audit (top ~500 US / 100 HK / 50 SG) on the batch queue
