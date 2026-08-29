"""The test suite must not be able to reach a live provider.

tests/conftest.py strips the provider API keys precisely so that an
accidental live call fails fast instead of costing money and minutes. Three
modules undermined that by calling `load_dotenv(..., override=True)` — one of
them at MODULE level — which handed the keys straight back to any test that
transitively imported them, and re-armed live calls for every test that ran
afterwards.

The symptom is not a failure, which is what makes it dangerous: the suite
still passes. It just stops being hermetic. Adding one test that imported
`analysis_service` took the suite from 213 seconds to 3h22m, and every one of
those extra seconds was a real API call.

This pins the invariant so the next `load_dotenv` added to an imported module
is caught by a fast failing test rather than by a three-hour test run.
"""

from __future__ import annotations

import os

import pytest

# The keys tests/conftest.py removes. Kept in sync deliberately by name so a
# key added there without being added here shows up as an obvious omission.
GUARDED_KEYS = (
    "ANTHROPIC_API_KEY",
    "FMP_API_KEY",
    "DEEPSEEK_API_KEY",
    "TAVILY_API_KEY",
    "DEEP_RESEARCH_API_KEY",
)

# Modules known to load .env.local, plus the entry points that pull them in.
IMPORT_SURFACE = (
    "src.main",
    "app.backend.services.analysis_service",
    "app.backend.routes.analysis",
)


def _present() -> dict[str, str | None]:
    return {k: os.environ.get(k) for k in GUARDED_KEYS}


def test_conftest_actually_stripped_the_keys():
    """A guard on the guard — if conftest stops stripping, every other
    assertion here becomes vacuously true."""
    still_set = [k for k, v in _present().items() if v is not None]
    assert not still_set, (
        f"conftest did not strip {still_set}; the suite can reach a live "
        f"provider and the isolation tests below prove nothing"
    )


@pytest.mark.parametrize("module", IMPORT_SURFACE)
def test_importing_does_not_restore_api_keys(module):
    before = _present()
    __import__(module)
    after = _present()
    restored = [k for k in GUARDED_KEYS
                if before[k] is None and after[k] is not None]
    assert not restored, (
        f"importing {module} restored {restored} — it calls load_dotenv with "
        f"override=True without a pytest guard, which re-arms live API calls "
        f"for every test that runs after it"
    )
