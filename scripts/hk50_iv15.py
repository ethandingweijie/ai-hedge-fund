"""
IV15 for the HK50 universe, using the SW46 card's IV15 logic.

IV15 = blend of:
  Valuation A — multi-stage owner-earnings discount model @ 15% RR over 15 yrs,
                Gordon terminal (g_term=3%).
  Valuation B — same 15-yr OE stream, terminal = E15 x AICT-tier multiple.
  Blend weight wA depends on AICT tier (Fortress 50/50; Wood skews to A).

AICT scope (per directive):
  * AICT modulation (growth haircut + Buffett terminal multiple + blend) applies
    ONLY to Software and Internet-Platform companies — the names whose moats are
    actually exposed to AI competitive threat.
  * Every other sector (banks, insurers, energy, utilities, telecom, autos,
    consumer brands, pharma/biotech, REITs, hardware OEMs, restaurants) uses a
    NEUTRAL path: no growth haircut, pure Gordon terminal, no Buffett multiple
    (wA = 1.0). AICT column shows "—" for these.

Adaptations for HK50 (flagged):
  * Owner Earnings proxied by trailing EPS (HK names carry little SBC).

Run: .venv\\Scripts\\python.exe scripts/hk50_iv15.py [EXTRA_TICKER ...]
"""
from __future__ import annotations

import io
import sys

