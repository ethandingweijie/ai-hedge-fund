"""Reader-text checks for the PM write-up (owner, 2026-11-25: CFA-grade report audit).

Everything here is deterministic and runs after the writer (and the stylist) have
returned. Nothing here touches a valuation number, a rating or a target; it only
decides what the published prose is allowed to say about them.

  * change attribution  -- prior vs current report: spot, blended intrinsic value,
    capture share, rating and target. The target rule is
    target = spot + capture x (IV - spot), so a target move splits exactly into a
    spot effect, an intrinsic-value effect and a capture effect. The writer is
    given this as the ONLY permitted explanation of a change, and the post-check
    removes upgrade / downgrade / capture / "mechanical" claims that contradict it.
  * unit lint    -- basis points printed as "points", "-0.0%", negative "upside".
  * jargon lint  -- operator vocabulary (engine, leg, clamp, norm, rolled, gate,
    cascade) rewritten into plain English.
  * risk theme   -- which theme of the rationale is the risk / bear theme, so the
    length cap never cuts it.
  * risks to rating -- 2-4 computed sentences on what would make the rating or the
    target wrong.
"""
from __future__ import annotations

import re
from typing import Optional

# ── shared helpers ───────────────────────────────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“])")


def _sentences(par: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(par) if s.strip()]


def _money(v, sym: str = "$") -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    neg = f < 0
    f = abs(f)
    s = f"{sym}{f:,.1f}" if f < 20 else f"{sym}{f:,.0f}"
    return ("-" + s) if neg else s


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# ── risk theme ───────────────────────────────────────────────────────────────

RISK_THEME_RE = re.compile(
    r"\b(risks?|bear[- ]case|downside (?:case|scenario|risk)|falsif\w*|would (?:be )?(?:wrong|break)|"
    r"what would (?:change|make)|the (?:main|key|primary) risk)\b", re.IGNORECASE)


def risk_theme_index(themes: list[str]) -> Optional[int]:
    """Index of the risk / bear theme: the LAST theme after the first that names a risk.
    None when no later theme does (theme 1 is always kept anyway)."""
    hit = None
    for i, t in enumerate(themes):
        if i and RISK_THEME_RE.search(t or ""):
            hit = i
    return hit


# ── unit lint ────────────────────────────────────────────────────────────────

def _pp(bps: float) -> str:
    pp = bps / 100.0
    return f"about {pp:.0f} percentage points" if abs(pp) >= 1 else f"about {pp:.1f} percentage points"


_BPS_WORDS = re.compile(r"(?:(about|roughly|around|approximately|some|nearly)\s+)?"
                        r"([+\-−]?\d{1,3}(?:,\d{3})+|[+\-−]?\d{3,})\s*(?:basis\s+points|bps|bp)\b",
                        re.IGNORECASE)
_THOUSAND_POINTS = re.compile(r"(?:(about|roughly|around|approximately|some|nearly)\s+)?"
                              r"(\d{1,3}(?:,\d{3})+)\s+points\b", re.IGNORECASE)
_NEG_ZERO = re.compile(r"[\-−]0(?:\.0+)?%")
_NEG_UPSIDE_BEFORE = re.compile(r"[\-−](\d+(?:\.\d+)?)%(\s+)upside\b", re.IGNORECASE)
_NEG_UPSIDE_AFTER = re.compile(
    r"\bupside((?:\s+(?:of|to\s+(?:our\s+|the\s+)?(?:12-month\s+)?(?:target|PT|price target)))?)(\s+(?:of\s+)?)[\-−](\d+(?:\.\d+)?)%",
    re.IGNORECASE)


