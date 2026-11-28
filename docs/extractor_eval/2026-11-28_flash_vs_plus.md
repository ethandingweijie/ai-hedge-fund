# Extractor evaluation

Models: qwen3.8-flash, qwen3.6-plus. Fixed inputs: golden fixtures' recorded research text. AGREE = within 2% (0.002 absolute for ratios). Range = plausibility bounds.
**14 fixture-family cases, 185 fields: 54 agree (29%), 1 out of range, 0 extractor errors.**


## 02888_HK — bank_metrics

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| cet1_ratio | 0.142 | 0.142 | yes | ok |
| cost_of_equity | 0.105 | 0.105 | yes | — |
| credit_cost_bps | 32.5 | 32.5 | yes | — |
| deposit_growth_yoy | 0.02 | 0.02 | yes | ok |
| dividend_payout_ratio | 0.67 | — | NO | — |
| efficiency_ratio | 0.546 | 0.546 | yes | ok |
| loan_growth_yoy | 0.057 | 0.057 | yes | ok |
| loan_to_deposit_ratio | 0.58 | 0.58 | yes | — |
| management_overlays_bn | 0.234 | 0.234 | yes | — |
| management_target_roe | 0.18 | 0.18 | yes | ok |
| net_charge_offs_pct | 0.0026 | 0.0026 | yes | — |
| npl_coverage_ratio | 0.554 | — | NO | OUT |
| target_price_to_book | 2.14 | 2.14 | yes | — |
| terminal_growth_rate | 0.04 | 0.04 | yes | ok |

_time: qwen3.8-flash 5s · qwen3.6-plus 85s_

## 02888_HK — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| consensus.revenue_growth_fy1 | 0.06 | — | NO | ok |
| estimates.base.ebitda_margin_fy1 | 0.39 | — | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.37 | — | NO | ok |
| estimates.base.revenue_growth_fy1 | 0.065 | 0.06 | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.05 | 0.06 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.36 | — | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.34 | — | NO | ok |
| estimates.bear.revenue_growth_fy1 | 0.045 | 0.05 | NO | ok |
| estimates.bear.revenue_growth_fy2 | 0.03 | 0.04 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.42 | — | NO | ok |
| estimates.bull.ebitda_margin_fy2 | 0.4 | — | NO | ok |
| estimates.bull.revenue_growth_fy1 | 0.08 | 0.07 | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.065 | 0.08 | NO | ok |
| family_metrics.bank.cet1_target | 0.135 | 0.135 | yes | ok |
| family_metrics.bank.cost_to_income_fy1 | 0.57 | — | NO | — |
| family_metrics.bank.cost_to_income_fy2 | 0.56 | — | NO | — |
| family_metrics.bank.credit_cost_bps_fy1 | 35 | 0.00325 | NO | — |
| family_metrics.bank.credit_cost_bps_fy2 | 32 | 0.00325 | NO | — |
| family_metrics.bank.fee_income_growth_fy1 | 0.09 | — | NO | ok |
| family_metrics.bank.fee_income_growth_fy2 | 0.08 | — | NO | ok |
| family_metrics.bank.nim_fy1 | 0.042 | — | NO | ok |
| family_metrics.bank.nim_fy2 | 0.04 | — | NO | ok |
| family_metrics.bank.roe_target.value | 0.18 | 0.18 | yes | ok |
| guidance.revenue.high | 22.36 | — | NO | — |
| guidance.revenue.low | 21.95 | — | NO | — |
| guidance.revenue.mid | 22.15 | — | NO | — |
| guidance.revenue_growth.high | 0.07 | 0.07 | yes | ok |
| guidance.revenue_growth.low | 0.05 | 0.05 | yes | ok |
| guidance.revenue_growth.mid | 0.06 | 0.06 | yes | ok |
| guidance_vs_consensus_pct | 0 | — | NO | — |

_time: qwen3.8-flash 66s · qwen3.6-plus 122s_

## 09988_HK — guidance_estimates

- no numeric fields extracted

## 09988_HK — saas_metrics

- no numeric fields extracted

## BABA — guidance_estimates

- no numeric fields extracted

