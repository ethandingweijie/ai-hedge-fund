# Owner-set valuation constants

## Why there is a constant at all

`Distributable CF Yield` (midstream) values a name as

```
(operating cash flow − maintenance capex) / shares ÷ target yield
```

The numerator is **distributable** cash flow: net of maintenance capex only,
because growth capex is discretionary and is what the distribution is paid
around. Until 2026-09-20 the denominator was the **peer free cash flow yield**,
which is net of *total* capex. Two different bases.

The mismatch stayed invisible while maintenance capex fell back to D&A, since
`OCF − D&A` is roughly free cash flow, so numerator and denominator happened to
agree. Energy Transfer's audited FY2025 maintenance capex — $1.32bn against
$5.68bn of D&A — ended the coincidence:

| | before | after |
|---|---|---|
| distributable CF / unit | $1.30 | $2.57 |
| target yield applied | 4.93% (peer FCF yield) | 4.93% |
| leg | $26.83 | **$49.04** |
| ET quote / consensus | | $21.14 / $23.00 |

ET's own market distributable yield is 12.1%. Capitalising it at 4.93% priced
the units at 2.3× spot. The accepted figure was right; the benchmark was on the
wrong basis.

## The constant

Stored in `src/data/valuation_constants.json`, read by
`src/data/valuation_constants.py`, one entry per profile:

```json
"Midstream / Pipelines": {
  "target_dcf_yield": 0.0975,
  "benchmark": "AMZI_coverage_adjusted",
  "benchmark_detail": {"proxy_symbol": "AMLP", "index_yield": 0.070,
                       "coverage_factor": 1.4},
  "tolerance_band": [0.085, 0.110],
  "inertia_bps": 50,
  "macro_triggers": {"ten_year_move_bps": 75, "index_yield_move_bps": 100},
  "observed_at_last_review": {"ten_year": 0.0501, "index_yield": 0.070},
  "last_reviewed": "2026-09-20",
  "effective_until": "2026-11-15"
}
```

**A profile with no entry does not price this leg.** `target_dcf_yield` returns
`None` and the method declines rather than reaching for a number from another
basis — the failure this document exists to prevent.

## Where the number comes from

```
target_dcf_yield = benchmark index distribution yield × sector coverage factor
```

* **Index yield** — the Alerian MLP index, using AMLP as the tradable proxy.
  Computed as the trailing four declared distributions over the current price
  rather than a vendor's yield field, so it is reproducible from the rows and a
  changed distribution appears the quarter it is declared.
* **Coverage factor** — midstream operators run distribution coverage of
  roughly 1.4–1.7×. 1.4 is the conservative end.

At 2026-09-20: AMLP 7.40% × 1.40 = **10.36%**, against the stored 9.75%.

## The review clock

**Scheduled:** once per reporting cycle — mid-February, May, August, November.

**Out-of-cycle triggers**, either one sufficient:

* the **10-year Treasury** moves ≥ 75bps from the value recorded at the last
  review (pipeline distributions compete with fixed income);
* the **benchmark index yield** moves ≥ 100bps (structural sector re-rating).

Both are evaluated on every run of the calibration script and reported whether
or not the quarter has ended.

## Two rules between the formula and the stored value

* **Inertia** — a candidate within 50bps of the current constant is not
  applied. Weekly index noise must not leak into a cash-flow anchor.
* **Tolerance band** — a candidate outside `[8.5%, 11.0%]` is clamped **and
  flagged**. Clamping silently would hide the only thing worth knowing: the
  benchmark has left the range the owner authored, so the band needs a
  decision, not the number a rounding.

## Running it

```bash
python scripts/calibrate_valuation_constants.py
```

Proposes only. It prints the feed, the formula, the band, the inertia verdict
and the review triggers, and writes nothing.

```bash
python scripts/calibrate_valuation_constants.py --accept --reviewer owner
```

Records the proposal, resets the review clock and appends to the entry's
`history`. **This is the owner's command.** Calibration proposes; the owner
decides — the same gate `industry_inputs` uses for cited figures. An automated
feed that moved a valuation constant without a decision would not be an
owner-set constant, it would be a tracking error with extra steps.

`DCF_YIELD_<PROFILE>` overrides the constant for one run (what-ifs); the name is
per profile so one override cannot silently move another.

## Known limitation: MLPs and C-corps are not the same

One constant currently serves both structures, and they do not trade alike:

| | market distributable yield | at 9.75% | consensus |
|---|---|---|---|
| ET (MLP) | 12.1% | $26.4 | $23.00 |
| OKE (C-corp) | 8.5% | $81.6 | $97.22 |

The constant lands ET near consensus and leaves OKE ~16% below it. Splitting
`Midstream / Pipelines` into MLP and C-corp variants, each with its own
benchmark and coverage factor, is the natural next step and is an owner
decision, not a code change to be made quietly.
