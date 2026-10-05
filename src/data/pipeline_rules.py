"""Deterministic rules for drug-pipeline inputs (owner, 2026-10-05).

Every error found when VRTX's Gemini pre-fill was re-checked maps to a rule here, so the same
class of error is caught on every ticker without a reviewer having to spot it:

  R1 single-asset peak   atumelnant carried Vertex's $5bn Palsonify + atumelnant COMBINED figure
  R2 launch vs trial     inaxaplin launched 2027 although its interim read out in early 2027
  R3 exclusivity         povetacicept's 2035 expiry sat before its 2039 peak
  R4 PTRS band           probabilities quoted from undated or generic sources (applied in the
                         engine conversion: an override is held within +/-15pp of the table)
  R5 completeness        zimislecel, a live Phase 3, was missing
  R6 freshness           a withdrawn filing timeline sat in an accepted entry

R1-R3 and R5 are HARD checks (industry_inputs.HARD_CHECKS): a failure blocks acceptance and
the entry is rebuilt. R6 never quarantines a leg; it flags the run.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Optional

#: R2: months from a Phase 3 primary completion to launch (about a year to file, ~10 months of
#: review) and from a Phase 2 primary completion (adds a pivotal decision).
LAUNCH_LAG_MONTHS = {"phase_3": 22, "phase_2": 34}
#: R3: US regulatory exclusivity from approval, years.
EXCLUSIVITY_YEARS = {"biologic": 12, "cell_gene": 12, "small_molecule": 5}
ORPHAN_YEARS = 7
#: R4: an asset-specific PTRS may move the phase x therapeutic-area table by at most this much,
#: and its source must be dated within PTRS_MAX_AGE_YEARS of the entry's as-of.
PTRS_BAND = 0.15
PTRS_MAX_AGE_YEARS = 1
#: R6: an acceptance older than this many days, or a dated event (PDUFA, primary completion)
#: passed since the build, flags the run for a re-check.
FRESHNESS_DAYS = 100

_COMBINED_WORDS = re.compile(r"\b(combined|together|collectively|in aggregate|aggregate|total(?:ling)?|portfolio)\b", re.I)
_DEAL_URL = re.compile(r"(to-acquire|acquisition|acquires|merger|completes-acquisition)", re.I)
_STOP = {"the", "and", "for", "with", "inc", "tablets", "tablet", "injection", "oral", "of", "placebo", "matched",
         "formerly", "known", "biological", "vaccine", "standard", "care", "dose", "therapy", "ophthalmic",
         "subcutaneous", "intravenous", "capsule", "capsules", "solution", "plus", "combination", "treatment",
         "investigator", "choice", "best", "supportive", "chemotherapy", "device", "delivery", "via", "drug",
         # salt forms and dosage words: "Irinotecan Hydrochloride Liposome Injection" is irinotecan liposome
         "hydrochloride", "mesylate", "sodium", "maleate", "tosylate", "succinate", "acetate", "phosphate",
         "sulfate", "citrate", "tartrate", "fumarate", "potassium", "calcium", "higher", "lower", "high", "low",
         "monotherapy", "platinum", "based", "long", "acting"}


def _tokens(name: str) -> set[str]:
    """Identifying tokens of a drug name: words of 4+ letters and codes (VX-880, CRN04894, ABP 234 --
    normalised without the separator)."""
    raw = re.findall(r"[A-Za-z]{2,}[- ]?[A-Za-z]?\d{2,}[A-Za-z]?|[A-Za-z]{4,}", name or "")   # SHR-A1811, HRS-9531, IBI363
    return {re.sub(r"[- ]", "", t.lower()) for t in raw if t.lower() not in _STOP}


def _names(a: dict) -> set[str]:
    out = _tokens(a.get("name") or "")
    for al in a.get("aliases") or []:
        out |= _tokens(al)
    return out


def _year(v) -> Optional[int]:
    m = re.search(r"(19|20)\d{2}", str(v or ""))
    return int(m.group(0)) if m else None


def _parse(d) -> Optional[date]:
    """ISO date or year-month (ClinicalTrials.gov gives either); None otherwise."""
    s = str(d or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def _add_months(d: date, m: int) -> date:
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, 1)


def table_ptrs(phase: Optional[str], indication: Optional[str]) -> float:
    """The engine's own default: cumulative phase PoS x therapeutic-area multiplier."""
    from src.data.sector_profiles import normalize_phase, phase_pos, therapeutic_area_pos_multiplier
    pk = normalize_phase(phase)
    if pk == "approved":
        return 1.0
    return max(0.005, min(1.0, phase_pos(pk) * therapeutic_area_pos_multiplier(indication)))


