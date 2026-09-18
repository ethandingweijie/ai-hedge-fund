"""Route (c) for Anta Sports (02020.HK): the look-through template and its wiring.

WHY THIS EXISTS. Anta routes to the `Apparel / Athletic Wear` profile, whose
methods are EV/EBITDA 0.40 (anchor), DCF (FCF+) 0.30, P/E (norm) 0.20 and
Brand Val 0.10. Not one of them can see a listed 42.5% associate. Measured at
17 Sep 2026: Amer Sports (NYSE: AS) has a USD 15.304bn market cap, so Anta's
stake is HKD 51.025bn gross and HKD 43.371bn after a 15% haircut -- against a
HKD 199.490bn market cap for the whole group. A quarter of the company was
invisible to every method in the profile.

WHAT THE OWNER AUTHORISED, and where the implementation deviates from the
literal instruction and why:

  * Amer Sports ownership 42.50%, sourced from post-IPO SEC filings / Anta's
    interim reports via AS Holding. Written as `stake_pct: 0.425`. AS GIVEN.
  * Holdco discount 15.0%. Written at STAKE level (`discount_pct: 0.15` on the
    Amer division) with `holdco_discount: [0.0, 0.0]` at group level. The
    owner's own bridge formula multiplies `(1 - Holdco Discount)` by ONLY the
    market value of listed associates; the owner's rationale names frictions of
    HOLDING THE ASSOCIATE (governance, double-tax, liquidity); and the template
    file's v4 rule is that discounts compose one way only, so a name discounted
    at the stake carries a group discount of zero. GenScript does exactly this
    with 20% on Legend and zero on the group. A group-level 15% would have
    taken the same haircut off the ANTA and FILA operating businesses.
  * The owner's two JSON snippets use a schema the engine does not read. Nine
    keys are dead: `template_type`, `associates`, `reporting_currency`,
    `listing_currency`, `last_verified`, `company_name`, `pricing_method`,
    `effective_ownership`, `source_currency`. `look_through_value` reads
    `name`, `currency`, `holdco_discount`, `divisions[]`, and per division
    `name`, `basis`, `listed`, `stake_pct`, `discount_pct`, `multiple_range`
    plus the stated-figure keys. Their absence is PINNED here so that writing
    them is a deliberate act with a reason attached.
  * The owner's `"holdco_discount": 0.15` scalar CRASHES. `d_lo, d_hi =
    tpl.get("holdco_discount") or [0.0, 0.0]` raises
    `TypeError: cannot unpack non-iterable float object` -- at VALUATION time,
    not load time, because nothing validates the file's shape. Pinned.
  * The owner's separate `apply_lookthrough_associate_bridge` additive bridge
    is NOT implemented. `SOTP / NAV` is a blend leg whose value ALREADY
    contains the associate, so adding it again in the aggregator double-counts
    Amer Sports; `value_per_share` already performs the FX conversion the
    bridge re-implements, so `core_equity * fx_rep_to_listing` converts twice;
    and the hook passes `get_market_cap_fn=get_prices`, which returns prices,
    not market capitalisation.

Route (b) stays rejected: `02020.HK` is NOT in `_SEGMENT_SOTP_TICKERS`.

Every test here is offline. The live figures quoted above were measured by
`scratchpad/preview_anta_sotp.py` and are recorded in the template's own
`source` fields; these tests pin the shape and the arithmetic, not the quotes.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.agents.analysis import holdco_sotp as h
from src.agents.analysis import dcf_agent as da

TICKER = "02020.HK"
RAW = "2020.HK"                       # what `to_fmp_symbol` produces
ASSOC = "AS"
CORE = "ANTA, FILA, Descente and Kolon Sport"
AMER = "Amer Sports"
END = "2026-09-17"

SHARES = 2.904e9                      # the line-item count the engine divides by
PRICE = 72.00                         # HKD close, 17 Sep 2026

#: THE TWO NET-DEBT INPUTS, AND WHY THERE ARE TWO.
#:
#: `ND_RAW_HKD` is CNY 20.590bn x 1.17371 = total debt less cash and equivalents
#: ONLY, which is exactly what FMP's own `netDebt` field reports and exactly what
#: `_net_debt_net_of_investments` exists to correct. The tests that use it are
#: arithmetic pins on the bridge GIVEN that input; they are not a claim that it
#: is Anta's net debt. It is what this module's first version assumed, and the
#: assumption is measured wrong -- see `ND_CASH_HKD`.
#:
#: `ND_CASH_HKD` is what the engine actually passes, read straight off
#: production run bda1622f (2026-09-18) `dcf_range["02020.HK"]["net_debt"]`:
#: -12,958,636,029 HKD, i.e. HKD 12.959bn of net CASH at the 30-Jun-2026 interim
#: (cash 17.2 + short-term investments 26.8 - debt 33.0 = CNY -11.1bn, at
#: 1.1704). At the 31-Dec-2025 year end the same convention gives CNY -5.616bn,
#: measured off `search_line_items`: cash 12.845 + STI 26.206 - debt 33.435.
#: Anta's CNY 26.206bn of short-term investments is the whole difference, and
#: the sign flips. `_net_debt_net_of_investments`'s own docstring names this
#: trap on Alibaba, including "the opposite sign from the SOTP in the same run".
#:
#: The 37.125bn HKD gap between the two moves the published look-through leg
#: from HKD 81.21/share to HKD 93.68/share and the premium to price from +12.8%
#: to +30.1%. Tests exist for BOTH so that a future reader cannot mistake the
#: superseded figure for the live one.
ND_RAW_HKD = 24.166e9                 # SUPERSEDED -- raw td - cash, wrong sign
ND_HKD = ND_RAW_HKD                   # kept as the name the arithmetic pins use
ND_CASH_HKD = -12.959e9               # what production passes, measured

#: The core division's HKD EBITDA on the production run, and the leg value it
#: produced. `(EBITDA x 9.0 + Amer net + net CASH) / shares` reproduces the
#: published 93.68 to within 0.14%, which is what proves the leg IS the
#: look-through and nothing else was folded into it.
_CORE_EBITDA_HKD_PROD = 24.011e9
_AMER_NET_HKD_PROD = 43.371e9
_PROD_SOTP_LEG = 93.68

_PATH = Path(h.__file__).resolve().parents[2] / "data" / "holdco_sotp_templates.json"

# The nine keys in the owner's snippets that nothing reads. Pinned absent so
# that writing one is a deliberate act rather than a silent no-op.
_DEAD_KEYS_TEMPLATE = {"template_type", "associates", "reporting_currency",
                       "listing_currency", "last_verified", "company_name"}
_DEAD_KEYS_DIVISION = {"pricing_method", "effective_ownership", "source_currency"}

# FX rates and figures measured 17 Sep 2026, used throughout so the arithmetic
# below is the arithmetic the real bridge ran rather than a round stand-in.
_USD_HKD = 7.84486
_CNY_HKD = 1.17371
_USD_CNY = 6.6978
_AMER_MCAP_USD = 15.304e9
_GROUP_EBITDA_CNY = 20.508e9
_AMER_EBITDA_USD = 1.106e9


def _doc():
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _tpl():
    return _doc()["templates"][TICKER]


def _div(name):
    return next(d for d in _tpl()["divisions"] if d["name"] == name)


# ── 1. the template key is the CANONICAL one ────────────────────────────────
class TestTheKeyIsCanonical:
    def test_the_key_is_02020_hk_not_2020_hk(self):
        t = _doc()["templates"]
        assert TICKER in t
        assert RAW not in t, (
            "to_fmp_symbol('02020.HK') strips the leading zero to '2020.HK' "
            "while canonical_ticker('2020.HK') restores it. template_for "
            "canonicalises, so the KEY has to be the canonical form or the "
            "lookup misses and the promote silently no-ops."
        )

    def test_both_spellings_resolve_to_the_same_template(self):
        a, b = h.template_for(TICKER), h.template_for(RAW)
        assert a is not None and b is not None
        assert a["name"] == b["name"] == "Anta Sports Products"

    def test_the_promote_accepts_both_spellings(self):
        assert da._in_lookthrough_promote(TICKER) is True
        assert da._in_lookthrough_promote(RAW) is True

    def test_canonicalisation_is_idempotent_on_every_member(self):
        from src.tools.ticker_canonical import canonical_ticker
        for t in sorted(da._LOOKTHROUGH_PROMOTE):
            assert canonical_ticker(t) == t, t

    @pytest.mark.parametrize("bad", ["", None, 0, "02020.HKX", "BIDU",
                                     "09888.HK", "VC2.SI", "NKE", "00700.HK",
                                     "700.HK", "02021.HK", ".HK"])
    def test_the_membership_test_refuses_what_is_not_promoted(self, bad):
        assert da._in_lookthrough_promote(bad) is False

    @pytest.mark.parametrize("spelling", ["02020.HK", "2020.HK", "02020", "2020",
                                          "02020.hk", "02020.HK ", " 02020.HK",
                                          "02020.HK\t", "02020.hk ", "2020.HK "])
    def test_every_spelling_the_canonicaliser_repairs_is_promoted(self, spelling):
        """`canonical_ticker` REPAIRS bare codes, case and padding, and the
        promote has to follow it rather than the raw string.

        Measured: `canonical_ticker('02020')` -> `'02020.HK'`, and
        `template_for('02020')` resolves to the Anta template. Same for
        `'02020.hk '`. So a bare or padded code is not a malformed non-member,
        it is a spelling the canonicaliser accepts. The property that matters is
        that BOTH gates agree on it: if `template_for` found the template but
        `_in_lookthrough_promote` did not, the SOTP would be computable and
        never used -- which is exactly the asymmetry `_in_lookthrough_promote`
        exists to close.
        """
        from src.tools.ticker_canonical import canonical_ticker
        assert canonical_ticker(spelling) == TICKER
        assert da._in_lookthrough_promote(spelling) is True
        assert (h.template_for(spelling) or {}).get("name") == "Anta Sports Products"

    def test_the_two_gates_agree_on_every_anta_spelling(self):
        """For THIS name, no spelling may satisfy one gate and miss the other.

        Scoped deliberately. It is NOT a global invariant: `BIDU`, `09888.HK`
        and `VC2.SI` all resolve to a template and are all deliberately absent
        from `_LOOKTHROUGH_PROMOTE` (Baidu is staging-gated, Olam is not in the
        pilot set), so "has a template" does not imply "is promoted" in
        general. What has to hold for Anta is the other direction too: every
        spelling that reaches the template also reaches the promote. A
        divergence either way is silent -- a template that never runs, or a
        promote that fires and finds nothing to value.
        """
        for spelling in ["02020.HK", "2020.HK", "02020", "2020", "02020.hk",
                         "02020.HKX", "", "NKE"]:
            has_tpl = h.template_for(spelling) is not None
            promoted = da._in_lookthrough_promote(spelling)
            assert has_tpl == promoted, (spelling, has_tpl, promoted)

    def test_having_a_template_does_not_imply_being_promoted(self):
        """The counterexample to the test above, pinned so nobody generalises it."""
        for t in ("BIDU", "09888.HK", "VC2.SI"):
            assert h.template_for(t) is not None, t
            assert da._in_lookthrough_promote(t) is False, t

    def test_membership_is_exactly_the_four_names(self):
        assert TICKER in da._LOOKTHROUGH_PROMOTE
        assert "BIDU" not in da._LOOKTHROUGH_PROMOTE
        assert "09888.HK" not in da._LOOKTHROUGH_PROMOTE
        assert sorted(da._LOOKTHROUGH_PROMOTE) == [
            "02020.HK", "BN4.SI", "S08.SI", "U96.SI"]

    def test_route_b_stays_rejected(self):
        assert TICKER not in da._SEGMENT_SOTP_TICKERS
        assert RAW not in da._SEGMENT_SOTP_TICKERS


# ── 2. the schema the ENGINE reads, not the one the instruction used ────────
class TestTheSchema:
    def test_no_dead_keys_on_the_template(self):
        for k in _DEAD_KEYS_TEMPLATE:
            assert k not in _tpl(), k

    def test_no_dead_keys_on_any_division(self):
        for d in _tpl()["divisions"]:
            for k in _DEAD_KEYS_DIVISION:
                assert k not in d, (d["name"], k)

    def test_the_currency_is_the_listing_currency_not_the_reporting_one(self):
        # v6b: parent net debt reaches the bridge in the template currency, so
        # it has to be HKD even though Anta REPORTS in CNY. Stated in CNY the
        # bridge would subtract an HKD net debt from a CNY gross asset value.
        assert _tpl()["currency"] == "HKD"
        assert h.currency_of(TICKER) == "HKD"

    def test_the_new_template_is_appended_last_and_the_count_is_fifteen(self):
        keys = list(_doc()["templates"])
        assert keys[-1] == TICKER, (
            "this file's key order is CREATION order, not alphabetical; a "
            "sorted insertion would drop 02020.HK at index 3 between 00019.HK "
            "and C07.SI for no reason a reader could explain"
        )
        assert len(keys) == 15
        # 10 shipped the template; 11 corrected its net-debt note. Nothing reads
        # `version` -- it is pinned so a correction to a SOURCED figure cannot
        # land silently in a file whose whole convention is that its numbers
        # are checked.
        assert _doc()["version"] == 11

    def test_every_template_in_the_file_declares_a_two_element_discount(self):
        # Nothing validates this file at load, and `look_through_value` unpacks
        # the value into two names. A scalar anywhere in here is a crash at
        # valuation time, swallowed by the call site's try/except into an
        # absent method.
        for k, t in _doc()["templates"].items():
            v = t.get("holdco_discount", [0.0, 0.0])
            assert isinstance(v, list) and len(v) == 2, (k, v)
            assert all(isinstance(x, (int, float)) for x in v), (k, v)

    def test_the_amer_division_carries_the_owner_sourced_parameters(self):
        d = _div(AMER)
        assert d["basis"] == "market_stake"
        assert d["listed"] == ASSOC
        assert d["stake_pct"] == pytest.approx(0.4250)
        assert d["discount_pct"] == pytest.approx(0.15)

    def test_the_core_division_carries_a_band_not_a_point(self):
        d = _div(CORE)
        assert d["basis"] == "ev_ebitda_range"
        lo, hi = d["multiple_range"]
        assert lo == pytest.approx(7.0) and hi == pytest.approx(11.0)
        assert lo < hi

    def test_the_group_discount_is_zero_because_the_haircut_is_at_stake_level(self):
        assert _tpl()["holdco_discount"] == [0.0, 0.0]
        assert _div(AMER)["discount_pct"] == pytest.approx(0.15)
        assert "discount_pct" not in _div(CORE)

    def test_the_puma_stake_is_deliberately_unmarked_and_says_so(self):
        assert not any("PUMA" in (d.get("listed") or "").upper()
                       for d in _tpl()["divisions"])
        assert "PUMA SE is deliberately NOT marked" in _tpl()["unmarked_note"]

    def test_the_shares_divergence_is_recorded(self):
        # The engine divides by the line-item count; the quote cross-check that
        # would have caught the 4.8% gap is skipped for HK and SG tickers.
        assert "2.904bn" in _tpl()["shares_note"]
        assert "2.771bn" in _tpl()["shares_note"]

    # ── the correction, pinned so it cannot be quietly reverted ──────────────
    def test_the_note_names_the_convention_that_makes_the_sign_flip(self):
        """The note must say WHICH liquidity definition its figure uses.

        The defect this pins against: v10's `net_debt_note` stated "Group net
        debt CNY 20.590bn at 31 Dec 2025 (total debt CNY 33.435bn less cash and
        equivalents CNY 12.845bn)". 20.590 is `total_debt - cash_and_equivalents`
        exactly, which is FMP's raw `netDebt` field, and
        `_net_debt_net_of_investments` subtracts short-term investments from
        precisely that figure. Anta holds CNY 26.206bn of them, so the engine is
        handed CNY 5.616bn of net CASH at that same year end. A note that quotes
        a net-debt number without naming its definition is unreadable, and this
        one was wrong.
        """
        note = _tpl()["net_debt_note"]
        assert "short-term investments" in note
        assert "_net_debt_net_of_investments" in note
        assert "26.206" in note
        # the corrected figure, and the superseded one kept visible as a correction
        assert "5.616" in note and "12.959" in note
        assert "20.590" in note
        assert "CORRECTED" in note

    def test_the_note_no_longer_claims_the_engine_deducts_the_raw_figure(self):
        """The superseded claim survives only as a quotation, and is contradicted.

        The note deliberately reproduces v10's sentence verbatim inside single
        quotes so the correction is auditable rather than a silent rewrite. So
        the assertion is not "the phrase is gone" -- it is that it appears
        exactly once, as the quoted original, and that the note's own statement
        of what the bridge does says the opposite.
        """
        note = _tpl()["net_debt_note"]
        assert note.count("The engine deducts it in full") == 1
        i = note.index("The engine deducts it in full")
        # it sits inside the quoted v10 text, not in the note's own voice
        assert note.count("'", 0, i) % 2 == 1, "the claim is not inside a quote"
        assert "ADDS it to gross asset value" in note

    def test_the_note_quotes_the_quarter_the_engine_refreshes_to(self):
        # S08.SI and C38U.SI both quote 30 Jun 2026, because
        # `_refresh_balance_sheet_from_latest_quarter` overlays the annual row
        # with the latest quarter. v10 quoted the year end instead.
        assert "30-Jun-2026" in _tpl()["net_debt_note"]

    def test_the_straddle_claim_is_withdrawn_in_the_core_source(self):
        """v10 said the 7-11x band straddled the price. On net cash it does not.

        Measured: 7.0x reads HKD 77.27 (+7.3%) and 11.0x reads HKD 110.35
        (+53.3%), so the ENTIRE band sits above the HKD 72.00 close. The claim
        mattered because "straddles the price rather than asserting upside" was
        part of why the band looked safe to ship.
        """
        src = _div(CORE)["source"]
        assert "STRADDLE CLAIM IS WITHDRAWN" in src
        assert "bda1622f" in src and "93.68" in src
        # the peer evidence is measured and must survive the correction intact
        for peer in ("Top Sports", "Li Ning", "Bosideng", "adidas", "Nike",
                     "On Holding"):
            assert peer in src, peer
        assert "5.3x" in src or "5.33x" in src

    def test_the_source_records_that_the_leg_is_scenario_invariant(self):
        # 93.68 appears in bear, base AND bull on the production run, at weight
        # 0.40 and as the anchor. A reader has to be able to see that from the
        # template, because `sotp_breakdown` is null in the payload and nothing
        # else in the run discloses it.
        src = _div(CORE)["source"]
        assert "SCENARIO-INVARIANT" in src
        assert "113.45" in src and "153.65" in src

    def test_the_v11_doc_line_records_the_correction(self):
        doc = _doc()["_doc"]
        v11 = [l for l in doc if l.startswith("v11:")]
        assert len(v11) == 1
        assert "_net_debt_net_of_investments" in v11[0]
        assert "93.68" in v11[0]
        # v10's line is still there: a correction is an addition, not a rewrite
        assert any(l.startswith("v10:") for l in doc)

    def test_no_other_template_was_touched_by_the_correction(self):
        doc = _doc()
        for k, t in doc["templates"].items():
            if k == TICKER:
                continue
            assert isinstance(t, dict) and t.get("divisions"), k
            assert isinstance(t.get("holdco_discount", [0, 0]), list), k


# ── 3. the trap the instruction's scalar would have hit ─────────────────────
class TestTheScalarDiscountTrap:
    def test_a_scalar_holdco_discount_raises_at_valuation_time(self, monkeypatch):
        """The owner's `"holdco_discount": 0.15` unpacks into two names."""
        monkeypatch.setattr(h, "template_for", lambda t: {
            "name": "x", "currency": "HKD", "holdco_discount": 0.15,
            "divisions": [{"name": "core", "basis": "ev_ebitda_range",
                           "multiple_range": [7.0, 11.0]}]})
        monkeypatch.setattr(h, "residual_ebitda", lambda *a, **k: 1e9)
        with pytest.raises(TypeError):
            h.look_through_value(TICKER, END)

    def test_a_scalar_survives_loading_and_only_fails_when_used(self):
        # json.loads is perfectly happy with it: there is no schema to reject.
        loaded = json.loads('{"holdco_discount": 0.15, "divisions": []}')
        assert loaded["holdco_discount"] == 0.15

    def test_the_crash_is_swallowed_by_the_call_site_into_an_absent_method(self):
        """Why that TypeError is worse than it looks.

        `_promote_lookthrough_sotp` wraps `can_value` in
        `try/except Exception: return profile_data, False`, and the valuation
        call site wraps the whole look-through in one too. A malformed template
        does not fail loudly; it makes SOTP / NAV not exist, and the profile
        falls back to EV/EBITDA and DCF as though nothing had been written.
        """
        body = inspect.getsource(da._promote_lookthrough_sotp)
        assert "except Exception" in body
        assert "return profile_data, False" in body


