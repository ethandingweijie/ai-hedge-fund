# Dynamic multiples: handoff plan

Written 2026-09-21 for continuing this work from another account. Everything
needed is in the repo; nothing depends on a previous session's scratch files.

---

## 1. Where things stand

### Live in production (myfork/main `75b797f`, deployed and healthy)

| Piece | What it does | Code |
|---|---|---|
| Comps history | Every weekly comps refresh appends its medians to `regional_comps_history` (source `refresh`), instead of only overwriting today's. | `src/data/regional_comps.py` |
| Quarterly backfill | Rebuilds the **through-cycle** multiples from annual filings, for US, HKSE and SES. Runs on the 2nd of Jan/Apr/Jul/Oct at 05:00 UTC, with a 4-hour timeout and an 80-day gate. | `src/data/comps_history_backfill.py`, `app/backend/worker.py`, `app/backend/scheduler_service.py` |
| Dynamic multiples engine | A through-cycle EV/EBITDA and P/E for **every** comps basket: US/HK/SG, industry and sector. | `src/data/dynamic_multiples.py` |
| Quarterly auto-update | Runs straight after the backfill and auto-applies within guardrails. | same |
| Valuation wiring | The EV/EBITDA (norm) and P/E (norm) legs price on the basket's dynamic multiple. | `src/agents/analysis/dcf_agent.py` (`_dynamic_norm_multiple`) |
| Model Accuracy log | Shows three confirmations: the update ran, it reached every industry, and it reached valuations. Includes pin/unpin. | `app/backend/routes/model_accuracy.py`, `app/frontend/src/pages/ModelAccuracyPage.tsx` |
| Energy SOTP multiples | Owner-set segment multiples: refining 4.50x, midstream 14.1x, chemicals 5.2x. Each band carries a written rationale. | `src/data/valuation_constants.json`, `dynamic_multiples.SEGMENT_BASELINES` |

### Not yet effective

**Production's `dynamic_multiples` table is empty.** The code is deployed, but
until the table is populated no valuation moves, and the Model Accuracy log
shows no run. This was deliberate. A defect was found in how the through-cycle
multiple was built (§3.1) and fixed in `75b797f`, and all history must be
rebuilt on the corrected basis before the table is filled.

---

## 2. Owner decisions already taken (do not re-ask)

| Decision | Choice |
|---|---|
| Scope | Dynamic multiples across **all industries**, not only energy |
| Automation | **Auto-apply within guardrails.** Band is ±20% of the basket's own through-cycle average; moves under 5% are held as noise; every clamp is flagged; owner-pinned baskets are never touched; kill switches exist |
| Where applied | **Normalised legs only**: EV/EBITDA (norm) and P/E (norm). Forward legs keep the live peer multiple. The bank P/E (norm) leg keeps its owner-calibrated P/E |
| Rollout | **Switch on straight away.** No shadow period |
| Quarterly backfill | Yes, quarterly (weekly would repeat unchanged work at about 3,900 FMP calls a run) |
| Log | The update must be logged on Model Accuracy, confirming it ran, reached the engine for every industry, and reached valuations |
| Energy starting points | Refining 4.50x (crack-rule trough), midstream 14.1x, chemicals 5.2x |
| Band rationale | Every band states why it sits where it does, on the report and the PDF |
| No silent multiples | Every multiple carries its derivation (standing rule) |

---

## 3. What was found along the way (context for the next steps)

### 3.1 The through-cycle multiple was on the wrong basis (fixed in `75b797f`)

The first version defined it as `EV / mean EBITDA level over the window`. The
normalised legs don't use a mean level: `_normalized_earnings` uses **mean
margin × current revenue**. For a growing company the mean level sits far below
current earnings, so the multiple was inflated and then applied to earnings that
were not deflated. That overvalued every grower. Visa's P/E (norm) went from
15.7x to 20.5x on that artefact alone.

It is now `EV_t / (mean margin × revenue_t)`, the leg's own basis, so growth
cancels out. An invariant test pins it: constant margin, doubling revenue and a
constant 10x must give 10x in every year.

Spot checks on the corrected basis (trailing EV/EBITDA against through-cycle):
- V: 25.7x against 24.3x
- NVDA: 33.8x against 43.6x (margin far above its own average)
- VLO: 8.5x against 6.6x

### 3.2 The rate proxy

There is no FRED key in either environment. The real rate is the 10y Treasury
minus FMP's `inflationRate`, which turns out to be the 10y breakeven: on
2021-01-04 it gives −1.08% against FRED DFII10's −1.07%. HK and SG use the US
real rate as a proxy, and each proposal flags that.

### 3.3 The betas are prior-dominated

FMP key-metrics returns five annual rows per company on this plan, so each
basket has about five points. Each beta is shrunk to a prior with weight
n/(n+10), roughly one third. Collinear rate/ROIC pairs fall back to the prior,
and a positive rate slope is clipped to zero (the spec says rates move
multiples inversely). The leave-one-out backtest does **not** yet favour the
betas: refining 11.7% static vs 12.3% dynamic, midstream 28.6% vs 39.5%. They
earn their keep as the weekly history accumulates. Today the work is done by
the band anchoring, the crack rule and the flags.

