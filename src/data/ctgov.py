"""ClinicalTrials.gov v2 API -- trial dates and a sponsor's late-stage studies (owner, 2026-10-05).

Public, keyless, read-only. Used by the pipeline rules (src/data/pipeline_rules.py) at build and
verify time only -- never inside a valuation run. Every failure returns None / [] so a rule that
needs the registry reports "unverified" instead of failing a build on a network error.
No personal data is sent: the only parameters are an NCT id or a sponsor name.
"""
from __future__ import annotations

from typing import Optional

import requests

_BASE = "https://clinicaltrials.gov/api/v2/studies"
_TIMEOUT = 20.0
_HEADERS = {"Accept": "application/json"}

#: Statuses that make a Phase 3 study part of the live pipeline.
_LIVE = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")


def _date(module: dict, key: str) -> Optional[str]:
    v = (module.get(key) or {}).get("date")
    return str(v) if v else None


def _summary(study: dict) -> dict:
    ps = study.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    arms = ps.get("armsInterventionsModule") or {}
    cond = ps.get("conditionsModule") or {}
    spons = ps.get("sponsorCollaboratorsModule") or {}
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "phases": design.get("phases") or [],
        "primary_completion": _date(status, "primaryCompletionDateStruct"),
        "completion": _date(status, "completionDateStruct"),
        "interventions": [i.get("name") for i in (arms.get("interventions") or []) if i.get("name")]
                         + [o for i in (arms.get("interventions") or []) for o in (i.get("otherNames") or [])],
        "conditions": cond.get("conditions") or [],
        "lead_sponsor": ((spons.get("leadSponsor") or {}).get("name")),
    }


def study(nct_id: str) -> Optional[dict]:
    """One study's summary (dates, phase, interventions), or None."""
    try:
        r = requests.get(f"{_BASE}/{nct_id}", headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return _summary(r.json())
    except Exception:  # noqa: BLE001
        return None


def sponsor_late_stage(sponsor: str, max_pages: int = 5) -> list[dict]:
    """Live Phase 3 (incl. Phase 2/3) interventional studies whose LEAD sponsor matches `sponsor`."""
    out: list[dict] = []
    token = None
    try:
        for _ in range(max_pages):
            params = {"query.lead": sponsor, "filter.overallStatus": "|".join(_LIVE),
                      "filter.advanced": "AREA[Phase](PHASE3) AND AREA[StudyType]INTERVENTIONAL",
                      "pageSize": 100, "format": "json"}
            if token:
                params["pageToken"] = token
            r = requests.get(_BASE, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code != 200:
                break
            j = r.json()
            out.extend(_summary(s) for s in (j.get("studies") or []))
            token = j.get("nextPageToken")
            if not token:
                break
    except Exception:  # noqa: BLE001
        return out
    return out