# ── 4. the bridge arithmetic, network stubbed ───────────────────────────────
def _stub(monkeypatch, *, amer_usd=_AMER_MCAP_USD, ebitda_cny=_GROUP_EBITDA_CNY):
    monkeypatch.setattr(h, "template_for", lambda t: _tpl())
    monkeypatch.setattr(h, "_market_value",
                        lambda listed, end: amer_usd if listed == ASSOC else None)

    def _ebitda(t, end):
        if t == TICKER:
            return (ebitda_cny, "CNY")
        if t == ASSOC:
            return (_AMER_EBITDA_USD, "USD")
        return None
    monkeypatch.setattr(h, "_ebitda_of", _ebitda)
    monkeypatch.setattr(h, "_net_debt_of", lambda t, end: None)

    def _fx(a, b):
        if a == b:
            return 1.0
        return {("USD", "HKD"): _USD_HKD, ("CNY", "HKD"): _CNY_HKD,
                ("USD", "CNY"): _USD_CNY,
                ("HKD", "CNY"): 1.0 / _CNY_HKD}.get((a, b))
    monkeypatch.setattr(h, "_fx", _fx)


class TestTheBridge:
    def test_the_look_through_completes_with_nothing_skipped(self, monkeypatch):
        _stub(monkeypatch)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert res is not None
        assert res["complete"] is True
        assert res["skipped"] == []
        assert res["reporting_currency"] == "HKD"

    def test_the_stake_is_marked_at_market_and_haired_once(self, monkeypatch):
        _stub(monkeypatch)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        amer = next(p for p in res["parts"] if p["division"] == AMER)
        assert amer["basis"] == "market_stake"
        assert amer["currency"] == "USD"
        assert amer["stake_pct"] == pytest.approx(0.425)
        assert amer["fx_to_reporting"] == pytest.approx(_USD_HKD)
        assert amer["gross_value"] == pytest.approx(
            _AMER_MCAP_USD * 0.425 * _USD_HKD, rel=1e-9)
        assert amer["value"] == pytest.approx(amer["gross_value"] * 0.85, rel=1e-12)
        assert amer["value"] < amer["gross_value"]

    def test_the_core_takes_the_band_midpoint(self, monkeypatch):
        _stub(monkeypatch)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        core = next(p for p in res["parts"] if p["division"] == CORE)
        ebitda_hkd = _GROUP_EBITDA_CNY * _CNY_HKD
        assert core["ebitda"] == pytest.approx(ebitda_hkd, rel=1e-9)
        assert core["value_low"] == pytest.approx(ebitda_hkd * 7.0, rel=1e-9)
        assert core["value_high"] == pytest.approx(ebitda_hkd * 11.0, rel=1e-9)
        assert core["value"] == pytest.approx(ebitda_hkd * 9.0, rel=1e-9)
        assert core["discount_pct"] == 0.0, "the haircut is on the stake, not the core"

    def test_the_band_width_does_not_move_the_answer(self, monkeypatch):
        """`_mid = ebitda * (lo + hi) / 2.0` feeds `value`, so a 7-11x band and
        a 9-9x band produce the SAME NAV. `multiple_range` is a point estimate
        with a disclosure band, not a range of answers, and nothing in the
        output says so. Pinned so nobody reads the band as a confidence
        interval on the valuation."""
        _stub(monkeypatch)
        wide = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        narrow = json.loads(json.dumps(_tpl()))
        narrow["divisions"][1]["multiple_range"] = [9.0, 9.0]
        monkeypatch.setattr(h, "template_for", lambda t: narrow)
        point = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert wide["net_asset_value"] == pytest.approx(
            point["net_asset_value"], rel=1e-12)
        assert wide["gross_asset_value"] == pytest.approx(
            point["gross_asset_value"], rel=1e-12)

    def test_the_group_discount_is_applied_once_and_is_zero(self, monkeypatch):
        _stub(monkeypatch)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert res["holdco_discount"] == 0.0
        assert res["holdco_discount_range"] == [0.0, 0.0]
        gross = sum(p["value"] for p in res["parts"])
        assert res["gross_asset_value"] == pytest.approx(gross, rel=1e-12)
        assert res["net_asset_value"] == pytest.approx(gross - ND_HKD, rel=1e-9)
        assert res["parent_net_debt"] == pytest.approx(ND_HKD, rel=1e-9)

    def test_net_debt_is_deducted_because_the_core_is_an_enterprise_value(self, monkeypatch):
        _stub(monkeypatch)
        with_debt = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        without = h.look_through_value(TICKER, END, net_debt=None)
        assert without["parent_net_debt"] == 0.0
        assert (without["net_asset_value"] - with_debt["net_asset_value"]
                == pytest.approx(ND_HKD, rel=1e-9))

    def test_the_core_basis_is_not_an_equity_basis(self):
        # This is what makes `_needs_debt` True at the dcf_agent call site:
        # `_any_ev = any(basis not in EQUITY_BASES)`. If ev_ebitda_range were an
        # equity basis the engine would pass net_debt=None and the SOTP would
        # count debt-financed assets as equity.
        assert "ev_ebitda_range" not in h.EQUITY_BASES
        assert "market_stake" in h.EQUITY_BASES
        bases = {d["basis"] for d in _tpl()["divisions"]}
        assert bases == {"market_stake", "ev_ebitda_range"}
        assert not bases <= h.EQUITY_BASES

    def test_value_per_share_is_nav_over_shares_in_the_listing_currency(self, monkeypatch):
        _stub(monkeypatch)
        vps = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                net_debt=ND_HKD)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert vps == pytest.approx(res["net_asset_value"] / SHARES, rel=1e-9)
        # Arithmetic on the SUPERSEDED net-debt input, kept as a pin on the
        # bridge's division and currency handling. It is NOT what production
        # publishes -- see test_..._on_the_net_cash_the_engine_actually_passes.
        assert vps == pytest.approx(81.21, abs=0.02)
        assert vps / PRICE - 1.0 == pytest.approx(0.1279, abs=0.0005)

    def test_the_band_ends_bracket_the_price_on_the_superseded_net_debt(self, monkeypatch):
        _stub(monkeypatch)
        out = {}
        for label, band in (("low", [7.0, 7.0]), ("high", [11.0, 11.0])):
            variant = json.loads(json.dumps(_tpl()))
            variant["divisions"][1]["multiple_range"] = band
            monkeypatch.setattr(h, "template_for", lambda t, _v=variant: _v)
            v = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                  net_debt=ND_HKD)
            out[label] = v / PRICE - 1.0
        assert out["low"] == pytest.approx(-0.1023, abs=0.0005)
        assert out["high"] == pytest.approx(0.3582, abs=0.0005)
        assert out["low"] < 0.0 < out["high"]

    # ── the same bridge on the input the engine ACTUALLY passes ──────────────
    def test_the_leg_production_publishes_on_the_net_cash_it_is_handed(self, monkeypatch):
        """Reproduce run bda1622f's `SOTP / NAV` = 93.68 from the bridge alone.

        This is the test that would have caught the v10 note's error before it
        shipped. It passes net CASH where the earlier tests pass net debt, and
        asserts against the number the deployed engine published rather than
        against a preview I computed by hand.
        """
        _stub(monkeypatch)
        vps = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                net_debt=ND_CASH_HKD)
        assert vps == pytest.approx(_PROD_SOTP_LEG, abs=0.35)
        assert vps / PRICE - 1.0 == pytest.approx(0.301, abs=0.005)

    def test_net_cash_raises_nav_above_gross_asset_value(self, monkeypatch):
        """A negative net debt is ADDED, so NAV > GAV. Pinned because it is the
        whole mechanism the v10 note got backwards."""
        _stub(monkeypatch)
        res = h.look_through_value(TICKER, END, net_debt=ND_CASH_HKD)
        assert res["net_asset_value"] > res["gross_asset_value"]
        assert res["net_asset_value"] == pytest.approx(
            res["gross_asset_value"] - ND_CASH_HKD, rel=1e-9)
        assert res["parent_net_debt"] == pytest.approx(ND_CASH_HKD, rel=1e-9)

    def test_the_37bn_swing_between_the_two_inputs(self, monkeypatch):
        """The gap between the superseded note and production, per share."""
        _stub(monkeypatch)
        a = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                              net_debt=ND_RAW_HKD)
        b = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                              net_debt=ND_CASH_HKD)
        assert b - a == pytest.approx((ND_RAW_HKD - ND_CASH_HKD) / SHARES, rel=1e-9)
        assert b - a == pytest.approx(12.78, abs=0.02)
        assert b > a

    def test_the_band_does_not_bracket_the_price_on_the_live_input(self, monkeypatch):
        """WITHDRAWN CLAIM, pinned as withdrawn.

        v10's `source` said the 7-11x band straddled the price "rather than
        asserting upside", which was part of why it looked safe to ship. On the
        net cash the engine actually passes, the whole band is above the close.
        """
        _stub(monkeypatch)
        out = {}
        for label, band in (("low", [7.0, 7.0]), ("high", [11.0, 11.0])):
            variant = json.loads(json.dumps(_tpl()))
            variant["divisions"][1]["multiple_range"] = band
            monkeypatch.setattr(h, "template_for", lambda t, _v=variant: _v)
            out[label] = h.value_per_share(TICKER, END, SHARES,
                                           to_currency="HKD",
                                           net_debt=ND_CASH_HKD) / PRICE - 1.0
        # MEASURED THROUGH THE STUB, not the production figures quoted in the
        # template's `source`. The stub converts CNY->HKD at 1.17371 against
        # the run's 1.1704 and carries core EBITDA of HKD 24.0704bn against the
        # run's 24.011bn, so its band ends read +7.53% / +53.57% where
        # production reads +7.33% / +53.26%. Asserting production's numbers
        # through the stub's arithmetic is the same category of mistake as the
        # v10 note -- mixing a figure from one input set into a computation run
        # on another -- and it is what this test did on its first draft.
        assert out["low"] == pytest.approx(0.07526, abs=0.0005)
        assert out["high"] == pytest.approx(0.53574, abs=0.0005)
        assert out["low"] > 0.0, "the band no longer straddles the price"
        # and the production figures, as pure arithmetic on the run's own
        # constants -- no stub, no bridge, nothing to drift
        assert (_CORE_EBITDA_HKD_PROD * 7.0 + _AMER_NET_HKD_PROD
                - ND_CASH_HKD) / SHARES / PRICE - 1.0 == pytest.approx(
                    0.0733, abs=0.0005)
        assert (_CORE_EBITDA_HKD_PROD * 11.0 + _AMER_NET_HKD_PROD
                - ND_CASH_HKD) / SHARES / PRICE - 1.0 == pytest.approx(
                    0.5326, abs=0.0005)

    def test_each_turn_of_the_multiple_is_worth_ebitda_over_shares(self, monkeypatch):
        """The sensitivity that decides the answer is the multiple, not the width.

        HKD 8.27/share per turn, so the 1.98x-20.48x range the peer set is
        actually priced across is worth more than the whole stake.
        """
        _stub(monkeypatch)
        per_turn = None
        prev = None
        for x in (5.33, 7.0, 9.0, 9.29, 11.0):
            variant = json.loads(json.dumps(_tpl()))
            variant["divisions"][1]["multiple_range"] = [x, x]
            monkeypatch.setattr(h, "template_for", lambda t, _v=variant: _v)
            v = h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                  net_debt=ND_CASH_HKD)
            if prev is not None:
                step = (v - prev[1]) / (x - prev[0])
                per_turn = step if per_turn is None else per_turn
                assert step == pytest.approx(per_turn, rel=1e-6)
            prev = (x, v)
        assert per_turn == pytest.approx(_CORE_EBITDA_HKD_PROD / SHARES, rel=0.01)
        assert per_turn == pytest.approx(8.27, abs=0.02)

    def test_the_leg_does_not_vary_with_the_scenario_inputs(self, monkeypatch):
        """93.68 is identical in bear, base and bull on the production run.

        MY FIRST DRAFT OF THIS TEST ASSERTED THE WRONG MECHANISM. It claimed
        `look_through_value` "takes no scenario argument at all". It does: there
        is a `scenario_toggles` parameter, feeding `_apply_toggles`, which can
        substitute a whole division's values via `toggle` + `toggle_override`.
        The invariance is NOT a property of the signature.

        It is a property of the CONTENTS, and three of them, each pinned:
          1. THIS template declares no `scenario_toggles` and no division
             carries a `toggle`, so `_apply_toggles` is a pass-through for it.
             (Measured, not assumed: exactly one of the fifteen templates uses
             the channel at all -- BIDU's `enable_kunlunxin_ipo_bull_case`,
             defaulting False. My first draft of this test claimed the channel
             was dead file-wide, which is wrong, and it is wrong in an
             interesting way: because of 3 below, BIDU's toggle cannot be
             switched on by the engine either, so the +US$79/ADS Kunlunxin case
             its docstring prices is reachable only from a test.)
          2. this template's two bases cannot move with a scenario in any case:
             a `market_stake` is marked at market and an `ev_ebitda_range` sits
             at a fixed midpoint, `(lo + hi) / 2`;
          3. the one production call site (`dcf_agent.py:5233`) passes no
             scenario argument, so even a template that DID declare a toggle
             would never be switched on by the engine.

        Consequence, and the reason this is worth a test: promoting the leg to
        anchor puts a scenario-blind number at the largest weight in the blend.
        The published spread compresses to 1.35x bear-to-bull against 1.68x for
        ICE, 1.79x for V and 1.55x for SCHW on the same engine the same day. It
        also cannot produce a bear>base inversion -- it dampens all three
        equally -- so `GATE_SCENARIO_ORDERING` is structurally blind to it.
        """
        import inspect
        sig = inspect.signature(h.look_through_value)
        # the channel EXISTS -- pinning its absence would be pinning a falsehood
        assert "scenario_toggles" in sig.parameters

        # 1. unused by THIS template; used by exactly one of the fifteen
        assert not _tpl().get("scenario_toggles")
        assert not any(d.get("toggle") for d in _tpl()["divisions"])
        users = [k for k, t in _doc()["templates"].items()
                 if t.get("scenario_toggles")
                 or any(d.get("toggle") for d in (t.get("divisions") or []))]
        assert users == ["BIDU"], users

        # 2. neither of this template's bases is scenario-aware
        assert [d["basis"] for d in _tpl()["divisions"]] == [
            "market_stake", "ev_ebitda_range"]

        # 3. the engine passes nothing, so switching a toggle on changes nothing
        _stub(monkeypatch)
        base = h.look_through_value(TICKER, END, net_debt=ND_CASH_HKD)
        for toggles in (None, {}, {"anything": True},
                        {"amer_sports_listed": True, "core_multiple": False}):
            got = h.look_through_value(TICKER, END, net_debt=ND_CASH_HKD,
                                       scenario_toggles=toggles)
            assert got["net_asset_value"] == pytest.approx(
                base["net_asset_value"], rel=1e-12)
            assert got["scenario_toggles"] == {}
        vals = [h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                  net_debt=ND_CASH_HKD) for _ in range(3)]
        assert len(set(vals)) == 1

    def test_the_production_call_site_passes_no_scenario_argument(self):
        """Pin 3 above, read off the source rather than inferred from a result.

        A template that declared a toggle would still never fire, because the
        engine's only call into the bridge passes four keyword arguments and
        `scenario_toggles` is not one of them. If a future change starts passing
        it, this fails and the question "is the anchor leg scenario-aware now?"
        gets asked out loud instead of being answered by a production run.
        """
        import inspect
        import src.agents.analysis.dcf_agent as da
        src_txt = inspect.getsource(da)
        i = src_txt.index("holdco_sotp.value_per_share(")
        call = src_txt[i:i + 400]
        assert "scenario_toggles" not in call.split(")")[0]
        assert "net_debt=_nd" in call
        assert "to_currency=_listing_ccy" in call

    def test_an_unavailable_stake_declines_the_whole_look_through(self, monkeypatch):
        _stub(monkeypatch, amer_usd=None)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert res["complete"] is False
        assert any(s["division"] == AMER for s in res["skipped"])
        assert h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                 net_debt=ND_HKD) is None
        assert AMER in (h.decline_reason(TICKER, END) or "")
        assert h.can_value(TICKER, END) is False

    def test_an_unsourced_stake_percentage_also_declines(self, monkeypatch):
        tpl = json.loads(json.dumps(_tpl()))
        tpl["divisions"][0]["stake_pct"] = None
        _stub(monkeypatch)
        monkeypatch.setattr(h, "template_for", lambda t: tpl)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert res["complete"] is False
        assert h.value_per_share(TICKER, END, SHARES, to_currency="HKD",
                                 net_debt=ND_HKD) is None

    def test_no_usd_hkd_rate_declines_rather_than_assuming_parity(self, monkeypatch):
        _stub(monkeypatch)
        monkeypatch.setattr(h, "_fx", lambda a, b: 1.0 if a == b else None)
        res = h.look_through_value(TICKER, END, net_debt=ND_HKD)
        assert res is None or res["complete"] is False

    def test_zero_shares_is_refused_not_divided(self, monkeypatch):
        _stub(monkeypatch)
        assert h.value_per_share(TICKER, END, 0, to_currency="HKD",
                                 net_debt=ND_HKD) is None
        assert h.value_per_share(TICKER, END, None, to_currency="HKD",
                                 net_debt=ND_HKD) is None


