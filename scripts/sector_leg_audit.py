"""Cross-sector leg audit against the biopharma-pass lessons (VALUATION_REVIEW_LESSONS.md).

Read-only. Three sources of a ``dcf_range`` entry per ticker:

    # 1. cache the latest production web_runs row per ticker (SELECT only)
    python scripts/sector_leg_audit.py fetch --out .audit_runs
    # 2. run the CURRENT engine locally (no research / LLM -> no guidance forecast)
    python scripts/sector_leg_audit.py local --out .audit_local LMT RTX NOC
    # 3. audit either directory
    python scripts/sector_leg_audit.py audit .audit_runs --json out.json

Checks (one letter each, matching the plan's checklist):
  Ea  basis: forward leg metric vs consensus (>15% gap, 5+ analysts) and NTM/TTM label vs peer basis
  Eb  share-count invariant status (or "no guidance forecast": the check never ran)
  Ec  capex vs D&A: D&A > 2x capex in history and the forecast's capex intensity vs history
  Ed  display = valuation: DCF revenue path vs guidance rows vs three-statement income
  Ee  named peers: members_used / peer_count / static fallback per multiple field
  Ef  net debt: net_debt_basis components present (the workbook tie-out checks the bridge)
  Eg  whole-company legs: SOTP priced share / default-tier multiples, leg outliers vs the blend
  Eh  flags vs what ran (stale consensus-years flag, stand-ins, failed invariants)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PYTHONUTF8"] = "1"

_GAP = 0.15
_FWD_RE = re.compile(r"forward|ntm|fy\+1|guidance|year-1|consensus", re.I)
_TTM_RE = re.compile(r"\bttm\b|trailing|5y|normalis|normaliz|mid-cycle", re.I)
_REVIEW_FLAG_RE = re.compile(r"backlog|ppa|rate base|\bnav\b|rnav|stand-in|pv-10|proxy|invariant .* failed|"
                             r"consensus sets|concession|lease", re.I)


def _prod_dsn() -> str:
    raw = (Path.home() / ".railway_pg_url").read_text(encoding="utf-8").strip()
    return re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/", raw)


def cmd_fetch(out: Path) -> None:
    import psycopg
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(_prod_dsn(), connect_timeout=20) as conn:
        conn.read_only = True
        rows = conn.execute(
            "SELECT DISTINCT ON (UPPER(ticker)) ticker, run_id, run_at, full_result_json FROM web_runs "
            "WHERE is_checkpoint = 0 AND full_result_json IS NOT NULL ORDER BY UPPER(ticker), run_at DESC").fetchall()
    idx = []
    for t, rid, rat, payload in rows:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", t.upper())
        (out / f"{safe}.json").write_text(payload, encoding="utf-8")
        idx.append({"ticker": t.upper(), "run_id": rid, "run_at": str(rat), "file": f"{safe}.json", "source": "prod"})
    (out / "_index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    print(f"{len(idx)} tickers cached in {out}")


def cmd_local(out: Path, tickers: list[str], sector_from_label: bool) -> None:
    os.environ.pop("DATABASE_URL", None)
    # An audit run must not research agency ratings live: it costs LLM calls and writes the local credit_ratings
    # table, which the run then reads (it surfaced in golden replays before they froze the lookup).
    os.environ.setdefault("CREDIT_RATING_RESEARCH", "0")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    os.environ.pop("DATABASE_URL", None)
    logging.disable(logging.CRITICAL)
    from src.agents.analysis import dcf_agent as d
    from src.memory.golden_state import build_state_from_lookups
    sys.path.insert(0, str(ROOT / "scripts"))
    from wave_baseline import _label_sector
    out.mkdir(parents=True, exist_ok=True)
    idx_path = out / "_index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
    done = {(r["ticker"], r["source"]) for r in idx if r.get("file")}
    key, end = os.environ.get("FMP_API_KEY"), date.today().isoformat()
    for t in tickers:
        if (t.upper(), "local") in done and (t.upper(), "probe") in done:
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", t.upper())

        def _state():
            st = build_state_from_lookups(t, end, api_key=key)
            if sector_from_label and not (st["data"].get("profile_names") or {}).get(t):
                _label, sec = _label_sector(t)
                if sec:
                    st["data"]["sector"] = sec
                    st["data"]["sectors"] = {t: sec}
            return st

        for mode in ("local", "probe"):
            fname = f"{safe}.json" if mode == "local" else f"{safe}__probe.json"
            if (t.upper(), mode) in done:
                continue
            try:
                st = _state()
                if mode == "probe":
                    est = _probe_block((json.loads((out / f"{safe}.json").read_text(encoding="utf-8"))
                                        ["data"]["dcf_range"][t]))
                    if est is None:
                        break
                    st["data"].setdefault("guidance_estimates", {})[t] = est
                with contextlib.redirect_stdout(io.StringIO()):
                    res = d.run_dcf_agent(st)
                data = {"dcf_range": {t: (res["data"]["dcf_range"].get(t) or {})}}
                (out / fname).write_text(json.dumps({"data": data}, default=str), encoding="utf-8")
                idx.append({"ticker": t.upper(), "run_at": end, "file": fname, "source": mode})
                print(f"{t} [{mode}]: {data['dcf_range'][t].get('profile')}", flush=True)
            except Exception as exc:  # noqa: BLE001
                idx.append({"ticker": t.upper(), "run_at": end, "file": None, "source": mode,
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
                print(f"{t} [{mode}]: ERROR {exc}", flush=True)
                break
        idx_path.write_text(json.dumps(idx, indent=1), encoding="utf-8")


#: The probe's guided EPS sits this far below street EPS -- a GAAP-vs-adjusted gap of the REGN kind -- and
#: its EBITDA margin this far below consensus (AMGN reported vs adjusted EBITDA).
_PROBE_EPS_SHIFT = 0.70
_PROBE_EBITDA_SHIFT = 0.75


def _probe_block(dr: dict) -> dict | None:
    """A synthetic HIGH-confidence guidance -> estimates block built from today's street consensus (revenue on
    consensus; EPS 30% and EBITDA margin 25% below it, EPS stated in the trading currency), so every
    guidance-gated rule executes: the street-basis guards must swap the forward legs back to consensus where 5+
    analysts cover the name, the share-count check must run, the forecast's capex must follow its intensity, and
    one revenue path must reach every tab."""
    b, sc = dr.get("base") or {}, dr.get("street_consensus") or {}
    eps, rev = sc.get("eps") or {}, sc.get("revenue") or {}
    rev0 = dr.get("revenue_base")
    r1, r2 = rev.get("fy1"), rev.get("fy2")
    if not (isinstance(rev0, (int, float)) and rev0 > 0 and isinstance(r1, (int, float)) and r1 > 0):
        return None
    g1 = max(min(r1 / rev0 - 1.0, 0.35), -0.25)
    g2 = max(min(r2 / r1 - 1.0, 0.35), -0.25) if isinstance(r2, (int, float)) and r2 > 0 else g1
    y1r, y1e = b.get("yr1_revenue"), b.get("yr1_ebitda_est")
    m1 = (_PROBE_EBITDA_SHIFT * y1e / y1r) if all(isinstance(x, (int, float)) and x > 0 for x in (y1r, y1e)) else None
    e1, e2 = eps.get("fy1"), eps.get("fy2")

    def row(dg):
        r = {"revenue_growth_fy1": g1 + dg, "revenue_growth_fy2": g2 + dg}
        if m1:
            r.update({"ebitda_margin_fy1": m1, "ebitda_margin_fy2": m1})
        if isinstance(e1, (int, float)) and e1 > 0:
            r["eps_fy1"] = _PROBE_EPS_SHIFT * e1 * (1 + dg)
            if isinstance(e2, (int, float)) and e2 > 0:
                r["eps_fy2"] = _PROBE_EPS_SHIFT * e2 * (1 + dg)
        return r
    return {"confidence": "HIGH", "fiscal_year_1": sc.get("fy1_period") or "", "fiscal_year_2": sc.get("fy2_period") or "",
            "_model": "audit-probe", "estimates": {"bear": row(-0.03), "base": row(0.0), "bull": row(0.03)},
            "guidance": {"eps": {"currency": dr.get("trading_currency")}}, "citations": []}


# ── checks ─────────────────────────────────────────────────────────────────────────────────────────

def _pct(a, b):
    try:
        return a / b - 1.0 if (a is not None and b) else None
    except TypeError:
        return None


def check(ticker: str, dr: dict) -> dict:
    base = dr.get("base") or {}
    legs = base.get("leg_inputs") or {}
    ew = base.get("effective_weights") or []
    weights = {e.get("method"): e.get("weight") for e in ew}
    gf = dr.get("guidance_forecast") if isinstance(dr.get("guidance_forecast"), dict) else None
    flags = base.get("forward_flags") or []
    f: dict[str, list] = {k: [] for k in ("Ea", "Eb", "Ec", "Ed", "Ee", "Ef", "Eg", "Eh")}
    analysts = ((dr.get("consensus_at_run") or {}).get("analyst_count")
                or (dr.get("street_consensus") or {}).get("analyst_count"))

    # Ea: basis
    for name, lg in legs.items():
        if not isinstance(lg, dict) or lg.get("kind") not in ("equity_multiple", "ev_multiple"):
            continue
        metric = str(lg.get("metric") or "")
        src = str((lg.get("multiple_parts") or {}).get("peer_source") or "")
        gap = _pct(lg.get("metric_value"), lg.get("consensus_value"))
        if gap is not None and abs(gap) > _GAP and "street" not in metric.lower() and "consensus" not in metric.lower():
            f["Ea"].append(f"{name} w={weights.get(name)}: metric {lg.get('metric_value'):.4g} vs consensus "
                           f"{lg.get('consensus_value'):.4g} ({gap:+.0%}) [{metric[:60]}]")
        fwd_peer = "forward basis" in src or "_ntm" in src
        if fwd_peer and _TTM_RE.search(metric) and not _FWD_RE.search(metric):
            f["Ea"].append(f"{name}: NTM peer multiple on a trailing metric [{metric[:50]} | {src[:50]}]")
        if (not fwd_peer) and name.lower().startswith(("forward", "fwd")) and src:
            f["Ea"].append(f"{name}: forward leg on a trailing peer multiple [{src[:60]}]")

    # Eb: share count
    if gf is None:
        f["Eb"].append("no guidance forecast: share-count invariant never ran")
    else:
        s = gf.get("share_count_status")
        if s in (None, "UNRESOLVED"):
            f["Eb"].append(f"share_count_status={s}")

    # Ec: capex vs D&A
    rows = [r for r in ((dr.get("financials_used") or {}).get("rows") or []) if isinstance(r, dict)][-3:]
    da = sum(abs(r.get("depreciation_and_amortization") or 0) for r in rows)
    cx = sum(abs(r.get("capital_expenditure") or 0) for r in rows)
    rv = sum(abs(r.get("revenue") or 0) for r in rows)
    ratio = da / cx if cx else None
    ec_info = {"da_capex_ratio": round(ratio, 2) if ratio else None,
               "hist_capex_pct": round(cx / rv, 4) if rv else None}
    if ratio and ratio > 2.0:
        if gf is None:
            f["Ec"].append(f"D&A {ratio:.1f}x capex (amortisation-heavy) and no guidance forecast: E1 rule unreachable")
        else:
            r1 = (gf.get("rows") or [{}])[0]
            fc = (r1.get("capex") or 0) / (r1.get("revenue") or 1)
            ec_info["fcst_capex_pct"] = round(fc, 4)
            if rv and fc > 1.5 * (cx / rv):
                f["Ec"].append(f"D&A {ratio:.1f}x capex; forecast capex {fc:.1%} of revenue vs history {cx / rv:.1%}")

    # Ed: display = valuation
    proj = dr.get("projection_rows") or []
    if gf and proj:
        for i, (p, g) in enumerate(zip(proj, gf.get("rows") or [])):
            gap = _pct(p.get("revenue"), g.get("revenue"))
            if gap is not None and abs(gap) > 0.01:
                f["Ed"].append(f"Yr{i + 1} DCF revenue {p.get('revenue'):.4g} vs guidance row {g.get('revenue'):.4g} ({gap:+.1%})")
                break
    ts_inc = ((dr.get("three_statements") or {}).get("income") or {})
    ts_rev = ts_inc.get("revenue") if isinstance(ts_inc, dict) else None
    if isinstance(ts_rev, list) and proj:
        vals = [v for v in ts_rev if isinstance(v, (int, float))]
        if vals:
            gap = _pct(proj[0].get("revenue"), vals[1] if len(vals) > 1 else vals[0])
            if gap is not None and abs(gap) > 0.01 and len(vals) > 1:
                f["Ed"].append(f"Yr1 DCF revenue vs three-statement FY+1E revenue {gap:+.1%}")

    # Ee: named peers
    mu = dr.get("multiples_used") or {}
    used_fields = set()
    for lg in legs.values():
        if isinstance(lg, dict):
            src = str((lg.get("multiple_parts") or {}).get("peer_source") or "")
            for k in (mu.get("fields") or {}):
                if re.search(rf"\b{k}\b", src):
                    used_fields.add(k)
    for k, v in (mu.get("fields") or {}).items():
        if k in ("cn_adr_haircut", "growth_avg", "roic") or not isinstance(v, dict):
            continue
        basis, n = v.get("basis"), v.get("peer_count")
        tag = "*" if k in used_fields else ""
        if basis == "static":
            f["Ee"].append(f"{k}{tag}: static table (no live peers)")
        elif not v.get("members_used"):
            f["Ee"].append(f"{k}{tag}: no members_used (basis {basis}, n={n})")
        elif n is not None and n < 3:
            f["Ee"].append(f"{k}{tag}: only {n} peers ({basis})")
    if mu.get("no_peer_multiples"):
        f["Ee"].insert(0, "no_peer_multiples=True")

    # Ef: net debt
    ndb = (dr.get("financials_used") or {}).get("net_debt_basis") or {}
    if not ndb.get("components"):
        f["Ef"].append("net_debt_basis has no components (no bridge)")

    # Eg: whole-company legs and outliers
    iv = base.get("intrinsic_value")
    vals = {n: (lg.get("value") if isinstance(lg, dict) else None) for n, lg in legs.items()}
    vals = {n: v for n, v in vals.items() if isinstance(v, (int, float)) and weights.get(n)}
    med = statistics.median(vals.values()) if vals else None
    for n, v in vals.items():
        if med and med > 0 and (v > 2.0 * med or v < 0.5 * med):
            f["Eg"].append(f"{n} w={weights.get(n)} value {v:.4g} vs median leg {med:.4g} ({v / med:.2f}x)")
    for n, lg in legs.items():
        if isinstance(lg, dict) and lg.get("kind") == "sotp":
            ps = lg.get("priced_share_of_revenue")
            if ps is not None and ps < 0.95:
                f["Eg"].append(f"{n} prices only {ps:.0%} of revenue")
            if lg.get("tier") == "default":
                f["Eg"].append(f"{n} w={weights.get(n)} on default-tier generic segment multiples")
    for e in ew:
        if e.get("value_key") and e.get("value_key") != e.get("method"):
            f["Eg"].append(f"stand-in: {e.get('method')} <- {e.get('value_key')} w={e.get('weight')}")

    # Eh: flags vs what ran
    for fl in flags:
        if gf and re.search(r"consensus sets .*years? 1", fl, re.I):
            f["Eh"].append("stale flag: consensus sets years 1-2 while a guidance forecast ran")
        if re.search(r"invariant .*FAILED", fl):
            f["Eh"].append(fl[:140])
    review = [fl[:160] for fl in flags if _REVIEW_FLAG_RE.search(fl) and not re.search(r"invariant .*FAILED", fl)]

    return {
        "ticker": ticker,
        "profile": dr.get("profile"),
        "sector": (dr.get("routing_trace") or {}).get("final_sector"),
        "anchor": dr.get("anchor_method"),
        "weights": weights,
        "iv": iv,
        "analysts": analysts,
        "guidance_forecast": gf is not None,
        "share_count_status": gf.get("share_count_status") if gf else None,
        "capex": ec_info,
        "legs": {n: {"value": vals.get(n), "metric": (lg.get("metric") if isinstance(lg, dict) else None),
                     "metric_value": lg.get("metric_value") if isinstance(lg, dict) else None,
                     "consensus_value": lg.get("consensus_value") if isinstance(lg, dict) else None,
                     "peer_source": ((lg.get("multiple_parts") or {}).get("peer_source") if isinstance(lg, dict) else None)}
                 for n, lg in legs.items()},
        "findings": {k: v for k, v in f.items() if v},
        "flags_for_review": review,
    }


def _probe_findings(dr: dict) -> dict:
    """P: what the synthetic guidance block (EPS 30% under consensus) did to this profile's legs."""
    base = dr.get("base") or {}
    flags = base.get("forward_flags") or []
    legs = base.get("leg_inputs") or {}
    w = {e.get("method"): e.get("weight") for e in (base.get("effective_weights") or [])}
    ge = dr.get("guidance_estimates") or {}
    p = []
    if not isinstance(dr.get("guidance_forecast"), dict):
        p.append(f"no guidance forecast built ({ge.get('not_applied_reason') or 'no reason recorded'})")
    guard = [f for f in flags if re.match(r"Forward (P/E|EV/EBITDA) on consensus", f)]
    for name, lg in legs.items():
        if not isinstance(lg, dict) or not w.get(name):
            continue
        metric = str(lg.get("metric") or "")
        if re.search(r"guidance|forecast", metric, re.I) and not re.search(r"consensus|street basis", metric, re.I):
            gap = _pct(lg.get("metric_value"), lg.get("consensus_value"))
            p.append(f"{name} w={w.get(name)} prices guidance metric [{metric[:70]}]"
                     + (f" {gap:+.0%} vs consensus" if gap is not None else " (no consensus beside it)"))
    eps_legs = [n for n in legs if w.get(n) and re.search(r"P/E|PEG", n)]
    if eps_legs and not any("P/E" in f for f in guard):
        p.append(f"EPS guard did not fire (EPS legs: {', '.join(eps_legs)}; analysts "
                 f"{(dr.get('street_consensus') or {}).get('analyst_count_eps')})")
    return {"P": p} if p else {}