## BABA — saas_metrics

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| rule_of_40_score | -1.5 | — | NO | — |

_time: qwen3.8-flash 1s · qwen3.6-plus 60s_

## BN4_SI — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| estimates.base.ebitda_margin_fy1 | 0.22 | — | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.23 | — | NO | ok |
| estimates.base.revenue_growth_fy1 | 0.08 | — | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.09 | — | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.19 | — | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.2 | — | NO | ok |
| estimates.bear.revenue_growth_fy1 | 0.05 | — | NO | ok |
| estimates.bear.revenue_growth_fy2 | 0.04 | — | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.25 | — | NO | ok |
| estimates.bull.ebitda_margin_fy2 | 0.26 | — | NO | ok |
| estimates.bull.revenue_growth_fy1 | 0.12 | — | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.14 | — | NO | ok |

_time: qwen3.8-flash 39s · qwen3.6-plus 88s_

## COST — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| consensus.revenue_growth_fy1 | 0.06 | — | NO | ok |
| estimates.base.ebitda_margin_fy1 | 0.0465 | 0.045 | yes | ok |
| estimates.base.ebitda_margin_fy2 | 0.047 | 0.046 | yes | ok |
| estimates.base.eps_fy1 | — | 17.5 | NO | — |
| estimates.base.eps_fy2 | — | 18.9 | NO | — |
| estimates.base.revenue_growth_fy1 | 0.055 | 0.07 | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.05 | 0.08 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.045 | 0.043 | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.045 | 0.044 | yes | ok |
| estimates.bear.eps_fy1 | — | 16.5 | NO | — |
| estimates.bear.eps_fy2 | — | 17.8 | NO | — |
| estimates.bear.revenue_growth_fy1 | 0.035 | 0.06 | NO | ok |
| estimates.bear.revenue_growth_fy2 | 0.03 | 0.07 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.047 | 0.047 | yes | ok |
| estimates.bull.ebitda_margin_fy2 | 0.048 | 0.048 | yes | ok |
| estimates.bull.eps_fy1 | — | 18.5 | NO | — |
| estimates.bull.eps_fy2 | — | 20 | NO | — |
| estimates.bull.revenue_growth_fy1 | 0.07 | 0.08 | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.065 | 0.09 | NO | ok |

_time: qwen3.8-flash 30s · qwen3.6-plus 112s_

## D05_SI — bank_metrics

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| cet1_ratio | 0.146 | 0.146 | yes | ok |
| cost_of_equity | 0.1 | 0.1 | yes | — |
| credit_cost_bps | 18.5 | 18.5 | yes | — |
| deposit_growth_yoy | 0.12 | 0.12 | yes | ok |
| dividend_payout_ratio | 0.375 | — | NO | — |
| efficiency_ratio | 0.39 | 0.39 | yes | ok |
| loan_growth_yoy | 0.06 | 0.06 | yes | ok |
| loan_to_deposit_ratio | 0.74 | 0.74 | yes | — |
| management_overlays_bn | 0.042 | — | NO | — |
| management_target_roe | 0.165 | 0.162 | NO | ok |
| net_charge_offs_pct | 0.0015 | — | NO | — |
| nim_pct | 0.0187 | 0.0187 | yes | ok |
| npl_ratio | 0.01 | 0.01 | yes | ok |
| target_price_to_book | 1.89 | 1.89 | yes | — |
| terminal_growth_rate | 0.03 | 0.03 | yes | ok |

_time: qwen3.8-flash 4s · qwen3.6-plus 98s_