def band_ptrs(value: Optional[float], phase: Optional[str], indication: Optional[str],
              period=None, as_of=None) -> tuple[float, str]:
    """R4: (the PTRS the engine uses, why). An override outside +/-PTRS_BAND of the table is held at
    the band edge; one whose source is older than PTRS_MAX_AGE_YEARS gives way to the table."""
    base = table_ptrs(phase, indication)
    if not isinstance(value, (int, float)) or not 0.0 < value <= 1.0:
        return base, "table"
    if (phase or "") == "approved":
        return 1.0, "approved"
    py, ay = _year(period), _year(as_of)
    if py and ay and ay - py > PTRS_MAX_AGE_YEARS:
        return base, f"table (source {py} older than {PTRS_MAX_AGE_YEARS}y)"
    lo, hi = max(0.005, base - PTRS_BAND), min(1.0, base + PTRS_BAND)
    if value < lo:
        return lo, f"held at table {base:.2f} - {PTRS_BAND:.2f}"
    if value > hi:
        return hi, f"held at table {base:.2f} + {PTRS_BAND:.2f}"
    return float(value), "cited"


def _r1(assets: list[dict]) -> dict:
    bad, unnamed = [], []
    for a in assets:
        ps = a.get("peak_sales") or {}
        quote, url = str(ps.get("quote") or ""), str(ps.get("source_url") or "")
        mine = _names(a)
        others = set().union(*[_names(b) for b in assets if b is not a]) - mine if len(assets) > 1 else set()
        q = quote.lower()
        why = []
        # A snippet that does not repeat the drug's name is common and not the failure mode; a figure
        # that covers more than the asset is (VRTX atumelnant). Unnamed quotes are reported, not failed.
        if mine and not any(t in q for t in mine) and not any(t in url.lower() for t in mine):
            unnamed.append(str(a.get("name")))
        if any(t in q for t in others):
            why.append("quote names another asset")
        if _COMBINED_WORDS.search(quote):
            why.append("quote is a combined/total figure")
        if _DEAL_URL.search(url):
            why.append("source is a deal announcement")
        if why and a.get("phase") != "approved":
            bad.append(f"{a.get('name')}: {'; '.join(why)}")
    detail = "; ".join(bad) if bad else f"no unapproved asset's peak is a combined, other-asset or deal figure"
    if unnamed:
        detail += f"; quote does not name the asset (check the source): {', '.join(unnamed)}"
    return {"check": "single-asset peak sales", "ok": not bad, "detail": detail}


def _launch_floor(a: dict) -> tuple[Optional[int], str]:
    ph = a.get("phase")
    if ph == "filed":
        d = _parse(a.get("pdufa_date"))
        return (d.year, f"PDUFA {d}") if d else (None, "no PDUFA date")
    if ph in LAUNCH_LAG_MONTHS:
        # An accelerated filing on a cited interim analysis (inaxaplin: 48-week interim, early 2027)
        # dates from the interim; otherwise from the registered primary completion.
        d_int = _parse(a.get("interim_readout")) if a.get("interim_source_url") else None
        d = d_int or _parse(a.get("primary_completion"))
        if d:
            lab = "interim readout" if d_int else "primary completion"
            return _add_months(d, LAUNCH_LAG_MONTHS[ph]).year, f"{lab} {d} + {LAUNCH_LAG_MONTHS[ph]} months"
        return None, "no primary completion date"
    return None, ""


def _r2(assets: list[dict]) -> dict:
    """A filed asset needs its PDUFA date and a Phase 3 asset its primary completion date (the rule
    cannot be skipped by leaving the date out); Phase 2 dates are checked when given."""
    bad = []
    for a in assets:
        if a.get("phase") == "approved":
            continue
        floor, basis = _launch_floor(a)
        ly = a.get("launch_year")
        if floor is None:
            if a.get("phase") in ("filed", "phase_3"):
                bad.append(f"{a.get('name')}: {basis}")
        elif not isinstance(ly, int) or ly < floor:
            bad.append(f"{a.get('name')}: launch {ly} before {floor} ({basis})")
    return {"check": "launch year vs trial dates", "ok": not bad,
            "detail": "; ".join(bad) if bad else "every asset launches on or after its trial floor"}


