"""Primary-source check for pipeline inputs (owner, 2026-10-07): 10-K, earnings 8-K and transcript, offline."""
from src.data import pipeline_sources as ps

TENK = ("Our late-stage pipeline includes cemdisiran, which is in a Phase 3 trial in generalized myasthenia gravis. "
        "REGN7508 is being studied in a Phase 3 program for thrombosis. "
        "Libtayo was approved by the FDA in 2018 for cutaneous squamous cell carcinoma. "
        "We are also studying fianlimab in combination with pembrolizumab in a pivotal trial. ")
EIGHTK = ("The FDA accepted for priority review the BLA for cemdisiran, with a target action date of November 30, 2026. "
          "If approved, cemdisiran would be the first siRNA for gMG. ")
TRANSCRIPT = "We started the Phase 3 program for olatorepatide in obesity. If approved, olatorepatide could launch in 2030."
SOURCES = {"10-K": {"url": "u1", "date": "2026-02-04", "text": TENK},
           "8-K": {"url": "u2", "date": "2026-07-30", "text": EIGHTK},
           "transcript": {"url": "u3", "date": "2026-07-31", "text": TRANSCRIPT}}


def _entry():
    return {"assets": [{"name": "Cemdisiran (ALN-CC5)", "phase": "filed"},
                       {"name": "Olatorepatide (HS-20094)", "phase": "phase_2"}],
            "approved_portfolio": [{"name": "Libtayo", "inn": "cemiplimab"}],
            "excluded_assets": [{"name": "Fianlimab", "reason": "missed its primary endpoint"}]}


def test_assets_are_corroborated_or_flagged_from_the_latest_source():
    r = ps.check(_entry(), SOURCES)
    a = {x["name"].split(" (")[0]: x for x in r["assets"]}
    assert a["Cemdisiran"]["sources"]["8-K"]["stage"] == "filed"
    assert a["Cemdisiran"]["sources"]["8-K"]["date"] == "November 30, 2026"
    # the transcript is the latest source and says Phase 3 for an input marked Phase 2
    assert a["Olatorepatide"]["verdict"].startswith("CHECK") and "phase_3" in a["Olatorepatide"]["verdict"]


def test_conditional_approval_is_not_a_stage():
    assert ps._stage_near(EIGHTK, EIGHTK.rfind("cemdisiran"), EIGHTK.rfind("cemdisiran") + 10) != "approved"
    assert ps._stage_near(TENK, TENK.find("Libtayo"), TENK.find("Libtayo") + 7) == "approved"


def test_uncovered_late_stage_names_skip_covered_comparators_and_single_inn_mentions():
    r = ps.check(_entry(), SOURCES)
    ids = {m["identifier"] for m in r["uncovered_late_stage"]}
    assert "REGN7508" in ids                       # a Phase 3 code the input does not mention
    assert "pembrolizumab" not in ids              # a combination partner, not an asset
    assert "fianlimab" not in {i.lower() for i in ids}   # excluded with a reason: covered


def test_excluded_asset_codes_in_free_text_count_as_covered():
    e = _entry()
    e["excluded_assets"].append({"name": "Factor XI antibodies", "reason": "no single-asset peak (REGN7508, REGN9933)"})
    ids = {m["identifier"] for m in ps.check(e, SOURCES)["uncovered_late_stage"]}
    assert "REGN7508" not in ids


def test_no_backspace_bytes_in_the_module():
    import inspect
    assert chr(8) not in inspect.getsource(ps)
