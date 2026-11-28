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

## Korea (KOSPI / KOSDAQ) -- questions for counsel before selling research on Korean equities
Questions only; no legal conclusion is implied. Engineering status: docs/markets/KOREA.md.
- [ ] Does a paid subscription delivering ratings, 12-month price targets and intrinsic values on KRX-listed shares to
      Korean residents fall under "investment advisory business" (투자자문업) or the reported "similar investment
      advisory business" (유사투자자문업) under the Financial Investment Services and Capital Markets Act (FSCMA), and
      what registration or report with the FSC / FSS would that require?
- [ ] Does it matter that the service is offered from Singapore, in English, over the internet, with no Korean-language
      marketing? Is there a cross-border / offshore exemption, and does it depend on the customer type (retail vs
      professional investor)?
- [ ] If Korean residents are to be excluded, what counts as a sufficient exclusion (sign-up country check, IP block,
      terms)?
- [ ] Does selling research on Korean listings to customers OUTSIDE Korea (Singapore, Hong Kong, US) raise any Korean
      requirement at all, or only the customer's home-jurisdiction rules already on the list above?
- [ ] Research-report rules: are there disclosure duties (conflicts, holdings, rating definitions, target-price
      history) for published research on Korean listings, e.g. under FSCMA or KOFIA rules, that apply to a non-broker?
- [ ] Data licensing: may KRX prices (via FMP), DART filings (OpenAPI terms of use) and S&P Global consensus figures
      (via stockanalysis.com) be redistributed inside a paid report? Does KRX require a market-data vendor licence to
      display delayed prices?
- [ ] Unfair-trading rules (FSCMA Arts. 174-178): any exposure from publishing price targets on thinly traded KOSDAQ
      names, or from the owner or staff holding names the service rates?
- [ ] Personal Information Protection Act (PIPA): obligations if Korean residents sign up (domestic representative,
      cross-border transfer consent).
- [ ] Korean VAT on electronically supplied services sold by a foreign provider to Korean consumers: registration
      threshold and how Stripe pricing should show it.
