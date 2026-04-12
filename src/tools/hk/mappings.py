"""
Chinese column name → English field name mappings for all AKShare HK endpoints.

These dicts are the translation layer between AKShare's Chinese DataFrame columns
and the Pydantic model field names used throughout the system.
"""

# HK_HIST_COLS (EastMoney stock_hk_hist) removed — replaced by Sina stock_hk_daily
# which already returns English column names (date/open/high/low/close/volume).

# ---------------------------------------------------------------------------
# stock_hk_financial_indicator_em  →  FinancialMetrics / LineItem fields
# This endpoint returns a single-row wide DataFrame.
# Parse with: dict(zip(df.columns, df.iloc[0]))  ← wide format (1 row, columns = labels)
# ---------------------------------------------------------------------------
HK_INDICATOR_COLS: dict[str, str] = {
    # Per-share metrics
    "基本每股收益(元)":    "earnings_per_share",
    "每股净资产(元)":      "book_value_per_share",
    "每股股息TTM(港元)":   "dividends_per_share",
    "每股经营现金流(元)":  "operating_cash_flow_per_share",
    # Share structure
    "已发行股本(股)":      "shares_outstanding",
    "法定股本(股)":        "authorized_shares",
    # Market data — value is full HKD (not 亿); _extract_market_cap handles scaling
    "总市值(港元)":        "market_cap_raw",
    "港股市值(港元)":      "hk_market_cap_raw",
    # Income — actual column names confirmed from live AKShare output
    "营业总收入":          "revenue",          # was 总营业收入 (wrong)
    "净利润":              "net_income",
    # Ratios
    "股东权益回报率(%)":   "return_on_equity", # was 净资产收益率(%) (wrong)
    "总资产回报率(%)":     "return_on_assets",
    "市盈率":              "price_to_earnings_ratio",
    "市净率":              "price_to_book_ratio",
    "销售净利率(%)":       "net_margin",
    "股息率TTM(%)":        "dividend_yield",
    "派息比率(%)":         "payout_ratio",
    # Growth (rolling) — actual column name confirmed from live AKShare output
    "营业总收入滚动环比增长(%)": "revenue_growth_qoq",  # was 总营业收入滚动环比增长(%)
    "净利润滚动环比增长(%)":    "earnings_growth_qoq",
}

# ---------------------------------------------------------------------------
# stock_hk_growth_comparison_em  →  FinancialMetrics growth fields
# Returns a comparison table; filter to the target ticker row first.
# ---------------------------------------------------------------------------
HK_GROWTH_COLS: dict[str, str] = {
    "基本每股收益同比增长率":  "earnings_per_share_growth",
    "营业收入同比增长率":      "revenue_growth",
    "营业利润同比增长率":      "operating_income_growth",
    "总资产同比增长率":        "total_assets_growth",
}

# ---------------------------------------------------------------------------
# stock_hk_valuation_comparison_em  →  FinancialMetrics valuation fields
# Returns a comparison table; filter to the target ticker row first.
# ---------------------------------------------------------------------------
HK_VALUATION_COLS: dict[str, str] = {
    "市盈率-TTM":  "price_to_earnings_ratio",
    "市盈率-LYR":  "price_to_earnings_ratio_lyr",
    "市净率-MRQ":  "price_to_book_ratio",
    "市净率-LYR":  "price_to_book_ratio_lyr",
    "市销率-TTM":  "price_to_sales_ratio",
    "市销率-LYR":  "price_to_sales_ratio_lyr",
    "市现率-TTM":  "price_to_cash_flow_ratio",
    "市现率-LYR":  "price_to_cash_flow_ratio_lyr",
}

