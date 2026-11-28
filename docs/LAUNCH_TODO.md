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

## Japan (.T) — licensing questions for counsel (questions only, no conclusions; see docs/markets/JAPAN.md)
- [ ] Does selling ratings / 12-month price targets on Tokyo-listed shares to subscribers in Japan require
      registration as an Investment Advisory and Agency Business (投資助言・代理業) under the Financial Instruments and
      Exchange Act (FIEA, 金融商品取引法), or does a publication / impersonal-advice exclusion apply to a
      subscription web product?
- [ ] Does the answer change for subscribers OUTSIDE Japan (SG/HK/US) buying research on Japanese equities: is the
      issuer's listing venue relevant, or only where the customer is?
- [ ] If registration is needed: can an offshore (Singapore-entity) provider register, and what are the capital,
      representative-in-Japan and Kanto Local Finance Bureau filing requirements and the timeline?
- [ ] FIEA advertising rules (広告規制) for the landing, pricing and marketing pages: required risk statements, fee
      disclosures, the prohibition on implying guaranteed returns; is a specific disclosure needed for AI-written reports?
- [ ] Pre-contract / contract-conclusion documents (契約締結前交付書面) and the advisory-contract cooling-off period:
      do they apply to a monthly subscription, and can they be delivered electronically?
- [ ] JSDA analyst-report rules (conflicts of interest, quiet periods, holdings disclosure): do they bind a
      non-member research seller?
- [ ] Re-publishing figures from TDnet 決算短信 PDFs and EDINET filings in a paid report: TDnet/EDINET terms of use,
      attribution, caching of PDFs, copyright in the documents.
- [ ] Data licences: does the FMP plan cover redistributing JPX prices and fundamentals to paying users (JPX
      market-data vendor licensing), and may the stockanalysis.com / S&P consensus target be displayed?
- [ ] APPI (個人情報保護法) for Japan-resident subscribers: cross-border transfer consent to Singapore / US processors,
      the privacy notice.
- [ ] Japanese consumption tax (JCT 10%) on digital services sold to Japan-resident consumers by a foreign provider:
      registration and qualified-invoice obligations.