def lint_units(text: str) -> tuple[str, list[str]]:
    """Rewrite unit errors in reader text; returns (text, list of what was changed)."""
    hits: list[str] = []
    out = text or ""

    def _bps(m):
        raw = m.group(2).replace(",", "").replace("−", "-")
        hits.append(f"basis points rewritten as percentage points: {m.group(0).strip()!r}")
        return _pp(abs(float(raw)))

    out = _BPS_WORDS.sub(_bps, out)

    def _pts(m):
        raw = float(m.group(2).replace(",", ""))
        hits.append(f"'{m.group(0).strip()}' read as basis points and rewritten")
        return _pp(raw)

    out = _THOUSAND_POINTS.sub(_pts, out)
    if _NEG_ZERO.search(out):
        hits.append("'-0.0%' rewritten as '0%'")
        out = _NEG_ZERO.sub("0%", out)

    def _nu_before(m):
        hits.append(f"negative upside rewritten as downside: {m.group(0)!r}")
        return f"{m.group(1)}%{m.group(2)}downside"

    out = _NEG_UPSIDE_BEFORE.sub(_nu_before, out)

    def _nu_after(m):
        hits.append(f"negative upside rewritten as downside: {m.group(0)!r}")
        word = "Downside" if m.group(0)[0].isupper() else "downside"
        return f"{word}{m.group(1)}{m.group(2)}{m.group(3)}%"

    out = _NEG_UPSIDE_AFTER.sub(_nu_after, out)
    return out, hits


# ── jargon lint ──────────────────────────────────────────────────────────────

#: Operator vocabulary that must not reach an investment committee (owner, 2026-11-25).
BANNED_JARGON = ("engine", "leg", "clamp", "clamped", "norm", "rolled", "gate", "cascade", "weight rolled")

_JARGON_REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bweight (?:was |is )?rolled into\b", re.I), "weight moved to"),
    (re.compile(r"\b(?:was |were |is |are )?rolled (?:into|onto|to)\b", re.I), "moved to"),
    (re.compile(r"\brolled\b", re.I), "moved"),
    (re.compile(r"\(norm\)", re.I), "(normalised)"),
    (re.compile(r"\bnorm\b", re.I), "normalised"),
    (re.compile(r"\bclamped\b", re.I), "capped"),
    (re.compile(r"\bclamping\b", re.I), "capping"),
    (re.compile(r"\bclamps\b", re.I), "caps"),
    (re.compile(r"\bclamp\b", re.I), "cap"),
    (re.compile(r"\blegs\b", re.I), "methods"),
    (re.compile(r"\bleg\b", re.I), "method"),
    (re.compile(r"\b(?:the|our|this) (?:valuation |pricing |model )?engine\b", re.I), "our model"),
    (re.compile(r"\b(?:valuation|pricing|model) engine\b", re.I), "model"),
    (re.compile(r"\bgated\b", re.I), "conditioned"),
    (re.compile(r"\bgates\b", re.I), "checks"),
    (re.compile(r"\bgate\b", re.I), "check"),
    (re.compile(r"\bcascades\b", re.I), "sequences"),
    (re.compile(r"\bcascade\b", re.I), "sequence"),
]
#: Flagged, not rewritten: a jet engine or an earnings engine is a real thing.
_JARGON_FLAG_ONLY = re.compile(r"\bengines?\b", re.I)