---

## 4. Finish the rollout (in order)

### Step A: rebuild history on the corrected basis

A rebuild into the **local** store was running on the old machine: US done
(6,975 medians), HK in progress, SG pending. Local sqlite does not travel with
an account.

**Recommended: rebuild straight into production.** A re-run replaces only its
own `backfill` rows and never touches a measured `refresh` row.

```bash
# production URL: ~/.railway_pg_url holds the INTERNAL host; swap in the proxy
#   postgresql://...@tokaido.proxy.rlwy.net:25751/<db>
DATABASE_URL=<prod proxy url> DYNAMIC_MULTIPLES_AUTO_DISABLED=true \
  python scripts/backfill_comps_history.py --exchange US --commit     # ~70 min
DATABASE_URL=<prod proxy url> DYNAMIC_MULTIPLES_AUTO_DISABLED=true \
  python scripts/backfill_comps_history.py --exchange HKSE --commit   # ~50 min
DATABASE_URL=<prod proxy url> DYNAMIC_MULTIPLES_AUTO_DISABLED=true \
  python scripts/backfill_comps_history.py --exchange SES --commit    # ~5 min
```

Check afterwards: `regional_comps_history` should hold about 7,000 US rows,
about 4,500 HKSE and about 400 SES, all with `source='backfill'`.

### Step B: re-read the energy starting points on the corrected basis

Midstream 14.1x and chemicals 5.2x were read off the **old** basis (§3.1).
Before populating, run:

```bash
DATABASE_URL=<prod proxy url> python scripts/calibrate_dynamic_multiples.py --backtest
```

Show the owner the corrected "market through-cycle multiple now" for midstream,
chemicals and refining. If they differ materially from 14.1x / 5.2x, ask whether
to re-anchor. **Do not change owner-set values without asking.** They sit in
`src/data/valuation_constants.json` (`segment_multiples`) and in
`dynamic_multiples.SEGMENT_BASELINES`, and the bands must hold the value
(`accept_value` refuses a value outside its band).

### Step C: populate production and switch it on

```bash
DATABASE_URL=<prod proxy url> python scripts/run_dynamic_multiples_update.py            # dry run
DATABASE_URL=<prod proxy url> python scripts/run_dynamic_multiples_update.py --commit \
  --trigger "initial (owner: switch on straight away)"
```

Expect about 226 US, 172 HK and 18 SG multiples. That count is per basket per
field, because most baskets carry both EV/EBITDA and P/E.

### Step D: measure the impact and verify the log

```bash
python scripts/measure_dynamic_multiples_impact.py AAPL MSFT NVDA COST V MU LMT JPM VLO PSX 00700.HK D05.SI
```

This runs locally with the feature off and on. It needs the local store
populated too, or `DATABASE_URL` pointed at production (read-only use).
Then open Model Accuracy → **Dynamic multiples** and confirm:
1. **Update ran**: one run, with a new / updated / held count.
2. **In the engine, every industry**: per market, the number of multiples live
   against the number of baskets with history.
3. **Reached valuations**: this starts at zero and rises as production runs
   price on the multiples. Re-run a few affected names, for example VLO, PSX,
   MU and V, to see it move.

On the old (wrong) basis the measured impact was: VLO −14.7%, PSX −13.4%,
MU −3.0%, V +7.3%, and zero for AAPL, MSFT, NVDA, COST, LMT, Tencent, JPM and
DBS. **Re-measure.** The V figure was mostly the §3.1 artefact.

---

## 5. Decision waiting on the owner: coverage

> **Only 32 of 104 valuation profiles (31%) have a normalised leg.**

The dynamic multiple is live for **every** industry, but it can only change a
valuation through a normalised leg, EV/EBITDA (norm) or P/E (norm). The owner
chose that placement deliberately. Most profiles don't have such a leg:

