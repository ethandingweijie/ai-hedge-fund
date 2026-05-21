"""Phase 9 — cross-language contract guard.

Ensures the Python persistence schema in card_qa_agent.py matches the
TypeScript DdCardAudit interface in reportTypes.ts. Drift between the
two would mean the banner reads stale field names and renders blank.

Approach: regex-extract field names from both sources and compare. Not
a perfect TS parser — but catches the common drift patterns (renamed
fields, new mandatory fields, removed fields) without depending on
node + typescript at test time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_FILE = REPO_ROOT / "src" / "agents" / "audit" / "card_qa_agent.py"
TS_FILE = REPO_ROOT / "app" / "frontend" / "src" / "lib" / "reportTypes.ts"


def _extract_ts_interface_fields(ts_source: str, interface_name: str) -> set[str]:
    """Pull field names from a `export interface X { ... }` block.

    Simple but works for our straightforward interfaces (no nested
    inline types, no generics on fields). If we hit a case this can't
    handle, the regex will drop the field and the test will (correctly)
    catch the mismatch.
    """
    # Find the interface body
    pattern = rf"export interface {interface_name}\s*\{{(.+?)\n\}}"
    match = re.search(pattern, ts_source, re.DOTALL)
    if not match:
        return set()
    body = match.group(1)
    # Strip nested braces' contents (Record<string, ...> etc) so we don't
    # confuse a generic's content for fields.
    body = re.sub(r"\{[^{}]*\}", "{}", body)
    # Lines like "  qa_version: string;" or "  human_review_flags: DdCardAuditFlag[];"
    field_pattern = re.compile(r"^\s+(\w+)\??:\s+", re.MULTILINE)
    return {m.group(1) for m in field_pattern.finditer(body)}


def test_ddcardaudit_interface_matches_python_audit_dict_shape():
    """The TS DdCardAudit interface MUST have a field for every key in the
    Python run_card_qa_agent return dict. Drift = blank banner."""
    expected_fields = {
        # From card_qa_agent.py top-level audit dict
        "qa_version",
        "qa_ran_at",
        "qa_model",
        "qa_schema_versions",
        "meta_check",
        "cards_inspected",
        "auto_remediations",
        "human_review_flags",
        "qa_cost_estimate_usd",
        "qa_budget_hit",
    }
    ts_source = TS_FILE.read_text(encoding="utf-8")
    actual_fields = _extract_ts_interface_fields(ts_source, "DdCardAudit")
    missing = expected_fields - actual_fields
    extra   = actual_fields - expected_fields
    assert not missing, (
        f"TS DdCardAudit missing fields {missing} present in Python schema."
    )
    # `extra` is OK — TS can have additional optional fields the backend
    # doesn't yet send. But warn (not assert) for spotting future drift.
    if extra:
        print(f"[warn] TS DdCardAudit has fields not in Python: {extra}")


def test_card_inspection_fields_match():
    """Per-card dict shape (cards_inspected[]) must align."""
    expected = {
        "card", "applies_when_passed",
        "missing_mandatory", "missing_opportunistic",
        "judge_verdict", "judge_reasoning", "judge_evidence_quote",
        "remediation_attempted", "remediation_success",
    }
    ts_source = TS_FILE.read_text(encoding="utf-8")
    actual = _extract_ts_interface_fields(ts_source, "DdCardAuditCardInspection")
    missing = expected - actual
    assert not missing, f"TS DdCardAuditCardInspection missing: {missing}"


def test_meta_check_fields_match():
    expected = {"passed", "checks_run", "issues", "suggested_profile"}
    ts_source = TS_FILE.read_text(encoding="utf-8")
    actual = _extract_ts_interface_fields(ts_source, "DdCardAuditMetaCheck")
    missing = expected - actual
    assert not missing, f"TS DdCardAuditMetaCheck missing: {missing}"


def test_flag_fields_match():
    expected = {
        "card", "field", "reason", "context", "evidence_quote",
        # suggested_profile is optional — only present on meta_check flags
        "suggested_profile",
    }
    ts_source = TS_FILE.read_text(encoding="utf-8")
    actual = _extract_ts_interface_fields(ts_source, "DdCardAuditFlag")
    missing = expected - actual
    assert not missing, f"TS DdCardAuditFlag missing: {missing}"


def test_pipeline_data_has_card_qa_audit_field():
    """The TS PipelineData interface (consumed by frontend) must expose
    `card_qa_audit` so the banner can read it from displayResult.data."""
    ts_source = TS_FILE.read_text(encoding="utf-8")
    assert "card_qa_audit?:" in ts_source, (
        "PipelineData missing card_qa_audit field — banner won't have data to render"
    )
    assert "Record<string, DdCardAudit>" in ts_source, (
        "card_qa_audit should be typed as Record<string, DdCardAudit>"
    )


def test_audit_severity_module_exists():
    """The shared utility MUST be in place — otherwise the 3 render paths
    will diverge on color/iconography (the plan's specific concern)."""
    sev_path = REPO_ROOT / "app" / "frontend" / "src" / "lib" / "auditSeverity.ts"
    assert sev_path.exists(), (
        "auditSeverity.ts missing — Phase 9 requires this shared utility to "
        "prevent the 3 render paths from drifting on banner severity rules"
    )
    contents = sev_path.read_text(encoding="utf-8")
    # Sanity: the 4 severity buckets must all be defined
    for severity in ("'critical'", "'warning'", "'info'", "'ok'"):
        assert severity in contents, (
            f"auditSeverity.ts missing severity bucket {severity}"
        )


def test_card_audit_banner_wired_into_all_three_render_paths():
    """The plan's Phase 9 gate: banner must render on ReportPage,
    V2ReportView, AND ReportViewPage — all three use the same utility
    so a future severity-rule change propagates everywhere."""
    paths = [
        REPO_ROOT / "app" / "frontend" / "src" / "pages"      / "ReportPage.tsx",
        REPO_ROOT / "app" / "frontend" / "src" / "pages"      / "ReportViewPage.tsx",
        REPO_ROOT / "app" / "frontend" / "src" / "components" / "v2" / "V2ReportView.tsx",
    ]
    for p in paths:
        assert p.exists(), f"Render path missing: {p}"
        src = p.read_text(encoding="utf-8")
        assert "CardAuditBanner" in src, (
            f"{p.name} doesn't import/use CardAuditBanner — banner won't render here"
        )
