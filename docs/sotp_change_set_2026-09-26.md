# SOTP change set (owner, 2026-09-26)

Owner decision, 2026-09-24: "the new approach is we will pre-determine the valuation methods. If SOTP
is found to be best, we will pre-fill business segments with Gemini instead of extractor determined
SOTP." Go given 2026-09-26 with two additions: normalised earnings legs on the China profile, and an
engine-preview check on the review gate.

## What changed

| Piece | Before | After |
|---|---|---|
| Source of the `SOTP (analyst)` leg | pipeline extractor first; owner-accepted Gemini input only when the extractor built nothing | owner-accepted Gemini input only (`canonical_data` through the bridge) |
| Extractor output | the leg's source | held on `most_recent["sotp_assumptions_extractor"]`, graded against the accepted leg, never weighted (`GATE_SOTP_EXTRACTOR_CROSSCHECK`, `applied: False`) |
| Promotion at 3.0 (task #25) | any profile, 75% of the blend whenever assumptions existed | retired; `_promote_sotp_analyst_profile` and `_SOTP_ANALYST_BLEND_WEIGHT` deleted |
| Snapshot attach (task #27) | six frozen extractor outputs attached in pipeline Phase 4.4 | retired; `attach_snapshot` deleted; module kept for `curated_holdco_discount` and segment memory |
| Segment-note promotion | `SOTP (segments)` inserted at 0.40 as anchor for Tencent, Xiaomi, Meituan and the refiners when the filing parsed | retired; Refining & Marketing declares `SOTP (segments)` at 0.40 (anchor stays on mid-cycle EV/EBITDA, other rows x0.6) |
| Owner rule 3 (precedence) | computed at runtime keyed on the accepted origin | declared: Aerospace Holdco (HK) lists `SOTP (analyst)` 0.35 and `shadow_methods: ["SOTP / NAV (look-through)"]` |
| Live gate (`_gate_live_sotp`) | replaced an implausible extraction with the snapshot | flags only ("outside the sell-side reference, review the accepted figures"); a Degraded table is named |
| Holdco pin | pinned every SOTP to the snapshot's curated discount | skipped for an accepted input (the cited, reviewed figure stands) |
| Pre-fill | hand lists per wave | `--profile-sotp`: every pinned ticker whose profile declares `SOTP (analyst)` (12 names today) |
| Review gate | build-time checks only | live `segments price in the engine` check on every SOTP row, naming the Degraded segments |
| Golden replay | read the developer's local review database | every review reads pending inside `pinned_env`; BABA and 09988.HK fixtures re-recorded against a review-cleared archive |

## China Internet Platform (Tech)

| Method | Weight | Anchor |
|---|---|---|
| DCF | 0.30 | yes |
| SOTP (analyst) | 0.35 | |
| EV/EBITDA (norm) | 0.20 | |
| P/E (norm) | 0.15 | |

Excluded: EV/Revenue, EV/NTM Revenue, P/BV, Forward P/E. Pinned: BABA, 09988.HK, PDD, JD, 09618.HK,
03690.HK, 00700.HK. JD moves from Consumer to Tech. Meituan leaves Local Services & Instant Retail.

Why 0.35: the registry's weight for a SOTP that is one method among several (Aerospace & Engineering
(SG), the four SG published-SOTP profiles, the Aerospace Holdco look-through); the sweep of 2026-09-26
at 0.25 / 0.35 / 0.45 moved BABA by $3 and Meituan by HK$1 and could not separate the weights
(`docs/baselines/china_platform_w*.json`). Why normalised legs: Meituan's NTM consensus EPS is 0.19 in the
2026 price war and a raw Forward P/E priced it at HK$3.24.

## Golden baseline

The profile-weights digest in `param_version` moves on every fixture; no valuation leaf moved on the
twelve untouched ones. BABA and 09988.HK were re-recorded on the new profile (with no accepted SOTP,
since the replay owns its review state): BABA 188.07 -> 150.42, 09988.HK 156.06 -> 103.54. The
recording's own live base (151.35 / 119.82) differs from the replay's, as it always has (the previous
BABA recording said 193.13 against a snapshot of 188.07); the suite pins the replay.

## Consequences to watch

- Names that were valued by promotion and have no declared SOTP now lose the leg: Xiaomi (01810.HK)
  loses `SOTP (segments)`; MSFT and AMZN lose the snapshot's analyst SOTP. Each is a profile decision
  for its own wave, not a regression to patch here.
- A profile that declares `SOTP (analyst)` with no accepted input renormalises the 0.35 onto its other
  legs and flags it. PDD (Degraded core) and JD (revoked) are in that state today.
- Production acceptance state is the owner's: BABA and 3690.HK accept, JD revoke, PDD and 00700.HK
  pending (Tencent's preview shows two Degraded segments until the pre-fill carries margins).