# ---------------------------------------------------------------------------
# stock_financial_hk_report_em  (symbol="利润表", indicator="年度")
#
# AKShare returns LONG-format DataFrame:
#   columns: REPORT_DATE, STD_ITEM_CODE, STD_ITEM_NAME, AMOUNT
# Key: STD_ITEM_NAME (Chinese)  →  Value: English field name
#
# Source: live inspection of Tencent (00700) data, April 2026.
# STD_ITEM_CODE → STD_ITEM_NAME reference:
#   004001001  营业额        (turnover / primary revenue)
#   004001002  其他营业收入
#   004001999  营运收入      (total operating revenue = 营业额 + 其他营业收入)
#   004005001  营运支出      (total operating expenses)
#   004007999  毛利          (gross profit)
#   004010003  销售及分销费用
#   004010004  行政开支
#   004010999  经营溢利      (operating profit)
#   004011201  融资成本      (finance costs = interest expense)
#   004011999  除税前溢利    (profit before tax = EBIT proxy)
#   004012999  除税后溢利    (profit after tax, total including minorities)
#   004025002  股东应占溢利  (profit attributable to shareholders = net income)
#   004027002  每股基本盈利  (basic EPS)
# ---------------------------------------------------------------------------
HK_INCOME_COLS: dict[str, str] = {
    "营业额":           "revenue",          # primary turnover (most reliable)
    "营运收入":         "revenue_total",    # total incl. other income — fallback
    "毛利":             "gross_profit",
    "经营溢利":         "operating_income",
    "融资成本":         "interest_expense",
    "除税前溢利":       "ebit",
    "除税后溢利":       "net_income_total", # total (incl. minority) — lower priority
    "股东应占溢利":     "net_income",       # attributable to parent — preferred
    "每股基本盈利":     "earnings_per_share",
    "销售及分销费用":   "selling_expense",
    "行政开支":         "general_and_administrative_expense",
    # D&A is not in the income statement for HK format; sourced from cashflow
}

# ---------------------------------------------------------------------------
# stock_financial_hk_report_em  (symbol="资产负债表", indicator="年度")
#
# STD_ITEM_CODE → STD_ITEM_NAME reference:
#   004001004  无形资产
#   004001999  非流动资产合计
#   004002001  存货
#   004002003  应收帐款
#   004002010  现金及等价物
#   004002999  流动资产合计
#   004009999  总资产
#   004011010  短期贷款
#   004011999  流动负债合计
#   004020001  长期贷款
#   004020999  非流动负债合计
#   004025999  总负债
#   004028999  净资产          (net assets = equity incl. minorities)
#   004030999  股东权益        (equity attributable to parent — preferred)
# ---------------------------------------------------------------------------
HK_BALANCE_COLS: dict[str, str] = {
    "现金及等价物":     "cash_and_equivalents",
    "应收帐款":         "accounts_receivable",
    "存货":             "inventory",
    "流动资产合计":     "current_assets",
    "非流动资产合计":   "non_current_assets",
    "总资产":           "total_assets",
    "短期贷款":         "short_term_debt",
    "流动负债合计":     "current_liabilities",
    "长期贷款":         "long_term_debt",
    "非流动负债合计":   "non_current_liabilities",
    "总负债":           "total_liabilities",
    "股东权益":         "shareholders_equity",   # parent-attributable (preferred)
    "净资产":           "net_assets",             # incl. minorities — fallback
    "无形资产":         "intangible_assets",
    # Goodwill: Tencent/large HK co. include goodwill inside intangibles or
    # as a separate item; map if present
    "商誉":             "goodwill",
}

# ---------------------------------------------------------------------------
# stock_financial_hk_report_em  (symbol="现金流量表", indicator="年度")
#
# STD_ITEM_CODE → STD_ITEM_NAME reference:
#   001009  折旧及摊销        (D&A — added back in indirect method)
#   003999  经营业务现金净额  (net cash from operations)
#   005005  购建固定资产      (capex outflow — negative in raw data)
#   005999  投资业务现金净额  (net cash from investing)
#   007999  融资业务现金净额  (net cash from financing)
# ---------------------------------------------------------------------------
HK_CASHFLOW_COLS: dict[str, str] = {
    # D&A item has a "加:" (add-back) prefix in the indirect-method cash flow statement
    "加:折旧及摊销":        "depreciation_and_amortization",
    "经营业务现金净额":     "operating_cash_flow",
    "投资业务现金净额":     "investing_cash_flow",
    "融资业务现金净额":     "financing_cash_flow",
    # Capital expenditure: fixed assets + intangibles (both are outflows, both negative)
    # 购建固定资产 maps to capex_raw; 购建无形资产及其他资产 goes to capex_intangibles_raw.
    # _compute_derived sums both into capital_expenditure.
    "购建固定资产":                     "capex_ppe_raw",           # PP&E purchases
    "购建无形资产及其他资产":           "capex_intangibles_raw",   # intangibles purchases
}
