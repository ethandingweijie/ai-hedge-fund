# Market input-data audit

Top 50 listings by market cap per exchange (FMP), 2026-11-28. FMP calls only; no valuation.

| exchange | audited | clean | statements not in listing ccy | share-count mismatch | missing core fields | stale (>18m) | no estimates |
|---|---|---|---|---|---|---|---|
| JPX | 50 | 49 | 0 | 1 | 0 | 0 | 0 |
| KSC | 50 | 46 | 0 | 4 | 0 | 0 | 0 |
| SHH | 50 | 37 | 3 | 11 | 0 | 0 | 0 |
| SHZ | 50 | 44 | 0 | 4 | 0 | 0 | 0 |

## JPX

| ticker | name | issues |
|---|---|---|
| 8035.T | Tokyo Electron Limited | share count 459,239,000 vs market cap / price 2,263,545,095 (x0.20) |

## KSC

| ticker | name | issues |
|---|---|---|
| 005380.KS | Hyundai Motor Company | share count 260,315,000 vs market cap / price 203,334,618 (x1.28) |
| 329180.KS | Hd Hyundai Heavy Industries Co., Ltd. | share count 88,768,000 vs market cap / price 104,932,779 (x0.85) |
| 034730.KS | SK Inc. | share count 73,068,000 vs market cap / price 54,543,372 (x1.34) |
| 000810.KS | Samsung Fire & Marine Insurance Co., Ltd | share count 49,111,000 vs market cap / price 39,828,323 (x1.23) |

## SHH

| ticker | name | issues |
|---|---|---|
| 601939.SS | China Construction Bank Corporation | share count 256,107,000,000 vs market cap / price 214,960,191,410 (x1.19) |
| 601288.SS | Agricultural Bank of China Limited | share count 373,129,487,000 vs market cap / price 319,244,000,000 (x1.17) |
| 600941.SS | China Mobile Limited | share count 21,751,931,000 vs market cap / price 13,942,208,433 (x1.56) |
| 600938.SS | CNOOC Limited | share count 47,529,954,000 vs market cap / price 34,566,182,777 (x1.38) |
| 688981.SS | Semiconductor Manufacturing Internationa | statements in USD (listing CNY) -- engine must FX-convert; share count 8,009,003,000 vs market cap / price 5,761,147,859 (x1.39) |
| 688256.SS | Cambricon Technologies Corporation Limit | share count 420,400,354 vs market cap / price 628,854,000 (x0.67) |
| 601658.SS | Postal Savings Bank of China Co., Ltd. | share count 119,731,507,000 vs market cap / price 100,238,886,492 (x1.19) |
| 688347.SS | Hua Hong Semiconductor Ltd | statements in USD (listing CNY) -- engine must FX-convert |
| 688235.SS | BeOne Medicines Ltd. Class A | statements in USD (listing CNY) -- engine must FX-convert; share count 1,474,829,908 vs market cap / price 1,045,737,902 (x1.41) |
| 600150.SS | China CSSC Holdings Limited | share count 6,329,337,000 vs market cap / price 7,525,620,000 (x0.84) |
| 688012.SS | Advanced Micro-Fabrication Equipment Inc | share count 621,021,470 vs market cap / price 957,842,000 (x0.65) |
| 601319.SS | The People's Insurance Company (Group) o | share count 44,223,991,000 vs market cap / price 35,497,800,000 (x1.25) |
| 688801.SS | Shanghai Enflame Technology Co. | no income statement |

## SHZ

| ticker | name | issues |
|---|---|---|
| 300502.SZ | Eoptolink Technology Inc., Ltd. | share count 996,021,184 vs market cap / price 1,394,737,276 (x0.71) |
| 300394.SZ | Suzhou TFC Optical Communication Co., Lt | share count 778,837,123 vs market cap / price 1,090,825,093 (x0.71) |
| 300033.SZ | Hithink RoyalFlush Information Network C | share count 537,600,000 vs market cap / price 752,640,000 (x0.71) |
| 001399.SZ | Hkc | no income statement |
| 301660.SZ | Cansemi Technology Inc. | no income statement |
| 000958.SZ | SPIC Industry-Finance Holdings Co., Ltd. | share count 12,960,088,000 vs market cap / price 18,316,500,000 (x0.71) |