# ── 5. the associate is equity-accounted, so nothing is subtracted twice ────
class TestTheAssociateIsNotConsolidated:
    def test_residual_ebitda_passes_the_group_figure_through(self, monkeypatch):
        """42.5% < 50%, so Amer is equity-accounted: its EBITDA was never in
        Anta's group figure and must not be taken out of it."""
        _stub(monkeypatch)
        got = h.residual_ebitda(TICKER, END, "HKD")
        assert got == pytest.approx(_GROUP_EBITDA_CNY * _CNY_HKD, rel=1e-9)
        assert got / (_GROUP_EBITDA_CNY * _CNY_HKD) == pytest.approx(1.0, rel=1e-12)

    def test_residual_ebitda_only_fires_with_exactly_one_multiple_division(self):
        _SELF_VALUING = {"market_stake", "transaction_anchor", "cap_rate",
                         "ev_ebit_range", "nil", "pe_range", "fixed_value",
                         "revenue_multiple"}
        pending = [d for d in _tpl()["divisions"] if d["basis"] not in _SELF_VALUING]
        assert len(pending) == 1, (
            "residual_ebitda returns None unless exactly ONE division awaits "
            "EBITDA. A third division on a multiple basis would silently make "
            "the whole look-through decline, and decline_reason would blame the "
            "missing EBITDA rather than the shape of the template."
        )
        assert pending[0]["name"] == CORE
        assert "ev_ebitda_range" not in _SELF_VALUING

    def test_parent_net_debt_removes_nothing_for_a_minority_stake(self, monkeypatch):
        _stub(monkeypatch)
        calls = []

        def _nd(t, end):
            calls.append(t)
            return (1.0e9, "USD")
        monkeypatch.setattr(h, "_net_debt_of", _nd)
        got = h.parent_net_debt(TICKER, END, ND_HKD, "HKD")
        assert got == pytest.approx(ND_HKD, rel=1e-12)
        assert calls == [], "Amer's borrowings were never consolidated"

    def test_the_stake_would_be_subtracted_if_it_crossed_fifty_percent(self, monkeypatch):
        """The 0.5 threshold is the whole reason the pass-through is correct.

        Same template, same stubs, one number changed: at 51% Amer's EBITDA and
        its net debt both become consolidated, so both must come out. This is
        what proves the 42.5% branch is a threshold and not a hard-coded skip.
        """
        tpl = json.loads(json.dumps(_tpl()))
        tpl["divisions"][0]["stake_pct"] = 0.51
        _stub(monkeypatch)
        monkeypatch.setattr(h, "template_for", lambda t: tpl)
        monkeypatch.setattr(h, "_net_debt_of", lambda t, end: (2.0e9, "USD"))

        resid = h.residual_ebitda(TICKER, END, "HKD")
        assert resid == pytest.approx(
            (_GROUP_EBITDA_CNY - _AMER_EBITDA_USD * _USD_CNY) * _CNY_HKD, rel=1e-9)
        assert resid < _GROUP_EBITDA_CNY * _CNY_HKD

        pnd = h.parent_net_debt(TICKER, END, ND_HKD, "HKD")
        assert pnd == pytest.approx(ND_HKD - 2.0e9 * _USD_HKD, rel=1e-9)