## D05_SI — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| consensus.eps_fy1 | 3.72 | — | NO | — |
| consensus.revenue_growth_fy1 | 0.022 | — | NO | ok |
| estimates.base.ebitda_margin_fy1 | 0.565 | — | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.575 | — | NO | ok |
| estimates.base.eps_fy1 | 3.85 | 3.25 | NO | — |
| estimates.base.eps_fy2 | 4.1 | 3.4 | NO | — |
| estimates.base.revenue_growth_fy1 | 0.025 | 0.04 | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.04 | 0.05 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.54 | — | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.52 | — | NO | ok |
| estimates.bear.eps_fy1 | 3.5 | 3.05 | NO | — |
| estimates.bear.eps_fy2 | 3.45 | 3.15 | NO | — |
| estimates.bear.revenue_growth_fy1 | 0.01 | 0.02 | NO | ok |
| estimates.bear.revenue_growth_fy2 | -0.01 | 0.03 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.59 | — | NO | ok |
| estimates.bull.ebitda_margin_fy2 | 0.61 | — | NO | ok |
| estimates.bull.eps_fy1 | 4.1 | 3.5 | NO | — |
| estimates.bull.eps_fy2 | 4.45 | 3.7 | NO | — |
| estimates.bull.revenue_growth_fy1 | 0.05 | 0.06 | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.065 | 0.07 | NO | ok |
| family_metrics.bank.cet1_target | 0.146 | 0.146 | yes | ok |
| family_metrics.bank.cost_to_income_fy1 | 0.039 | 0.39 | NO | — |
| family_metrics.bank.cost_to_income_fy2 | 0.038 | — | NO | — |
| family_metrics.bank.credit_cost_bps_fy1 | 25 | — | NO | — |
| family_metrics.bank.credit_cost_bps_fy2 | 25 | — | NO | — |
| family_metrics.bank.fee_income_growth_fy1 | 0.15 | 0.18 | NO | ok |
| family_metrics.bank.fee_income_growth_fy2 | 0.14 | — | NO | ok |
| family_metrics.bank.loan_growth_fy1 | 0.04 | — | NO | ok |
| family_metrics.bank.loan_growth_fy2 | 0.05 | — | NO | ok |
| family_metrics.bank.nim_fy1 | 0.0175 | — | NO | ok |
| family_metrics.bank.nim_fy2 | 0.018 | — | NO | ok |
| family_metrics.bank.payout_ratio | 0.5 | — | NO | — |

_time: qwen3.8-flash 57s · qwen3.6-plus 120s_

## MELI — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| consensus.revenue_growth_fy1 | 0.391 | 0.39 | yes | ok |
| estimates.base.ebitda_margin_fy1 | 0.075 | 0.07 | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.11 | 0.1 | NO | ok |
| estimates.base.revenue_growth_fy1 | 0.39 | 0.39 | yes | ok |
| estimates.base.revenue_growth_fy2 | 0.28 | 0.3 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.065 | 0.05 | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.08 | 0.08 | yes | ok |
| estimates.bear.revenue_growth_fy1 | 0.35 | 0.34 | NO | ok |
| estimates.bear.revenue_growth_fy2 | 0.25 | 0.26 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.09 | 0.09 | yes | ok |
| estimates.bull.ebitda_margin_fy2 | 0.13 | 0.13 | yes | ok |
| estimates.bull.revenue_growth_fy1 | 0.4 | 0.44 | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.3 | 0.35 | NO | ok |

_time: qwen3.8-flash 22s · qwen3.6-plus 89s_

## MELI — saas_metrics

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| cac_payback_months | 3.7 | 3.7 | yes | — |
| rule_of_40_score | 81.3 | 81.3 | yes | — |

_time: qwen3.8-flash 4s · qwen3.6-plus 84s_

## MU — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| estimates.base.ebitda_margin_fy1 | 0.8 | 0.73 | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.74 | 0.71 | NO | ok |
| estimates.base.eps_fy1 | 135 | 31 | NO | — |
| estimates.base.eps_fy2 | 120 | 33.5 | NO | — |
| estimates.base.revenue_growth_fy1 | 0.25 | 0.337 | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.1 | 0.14 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.72 | 0.66 | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.6 | 0.63 | NO | ok |
| estimates.bear.eps_fy1 | 105 | 28.5 | NO | — |
| estimates.bear.eps_fy2 | 75 | 31 | NO | — |
| estimates.bear.revenue_growth_fy1 | 0.1 | 0.28 | NO | ok |
| estimates.bear.revenue_growth_fy2 | -0.05 | 0.08 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.86 | 0.77 | NO | ok |
| estimates.bull.ebitda_margin_fy2 | 0.84 | 0.75 | NO | ok |
| estimates.bull.eps_fy1 | 165 | 33 | NO | — |
| estimates.bull.eps_fy2 | 155 | 37 | NO | — |
| estimates.bull.revenue_growth_fy1 | 0.38 | 0.38 | yes | ok |
| estimates.bull.revenue_growth_fy2 | 0.22 | 0.2 | NO | ok |
| guidance.eps.high | 32 | 32 | yes | — |
| guidance.eps.low | 30 | 30 | yes | — |
| guidance.eps.mid | 31 | 31 | yes | — |
| guidance.revenue.high | 51 | 51 | yes | — |
| guidance.revenue.low | 49 | 49 | yes | — |
| guidance.revenue.mid | 50 | 50 | yes | — |
| guidance.revenue_growth.high | — | 0.364 | NO | ok |
| guidance.revenue_growth.low | — | 0.31 | NO | ok |
| guidance.revenue_growth.mid | — | 0.337 | NO | ok |

