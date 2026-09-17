"""The golden fixture secret gate must not report clean while blind.

`tests/fixtures/golden/` is NOT gitignored, so a leaked key is a credential in
git history. One already happened: `FMP_API_KEY` reached `BN4_SI/raw/calls.json`
through `_fmp_get`'s positional `api_key` at index 2.

The gate that was supposed to catch the next one read only `os.environ`, and
`assert_no_secrets` short-circuited to `[]` when that read came back empty — so
"a bare process with no dotenv loaded" and "a clean fixture" produced the same
answer. Measured on this repo: a bare `.venv` python has **0** of the 9
`SECRET_ENV_VARS` in its environment, while `.env.local` holds 8 of them
including `FMP_API_KEY`. `.env` (533 bytes, 3 keys) does NOT hold it, so a
`load_dotenv()` with no argument finds `.env` first and arms 3 of 9 — missing
precisely the credential with a history.

`scripts/record_golden_fixtures.py` L47-48 does load `.env.local` explicitly, so
the record pass itself was never blind. These tests pin the hardening that makes
the gate independent of any caller remembering to, and the fail-closed behaviour
that makes blindness loud instead of clean.

Every test here points `PROJECT_ROOT` at a tmp dir and uses invented values, so
no test reads or writes a real credential.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.memory import golden_capture as gc

FAKE = "sk-INVENTED-FOR-TEST-0123456789"
FAKE_ALT = "sk-INVENTED-SECOND-VALUE-987654"


def _root(monkeypatch, tmp_path: Path, **files: str) -> Path:
    """A fake project root whose dotenv files are the ones given."""
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    monkeypatch.setattr(gc, "PROJECT_ROOT", tmp_path)
    for name in gc.SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


# ── A. The union arms the sweep from disk ────────────────────────────────────


def test_a_process_with_no_env_vars_at_all_is_still_armed(monkeypatch, tmp_path):
    """THE defect: env-only arming returns nothing here, so the gate is blind."""
    _root(monkeypatch, tmp_path, **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    assert gc.known_secrets() == {FAKE}


def test_the_live_dotenv_is_read_and_the_stale_one_is_not_enough(monkeypatch,
                                                                  tmp_path):
    """`.env` without FMP_API_KEY is this repo's real shape, and it is why a
    bare `load_dotenv()` armed 3 of 9 and missed the key that leaked."""
    _root(monkeypatch, tmp_path,
          **{".env": f"FRED_API_KEY={FAKE_ALT}\n",
             ".env.local": f"FMP_API_KEY={FAKE}\n"})
    secrets = gc.known_secrets()
    assert FAKE in secrets, "the live .env.local value must be swept"
    assert FAKE_ALT in secrets, "and the stale .env value too — both are real"


def test_a_name_present_in_two_files_with_different_values_arms_both(monkeypatch,
                                                                    tmp_path):
    """Measured on this repo: ANTHROPIC_API_KEY has 2 distinct values on disk.
    Scrubbing only one would leak the other."""
    _root(monkeypatch, tmp_path,
          **{".env": f"ANTHROPIC_API_KEY={FAKE}\n",
             ".env.local": f"ANTHROPIC_API_KEY={FAKE_ALT}\n"})
    secrets = gc.known_secrets()
    assert {FAKE, FAKE_ALT} <= secrets


def test_an_env_var_still_arms_the_sweep_even_when_no_file_names_it(monkeypatch,
                                                                    tmp_path):
    """The union adds disk to env; it must not replace env."""
    _root(monkeypatch, tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE)
    assert FAKE in gc.known_secrets()


def test_a_value_shorter_than_the_threshold_is_a_placeholder_not_a_secret(
        monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path, **{".env.local": "FMP_API_KEY=abc1234\n"})
    assert gc.known_secrets() == set()


def test_the_threshold_is_inclusive_and_erreds_toward_sweeping(monkeypatch,
                                                               tmp_path):
    """`MIN_SECRET_LEN` is 8 and the comparison is `>=`, so an 8-char
    placeholder like "changeme" IS swept. That is the right direction for a
    gate — scrubbing a non-secret costs nothing, missing one is a credential in
    git history. A first draft of this test asserted the opposite, having
    assumed 8 meant "more than 8"."""
    assert gc.MIN_SECRET_LEN == 8
    _root(monkeypatch, tmp_path, **{".env.local": "FMP_API_KEY=changeme\n"})
    assert gc.known_secrets() == {"changeme"}
    assert len("changeme") == gc.MIN_SECRET_LEN


def test_a_quoted_value_is_unquoted_before_comparison(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path,
          **{".env.local": f'FMP_API_KEY="{FAKE}"\n'})
    assert gc.known_secrets() == {FAKE}


def test_a_commented_out_line_arms_nothing(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path,
          **{".env.local": f"# FMP_API_KEY={FAKE}\n"})
    assert gc.known_secrets() == set()


def test_a_missing_dotenv_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    """Only `.env` exists; the two `.env.local` paths must not raise."""
    _root(monkeypatch, tmp_path, **{".env": f"SMTP_PASS={FAKE}\n"})
    assert gc.known_secrets() == {FAKE}


def test_a_name_outside_the_list_is_not_treated_as_a_secret(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path, **{".env.local": f"VITE_API_URL={FAKE}\n"})
    assert gc.known_secrets() == set()


def test_the_frontend_dotenv_is_read_too(monkeypatch, tmp_path):
    """It carries no listed name today, but if one is added there the sweep
    must already be looking."""
    _root(monkeypatch, tmp_path,
          **{"app/frontend/.env.local": f"GEMINI_API_KEY={FAKE}\n"})
    assert gc.known_secrets() == {FAKE}
    assert "app/frontend/.env.local" in gc.SECRET_DOTENV_FILES


def test_the_live_file_is_in_the_list_the_record_script_loads():
    """Pin the file set itself. Dropping `.env.local` from it re-opens the hole
    this whole module exists to close, and no other test would notice."""
    assert ".env.local" in gc.SECRET_DOTENV_FILES
    assert "FMP_API_KEY" in gc.SECRET_ENV_VARS


# ── B. The gate fails closed instead of reporting clean ──────────────────────


def test_an_unarmed_gate_raises_rather_than_returning_an_empty_list(monkeypatch,
                                                                    tmp_path):
    """`return []` on an empty comparison set is the fail-open: "I checked
    nothing" wearing the same clothes as "I found nothing"."""
    _root(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="cannot certify"):
        gc.assert_no_secrets([tmp_path / "whatever.json"])