# ── 6. the promote rewires the profile ─────────────────────────────────────
def _anta_profile():
    """The measured `Apparel / Athletic Wear` rows for this ticker."""
    return {"name": "Apparel / Athletic Wear", "methods": [
        {"name": "EV/EBITDA", "weight": 0.40, "anchor": True, "implementable": True},
        {"name": "DCF (FCF+)", "weight": 0.30, "anchor": False, "implementable": True},
        {"name": "P/E (norm)", "weight": 0.20, "anchor": False, "implementable": True},
        {"name": "Brand Val", "weight": 0.10, "anchor": False, "implementable": False},
    ]}


def _patch_promote(monkeypatch, *, enabled=True, can=True):
    monkeypatch.setattr(da, "_accepted_division_ebitda", lambda t: {})
    monkeypatch.setattr(h, "enabled_for", lambda t: enabled)
    monkeypatch.setattr(h, "can_value", lambda t, e, **k: can)


class TestThePromote:
    def test_it_inserts_sotp_nav_as_the_anchor_at_040(self, monkeypatch):
        _patch_promote(monkeypatch)
        out, fired = da._promote_lookthrough_sotp(_anta_profile(), TICKER, END)
        assert fired is True
        w = {m["name"]: m["weight"] for m in out["methods"]}
        assert w["SOTP / NAV"] == pytest.approx(da._LOOKTHROUGH_PROMOTE_WEIGHT)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)
        sotp = next(m for m in out["methods"] if m["name"] == "SOTP / NAV")
        assert sotp["anchor"] is True and sotp["implementable"] is True

    def test_it_demotes_the_existing_anchor_and_scales_the_rest(self, monkeypatch):
        _patch_promote(monkeypatch)
        out, _ = da._promote_lookthrough_sotp(_anta_profile(), TICKER, END)
        scale = 1.0 - da._LOOKTHROUGH_PROMOTE_WEIGHT
        for before, after in zip(_anta_profile()["methods"], out["methods"]):
            assert after["name"] == before["name"]
            assert after["weight"] == pytest.approx(before["weight"] * scale)
            assert after["anchor"] is False, before["name"]
        assert sum(1 for m in out["methods"] if m.get("anchor")) == 1

    def test_it_is_copy_on_write(self, monkeypatch):
        _patch_promote(monkeypatch)
        prof = _anta_profile()
        out, _ = da._promote_lookthrough_sotp(prof, TICKER, END)
        assert out is not prof
        assert len(prof["methods"]) == 4, "the input profile must not be mutated"
        assert prof["methods"][0]["anchor"] is True
        assert prof["methods"][0]["weight"] == pytest.approx(0.40)

    def test_it_fires_on_the_raw_spelling_too(self, monkeypatch):
        _patch_promote(monkeypatch)
        out, fired = da._promote_lookthrough_sotp(_anta_profile(), RAW, END)
        assert fired is True
        assert any(m["name"] == "SOTP / NAV" for m in out["methods"])

    def test_it_no_ops_when_a_sotp_anchor_is_already_present(self, monkeypatch):
        _patch_promote(monkeypatch)
        prof = _anta_profile()
        prof["methods"].append({"name": "SOTP / NAV", "weight": 0.5, "anchor": True})
        out, fired = da._promote_lookthrough_sotp(prof, TICKER, END)
        assert fired is False and out is prof

    def test_it_no_ops_when_the_feature_flag_is_off(self, monkeypatch):
        _patch_promote(monkeypatch, enabled=False)
        prof = _anta_profile()
        out, fired = da._promote_lookthrough_sotp(prof, TICKER, END)
        assert fired is False and out is prof

    def test_it_no_ops_when_the_look_through_cannot_complete(self, monkeypatch):
        _patch_promote(monkeypatch, can=False)
        prof = _anta_profile()
        out, fired = da._promote_lookthrough_sotp(prof, TICKER, END)
        assert fired is False and out is prof

    def test_it_never_touches_a_name_that_is_not_promoted(self, monkeypatch):
        _patch_promote(monkeypatch)
        prof = _anta_profile()
        out, fired = da._promote_lookthrough_sotp(prof, "NKE", END)
        assert fired is False and out is prof

    def test_it_refuses_an_empty_profile(self, monkeypatch):
        _patch_promote(monkeypatch)
        assert da._promote_lookthrough_sotp(None, TICKER, END) == (None, False)
        empty = {"methods": []}
        assert da._promote_lookthrough_sotp(empty, TICKER, END) == (empty, False)