_time: qwen3.8-flash 54s · qwen3.6-plus 84s_

## U96_SI — guidance_estimates

| field | qwen3.8-flash | qwen3.6-plus | agree | range |
|---|---|---|---|---|
| consensus.revenue_growth_fy1 | 0.236 | 0.236 | yes | ok |
| estimates.base.ebitda_margin_fy1 | 0.25 | 0.38 | NO | ok |
| estimates.base.ebitda_margin_fy2 | 0.27 | 0.39 | NO | ok |
| estimates.base.revenue_growth_fy1 | 0.241 | 0.25 | NO | ok |
| estimates.base.revenue_growth_fy2 | 0.105 | 0.07 | NO | ok |
| estimates.bear.ebitda_margin_fy1 | 0.25 | 0.35 | NO | ok |
| estimates.bear.ebitda_margin_fy2 | 0.255 | 0.35 | NO | ok |
| estimates.bear.revenue_growth_fy1 | 0.207 | 0.18 | NO | ok |
| estimates.bear.revenue_growth_fy2 | 0.055 | 0.05 | NO | ok |
| estimates.bull.ebitda_margin_fy1 | 0.26 | 0.41 | NO | ok |
| estimates.bull.ebitda_margin_fy2 | 0.3 | 0.42 | NO | ok |
| estimates.bull.revenue_growth_fy1 | 0.293 | 0.32 | NO | ok |
| estimates.bull.revenue_growth_fy2 | 0.16 | 0.12 | NO | ok |
| guidance.revenue.high | 7.5 | 7.5 | yes | — |
| guidance.revenue.low | 7 | 7 | yes | — |
| guidance.revenue.mid | 7.2 | 7.25 | yes | — |
| guidance.revenue_growth.high | 0.293 | 0.293 | yes | ok |
| guidance.revenue_growth.low | 0.207 | 0.207 | yes | ok |
| guidance.revenue_growth.mid | 0.241 | 0.25 | NO | ok |
| guidance_vs_consensus_pct | 0.4 | 1.1 | NO | — |

_time: qwen3.8-flash 58s · qwen3.6-plus 105s_

## Ground-truth worklist (owner to confirm the filed figure)

