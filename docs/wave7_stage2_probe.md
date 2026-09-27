# Wave 7 (Technology and communications): Stage 2 live routing probe and architecture review

Measured 2026-09-27 against FMP `profile` (`get_fmp_classification`, `docs/baselines/w7_probe_labels.json`),
read-only. Routing is exact-case, so **these strings are the only ones a row may be written from**.
`row ->` is what `profile_for_ticker` returns today; `pin ->` is `TICKER_SECTOR_LOOKUP`; a pin with a
non-empty profile beats the row. None of the fourteen labels is in `routing_scope`, so in production every
unpinned name is placed by the ratio ladder.

## 1. Universe probed (49; the ◆ Stage 1 checkpoint decides keep or cut)

| Ticker | FMP industry (exact) | row -> | pin -> | Note |
|---|---|---|---|---|
| NVDA, AVGO, AMD, QCOM | `Semiconductors` | Semiconductor / IDM / Foundry | Fabless | pins carry them |
| MU | `Semiconductors` | IDM / Foundry | Memory / DRAM-NAND | golden fixture |
| ASML, AMAT | `Semiconductors` | IDM / Foundry | Equipment / EDA | FMP labels equipment makers `Semiconductors`; `Semiconductor Equipment & Materials` has no members in the store |
| INTC | `Semiconductors` | IDM / Foundry | IDM / Foundry | |
| TXN, LRCX, KLAC | `Semiconductors` | IDM / Foundry | **(empty)** | TXN is an analog IDM; LRCX, KLAC are equipment on the foundry row |
| AAPL | `Consumer Electronics` | Hyperscaler / Tech Conglomerate | Hyperscaler | golden fixture |
| GOOG, META | `Internet Content & Information` | Mature Platform | Hyperscaler | |
| MSFT, ORCL | `Software - Infrastructure` | Mature SaaS | Hyperscaler | |
| PLTR | `Software - Infrastructure` | Mature SaaS | Growth SaaS | |
| CRWD | `Software - Infrastructure` | Mature SaaS | Cybersecurity / Mission-Critical SaaS | |
| CRM, ADBE, NOW | `Software - Application` | Mature SaaS | Mature SaaS | |
| CSCO | `Communication Equipment` | **none** | **(empty)** | ladder |
| ANET | `Computer Hardware` | Mature Platform | **(empty)** | |
| NFLX | `Entertainment` | **none** | **(empty)** | ladder |
| DIS | `Entertainment` | **none** | **Travel & Dining** | parks pin on a media company |
| T, TMUS | `Telecommunications Services` | Telco / Stable Growth | **(empty)** | |
| VZ | `Telecommunications Services` | Stable Growth | Stable Growth | |
| ACN, IBM | `Information Technology Services` | **none** | **(empty)** | IT Services profile exists, no row |
| 00700.HK | `Internet Content & Information` | Hyperscaler (HK row) | China Internet Platform | |
| 09888.HK, 01024.HK | `Internet Content & Information` | Mature Platform | **(empty)** | Baidu, Kuaishou on a US platform table |
| 09999.HK | `Electronic Gaming & Multimedia` | **none** | **(empty)** | NetEase; ladder |
| 00941.HK, 00728.HK, 00762.HK | `Telecommunications Services` | Stable Growth | **(empty)** | China Mobile, Telecom, Unicom |
| 01810.HK | `Consumer Electronics` | Consumer Electronics / Hardware Ecosystem | same | |
| 00981.HK, 01347.HK | `Semiconductors` | IDM / Foundry | **(empty)** | SMIC, Hua Hong: the right row |
| 00992.HK | `Computer Hardware` | Mature Platform | **(empty)** | Lenovo |
| 02382.HK, 00285.HK | `Hardware, Equipment & Parts` | Mature Platform | **(empty)** | Sunny Optical, BYD Electronic |
| 00020.HK | `Software - Application` | Mature SaaS | **(empty)** | SenseTime, loss-making |
| Z74.SI, CJLU.SI | `Telecommunications Services` | Telco / Infrastructure (SG) | Z74 pinned; CJLU empty | |
| V03.SI, AWX.SI | `Hardware, Equipment & Parts`, `Semiconductors` | Tech Manufacturing / EMS (SG) | same | |
| AIY.SI | `Software - Application` | **WealthTech & Specialty Financials (SG)** | **(empty)** | iFAST: a platform labelled software; SG market map |

## 2. Cohort depth and the current market (2026-09-27 store)

| Label | US n / P/E / EV/EBITDA / NTM P/E | HKSE | SES |
|---|---|---|---|
| `Semiconductors` | 18 / 45.3x / 29.8x / 27.7x | 6 / 50.6x / 34.7x / 48.0x | sector rung |
| `Semiconductor Equipment & Materials` | no members (sector rung 33) | sector rung | sector rung |
| `Software - Infrastructure` | 12 / 36.4x / 24.8x / 41.6x | 6 / 16.8x / 19.5x | sector rung |
| `Software - Application` | 14 / 25.7x / 19.4x / 21.7x | 7 / 50.7x / 29.5x | sector rung |
| `Internet Content & Information` | 15 / 27.1x / 21.1x / 13.1x | 11 / 9.3x / 6.2x / 12.4x | sector rung |
| `Consumer Electronics` | sector rung | 7 / 18.5x / 7.6x | sector rung |
| `Computer Hardware` | 13 / 26.7x / 17.3x / 18.5x | sector rung | sector rung |
| `Hardware, Equipment & Parts` | 17 / 39.3x / 26.9x / 20.6x | 11 / 13.0x / 7.9x | 5 / 20.7x / 11.3x |
| `Communication Equipment` | 14 / 40.5x / 22.3x / 20.2x | 11 / 34.2x / 24.0x | sector rung |
| `Electronic Gaming & Multimedia` | sector rung | 13 / 11.1x / 6.0x | sector rung |
| `Entertainment` | 13 / 48.0x / 16.3x / 17.3x | 5 / 15.3x / 5.6x | sector rung |
| `Telecommunications Services` | 15 / 10.7x / 5.6x / 10.6x | 10 / 10.8x / 4.2x / 10.9x | sector rung |
| `Information Technology Services` | 17 / 14.6x / 9.1x / 12.7x | 11 / 11.9x / 12.7x | sector rung |
| `Advertising Agencies` | 9 / 20.8x / 10.5x / 9.5x | 12 / 15.3x / 9.9x | sector rung |

