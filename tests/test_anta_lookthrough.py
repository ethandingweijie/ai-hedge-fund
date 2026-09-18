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
ND_HKD = 24.166e9                     # CNY 20.590bn x 1.17371
PRICE = 72.00                         # HKD close, 17 Sep 2026

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
        assert _doc()["version"] == 10

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
        assert vps == pytest.approx(81.21, abs=0.02)
        assert vps / PRICE - 1.0 == pytest.approx(0.1279, abs=0.0005)

    def test_the_band_ends_bracket_the_price_rather_than_asserting_upside(self, monkeypatch):
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