def cmd_audit(src: Path, out_json: Path | None, tieout: bool) -> None:
    idx = json.loads((src / "_index.json").read_text(encoding="utf-8"))
    results = []
    for r in idx:
        if not r.get("file"):
            results.append({"ticker": r["ticker"], "error": r.get("error")})
            continue
        doc = json.loads((src / r["file"]).read_text(encoding="utf-8"))
        dr_all = (doc.get("data") or {}).get("dcf_range") or {}
        for t, dr in dr_all.items():
            if not isinstance(dr, dict) or not dr.get("base"):
                continue
            res = check(t, dr)
            if r.get("source") == "probe":
                res["findings"].update(_probe_findings(dr))
            res.update({"run_at": r.get("run_at"), "source": r.get("source"),
                        "param_version": dr.get("param_version")})
            if tieout:
                res["tieout"] = _tieout(src / r["file"])
            results.append(res)
    for res in results:
        if res.get("error"):
            print(f"{res['ticker']:10s} ERROR {res['error']}")
            continue
        print(f"\n{res['ticker']:10s} [{res.get('source')}] {res['sector']} | {res['profile']} | {str(res.get('run_at'))[:10]} "
              f"| gf={res['guidance_forecast']} sc={res['share_count_status']} | {res['capex']}")
        for k, items in res["findings"].items():
            for it in items:
                print(f"   {k}  {it}")
        if res.get("tieout"):
            print(f"   TIE {res['tieout']}")
    if out_json:
        out_json.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"\nwritten {out_json}")


def _tieout(path: Path) -> str:
    import subprocess
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "workbook_tieout.py"), str(path)],
                       capture_output=True, text=True, cwd=ROOT, timeout=900)
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if "checks pass" in ln or "FAIL" in ln or "Error" in ln]
    return " | ".join(lines)[:600] or f"exit {p.returncode}"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("fetch"); a1.add_argument("--out", required=True)
    a2 = sub.add_parser("local"); a2.add_argument("--out", required=True); a2.add_argument("tickers", nargs="+")
    a2.add_argument("--sector-from-label", action="store_true")
    a3 = sub.add_parser("audit"); a3.add_argument("src"); a3.add_argument("--json"); a3.add_argument("--tieout", action="store_true")
    a = ap.parse_args()
    if a.cmd == "fetch":
        cmd_fetch(Path(a.out))
    elif a.cmd == "local":
        cmd_local(Path(a.out), a.tickers, a.sector_from_label)
    else:
        cmd_audit(Path(a.src), Path(a.json) if a.json else None, a.tieout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
