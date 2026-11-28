"""D4 (owner, 2026-11-28): the signed bias table reports direction, not just spread."""
import importlib.util
import math
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "scripts" / "signed_bias.py"
_spec = importlib.util.spec_from_file_location("signed_bias", _p)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)


def test_log_ratio_and_typical_gap():
    assert sb._log(50, 100) == math.log(0.5) and sb._log(None, 1) is None and sb._log(-1, 1) is None
    assert sb._stats([math.log(0.7)] * 5).startswith("-30%")


def test_table_signs_and_sources_never_mix():
    rows = [{"source": "runs", "ticker": f"T{i}", "market": "US", "profile": "P", "iv": 70, "spot": 100,
             "consensus": None, "verdict": None} for i in range(4)]
    rows += [{"source": "golden", "ticker": "G", "market": "US", "profile": "P", "iv": 130, "spot": 100,
              "consensus": 100, "verdict": None}]
    lines = sb.table(rows, "market")
    assert any(l.startswith("| runs | US | 4 | -30%") for l in lines)
    assert any(l.startswith("| golden | US | 1 | +30%") for l in lines)