def test_the_raise_names_what_would_have_to_be_true_to_fix_it(monkeypatch,
                                                              tmp_path):
    _root(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as ei:
        gc.assert_no_secrets([])
    msg = str(ei.value)
    assert "allow_unarmed" in msg
    assert "env vars armed" in msg


def test_a_keyless_box_can_opt_out_explicitly(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path)
    assert gc.assert_no_secrets([tmp_path / "x.json"], allow_unarmed=True) == []


def test_an_armed_gate_still_catches_a_real_leak(monkeypatch, tmp_path):
    """The fail-closed change must not have broken the thing it guards."""
    root = _root(monkeypatch, tmp_path / "proj",
                 **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    fixture = tmp_path / "calls.json"
    fixture.write_text(f'{{"url": "https://x/apikey={FAKE}"}}', encoding="utf-8")
    assert gc.assert_no_secrets([fixture]) == [
        f"{fixture}: contains a live secret value"]
    assert root.exists()


def test_an_armed_gate_reports_nothing_for_a_clean_fixture(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path / "proj",
          **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    clean = tmp_path / "clean.json"
    clean.write_text('{"url": "https://x/apikey=<redacted>"}', encoding="utf-8")
    assert gc.assert_no_secrets([clean]) == []


def test_a_secret_embedded_in_a_longer_string_is_still_caught(monkeypatch,
                                                              tmp_path):
    """The historical leak was a DSN/URL, not a bare value."""
    _root(monkeypatch, tmp_path / "proj",
          **{".env.local": f"DATABASE_URL={FAKE}\n"})
    p = tmp_path / "web_run.json"
    p.write_text(f'{{"dsn": "postgres://u:{FAKE}@host/db"}}', encoding="utf-8")
    assert gc.assert_no_secrets([p])


# ── C. The diagnostic reports names, never values ────────────────────────────


def test_unarmed_secret_vars_returns_names_not_values(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path, **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    missing = gc.unarmed_secret_vars()
    assert missing == ["FMP_API_KEY"]
    assert FAKE not in missing
    # and it is printed by the record pass, so it must stay value-free
    assert all(len(x) < gc.MIN_SECRET_LEN or x in gc.SECRET_ENV_VARS
               for x in missing)
    assert root.exists()


def test_unarmed_secret_vars_is_empty_once_the_env_matches_the_disk(monkeypatch,
                                                                    tmp_path):
    _root(monkeypatch, tmp_path, **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    monkeypatch.setenv("FMP_API_KEY", FAKE)
    assert gc.unarmed_secret_vars() == []


def test_a_stale_env_value_that_disk_does_not_hold_is_still_flagged(monkeypatch,
                                                                    tmp_path):
    """The check is "env holds one of the on-disk values", not "env is
    non-empty" — a rotated key left in the environment would otherwise pass."""
    _root(monkeypatch, tmp_path, **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    monkeypatch.setenv("FMP_API_KEY", FAKE_ALT)
    assert gc.unarmed_secret_vars() == ["FMP_API_KEY"]
    # but the union still sweeps BOTH, so a leak of either is caught
    assert {FAKE, FAKE_ALT} <= gc.known_secrets()


# ── D. The record pass loads the live file first ─────────────────────────────


def test_the_record_script_loads_env_local_with_override_before_env():
    """Precedence matters: `.env.local` is live, `.env` is stale. Loading
    `.env` second WITHOUT override keeps `.env.local` winning for a name both
    define, which is the intended result."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "record_golden_fixtures.py").read_text(encoding="utf-8")
    i_local = src.index('load_dotenv(ROOT / ".env.local", override=True)')
    i_plain = src.index('load_dotenv(ROOT / ".env")')
    assert i_local < i_plain, ".env.local must be loaded first, with override"


def test_the_record_script_reports_its_own_arming():
    """A gate that prints its armed count cannot silently go blind."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "record_golden_fixtures.py").read_text(encoding="utf-8")
    assert "secret sweep armed with" in src
    assert "gc.unarmed_secret_vars()" in src


def test_known_secrets_never_prints_a_value(capfd, monkeypatch, tmp_path):
    """The whole point of names-only diagnostics."""
    _root(monkeypatch, tmp_path, **{".env.local": f"FMP_API_KEY={FAKE}\n"})
    gc.known_secrets()
    gc.unarmed_secret_vars()
    try:
        gc.assert_no_secrets([])
    except RuntimeError as exc:
        assert FAKE not in str(exc), "the raise message must not carry a value"
    out = capfd.readouterr()
    assert FAKE not in out.out and FAKE not in out.err


def test_the_docstring_records_why_the_files_are_read():
    """So the next person does not "simplify" it back to env-only."""
    doc = inspect.getdoc(gc.known_secrets) or ""
    assert "union" in doc.lower()
    assert "CLEAN" in doc or "clean" in doc