def exclusivity_end(a: dict) -> Optional[int]:
    """R3: the later of the patent expiry and the regulatory floor from launch (modality, orphan)."""
    ly = a.get("launch_year")
    reg = None
    if isinstance(ly, int):
        yrs = EXCLUSIVITY_YEARS.get(str(a.get("modality") or ""), None)
        if a.get("orphan"):
            yrs = max(yrs or 0, ORPHAN_YEARS)
        reg = (ly + yrs) if yrs else None
    pe = a.get("patent_expiry") if isinstance(a.get("patent_expiry"), int) else None
    vals = [v for v in (pe, reg) if v]
    return max(vals) if vals else None


def _r3(assets: list[dict]) -> dict:
    bad = []
    for a in assets:
        if a.get("phase") == "approved":
            continue
        end = exclusivity_end(a)
        peak_y = _year((a.get("peak_sales") or {}).get("period"))
        ly = a.get("launch_year")
        if end and isinstance(ly, int) and end <= ly:
            bad.append(f"{a.get('name')}: exclusivity {end} not after launch {ly}")
        elif end and peak_y and peak_y > end:
            bad.append(f"{a.get('name')}: peak {peak_y} after exclusivity end {end}")
    return {"check": "exclusivity covers the peak", "ok": not bad,
            "detail": "; ".join(bad) if bad else "no peak year falls after its asset's exclusivity"}


def _r5(assets: list[dict], excluded: Iterable[dict], registry: Optional[list[dict]],
        company_pipeline: Optional[list[str]], approved_portfolio: Optional[list[str]] = None) -> dict:
    """Every live Phase 3 the company leads on ClinicalTrials.gov, and every name in its own pipeline
    disclosure, is in the input or excluded with a reason."""
    if registry is None and company_pipeline is None:
        return {"check": "pipeline completeness", "ok": None, "detail": "no registry or company pipeline list supplied"}
    known = set().union(*[_names(a) for a in assets]) if assets else set()
    for x in excluded or []:
        known |= _tokens(str(x.get("name") or ""))
    for n in approved_portfolio or []:
        known |= _tokens(str(n))
    missing = []
    for s in registry or []:
        # Covered when one intervention is FULLY known -- every identifying name in it. A shared
        # ingredient (tezacaftor in Alyftrek and Trikafta) or a comparator alone does not cover a study.
        # A combination string ("TACE+Camrelizumab+Apatinib", "SHR-1316、Paclitaxel") is split into its parts.
        parts = [p for i in (s.get("interventions") or []) for p in re.split(r"[、,;+；，]|\band\b|\bplus\b|\bwith\b|\bor\b", str(i))]
        ivs = [t for t in (_tokens(p) for p in parts) if t]
        if ivs and not any(t <= known for t in ivs):
            missing.append(f"{s.get('nct_id')} ({', '.join((s.get('interventions') or [])[:2])})")
    for n in company_pipeline or []:
        if _tokens(n) and not _tokens(n) & known:
            missing.append(n)
    return {"check": "pipeline completeness", "ok": not missing,
            "detail": ("missing (add or exclude with a reason): " + "; ".join(missing[:12])) if missing
            else f"{len(registry or [])} registry Phase 3 stud(ies) and {len(company_pipeline or [])} disclosed name(s) covered"}


def check(data: dict, registry: Optional[list[dict]] = None,
          company_pipeline: Optional[list[str]] = None) -> list[dict]:
    """R1-R3 and R5 on a pipeline entry's `data`; R4 is applied in the engine conversion."""
    assets = list(data.get("assets") or [])
    return [_r1(assets), _r2(assets), _r3(assets),
            _r5(assets, data.get("excluded_assets") or [], registry, company_pipeline,
                data.get("approved_portfolio") or [])]


HARD = ("single-asset peak sales", "launch year vs trial dates", "exclusivity covers the peak", "pipeline completeness")


def failed_hard(e: dict) -> list[str]:
    """The hard pipeline rules an entry's stored checks fail (an accepted entry keeps pricing; the run says so)."""
    return [c["check"] for c in (e.get("checks") or []) if c.get("check") in HARD and c.get("ok") is False]