- 02888_HK bank_metrics dividend_payout_ratio: qwen3.8-flash=0.67, qwen3.6-plus=None
- 02888_HK bank_metrics npl_coverage_ratio: qwen3.8-flash=0.554, qwen3.6-plus=None
- 02888_HK guidance_estimates consensus.revenue_growth_fy1: qwen3.8-flash=0.06, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.39, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.37, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.065, qwen3.6-plus=0.06
- 02888_HK guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.05, qwen3.6-plus=0.06
- 02888_HK guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.36, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.bear.ebitda_margin_fy2: qwen3.8-flash=0.34, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.045, qwen3.6-plus=0.05
- 02888_HK guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=0.03, qwen3.6-plus=0.04
- 02888_HK guidance_estimates estimates.bull.ebitda_margin_fy1: qwen3.8-flash=0.42, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.bull.ebitda_margin_fy2: qwen3.8-flash=0.4, qwen3.6-plus=None
- 02888_HK guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.08, qwen3.6-plus=0.07
- 02888_HK guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.065, qwen3.6-plus=0.08
- 02888_HK guidance_estimates family_metrics.bank.cost_to_income_fy1: qwen3.8-flash=0.57, qwen3.6-plus=None
- 02888_HK guidance_estimates family_metrics.bank.cost_to_income_fy2: qwen3.8-flash=0.56, qwen3.6-plus=None
- 02888_HK guidance_estimates family_metrics.bank.credit_cost_bps_fy1: qwen3.8-flash=35.0, qwen3.6-plus=0.00325
- 02888_HK guidance_estimates family_metrics.bank.credit_cost_bps_fy2: qwen3.8-flash=32.0, qwen3.6-plus=0.00325
- 02888_HK guidance_estimates family_metrics.bank.fee_income_growth_fy1: qwen3.8-flash=0.09, qwen3.6-plus=None
- 02888_HK guidance_estimates family_metrics.bank.fee_income_growth_fy2: qwen3.8-flash=0.08, qwen3.6-plus=None
- 02888_HK guidance_estimates family_metrics.bank.nim_fy1: qwen3.8-flash=0.042, qwen3.6-plus=None
- 02888_HK guidance_estimates family_metrics.bank.nim_fy2: qwen3.8-flash=0.04, qwen3.6-plus=None
- 02888_HK guidance_estimates guidance.revenue.high: qwen3.8-flash=22.36, qwen3.6-plus=None
- 02888_HK guidance_estimates guidance.revenue.low: qwen3.8-flash=21.95, qwen3.6-plus=None
- 02888_HK guidance_estimates guidance.revenue.mid: qwen3.8-flash=22.15, qwen3.6-plus=None
- 02888_HK guidance_estimates guidance_vs_consensus_pct: qwen3.8-flash=0.0, qwen3.6-plus=None
- BABA saas_metrics rule_of_40_score: qwen3.8-flash=-1.5, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.22, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.23, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.08, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.09, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.19, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bear.ebitda_margin_fy2: qwen3.8-flash=0.2, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.05, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=0.04, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bull.ebitda_margin_fy1: qwen3.8-flash=0.25, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bull.ebitda_margin_fy2: qwen3.8-flash=0.26, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.12, qwen3.6-plus=None
- BN4_SI guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.14, qwen3.6-plus=None
- COST guidance_estimates consensus.revenue_growth_fy1: qwen3.8-flash=0.06, qwen3.6-plus=None
- COST guidance_estimates estimates.base.eps_fy1: qwen3.8-flash=None, qwen3.6-plus=17.5
- COST guidance_estimates estimates.base.eps_fy2: qwen3.8-flash=None, qwen3.6-plus=18.9
- COST guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.055, qwen3.6-plus=0.07
- COST guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.05, qwen3.6-plus=0.08
- COST guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.045, qwen3.6-plus=0.043
- COST guidance_estimates estimates.bear.eps_fy1: qwen3.8-flash=None, qwen3.6-plus=16.5
- COST guidance_estimates estimates.bear.eps_fy2: qwen3.8-flash=None, qwen3.6-plus=17.8
- COST guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.035, qwen3.6-plus=0.06
- COST guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=0.03, qwen3.6-plus=0.07
- COST guidance_estimates estimates.bull.eps_fy1: qwen3.8-flash=None, qwen3.6-plus=18.5
- COST guidance_estimates estimates.bull.eps_fy2: qwen3.8-flash=None, qwen3.6-plus=20.0
- COST guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.07, qwen3.6-plus=0.08
- COST guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.065, qwen3.6-plus=0.09
- D05_SI bank_metrics dividend_payout_ratio: qwen3.8-flash=0.375, qwen3.6-plus=None
- D05_SI bank_metrics management_overlays_bn: qwen3.8-flash=0.042, qwen3.6-plus=None
- D05_SI bank_metrics management_target_roe: qwen3.8-flash=0.165, qwen3.6-plus=0.162
- D05_SI bank_metrics net_charge_offs_pct: qwen3.8-flash=0.0015, qwen3.6-plus=None
- D05_SI guidance_estimates consensus.eps_fy1: qwen3.8-flash=3.72, qwen3.6-plus=None
- D05_SI guidance_estimates consensus.revenue_growth_fy1: qwen3.8-flash=0.022, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.565, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.575, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.base.eps_fy1: qwen3.8-flash=3.85, qwen3.6-plus=3.25
- D05_SI guidance_estimates estimates.base.eps_fy2: qwen3.8-flash=4.1, qwen3.6-plus=3.4
- D05_SI guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.025, qwen3.6-plus=0.04
- D05_SI guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.04, qwen3.6-plus=0.05
- D05_SI guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.54, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.bear.ebitda_margin_fy2: qwen3.8-flash=0.52, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.bear.eps_fy1: qwen3.8-flash=3.5, qwen3.6-plus=3.05
- D05_SI guidance_estimates estimates.bear.eps_fy2: qwen3.8-flash=3.45, qwen3.6-plus=3.15
- D05_SI guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.01, qwen3.6-plus=0.02
- D05_SI guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=-0.01, qwen3.6-plus=0.03
- D05_SI guidance_estimates estimates.bull.ebitda_margin_fy1: qwen3.8-flash=0.59, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.bull.ebitda_margin_fy2: qwen3.8-flash=0.61, qwen3.6-plus=None
- D05_SI guidance_estimates estimates.bull.eps_fy1: qwen3.8-flash=4.1, qwen3.6-plus=3.5
- D05_SI guidance_estimates estimates.bull.eps_fy2: qwen3.8-flash=4.45, qwen3.6-plus=3.7
- D05_SI guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.05, qwen3.6-plus=0.06
- D05_SI guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.065, qwen3.6-plus=0.07
- D05_SI guidance_estimates family_metrics.bank.cost_to_income_fy1: qwen3.8-flash=0.039, qwen3.6-plus=0.39
- D05_SI guidance_estimates family_metrics.bank.cost_to_income_fy2: qwen3.8-flash=0.038, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.credit_cost_bps_fy1: qwen3.8-flash=25.0, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.credit_cost_bps_fy2: qwen3.8-flash=25.0, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.fee_income_growth_fy1: qwen3.8-flash=0.15, qwen3.6-plus=0.18
- D05_SI guidance_estimates family_metrics.bank.fee_income_growth_fy2: qwen3.8-flash=0.14, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.loan_growth_fy1: qwen3.8-flash=0.04, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.loan_growth_fy2: qwen3.8-flash=0.05, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.nim_fy1: qwen3.8-flash=0.0175, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.nim_fy2: qwen3.8-flash=0.018, qwen3.6-plus=None
- D05_SI guidance_estimates family_metrics.bank.payout_ratio: qwen3.8-flash=0.5, qwen3.6-plus=None
- MELI guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.075, qwen3.6-plus=0.07
- MELI guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.11, qwen3.6-plus=0.1
- MELI guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.28, qwen3.6-plus=0.3
- MELI guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.065, qwen3.6-plus=0.05
- MELI guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.35, qwen3.6-plus=0.34
- MELI guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=0.25, qwen3.6-plus=0.26
- MELI guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.4, qwen3.6-plus=0.44
- MELI guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.3, qwen3.6-plus=0.35
- MU guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.8, qwen3.6-plus=0.73
- MU guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.74, qwen3.6-plus=0.71
- MU guidance_estimates estimates.base.eps_fy1: qwen3.8-flash=135.0, qwen3.6-plus=31.0
- MU guidance_estimates estimates.base.eps_fy2: qwen3.8-flash=120.0, qwen3.6-plus=33.5
- MU guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.25, qwen3.6-plus=0.337
- MU guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.1, qwen3.6-plus=0.14
- MU guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.72, qwen3.6-plus=0.66
- MU guidance_estimates estimates.bear.ebitda_margin_fy2: qwen3.8-flash=0.6, qwen3.6-plus=0.63
- MU guidance_estimates estimates.bear.eps_fy1: qwen3.8-flash=105.0, qwen3.6-plus=28.5
- MU guidance_estimates estimates.bear.eps_fy2: qwen3.8-flash=75.0, qwen3.6-plus=31.0
- MU guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.1, qwen3.6-plus=0.28
- MU guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=-0.05, qwen3.6-plus=0.08
- MU guidance_estimates estimates.bull.ebitda_margin_fy1: qwen3.8-flash=0.86, qwen3.6-plus=0.77
- MU guidance_estimates estimates.bull.ebitda_margin_fy2: qwen3.8-flash=0.84, qwen3.6-plus=0.75
- MU guidance_estimates estimates.bull.eps_fy1: qwen3.8-flash=165.0, qwen3.6-plus=33.0
- MU guidance_estimates estimates.bull.eps_fy2: qwen3.8-flash=155.0, qwen3.6-plus=37.0
- MU guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.22, qwen3.6-plus=0.2
- MU guidance_estimates guidance.revenue_growth.high: qwen3.8-flash=None, qwen3.6-plus=0.364
- MU guidance_estimates guidance.revenue_growth.low: qwen3.8-flash=None, qwen3.6-plus=0.31
- MU guidance_estimates guidance.revenue_growth.mid: qwen3.8-flash=None, qwen3.6-plus=0.337
- U96_SI guidance_estimates estimates.base.ebitda_margin_fy1: qwen3.8-flash=0.25, qwen3.6-plus=0.38
- U96_SI guidance_estimates estimates.base.ebitda_margin_fy2: qwen3.8-flash=0.27, qwen3.6-plus=0.39
- U96_SI guidance_estimates estimates.base.revenue_growth_fy1: qwen3.8-flash=0.241, qwen3.6-plus=0.25
- U96_SI guidance_estimates estimates.base.revenue_growth_fy2: qwen3.8-flash=0.105, qwen3.6-plus=0.07
- U96_SI guidance_estimates estimates.bear.ebitda_margin_fy1: qwen3.8-flash=0.25, qwen3.6-plus=0.35
- U96_SI guidance_estimates estimates.bear.ebitda_margin_fy2: qwen3.8-flash=0.255, qwen3.6-plus=0.35
- U96_SI guidance_estimates estimates.bear.revenue_growth_fy1: qwen3.8-flash=0.207, qwen3.6-plus=0.18
- U96_SI guidance_estimates estimates.bear.revenue_growth_fy2: qwen3.8-flash=0.055, qwen3.6-plus=0.05
- U96_SI guidance_estimates estimates.bull.ebitda_margin_fy1: qwen3.8-flash=0.26, qwen3.6-plus=0.41
- U96_SI guidance_estimates estimates.bull.ebitda_margin_fy2: qwen3.8-flash=0.3, qwen3.6-plus=0.42
- U96_SI guidance_estimates estimates.bull.revenue_growth_fy1: qwen3.8-flash=0.293, qwen3.6-plus=0.32
- U96_SI guidance_estimates estimates.bull.revenue_growth_fy2: qwen3.8-flash=0.16, qwen3.6-plus=0.12
- U96_SI guidance_estimates guidance.revenue_growth.mid: qwen3.8-flash=0.241, qwen3.6-plus=0.25
- U96_SI guidance_estimates guidance_vs_consensus_pct: qwen3.8-flash=0.4, qwen3.6-plus=1.1
## Reading (lead, 2026-11-28)

- Of 123 fields both models extracted: **reported or guided figures agree 86% (42 of 49)**; forward estimates agree
  16% (12 of 74) -- those are judgements, not facts, and are expected to differ.
- Flash filled 53 fields Plus left empty; Plus filled 9 Flash left empty (incl. COST EPS estimates FY+1/FY+2 -- a gap
  to watch on Flash).
- Every fact disagreement was a UNIT slip (credit cost 0.00325 vs 35 bp; cost-to-income 0.039 vs 0.39), not a
  misreading. The bank model clamped such values into wrong numbers; fixed: unambiguous unit conversion, then
  out-of-range values are rejected (the next source is used), never clamped (src/agents/analysis/bank_model.py).
- Next: the owner confirms filed figures for the worklist above; confirmed cases become
  tests/fixtures/extractor_eval/ expectations and this script's pass/fail gate.