The SES Technology sector rung (n=11, 25.8x / 12.2x) is what every SG name reads.

## 3. The architecture as it stands

**Profiles.** Tech 15, Semiconductor 5, Telco 2, ProfessionalServices 3. None carries statics in
`SECTOR_PEER_MULTIPLES` or a basket in `SECTOR_PEER_BASKETS`.

**The static tech table.** For any Tech-sector profile named in `_TECH_SUBTYPE_MULTIPLES` the EV/EBITDA,
EV/EBIT and P/S legs read that table instead of the live cohort (`dcf_agent.py` 6078, 6514, 6535). The
table is a 2024 snapshot: Hyperscaler 20x EV/EBITDA / 28x P/E / 7.5x EV/Revenue, Mature Platform 18x /
24x, Mature SaaS 22x / 28x / 10x, Growth SaaS 45x / 65x / 22x, Cybersecurity 55x / 70x, Hyper-Growth 55x
/ 80x, High-Growth AI 65x / 100x, Early Platform 25x / 35x / 4x, Levered Subscription 12x / 18x. The
same table is the terminal multiple every growth profile converges to (`_TERMINAL_MULTIPLE_CONVERGENCE`:
Growth SaaS, Hyper-Growth, High-Growth AI and Cybersecurity all mature into "Mature SaaS" at 10x
revenue). The live US `Software - Infrastructure` cohort is 24.8x EV/EBITDA / 36.4x P/E today, so the
static table is a basis nobody re-derived, the same finding as the bank calibration in Wave 6.

**Semiconductors** are not a tech sub-type and price on the live cohort; the five profiles differ by
weights only (Fabless P/E .35 anchor; IDM / Foundry EV/EBITDA .35; Memory P/E (norm) .45, cyclical;
Equipment / EDA P/E .35; OSAT EV/EBITDA .40). FMP labels equipment makers `Semiconductors`, so the
equipment cohort is empty and ASML, AMAT, LRCX, KLAC price on the whole-semiconductor median (45x
trailing, 27.7x NTM) unless pinned.

**Telco.** `Stable Growth` (EV/EBITDA .35, DDM .25, DCF (2-stage) .25, EPV .15) is a generic table for
T, VZ, TMUS, China Mobile, Telecom, Unicom; the SG names have a calibrated `Telco / Infrastructure (SG)`.
The US telecom cohort is 5.6x EV/EBITDA / 10.7x; HK 4.2x / 10.8x.

**Communication and media** have no profile at all: `Entertainment` (NFLX, DIS), `Electronic Gaming &
Multimedia` (NetEase), `Advertising Agencies`, `Communication Equipment` (CSCO, ANET by `Computer
Hardware`), `Information Technology Services` (ACN, IBM: the IT Services profile exists with no row).

**What the pins hide.** The US mega-caps are pinned (Hyperscaler for GOOG, MSFT, META, ORCL, AAPL;
Fabless for NVDA, AVGO, AMD, QCOM) so Stage 0 will look right on them and wrong on everything unpinned:
CSCO, NFLX, ACN, IBM, ANET, TXN, LRCX, KLAC, T, TMUS and every HK name outside Tencent and Xiaomi.

**Growth machinery.** `_HIGH_SBC_PROFILES` widen the terminal band (bear 0.70x, bull 1.15x); tech names
with SBC over 10% of revenue take a 10% EV-multiple haircut; the owner's CRWD rule keeps full SBC in
the DCF with no fade. `Memory / DRAM-NAND` is the one cyclical profile in the wave.

## 4. What Stage 1 (universe, ◆ owner) has to settle

1. Keep or cut: DIS (media conglomerate on a parks pin), AIY.SI (iFAST, a Financials platform under a
   software label), 00020.HK (SenseTime, loss-making), CJLU.SI (a fibre trust).
2. Whether the static tech table is re-derived from the live cohorts (the Wave 6 pattern) or retired in
   favour of cohort multiples with profile statics as the fallback.
3. New profiles the labels need: Telecom Carrier (US and China; Stable Growth is generic), Media &
   Streaming (NFLX, DIS, NetEase), Networking & Communication Equipment (CSCO, ANET), IT Services row
   (ACN, IBM on the existing profile), Analog / Mixed-signal IDM (TXN) and a Semiconductor Equipment
   pin set (ASML, AMAT, LRCX, KLAC) since the label cannot separate them.
4. Whether the HK internet names outside the China Internet Platform pins (Baidu, Kuaishou, NetEase)
   join that profile or take a China Media & Gaming table.