def _wrap_stdout_utf8() -> None:
    """LAZY UTF-8 stdout wrap — call only from main()/__main__, never at import
    time (this module's IV15 functions are imported by the FastAPI backend; see
    the twin in hk50_gap_analysis.py)."""
    if getattr(sys.stdout, "encoding", "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


from src.tools.api import get_financial_metrics  # noqa: E402

END_DATE = "2026-05-30"
R = 0.15           # required return
G_TERM = 0.03      # base terminal growth
G_CAP = 0.25       # cap stage-1 growth to avoid absurd extrapolation

# AICT tier -> (growth_haircut, terminal_multiple, blend_weight_on_ValA)
TIER_PARAMS = {
    "Fortress": (0.00, 25.0, 0.50),
    "Castle":   (0.10, 20.0, 0.50),
    "Chapel":   (0.25, 15.0, 0.60),
    "Stone":    (0.40, 10.0, 0.65),
    "Wood":     (0.60,  7.0, 0.75),
}

# AICT tiers — assigned ONLY to Software & Internet-Platform names (EDIT ME).
# These are the only businesses whose moats AICT is meant to price. Any ticker
# NOT in this dict is treated as a non-AICT name (neutral Gordon IV15 path).
AICT_TIER = {
    "0700.HK": "Castle",   # Tencent — dominant social / gaming / cloud platform
    "9988.HK": "Castle",   # Alibaba — e-commerce + cloud
    "9999.HK": "Castle",   # NetEase — gaming software
    "3690.HK": "Chapel",   # Meituan — local-services platform
    "1024.HK": "Chapel",   # Kuaishou — short-video platform
    "9618.HK": "Chapel",   # JD.com — e-commerce platform
    "9626.HK": "Chapel",   # Bilibili — video / content platform
    "TCOM":    "Chapel",   # Trip.com — online-travel platform
    # ── expansion batch (50→100): SW/Internet names only (keyed by HK ticker,
    #    matching runner._compute_iv15). Non-software adds use the neutral path.
    "9698.HK": "Stone",    # GDS — data-center infra (picks-and-shovels)
    "0780.HK": "Chapel",   # Tongcheng — OTA platform
    "3888.HK": "Chapel",   # Kingsoft — WPS office + games
    "0268.HK": "Stone",    # Kingdee — cloud ERP
    "2400.HK": "Stone",    # XD Inc — game platform/publisher
    "0772.HK": "Stone",    # China Literature — online content / IP
    "6060.HK": "Stone",    # ZhongAn — insurtech platform
    "0241.HK": "Chapel",   # Alibaba Health — healthcare e-commerce platform
}
DEFAULT_TIER = "Chapel"   # fallback for an AICT-eligible name lacking an explicit tier
# Neutral (non-AICT) terminal: no haircut, pure Gordon, no Buffett multiple.
NEUTRAL_PARAMS = (0.0, None, 1.0)   # (growth_haircut, terminal_multiple, blend_weight_on_ValA)

HK50 = [
    ("0700.HK", "Tencent"), ("9988.HK", "Alibaba"), ("3690.HK", "Meituan"),
    ("1024.HK", "Kuaishou"), ("1810.HK", "Xiaomi"), ("9618.HK", "JD.com"),
    ("9999.HK", "NetEase"), ("9626.HK", "Bilibili"), ("0992.HK", "Lenovo"),
    ("1211.HK", "BYD"), ("0175.HK", "Geely"), ("2015.HK", "Li Auto"),
    ("9868.HK", "XPeng"), ("6862.HK", "Haidilao"), ("6690.HK", "Haier"),
    ("2020.HK", "Anta"), ("2331.HK", "Li Ning"), ("0288.HK", "WH Group"),
    ("2313.HK", "Shenzhou"), ("CHA", "Chagee(ADR)"), ("EH", "EHang(ADR)"),
    ("YUMC", "Yum China(ADR)"), ("TCOM", "Trip.com(ADR)"), ("1801.HK", "Innovent"),
    ("2269.HK", "WuXi Bio"), ("1093.HK", "CSPC"), ("1177.HK", "Sino Biopharm"),
    ("0005.HK", "HSBC"), ("2888.HK", "StanChart"), ("1398.HK", "ICBC"),
    ("0939.HK", "CCB"), ("3988.HK", "BoC"), ("3968.HK", "CMB"),
    ("0011.HK", "Hang Seng Bank"), ("2388.HK", "BOCHK"), ("2318.HK", "Ping An"),
    ("2628.HK", "China Life"), ("1299.HK", "AIA"), ("0941.HK", "China Mobile"),
    ("0762.HK", "China Unicom"), ("0728.HK", "China Telecom"), ("0883.HK", "CNOOC"),
    ("0857.HK", "PetroChina"), ("0386.HK", "Sinopec"), ("1088.HK", "Shenhua"),
    ("0002.HK", "CLP"), ("0006.HK", "Power Assets"), ("0003.HK", "HK&China Gas"),
    ("1109.HK", "CR Land"), ("0823.HK", "Link REIT"),
]

# P/IV15 valuation-score ladder (SW46 composite, 35-pt bucket)
PIV_LADDER = [
    (0.50, 35), (0.75, 32), (0.90, 28), (1.00, 24), (1.25, 20),
    (1.50, 17), (2.00, 14), (3.00, 8), (5.00, 5), (10.0, 3),
]


def _yf_info(ticker):
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def iv15(E0, g1, haircut, mult, wA):
    """Return (ivA, ivB, iv15) per share.

    Valuation A = Gordon terminal (g_term). Valuation B = E15 x terminal multiple
    (Buffett); only meaningful when wA < 1. Neutral names pass mult=None, wA=1.0
    so IV15 collapses to pure Gordon with no growth haircut.
    """
    g1a = g1 * (1 - haircut)
    # Build 15-year owner-earnings (EPS) path
    eps_path = []
    e = E0
    for y in range(1, 16):
        if y <= 5:
            g = g1a
        elif y <= 10:                       # linear fade g1a -> G_TERM
            g = g1a + (G_TERM - g1a) * ((y - 5) / 5.0)
        else:
            g = G_TERM
        e = e * (1 + g)
        eps_path.append(e)
    pv_stream = sum(eps_path[y - 1] / (1 + R) ** y for y in range(1, 16))
    e15 = eps_path[-1]
    disc15 = (1 + R) ** 15
    tvA = e15 * (1 + G_TERM) / (R - G_TERM)        # Gordon
    ivA = pv_stream + tvA / disc15
    if mult is not None:
        tvB = e15 * mult                            # Buffett multiple
        ivB = pv_stream + tvB / disc15
    else:
        ivB = ivA
    return ivA, ivB, wA * ivA + (1 - wA) * ivB


def piv_score(piv):
    if piv is None or piv < 0:
        return -2
    for thr, pts in PIV_LADDER:
        if piv <= thr:
            return pts
    return -2


def build_inputs(ticker):
    """Return dict with E0, roe, payout, g_signal, pe, currency (multi-source).

    ADRs (non-.HK) are FMP-sourced: roe/payout/pe come from FMP, and EPS is
    derived as market_price / FMP_P_E so it stays in the USD quote currency.
    (FMP's raw netIncomePerShareTTM is reporting-currency — CNY for China ADRs —
    so it would mismatch the USD price; the FMP P/E is quote-consistent instead.)
    HK names keep the AKShare EPS path unchanged (EPS in listing currency).
    """
    from src.tools.api import is_hk_ticker  # local import to avoid load-order issues

    rows = get_financial_metrics(ticker, END_DATE, "ttm", 1)
    fm = rows[0] if rows else None
    yi = _yf_info(ticker)

    def fmg(a):
        return getattr(fm, a, None) if fm else None

    roe = fmg("return_on_equity")
    if roe is None and yi.get("returnOnEquity") is not None:
        roe = yi["returnOnEquity"]
    payout = fmg("payout_ratio")
    if payout is None and yi.get("payoutRatio") is not None:
        payout = yi["payoutRatio"]
    pe = fmg("price_to_earnings_ratio") or yi.get("trailingPE")
    currency = fmg("currency") or yi.get("financialCurrency") or yi.get("currency") or "?"
    mkt_price = yi.get("currentPrice") or yi.get("regularMarketPrice")

    is_hk = is_hk_ticker(ticker)
    if not is_hk and pe and pe > 0 and mkt_price:
        # ADR / US: FMP P/E is *usually* quote-consistent → derive EPS in quote
        # currency from price / P/E.
        E0 = mkt_price / pe
        price = mkt_price
        currency = yi.get("currency") or currency
        # GUARD — FMP occasionally ships a CURRENCY-MIXED P/E for ADRs whose
        # reporting currency differs from the quote currency (e.g. BABA: USD
        # ADR price ÷ CNY-denominated EPS → P/E ~2.5 vs the real ~19). That
        # makes E0 the foreign-currency EPS and inflates IV15 ~FX-fold (BABA
        # showed IV15 $474 / P-IV15 0.26 when the true figures were ~$61 /
        # ~2.0). yfinance's trailingEps is already quote-consistent, so when
        # the FMP-implied EPS diverges from it by >2.5x, trust yfinance.
        yf_eps = yi.get("trailingEps")
        if yf_eps and yf_eps > 0 and E0 > 0:
            divergence = max(E0 / yf_eps, yf_eps / E0)
            if divergence > 2.5:
                E0 = yf_eps
                pe = mkt_price / yf_eps  # keep pe quote-consistent for downstream
    else:
        # HK (AKShare) or missing P/E: AKShare EPS is reliable & in listing ccy.
        E0 = fmg("earnings_per_share") or yi.get("trailingEps")
        price = (E0 * pe) if (E0 and pe and E0 > 0 and pe > 0) else mkt_price

    # growth signal: conservative min of forward-EPS growth & revenue growth
    cands = []
    fwd, trl = yi.get("forwardEps"), yi.get("trailingEps")
    if fwd and trl and trl != 0:
        cands.append(fwd / trl - 1.0)
    rg = fmg("revenue_growth")
    if rg is None:
        rg = yi.get("revenueGrowth")
    if rg is not None:
        cands.append(rg)
    g_signal = min(cands) if cands else None

    return {"E0": E0, "roe": roe, "payout": payout, "pe": pe,
            "currency": currency, "g_signal": g_signal, "price": price}


def stage1_growth(inp):
    payout = inp["payout"] if inp["payout"] is not None else 0.4
    payout = min(max(payout, 0.0), 1.0)
    g_sus = inp["roe"] * (1 - payout) if inp["roe"] is not None else None
    cands = [g for g in (inp["g_signal"], g_sus) if g is not None]
    g1 = min(cands) if cands else 0.05      # default 5% if no signal
    return max(0.0, min(g1, G_CAP))


def main():
    _wrap_stdout_utf8()
    extra = [(t, "USER-ADDED") for t in sys.argv[1:]]
    universe = HK50 + extra
    print("=" * 122)
    print(f"HK50 IV15  |  SW46 logic (15yr DDM @ {int(R*100)}% RR + Buffett-multiple blend)  |  end={END_DATE}")
    print(f"AICT modulation applies ONLY to SW/Internet-Platform names; all other sectors use a neutral Gordon path (AICT=—)")
    print(f"OE proxied by trailing EPS; price = EPS x P/E (same currency as EPS)")
    print("=" * 122)
    hdr = (f"{'Ticker':<9} {'Name':<16} {'AICT':<8} {'cur':<4} {'EPS':>8} {'g1':>6} "
           f"{'IV_A':>9} {'IV_B':>9} {'IV15':>9} {'Price':>9} {'P/IV15':>7} {'Vpts':>5}  read")
    print(hdr)
    print("-" * 122)

    rows = []
    for ticker, name in universe:
        is_aict = ticker in AICT_TIER
        if is_aict:
            tier = AICT_TIER[ticker]
            haircut, mult, wA = TIER_PARAMS[tier]
            tier_label = tier
        else:
            haircut, mult, wA = NEUTRAL_PARAMS
            tier_label = "—"            # em dash: AICT not applied
        try:
            inp = build_inputs(ticker)
        except Exception as e:
            print(f"{ticker:<9} {name:<16} {tier_label:<8} ERR {type(e).__name__}: {e}")
            continue
        E0 = inp["E0"]
        if E0 is None or E0 <= 0:
            print(f"{ticker:<9} {name:<16} {tier_label:<8} {inp['currency']:<4} {'n/m':>8}  "
                  f"(EPS<=0 or missing — owner-earnings model not meaningful)")
            continue
        g1 = stage1_growth(inp)
        ivA, ivB, iv = iv15(E0, g1, haircut, mult, wA)
        price = inp["price"]
        piv = (price / iv) if (price and iv > 0) else None
        vpts = piv_score(piv)
        read = ("DEEP VALUE" if piv and piv <= 0.75 else
                "undervalued" if piv and piv <= 1.0 else
                "fair" if piv and piv <= 1.5 else
                "rich" if piv and piv <= 3.0 else
                "expensive" if piv else "n/a")
        rows.append((ticker, name, tier_label, iv, price, piv, vpts))
        ps = f"{price:9.2f}" if price else f"{'n/a':>9}"
        pv = f"{piv:7.2f}" if piv else f"{'n/a':>7}"
        print(f"{ticker:<9} {name:<16} {tier_label:<8} {inp['currency']:<4} {E0:8.2f} {g1*100:5.1f}% "
              f"{ivA:9.2f} {ivB:9.2f} {iv:9.2f} {ps} {pv} {vpts:>5}  {read}")

    print("=" * 122)
    ranked = [r for r in rows if r[5] is not None]
    print(f"\nCHEAPEST BY P/IV15 (lower = more undervalued vs 15%-return intrinsic value):")
    for r in sorted(ranked, key=lambda x: x[5])[:15]:
        print(f"  P/IV15={r[5]:5.2f}  Vpts={r[6]:>3}  {r[0]:<9} {r[1]:<16} [{r[2]}]")
    print(f"\nMOST EXPENSIVE BY P/IV15:")
    for r in sorted(ranked, key=lambda x: x[5], reverse=True)[:8]:
        print(f"  P/IV15={r[5]:5.2f}  Vpts={r[6]:>3}  {r[0]:<9} {r[1]:<16} [{r[2]}]")


if __name__ == "__main__":
    main()