def _match_case(src: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if src[:1].isupper() else repl


def lint_jargon(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text or ""
    for pat, repl in _JARGON_REWRITES:
        def _sub(m, _r=repl):
            hits.append(f"jargon {m.group(0)!r} -> {_r!r}")
            return _match_case(m.group(0), _r)
        out = pat.sub(_sub, out)
    for m in _JARGON_FLAG_ONLY.finditer(out):
        hits.append(f"jargon {m.group(0)!r} left in place (flag only)")
    return out, hits


# ── material news since the prior report (owner, 2026-11-27) ─────────────────

_EST_LABELS = {"revenue_growth_fy1": ("FY+1 revenue growth", "pct"),
               "revenue_growth_fy2": ("FY+2 revenue growth", "pct"),
               "ebitda_margin_fy1": ("FY+1 EBITDA margin", "pct"),
               "ebitda_margin_fy2": ("FY+2 EBITDA margin", "pct"),
               "eps_fy1": ("FY+1 EPS", "eps"), "eps_fy2": ("FY+2 EPS", "eps")}


def _est_fmt(v, kind: str, sym: str) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    return f"{f * 100:.1f}%" if kind == "pct" else f"{'-' if f < 0 else ''}{sym}{abs(f):,.2f}"


def material_update_sentence(mu: Optional[dict], sym: str = "$") -> Optional[str]:
    """One computed sentence on what this report did with material news since the
    prior one: folded in (with the base-case estimate changes) or not yet reflected."""
    if not mu or not mu.get("status"):
        return None
    evs = [e for e in (mu.get("events") or []) if e.get("headline")][:3]
    heads = "; ".join((f"{e['date']}: " if e.get("date") else "") + str(e["headline"]) for e in evs)
    if mu["status"] == "not_reflected":
        return ("Rating under review: material news since our previous report"
                + (f" ({heads})" if heads else "")
                + " is not yet reflected in our estimates, so the target predates it.")
    s = "This report incorporates material news since our previous report" + (f" ({heads})" if heads else "") + "."
    ch = mu.get("estimate_changes")
    if ch:
        bits = []
        for c in ch[:4]:
            lab, kind = _EST_LABELS.get(c.get("field"), (str(c.get("field")), "pct"))
            if c.get("prior") is None:
                bits.append(f"{lab} newly estimated at {_est_fmt(c.get('new'), kind, sym)}")
            elif c.get("new") is None:
                bits.append(f"{lab} no longer estimated (was {_est_fmt(c.get('prior'), kind, sym)})")
            else:
                bits.append(f"{lab} {_est_fmt(c.get('prior'), kind, sym)} to {_est_fmt(c.get('new'), kind, sym)}")
        s += " Base-case estimates changed: " + ", ".join(bits) + "."
    elif ch == []:
        s += " Our base-case estimates did not change."
    return s


# ── change attribution ───────────────────────────────────────────────────────

_RATING_RANK = {"overweight": 2, "buy": 2, "neutral": 1, "hold": 1, "cover": 1,
                "underweight": 0, "sell": 0, "short": 0}
_RANK_LABEL = {2: "Overweight", 1: "Neutral", 0: "Underweight"}

_PRIOR_CAPTURE_RE = re.compile(
    r"(\d{1,3})\s*%[-\s]*(?:of[-\s]*(?:the[-\s]*)?(?:spot[-\s]*to[-\s]*IV[-\s]*|IV[-\s]*spot[-\s]*)?gap[-\s]*)?"
    r"(?:convergence|capture)", re.IGNORECASE)


def _rank(label) -> Optional[int]:
    return _RATING_RANK.get(str(label or "").strip().lower())


def build_change_attribution(prior_recap: Optional[dict], *, spot, blended_iv, capture, target,
                             rating_label, capture_reason: str = "", sym: str = "$",
                             material_update: Optional[dict] = None,
                             rating_cause: Optional[str] = None) -> dict:
    """Prior vs current report, decomposed on the target rule. Deterministic.

    `rating_cause` (owner, 2026-11-27): a rule that set the rating this run (the Neutral
    cap on an unreconciled valuation, Under Review). A rating change is then credited to
    it, and a sentence crediting the change to news is removed by check_change_claims."""
    spot, iv1, c1, t1 = _num(spot), _num(blended_iv), _num(capture), _num(target)
    cur = {"spot": spot, "blended_iv": iv1, "capture": c1, "target": t1, "rating": rating_label,
           "capture_reason": capture_reason or None}
    pr = prior_recap or {}
    if not pr:
        return {"has_prior": False, "current": cur, "rating_change": "initiation",
                "sentence": "This is our first report on the stock; there is no prior rating or target to compare.",
                "prompt_block": ("No prior report: this is an initiation. Do not write upgrade, downgrade, "
                                 "maintain, revised or cut.")}
    pj = pr.get("recap_json") or {}
    s0 = _num(pj.get("price_at_run")) or _num(pr.get("price_at_run"))
    t0 = _num(pj.get("price_target"))
    iv0 = _num(pj.get("blended_iv"))
    c0 = _num(pj.get("capture"))
    c0_src = "prior report" if c0 is not None else None
    prior_text = " ".join(str(x or "") for x in (pj.get("rationale"), pr.get("recap_text")))
    if c0 is None:
        m = _PRIOR_CAPTURE_RE.search(prior_text)
        if m and 0 < int(m.group(1)) <= 100:
            c0, c0_src = int(m.group(1)) / 100.0, "prior report text"
    if iv0 is None and c0 and s0 and t0:
        iv0 = s0 + (t0 - s0) / c0                     # implied by the target rule
    if c0 is None and iv0 and s0 and t0 and abs(iv0 - s0) > 1e-9:
        _c = (t0 - s0) / (iv0 - s0)
        if 0 < _c <= 1:
            c0, c0_src = round(_c, 4), "implied by the prior target"
    r0_label = pj.get("rating_label") or pr.get("rating_label")
    r0 = _rank(r0_label) if r0_label else _rank(pr.get("final_action") or pj.get("final_action"))
    r1 = _rank(rating_label)
    if r0 is None or r1 is None:
        rc = "unknown"
    else:
        rc = "unchanged" if r0 == r1 else ("upgrade" if r1 > r0 else "downgrade")
    prior = {"date": str(pr.get("run_at") or "")[:10] or None, "spot": s0, "blended_iv": iv0, "capture": c0,
             "capture_source": c0_src, "target": t0,
             "rating": _RANK_LABEL.get(r0) if r0 is not None else (r0_label or pr.get("final_action"))}

    drivers = None
    capture_changed = None
    if c0 is not None and c1 is not None:
        capture_changed = abs(c0 - c1) > 0.005
    if None not in (s0, t0, spot, t1):
        dt = t1 - t0
        if None not in (c0, iv0, c1, iv1):
            d_spot = (1 - c0) * (spot - s0)
            d_iv = c0 * (iv1 - iv0)
            d_cap = (c1 - c0) * (iv1 - spot)
            drivers = {"spot": round(d_spot, 2), "intrinsic_value": round(d_iv, 2), "capture": round(d_cap, 2),
                       "residual": round(dt - d_spot - d_iv - d_cap, 2)}
    dominant = None
    if drivers:
        dominant = max(("spot", "intrinsic_value", "capture"), key=lambda k: abs(drivers[k]))

    when = f"our previous report on {prior['date']}" if prior["date"] else "our previous report"
    parts: list[str] = []
    r_now = _RANK_LABEL.get(r1) if r1 is not None else str(rating_label or "")
    if rc == "unchanged":
        parts.append(f"We maintain our {r_now} rating from {when}.")
    elif rc in ("upgrade", "downgrade"):
        verb = "upgrade" if rc == "upgrade" else "downgrade"
        parts.append(f"We {verb} to {r_now} from {prior['rating']} in {when}.")
    if t0 and t1:
        pct = (t1 / t0 - 1) * 100
        if abs(pct) < 0.5:
            parts.append(f"The 12-month target is unchanged at about {_money(t1, sym)}.")
        else:
            s = f"The 12-month target moves from {_money(t0, sym)} to {_money(t1, sym)} ({pct:+.0f}%)"
            if drivers:
                bits = []
                if s0 and spot and abs(drivers["spot"]) >= 0.005 * t0:
                    bits.append(f"the share-price move from {_money(s0, sym)} to {_money(spot, sym)} accounts for "
                                f"{_money(drivers['spot'], sym)}")
                if abs(drivers["intrinsic_value"]) >= 0.005 * t0:
                    word = "lower" if iv1 < iv0 else "higher"
                    bits.append(f"a {word} intrinsic value (about {_money(iv0, sym)} to {_money(iv1, sym)}) for "
                                f"{_money(drivers['intrinsic_value'], sym)}")
                if capture_changed:
                    bits.append(f"a change in the share of the price-to-value gap the target closes "
                                f"({c0:.0%} to {c1:.0%}) for {_money(drivers['capture'], sym)}")
                if bits:
                    s += ": " + ", ".join(bits)
            parts.append(s + ".")
    if capture_changed is False:
        parts.append(f"The target still closes {c1:.0%} of the gap between price and intrinsic value; that share did "
                     "not change.")
    elif capture_changed is None and c1 is not None:
        parts.append(f"The target closes {c1:.0%} of the gap between price and intrinsic value.")
    _cause = rating_cause if rc in ("upgrade", "downgrade") and rating_cause else None
    if _cause:
        parts.insert(1 if parts else 0, f"The rating change reflects {_cause}, not a change in our view of the business.")
    _mu_s = material_update_sentence(material_update, sym)
    if _mu_s:
        parts.insert(0, _mu_s)
    sentence = " ".join(parts)

    rating_instr = {
        "unchanged": f"The rating is UNCHANGED ({r_now}): write 'maintain'; never 'upgrade' or 'downgrade'.",
        "upgrade": f"The rating is an UPGRADE from {prior['rating']} to {r_now}.",
        "downgrade": f"The rating is a DOWNGRADE from {prior['rating']} to {r_now}.",
        "unknown": "The prior rating cannot be compared: do not write upgrade or downgrade.",
    }[rc]
    if _cause:
        rating_instr += (f" The cause of the rating change is {_cause}. Do NOT attribute the rating change to "
                         "news, partnerships, results or any business development.")
    block = ("Change since the prior report (computed; the ONLY permitted explanation of any change in rating or "
             f"target -- add no other cause):\n{sentence}\n{rating_instr}")
    if c1 is not None:
        block += (f"\nWhy the target closes {c1:.0%} of the gap: {capture_reason or 'the house target rule'}. "
                  "Do not attribute it to the macro regime, volatility, sentiment or 'conservatism'.")
    if dominant == "intrinsic_value":
        block += "\nThe target move is driven mainly by our lower/higher intrinsic value: do not call it 'mechanical'."
    if _mu_s:
        block += ("\nState the material-news sentence above in the rationale. The news may be cited as the reason "
                  "the estimates moved, never as a reason beyond the computed target change.")
    _evs = [str(e.get("headline") or "") for e in ((material_update or {}).get("events") or []) if e.get("headline")]
    return {"has_prior": True, "material_update_sentence": _mu_s, "rating_cause": _cause,
            "material_headlines": _evs,
            "headline_fallback": (f"{r_now}: rating reflects {_cause}" + (f"; 12-month target {_money(t1, sym)}" if t1 else ""))
            if _cause else None, "prior": prior, "current": cur, "rating_change": rc,
            "target_change": round(t1 - t0, 2) if (t0 and t1) else None,
            "target_change_pct": round((t1 / t0 - 1) * 100, 1) if (t0 and t1) else None,
            "drivers": drivers, "dominant_driver": dominant, "capture_changed": capture_changed,
            "sentence": sentence, "prompt_block": block}


_UP_RE = re.compile(r"\bupgrad\w*", re.I)
_DOWN_RE = re.compile(r"\bdowngrad\w*", re.I)
_OUR_RATING_RE = re.compile(r"\b(overweight|neutral|underweight|buy|hold|sell|rating|from\s+(?:BUY|HOLD|SELL|SHORT))\b",
                            re.I)
_STREET_RE = re.compile(r"\b(analysts?|street|brokers?|credit|moody|s&p global|fitch|agency|consensus)\b", re.I)
_CAPTURE_RE = re.compile(r"\d{1,3}\s*%[-\s]*(?:of[-\s]*(?:the[-\s]*)?(?:IV[-\s]*|spot[-\s]*to[-\s]*IV[-\s]*)?gap[-\s]*)?"
                         r"(?:convergence|capture)|convergence (?:rule|assumption|share)", re.I)
_CAUSAL_RE = re.compile(r"\b(tighter|looser|lower|higher|now|reduction|reduced|revised|cut|raised|"
                        r"application of|new|changed?|moved|shift(?:ed)?)\b", re.I)


def _claims_capture_change(s: str) -> bool:
    """A change word within 60 characters of a capture mention ("a tighter 50% convergence",
    "a 50% convergence rule that now points lower"); a plain statement of the method is not."""
    for m in _CAPTURE_RE.finditer(s):
        if _CAUSAL_RE.search(s[max(0, m.start() - 60):m.end() + 60]):
            return True
    return False
_MACRO_CAUSE_RE = re.compile(r"\b(regime|macro|risk-off|volatil\w*|sentiment|conservat\w*|caution\w*)\b", re.I)
_MECHANICAL_RE = re.compile(r"\bmechanical\b", re.I)
#: Business-news cues: a rating change credited to one of these is wrong when a rule set the rating.
_NEWS_CAUSE_RE = re.compile(r"\b(partner\w*|launch\w*|contract\w*|acqui\w*|guidance|results?|announc\w*|"
                            r"developments?|news|collaborat\w*|deal|rollout|stabili[sz]\w*|momentum|"
                            r"cable|AI)\b", re.I)


def _cites_news(s: str, headlines: list[str]) -> bool:
    low = s.lower()
    for h in headlines or []:
        words = [w for w in re.findall(r"[a-z0-9]+", h.lower()) if len(w) > 3][:3]
        if words and all(w in low for w in words):
            return True
    return bool(_NEWS_CAUSE_RE.search(s))
#: A sentence that only continues a removed claim ("Those conditions cap conviction ...").
_FOLLOW_ON_RE = re.compile(r"^(?:Those|These|This|That|Such)\s+(?:conditions|factors|reasons|dynamics|headwinds|developments?|"
                           r"considerations|circumstances|cautions?)\b", re.I)


def check_change_claims(text: str, attribution: dict,
                        replacement: Optional[str] = None) -> tuple[str, list[str]]:
    """Remove sentences whose change narrative contradicts the computed attribution.
    The first offending sentence is replaced by `replacement` (default: the attribution
    sentence)."""
    if not text or not attribution:
        return text, []
    rc = attribution.get("rating_change")
    cap_changed = attribution.get("capture_changed")
    cur_cap = (attribution.get("current") or {}).get("capture")
    dominant = attribution.get("dominant_driver")
    cause = attribution.get("rating_cause")
    heads = attribution.get("material_headlines") or []
    repl = replacement if replacement is not None else attribution.get("sentence")
    hits: list[str] = []
    replaced = False
    prev_removed = False
    out_pars: list[str] = []
    for par in (text or "").split("\n"):
        kept: list[str] = []
        for s in _sentences(par):
            why = None
            if prev_removed and _FOLLOW_ON_RE.match(s):
                hits.append(f"follow-on to a removed claim: {s[:160]!r}")
                continue
            prev_removed = False
            ours = _OUR_RATING_RE.search(s) and not _STREET_RE.search(s)
            if ours and _UP_RE.search(s) and rc in ("unchanged", "downgrade", "initiation", "unknown"):
                why = f"says 'upgrade' but the rating change is {rc}"
            elif ours and _DOWN_RE.search(s) and rc in ("unchanged", "upgrade", "initiation", "unknown"):
                why = f"says 'downgrade' but the rating change is {rc}"
            elif cap_changed is False and _claims_capture_change(s):
                why = "attributes the change to the convergence share, which did not change"
            elif _CAPTURE_RE.search(s) and cur_cap is not None and _MACRO_CAUSE_RE.search(s):
                why = "attributes the convergence share to a cause the target rule does not use"
            elif _MECHANICAL_RE.search(s) and dominant == "intrinsic_value":
                why = "calls the revision mechanical, but a lower or higher intrinsic value drives it"
            elif (cause and (_UP_RE.search(s) or _DOWN_RE.search(s)) and not _STREET_RE.search(s)
                  and _cites_news(s, heads)):
                why = f"credits the rating change to news, but {cause} set the rating"
            if why:
                prev_removed = True
                hits.append(f"{why}: {s[:160]!r}")
                if not replaced and repl:
                    kept.append(repl)
                    replaced = True
                continue
            kept.append(s)
        if kept:
            out_pars.append(" ".join(kept))
    return "\n".join(out_pars), hits


# ── risks to rating ──────────────────────────────────────────────────────────

def build_risks_to_rating(*, rating_label: Optional[str], spot, target, view: Optional[dict],
                          pt_bridge: Optional[dict], scenario: Optional[dict], trap: Optional[dict],
                          sym: str = "$") -> list[str]:
    """2-4 computed sentences on what would make the rating or the target wrong."""
    out: list[str] = []
    spot, target = _num(spot), _num(target)
    view = view or {}
    bench = view.get("benchmark") or {}
    exp = _num(bench.get("expected_return"))
    yld = _num(view.get("dividend_yield")) or 0.0
    lbl = str(rating_label or view.get("rating_label") or "").strip()
    rank = _rank(lbl)
    scen = scenario or {}
    bridge_sc = (pt_bridge or {}).get("scenarios") or {}

    def _assump(case):
        a = str((scen.get(case) or {}).get("assumptions") or "").strip()
        a = re.split(r"(?<=[.;])\s", a)[0].rstrip(".;") if a else ""
        return a[:160]

    def _rating_at(tr):
        if exp is None or tr is None:
            return None
        from src.decisions.ratings import rate, RATING_LABELS
        return RATING_LABELS[rate(tr, exp)]

    # 1. the adverse scenario, priced on the same target rule
    case = "bull" if rank == 0 else "bear"
    sc = bridge_sc.get(case) or {}
    tgt_c, tr_c = _num(sc.get("target")), _num(sc.get("total_return"))
    iv_c = _num(sc.get("intrinsic_value")) or _num((scen.get(case) or {}).get("fair_value"))
    if spot and tgt_c:
        r_at = _rating_at(tr_c)
        why = _assump(case)
        s = (f"{'Upside' if case == 'bull' else 'Downside'} risk: in our {case} case"
             + (f" ({why})" if why else "")
             + (f" intrinsic value is about {_money(iv_c, sym)} and" if iv_c else "")
             + f" the 12-month target would be {_money(tgt_c, sym)} ({(tgt_c / spot - 1) * 100:+.0f}% from today's price)")
        if r_at and lbl and r_at.lower() != lbl.lower():
            s += f", which would make the rating {r_at}, not {lbl}"
        out.append(s + ".")
    # 2. the target at which the rating changes
    if spot and target and exp is not None and rank is not None:
        thr = 0.05
        if rank == 2:
            t_edge = spot * (1 + exp + thr - yld)
            out.append(f"The rating falls to Neutral if the 12-month target drops below about {_money(t_edge, sym)} at "
                       f"today's price ({(t_edge / target - 1) * 100:+.0f}% versus our target), the point where expected "
                       f"total return no longer beats the benchmark by five percentage points.")
        elif rank == 0:
            t_edge = spot * (1 + exp - thr - yld)
            out.append(f"The rating rises to Neutral if the 12-month target exceeds about {_money(t_edge, sym)} at "
                       f"today's price ({(t_edge / target - 1) * 100:+.0f}% versus our target).")
        elif rank == 1:
            up = spot * (1 + exp + thr - yld)
            dn = spot * (1 + exp - thr - yld)
            out.append(f"The rating stays Neutral while the 12-month target sits between about {_money(dn, sym)} and "
                       f"{_money(up, sym)} at today's price.")
    # 3. value-trap checks that came back RED or AMBER
    for key, label in (("structural_decline", "Structural decline"), ("balance_sheet_deterioration", "Balance sheet"),
                       ("earnings_cashflow_mismatch", "Earnings quality"), ("dividend_sustainability", "Dividend"),
                       ("insider_behaviour", "Insider selling")):
        chk = (trap or {}).get(key) or {}
        if isinstance(chk, dict) and str(chk.get("status") or "").upper() in ("RED", "AMBER") and len(out) < 4:
            ev = re.split(r"(?<=[.])\s", str(chk.get("evidence") or "").strip())[0][:200]
            if ev:
                out.append(f"{label} ({str(chk['status']).upper()}): {ev.rstrip('.')}.")
    return out[:4]