# ── 7. the SOTP-family weight-split guard still engages ─────────────────────
class TestTheAnalystSplitGuard:
    def test_sotp_nav_is_in_the_sotp_led_family(self):
        assert "SOTP / NAV" in da._SOTP_LED_METHODS

    def test_a_promoted_profile_makes_the_analyst_share_not_sweep(self, monkeypatch):
        """The reason the owner's additive bridge is not needed.

        `_promote_sotp_analyst_profile` splits its 3.0 blend weight across
        every `_SOTP_LED_METHODS` member present plus one. Before the promote
        Anta had no such member, so an extractor-built analyst SOTP note would
        have taken 3.0 of a 4.0 total -- 75% of the blend -- and published
        whatever the note said. That is the BN4.SI failure the guard was written
        for, and 75% is the same share the engine's own comment records for it
        ("the extractor's S$7.12 outvoted them 75% to 15%"). After the promote
        the analyst takes 1.5 of 4.0 -- 37.5% -- and the curated look-through
        keeps the larger share at 1.90.
        """
        _patch_promote(monkeypatch)
        promoted, fired = da._promote_lookthrough_sotp(_anta_profile(), TICKER, END)
        assert fired is True

        after = da._promote_sotp_analyst_profile(promoted, True)
        w = {m["name"]: m["weight"] for m in after["methods"]}
        share = da._SOTP_ANALYST_BLEND_WEIGHT / 2.0
        assert share == pytest.approx(1.5)
        assert w["SOTP (analyst)"] == pytest.approx(share)
        assert w["SOTP / NAV"] == pytest.approx(0.40 + share)
        assert w["SOTP / NAV"] > w["SOTP (analyst)"]
        assert w["SOTP (analyst)"] / sum(w.values()) == pytest.approx(0.375, abs=1e-6)

    def test_without_the_promote_the_same_note_would_have_swept(self):
        before = da._promote_sotp_analyst_profile(_anta_profile(), True)
        w = {m["name"]: m["weight"] for m in before["methods"]}
        assert w["SOTP (analyst)"] == pytest.approx(da._SOTP_ANALYST_BLEND_WEIGHT)
        # 3.0 of a 4.0 total: the raw profile's four rows sum to 1.0 and the
        # analyst leg is added on top at the full blend weight, because there
        # is no `_SOTP_LED_METHODS` peer to split it with.
        assert sum(w.values()) == pytest.approx(4.0, abs=1e-9)
        assert w["SOTP (analyst)"] / sum(w.values()) == pytest.approx(0.75, abs=1e-6)
        assert "SOTP / NAV" not in w

    def test_no_assumptions_means_no_analyst_leg_at_all(self, monkeypatch):
        _patch_promote(monkeypatch)
        promoted, _ = da._promote_lookthrough_sotp(_anta_profile(), TICKER, END)
        assert da._promote_sotp_analyst_profile(promoted, False) is promoted