def freshness(e: dict, reviewed_at: Optional[str], today: Optional[date] = None) -> Optional[str]:
    """R6: a flag when the accepted entry is older than FRESHNESS_DAYS or a dated event in it has
    passed since it was built; None when fresh."""
    today = today or date.today()
    built = _parse(str(e.get("built_at") or "")[:10])
    rev = _parse(str(reviewed_at or "")[:10]) or built
    why = []
    if rev and (today - rev).days > FRESHNESS_DAYS:
        why.append(f"accepted {(today - rev).days} days ago (> {FRESHNESS_DAYS})")
    for a in (e.get("data") or {}).get("assets") or []:
        for k, lab in (("pdufa_date", "PDUFA"), ("primary_completion", "primary completion")):
            d = _parse(a.get(k))
            if d and built and built < d <= today:
                why.append(f"{a.get('name')} {lab} {d} has passed")
    fh = failed_hard(e)
    if fh:
        why.append("fails rule(s) " + ", ".join(fh) + " (re-verify and re-accept)")
    return ("Pipeline input needs a re-check: " + "; ".join(why)) if why else None


# ── registry lookups (build / verify time only) ───────────────────────────────

#: ClinicalTrials.gov lead-sponsor search terms where the company name FMP reports does not match;
#: several terms when trials run under subsidiaries (Hengrui: Jiangsu / Shanghai / Guangdong Hengrui
#: and Suzhou Suncadia).
SPONSOR_NAMES = {
    "01276.HK": ("Hengrui", "Suzhou Suncadia"), "600276.SS": ("Hengrui", "Suzhou Suncadia"),
    "01801.HK": ("Innovent Biologics",), "09688.HK": ("Zai Lab",), "02197.HK": ("Clover Biopharmaceuticals",),
    "PFE": ("Pfizer",), "MRNA": ("ModernaTX",), "VRTX": ("Vertex Pharmaceuticals",), "CRSP": ("CRISPR Therapeutics",),
    "BEAM": ("Beam Therapeutics",), "KYMR": ("Kymera Therapeutics",),
}


def sponsors_for(ticker: str, company: Optional[str]) -> tuple[str, ...]:
    return SPONSOR_NAMES.get(ticker.upper()) or (sponsor_for(ticker, company),)


def sponsor_for(ticker: str, company: Optional[str]) -> str:
    if ticker.upper() in SPONSOR_NAMES:
        return SPONSOR_NAMES[ticker.upper()][0]
    s = re.sub(r"[,.]?\s*(Incorporated|Inc|Corporation|Corp|Co\.?,?\s*Ltd|Limited|Ltd|plc|N\.V\.|AG|SA)\.?$", "",
               str(company or ticker).strip(), flags=re.I)
    return s.strip()


def enrich_dates(data: dict, fetch=None) -> tuple[dict, list[str]]:
    """Fill (or correct) each Phase 2/3 asset's primary completion from its pivotal trial on
    ClinicalTrials.gov -- the registry is the authority over a model-written date. Returns
    (data, notes). `fetch` is ctgov.study (injectable for tests)."""
    if fetch is None:
        from src.data.ctgov import study as fetch
    notes = []
    for a in data.get("assets") or []:
        if a.get("phase") not in ("phase_2", "phase_3") or not a.get("nct_ids"):
            continue
        dates = [s.get("primary_completion") for s in (fetch(n) for n in a["nct_ids"]) if s and s.get("primary_completion")]
        if not dates:
            continue
        reg = min(dates)
        if a.get("primary_completion") != reg:
            if a.get("primary_completion"):
                notes.append(f"{a.get('name')}: primary completion {a['primary_completion']} -> {reg} (ClinicalTrials.gov)")
            a["primary_completion"] = reg
    return data, notes


def score(data: dict, ticker: str, company: Optional[str], registry_fetch=None) -> list[dict]:
    """The rule checks for an entry, with the sponsor's live Phase 3 studies as the completeness list."""
    if registry_fetch is None:
        from src.data.ctgov import sponsor_late_stage as registry_fetch
    reg, seen = [], set()
    try:
        for sp in sponsors_for(ticker, company):
            for r in registry_fetch(sp) or []:
                if r.get("nct_id") not in seen:
                    seen.add(r.get("nct_id")); reg.append(r)
    except Exception:  # noqa: BLE001
        reg = None
    # An empty list is an answer (the company leads no live Phase 3); None is a failed lookup.
    return check(data, registry=reg)
