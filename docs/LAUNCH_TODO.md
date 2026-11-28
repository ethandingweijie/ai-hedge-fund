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