| Sector | Profiles with a normalised leg |
|---|---|
| Tech | 1 of 14 |
| Consumer | 1 of 13 |
| Industrials | 0 of 7 |
| Healthcare (incl. services, biopharma) | 0 of 11 |
| Semiconductor | 1 of 5 |
| Resources | 4 of 4 |
| Financials | 20 of 24 (banks use the calibrated P/E, so they're untouched either way) |

So AAPL, MSFT, NVDA, COST, LMT and Tencent **won't move at all**. Their
profiles value them on DCF, forward multiples and other legs, not normalised
ones. In practice the multiple reaches **cyclicals, resources and non-bank
financials**.

**Closing the gap means adding a normalised leg to the profiles that lack one.**
That is a method-weight change, and a real decision rather than a formality:

- Every affected profile's weights shift to make room, so the owner must pick
  the weight. 10–20% is a reasonable starting range.
- It is not valuation-neutral for growth names. A normalised leg values
  **mean margin × current revenue**. For a company whose margins have expanded
  (NVDA, most of mega-cap tech), mean margin sits below today's, so the leg
  comes in below the forward legs and pulls IV **down**. For a company whose
  margins are depressed, it pulls IV up.
- The alternative of putting the dynamic multiple into the forward or TTM legs
  is **not** recommended. It pairs a through-cycle multiple with current or
  next-year earnings, the same basis mismatch this whole piece of work was
  built to remove.

**Options to put to the owner:**
1. **Leave it** (current state): the multiple reaches cyclicals, resources and
   non-bank financials only.
2. **Add a normalised leg at a small weight (10–20%) to every profile that
   lacks one.** Every industry then feeds valuations, and each profile's
   through-cycle anchor becomes explicit. Measure the IV shift per profile
   first; growth names will move down.
3. **Add it only to selected sectors** (for example Industrials, Consumer and
   Healthcare, not high-growth Tech), where mean-reversion is the right
   assumption.

The recommendation is **option 3, measured first**. Mean reversion is the
premise of a normalised leg, and it fits industrial, consumer and healthcare
businesses better than structural-growth tech.

---

## 6. Other open items

| Item | Status / what's needed |
|---|---|
| **Patch pass 3** (VLO, KMI, COP stored runs) | The old weighted 12m target (349.22 / 32.40 / 136.13) is still quoted in `research_view`, `decision_inputs`, some prose and a reconciliation flag. Script: `scripts/patch_prod_weighted_pt.py` (dry run first, then `--commit`). It rewrites only named target fields plus text inside the decision/scenario blocks, and never market data (KMI has a real historical close of exactly 32.40). The auto-mode classifier blocked this production write before, so it needs the owner's explicit go-ahead. |
| Fuel marketing / renewable fuels / ethanol | Still at static band highs (8x / 8x / 6x) while refining sits at trough 4.5x. The engine proposed 6.51 / 6.51 / 4.65. Owner to decide. |
| Bank P/E (norm) | Uses the owner-calibrated P/E, not the dynamic multiple. Ask whether banks should join. |
| MLP vs C-corp midstream | One distributable-CF yield constant (9.75%) serves both. ET's market yield is 12.1%, OKE's 8.5%. |
| CNOOC PV-10 | $114.8bn verified twice against the owner's $60–75bn. Needs adjudication. |
| HK/SG share count | The current-share-count correction reaches only names with a live quote; HK and SG fetch none. |
| First automated quarter | Today's backfill counts for the quarter under the 80-day gate, so the **2 Oct 2026** fire will skip. The first automatic update is **2 Jan 2027**. Run Steps A–C manually now. |
| Known failing test | `tests/test_screener_intl.py::TestCacheKeyVersionFallback::test_previous_version_serves_when_the_new_key_is_empty`. Pre-existing and unrelated. |

---

## 7. Working notes (things that bit this session)

- **Push only to `myfork`, never `origin`.** Stage explicit paths, never
  `git add -A`: another session shares the tree. Don't commit
  `regime_state.json`, `trade_log.json` or the `.cache/` directory.
- **Production DB:** `~/.railway_pg_url` holds the internal host; replace it
  with `tokaido.proxy.rlwy.net:25751`. A literal `%` in SQL breaks psycopg, so
  use parameters.
- **Railway CLI** needs `-p 050389fd-bcb9-490c-899d-e6a32214ab3e -e 51c55952-3eaa-4827-a1ed-308b771e8a25`.
  Services: `ai-hedge-fund` (web), `worker`, `gleaming-rejoicing` (scheduler).
- **Shell heredocs mangle backslash escapes** (`\n`, line continuations) in
  this environment. Write patch scripts with a file-writing tool and run the
  file.
- **Tests must never write to the real database.** A backfill test once ran the
  chained multiple update for real. `tests/test_comps_history_backfill.py` now
  switches the update off by default.
- **Golden replay pins `DYNAMIC_MULTIPLES_ENABLED=false`.** A table that moves
  every quarter cannot sit under a fixed baseline. The live path is covered by
  `tests/test_dynamic_multiples_industry.py`.
- **Never hard-delete production data.** Supply scripts for the owner to run.
- Switches:
  - `DYNAMIC_MULTIPLES_ENABLED=false`: valuations ignore the table.
  - `DYNAMIC_MULTIPLES_AUTO_DISABLED=true`: the quarterly update is skipped.
  - `COMPS_HISTORY_BACKFILL_DISABLED=true`: the backfill is skipped.
  - `COMPS_HISTORY_MARKETS=US,HKSE,SES`: which markets the backfill covers.

## 8. Related docs

- `docs/valuation_constants.md`: owner-set constants and the quarterly
  calibration governance (the midstream distributable-CF yield).
- `~/.claude/plans/dynamic-multiples-engine.md` and
  `~/.claude/plans/can-we-try-1-eager-dijkstra.md`: the original plans, on the
  old machine only. This document supersedes them for the handoff.
