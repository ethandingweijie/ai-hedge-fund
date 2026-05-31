"""Backend↔frontend field-name parity (Phase 4c — regression guard).

The Class-2 defect was silent: the backend `_extract_pipeline_assets` emits
`peak_sales_usd` (raw $), but the frontend `BiopharmaPipelineAsset` only read
`peak_sales_bn` ($B). No transform on the wire → every Peak/rNPV cell rendered
"—". Nothing failed; the field name simply drifted on one side only.

This test makes that class of drift visible in CI instead of in production:
it statically asserts that every field the backend extractor EMITS has a
declared home in the TS interface the UI reads. Rename `peak_sales_usd` on
either side without updating the other → this test fails.

Pure source/text parsing — no LLM calls, no network, no backend run.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from src.agents.industry import deep_research


_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPORT_TYPES_TS = _REPO_ROOT / "app" / "frontend" / "src" / "lib" / "reportTypes.ts"

# The exact set the extractor is expected to emit. If a new field is added to
# `out.append({...})`, this test fails on the EXPECTED-set check below as a
# deliberate prompt: "you added a pipeline field — did you wire it to the TS
# interface too?" Update BOTH this set and BiopharmaPipelineAsset together.
_EXPECTED_BACKEND_KEYS = {
    "name", "phase", "peak_sales_usd", "launch_year", "indication", "evidence",
}


def _backend_emitted_keys() -> set[str]:
    """Parse the keys of the dict the extractor appends to its output list.

    We isolate the `out.append({ ... })` literal so we don't pick up keys from
    `a.get("peak_sales_usd", 0)` reads elsewhere in the function body.
    """
    src = inspect.getsource(deep_research._extract_pipeline_assets)
    marker = "out.append({"
    start = src.index(marker)
    # Walk braces from the opening `{` to its match.
    brace_start = src.index("{", start)
    depth = 0
    end = None
    for i in range(brace_start, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "could not find matching brace for out.append({...})"
    literal = src[brace_start : end + 1]
    # Keys are `"<key>":` at the head of each entry.
    return set(re.findall(r'"(\w+)"\s*:', literal))


def _ts_interface_fields(interface_name: str) -> set[str]:
    """Parse the declared field names of a TS interface from reportTypes.ts."""
    text = _REPORT_TYPES_TS.read_text(encoding="utf-8")
    m = re.search(
        rf"export interface {re.escape(interface_name)}\s*\{{(.*?)\n\}}",
        text,
        re.DOTALL,
    )
    assert m, f"interface {interface_name} not found in {_REPORT_TYPES_TS}"
    body = m.group(1)
    fields: set[str] = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        fm = re.match(r"(\w+)\??\s*:", line)
        if fm:
            fields.add(fm.group(1))
    return fields


def test_extractor_emits_the_expected_key_set():
    """Backend emits exactly the documented pipeline-asset key set. Catches a
    silent NEW field addition that hasn't been wired to the frontend."""
    assert _backend_emitted_keys() == _EXPECTED_BACKEND_KEYS


def test_every_backend_pipeline_key_has_a_ts_home():
    """Every field the backend emits is declared on BiopharmaPipelineAsset.

    This is the direct regression guard for the peak_sales_usd vs peak_sales_bn
    class: rename the backend field and the TS interface no longer covers it →
    fail here, in CI, not silently in the rendered card."""
    backend = _backend_emitted_keys()
    ts = _ts_interface_fields("BiopharmaPipelineAsset")
    orphans = backend - ts
    assert not orphans, (
        f"backend pipeline fields with no home in BiopharmaPipelineAsset: "
        f"{sorted(orphans)} (the peak_sales_usd/peak_sales_bn drift class)"
    )


def test_peak_sales_usd_specifically_is_on_both_sides():
    """Pin the exact field whose drift caused the Class-2 blank-rNPV bug."""
    assert "peak_sales_usd" in _backend_emitted_keys()
    assert "peak_sales_usd" in _ts_interface_fields("BiopharmaPipelineAsset")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
