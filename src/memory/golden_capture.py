"""Golden-fixture capture/replay harness (Phase 0, item 7).

The valuation engine's end-to-end outputs were never pinned, so the
reinvestment charge moved DCF values between -84% and -309% and the suite
still passed. This module is the recording equipment the golden suite runs
on: it captures every external input to ``run_dcf_agent`` during one live
run, and replays it byte-for-byte offline.

Two capture layers, deliberately different:

1. **Raw FMP JSON** (``src.tools.api._fmp_get``). Recorded unparsed and
   replayed unparsed, so the real parsing code in ``search_line_items``,
   ``get_analyst_estimates``, ``get_prices``, ``get_fx_rate`` and
   ``get_price_target_consensus`` runs during replay exactly as it did
   during capture. Patching the high-level seams instead would freeze the
   *parsed* objects and silently stop testing the parser.

   ``_fmp_get`` is the single network choke point for all three markets —
   HK and SG route through it via ``intl_provider.try_fmp`` before any
   akshare/yfinance fallback (verified 2026-09-16 on 02888.HK and D05.SI).

2. **Recorded returns** for the DB- and file-backed reads
   (``src.memory.*``, ``segment_providers``, the curated SOTP tables).
   These are not network calls with a raw response to replay; they read
   local sqlite files that are gitignored, so a replay that called through
   would produce machine-specific numbers. They are frozen instead.

The frozen layer is limited to LLM-only and curated inputs. Deterministic
KPIs are *not* frozen — replay recomputes them from the recorded raw
series, so Phase 1.3's precedence flip is exercised offline rather than
masked by stale metrics.

Replay keys each call on ``(target name, normalised args)``. Repeat
identical calls are served from a per-key queue in call order, which keeps
a function that returns different values on successive calls honest.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

#: Raw-response capture point. Every FMP read in the engine funnels here.
FMP_TARGET = "src.tools.api._fmp_get"

#: DB/file-backed reads frozen as (args -> return value).
#: ``src.memory.assumption_store`` is patched at source because the engine
#: imports it inline (``from src.memory import assumption_store``) at call
#: time, so patching a dcf_agent attribute would miss it.
#:
#: ``get_fred_spread`` is here rather than in the raw layer because it is a
#: bare ``urllib.request.urlopen`` with no shared choke point, and because its
#: absence is silent: the caller falls back to a Damodaran static table. An
#: uncaptured FRED seam is what made the first BN4.SI replay diverge
#: (base IV 5.83 recorded with a live spread, 6.86 replayed on the fallback)
#: — a WACC input moving the answer 18% with nothing in the diff to explain it.
FROZEN_TARGETS: tuple[str, ...] = (
    "src.memory.assumption_store.get_latest_earnings_assumptions",
    "src.memory.assumption_store.get_open_challenges",
    "src.memory.assumption_store.get_analyst_reports",
    "src.memory.analyst_basis.get_analyst_basis",
    "src.memory.analyst_basis.method_disagrees",
    "src.memory.calibration.active_version",
    "src.memory.calibration.iv_multiplier",
    "src.memory.calibration.apply_profile_weights",
    "src.memory.assumption_steward.steward_enabled",
    "src.tools.segment_providers.get_segment_footnote",
    "src.tools.fred.get_fred_spread",
    "src.tools.api.get_company_industry",
    "src.tools.api.get_listing_currency",
    "src.agents.analysis.sotp_snapshot.curated_holdco_discount",
    "src.agents.analysis.sotp_snapshot.load_sotp_snapshot",
    "src.agents.analysis.sotp_snapshot.lookup_snapshot",
    "src.agents.analysis.sotp_ground_truth.check_table",
)

ALL_TARGETS: tuple[str, ...] = (FMP_TARGET,) + FROZEN_TARGETS

#: Positional argument indexes that must never enter a replay key or a
#: fixture. ``_fmp_get(path, params, api_key, uncap)`` takes the key
#: POSITIONALLY at index 2 — filtering only the ``api_key`` keyword let a live
#: key be written into tests/fixtures/golden/BN4_SI/raw/calls.json, which is
#: not gitignored. Redaction is therefore positional per target, and
#: :func:`scrub_secrets` sweeps the whole document as a second line of defence.
SECRET_POSITIONAL_ARGS: dict[str, frozenset[int]] = {
    FMP_TARGET: frozenset({2}),
}

#: Keyword names dropped from every replay key.
SECRET_KWARGS: frozenset[str] = frozenset({"api_key", "apikey", "key", "token"})

#: Env vars whose values are secrets and must not survive into a fixture.
SECRET_ENV_VARS: tuple[str, ...] = (
    "FMP_API_KEY", "FRED_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
    "DEEP_RESEARCH_API_KEY", "DEEPSEEK_API_KEY", "TAVILY_API_KEY",
    "DATABASE_URL", "SMTP_PASS",
)

#: Stand-in written in place of a redacted secret.
REDACTED = "<redacted>"

#: Repo root. ``golden_capture`` lives at ``src/memory/``, so parents[2].
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Dotenv files this project keeps secrets in.
#:
#: ``.env.local`` is the LIVE one (20 keys). ``.env`` is older and holds a
#: 3-key subset — crucially it has NO ``FMP_API_KEY``, which is the key that
#: actually leaked into ``BN4_SI/raw/calls.json`` once. So a caller that does a
#: bare ``load_dotenv()`` finds ``.env`` first, arms 3 of the 9 names above,
#: and the sweep goes blind to exactly the credential with a history.
#: ``app/frontend/.env.local`` carries no name from the list, but is read so
#: that adding one there later does not silently reopen the hole.
SECRET_DOTENV_FILES: tuple[str, ...] = (
    ".env", ".env.local", "app/frontend/.env.local",
)

#: Shortest value treated as a real secret rather than a placeholder.
MIN_SECRET_LEN = 8


# ── Target resolution ────────────────────────────────────────────────────────

def resolve(target: str) -> tuple[Any, str]:
    """Split ``pkg.mod.attr`` into (module_object, attribute_name)."""
    mod_name, _, attr = target.rpartition(".")
    if not mod_name:
        raise ValueError(f"target must be a dotted path: {target!r}")
    import importlib
    return importlib.import_module(mod_name), attr


def _key_args(target: str, args: tuple, kwargs: dict) -> list:
    """Normalise call arguments into a JSON-stable list.

    Secret positions are replaced with a placeholder rather than dropped, so
    the argument SHAPE stays visible in a key and two calls that differ only
    by key still collide (which is correct — they fetch the same resource).

    Objects that are not natively serialisable fall back to their repr, which
    is stable for the pydantic models and dicts the engine passes here.
    """
    secret_positions = SECRET_POSITIONAL_ARGS.get(target, frozenset())
    out: list = []
    for i, a in enumerate(args):
        out.append(REDACTED if i in secret_positions else _norm(a))
    for k in sorted(kwargs):
        if k.lower() in SECRET_KWARGS:
            out.append([k, REDACTED])
            continue
        out.append([k, _norm(kwargs[k])])
    return out


def _norm(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return repr(v)


def make_key(target: str, args: tuple, kwargs: dict) -> str:
    payload = [target, _key_args(target, args, kwargs)]
    return json.dumps(payload, sort_keys=True, default=repr)


def _dotenv_secrets(root: Path | None = None) -> dict[str, set[str]]:
    """Every :data:`SECRET_ENV_VARS` value written in the project's dotenv files.

    PARSED, not loaded: reading the files directly means the sweep is armed
    even in a process that never imported dotenv, and it never mutates
    ``os.environ`` as a side effect of a security check. Returns names mapped
    to every value seen for them, because more than one file can name the same
    var with a different value and both must be scrubbed.
    """
    root = root or PROJECT_ROOT
    found: dict[str, set[str]] = {}
    for rel in SECRET_DOTENV_FILES:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            name = name.strip()
            if name not in SECRET_ENV_VARS:
                continue
            val = val.strip().strip('"').strip("'")
            if len(val) >= MIN_SECRET_LEN:
                found.setdefault(name, set()).add(val)
    return found


def unarmed_secret_vars(root: Path | None = None) -> list[str]:
    """Names of secrets that exist on disk but are absent from ``os.environ``.

    Diagnostics only, and names only — never a value. A record pass that
    reports a non-empty list is running with an environment that does not match
    the files, which is how a caller ends up scrubbing against a smaller set of
    credentials than the project actually holds.
    """
    on_disk = _dotenv_secrets(root)
    out: list[str] = []
    for name, vals in on_disk.items():
        env_val = os.environ.get(name) or ""
        if env_val not in vals:
            out.append(name)
    return sorted(out)


def known_secrets(root: Path | None = None) -> set[str]:
    """Live secret values that must never be written into a fixture.

    The union of the environment and the project's dotenv files. A fixture is
    committed test data, so a leaked key is a credential in git history —
    positional redaction stops the known argument slots, and this sweep catches
    anything that arrived inside a response body or an echoed request URL.

    Reading the files as well as the environment is the point. An env-only
    version of this function is armed by whatever the caller happened to load,
    and ``assert_no_secrets`` returns ``[]`` when it is handed an empty set — so
    an under-armed gate reports CLEAN, which is indistinguishable from a
    genuinely clean fixture. ``scripts/record_golden_fixtures.py`` does load
    ``.env.local`` explicitly (L47-48), so the record pass was never blind; but
    the gate should not depend on a caller 140 lines away remembering to.
    """
    out: set[str] = set()
    for var in SECRET_ENV_VARS:
        val = os.environ.get(var)
        if val and len(val) >= MIN_SECRET_LEN:
            out.add(val)
    for vals in _dotenv_secrets(root).values():
        out |= vals
    return out


def scrub_secrets(obj: Any, secrets: set[str] | None = None) -> Any:
    """Recursively replace any known secret value with :data:`REDACTED`.

    Also matches secrets embedded in longer strings (a request URL carrying
    ``apikey=...``, a libpq DSN with a password), which is how a key would
    survive a naive whole-value comparison.
    """
    secrets = known_secrets() if secrets is None else secrets
    if not secrets:
        return obj
    if isinstance(obj, str):
        if obj in secrets:
            return REDACTED
        hit = next((s for s in secrets if s in obj), None)
        return obj.replace(hit, REDACTED) if hit else obj
    if isinstance(obj, dict):
        return {k: scrub_secrets(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_secrets(v, secrets) for v in obj]
    return obj


# ── Recording ────────────────────────────────────────────────────────────────

class Recorder:
    """Installs capturing wrappers over :data:`ALL_TARGETS`.

    Use as a context manager. ``calls`` is the list to serialise into the
    fixture; each entry is ``{key, target, result}`` in call order.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._undo: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        #: Targets that were hit. Anything in ALL_TARGETS missing from here
        #: after a run means the engine never called it for that ticker.
        self.hits: dict[str, int] = {}
        #: Snapshot taken at install time, so a key loaded later by an
        #: import-time dotenv cannot slip past the sweep.
        self._secrets = known_secrets()
        #: Set while a FROZEN_TARGET's real implementation is on the stack.
        #: Calls made from inside it are not recorded: on replay the frozen
        #: wrapper returns the recorded value before reaching them, so
        #: recording them would leave a permanent ``unused()`` entry and blind
        #: the drift assertion. (``get_company_industry`` and
        #: ``get_listing_currency`` both fetch ``/stable/profile`` internally;
        #: their return values are what replay serves.)
        self._local = threading.local()

    @property
    def _in_frozen(self) -> bool:
        return getattr(self._local, "in_frozen", False)

    def __enter__(self) -> "Recorder":
        for target in ALL_TARGETS:
            self._install(target)
        return self

    def __exit__(self, *exc) -> None:
        while self._undo:
            self._undo.pop()()

    def _install(self, target: str) -> None:
        module, attr = resolve(target)
        original = getattr(module, attr)
        recorder = self
        is_frozen = target in FROZEN_TARGETS

        def wrapper(*args, **kwargs):
            if is_frozen:
                prev = getattr(recorder._local, "in_frozen", False)
                recorder._local.in_frozen = True
                try:
                    result = original(*args, **kwargs)
                finally:
                    recorder._local.in_frozen = prev
            else:
                result = original(*args, **kwargs)
            key = make_key(target, args, kwargs)
            with recorder._lock:
                if recorder._in_frozen and not is_frozen:
                    return result   # nested under a frozen read; not replayed
                recorder.calls.append({
                    "key": key,
                    "target": target,
                    "result": scrub_secrets(_norm(result), recorder._secrets),
                })
                recorder.hits[target] = recorder.hits.get(target, 0) + 1
            return result

        setattr(module, attr, wrapper)
        self._undo.append(lambda: setattr(module, attr, original))


# ── Replay ───────────────────────────────────────────────────────────────────

class ReplayError(AssertionError):
    """An offline run asked for something the fixture does not contain."""


class Replayer:
    """Serves recorded results; never touches the network or the DB.

    An unrecorded call raises rather than returning None. A silent None would
    let a missing fixture degrade a valuation into a fallback path and still
    "pass", which is exactly the failure mode the golden suite exists to
    catch.

    The raise is best-effort, and the miss list is the real gate: the engine
    wraps most of its optional inputs in ``except Exception`` (``_regional_peer_multiples``
    returns ``{}`` on any failure), so a :class:`ReplayError` raised there is
    swallowed and the run completes on degraded inputs. Every miss is recorded
    on ``self.misses`` regardless, and ``test_golden_valuation`` asserts that
    list is empty — which is what actually caught the first basket's
    cache-warming defect rather than the exception.
    """

    def __init__(self, calls: list[dict]) -> None:
        self._queues: dict[str, list[Any]] = {}
        for entry in calls:
            self._queues.setdefault(entry["key"], []).append(entry["result"])
        self._lock = threading.Lock()
        self._undo: list[Callable[[], None]] = []
        #: Keys served, for the "fixture was never used" assertion.
        self.served: set[str] = set()
        self.misses: list[str] = []

    def __enter__(self) -> "Replayer":
        for target in ALL_TARGETS:
            self._install(target)
        return self

    def __exit__(self, *exc) -> None:
        while self._undo:
            self._undo.pop()()

    def _install(self, target: str) -> None:
        module, attr = resolve(target)
        original = getattr(module, attr)
        replayer = self

        def wrapper(*args, **kwargs):
            key = make_key(target, args, kwargs)
            with replayer._lock:
                queue = replayer._queues.get(key)
                if not queue:
                    replayer.misses.append(key)
                    raise ReplayError(
                        f"unrecorded call during golden replay: {target}{_sig(args, kwargs)}\n"
                        f"  key={key[:200]}"
                    )
                replayer.served.add(key)
                # A single recorded value is reused for repeat calls; a
                # multi-value queue is consumed in order.
                return queue[0] if len(queue) == 1 else queue.pop(0)

        setattr(module, attr, wrapper)
        self._undo.append(lambda: setattr(module, attr, original))

    def unused(self) -> list[str]:
        """Recorded keys never asked for — a fixture drifting out of date."""
        return [k for k in self._queues if k not in self.served]


def _sig(args: tuple, kwargs: dict) -> str:
    parts = [repr(a)[:60] for a in args]
    parts += [f"{k}={repr(v)[:40]}" for k, v in sorted(kwargs.items())
              if k != "api_key"]
    return "(" + ", ".join(parts) + ")"


# ── Fixture IO ───────────────────────────────────────────────────────────────

GOLDEN_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden"


def fixture_dir(ticker: str) -> Path:
    return GOLDEN_ROOT / ticker.replace(".", "_")


def write_fixture(ticker: str, doc: dict) -> Path:
    """Write ``raw/calls.json`` plus capture metadata.

    The whole document is swept for known secrets on the way out, so a key
    that reached a fixture through a path nobody anticipated still does not
    get committed.
    """
    secrets = known_secrets()
    d = fixture_dir(ticker)
    (d / "raw").mkdir(parents=True, exist_ok=True)
    calls_path = d / "raw" / "calls.json"
    with open(calls_path, "w", encoding="utf-8") as fh:
        json.dump(scrub_secrets(doc["calls"], secrets), fh, indent=1,
                  default=repr, sort_keys=False)
    meta_path = d / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(scrub_secrets({k: v for k, v in doc.items() if k != "calls"},
                                secrets),
                  fh, indent=2, default=str)
    return calls_path


def write_web_run(ticker: str, doc: dict) -> Path:
    """Persist the archived production row that supplies replay's frozen
    LLM-only inputs. Swept for secrets on the same reasoning."""
    path = fixture_dir(ticker) / "web_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scrub_secrets(doc, known_secrets()), fh, indent=1, default=str)
    return path


def assert_no_secrets(paths: list[Path], *,
                      allow_unarmed: bool = False) -> list[str]:
    """Return every path that still contains a known secret value.

    Run after a record pass. A fixture directory is committed test data and is
    NOT gitignored, so this is the last gate before a credential reaches git
    history.

    Raises rather than returning ``[]`` when it has no secret to compare
    against. An empty comparison set used to short-circuit to a clean report,
    which is exactly wrong for a gate: "I checked nothing" and "I found
    nothing" are different statements, and only the second one is a pass. The
    caller that really is running on a keyless box passes
    ``allow_unarmed=True`` and says so out loud.
    """
    secrets = known_secrets()
    if not secrets:
        if allow_unarmed:
            return []
        on_disk = sorted(_dotenv_secrets())
        raise RuntimeError(
            "assert_no_secrets() has no secret value to compare against, so it "
            "cannot certify these fixtures as clean.\n"
            f"  env vars armed  : 0 of {len(SECRET_ENV_VARS)}\n"
            f"  dotenv files  : {', '.join(SECRET_DOTENV_FILES)}\n"
            f"  names on disk : {on_disk or 'none'}\n"
            "Load the project's dotenv files before recording, or pass "
            "allow_unarmed=True if this environment genuinely holds no keys."
        )
    leaked: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for s in secrets:
            if s in text:
                leaked.append(f"{p}: contains a live secret value")
                break
    return leaked


def read_fixture(ticker: str) -> tuple[list[dict], dict]:
    d = fixture_dir(ticker)
    with open(d / "raw" / "calls.json", encoding="utf-8") as fh:
        calls = json.load(fh)
    with open(d / "meta.json", encoding="utf-8") as fh:
        meta = json.load(fh)
    return calls, meta


def comps_from_entry(entry: dict) -> dict:
    """The live peer multiples a run resolved, as `get_regional_multiples`
    returned them, plus the comps age. Static/dynamic fields are not comps and
    are left out: replay recomputes those from the tables in code."""
    mu = (entry or {}).get("multiples_used") or {}
    fields = {}
    for f, info in (mu.get("fields") or {}).items():
        if (info or {}).get("basis") in ("industry", "sector") and info.get("value") is not None:
            fields[f] = {k: info.get(k) for k in
                         ("value", "basis", "cohort", "peer_count", "key", "exchange")}
    return {"fields": fields, "comp_age_days": mu.get("comp_age_days")}


def write_comps(ticker: str, comps: dict) -> Path:
    path = fixture_dir(ticker) / "comps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(comps, fh, indent=1, sort_keys=True)
    return path


def read_comps(ticker: str) -> dict | None:
    path = fixture_dir(ticker) / "comps.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class frozen_comps:
    """Serve a fixture's recorded comps instead of the regional_comps table.

    Without this, replay read the LOCAL store: a local refresh moved every
    fixture's peer multiples (US names flipped from static tables to live
    industry medians) with no code change. A fixture without comps.json
    resolves no live comps at all, which is what a stale store gave it.
    """

    def __init__(self, comps: dict | None):
        c = comps or {}
        self._fields = c.get("fields") or {}
        self._age = c.get("comp_age_days")

    def __enter__(self):
        from src.data import regional_comps as rc
        self._rc = rc
        self._orig = (rc.get_regional_multiples, rc.latest_refresh_age_days)
        fields, age = self._fields, self._age
        rc.get_regional_multiples = lambda *a, **k: {f: dict(v) for f, v in fields.items()}
        rc.latest_refresh_age_days = lambda *a, **k: age
        return self

    def __exit__(self, *exc):
        self._rc.get_regional_multiples, self._rc.latest_refresh_age_days = self._orig
        return False


def read_web_run(ticker: str) -> dict | None:
    path = fixture_dir(ticker) / "web_run.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def available_tickers() -> list[str]:
    if not GOLDEN_ROOT.exists():
        return []
    out = []
    for p in sorted(GOLDEN_ROOT.iterdir()):
        if p.is_dir() and (p / "raw" / "calls.json").exists():
            out.append(p.name)
    return out


# ── Hermetic environment ─────────────────────────────────────────────────────

#: Env vars that must be pinned so a replay is reproducible on any machine.
#: ASSUMPTION_STEWARD gates a DB read; EARNINGS_ASSUMPTIONS gates the R1
#: guidance path; VALUATION_CALIBRATION_DISABLED would short-circuit the
#: calibration seam the fixture records.
PINNED_ENV = {
    "ASSUMPTION_STEWARD": "true",
    "EARNINGS_ASSUMPTIONS": "true",
    "VALUATION_CALIBRATION_DISABLED": "false",
    "PYTHONUTF8": "1",
}


class pinned_env:
    """Pin :data:`PINNED_ENV` and make the process strictly offline.

    Every credential in :data:`SECRET_ENV_VARS` is REMOVED, not merely
    defaulted — an unset key is a guarantee, a dummy key is a hope. With no
    ``FMP_API_KEY`` a stray uncaptured request cannot authenticate, and with
    no ``FRED_API_KEY`` the spread cannot be fetched live behind the frozen
    ``get_fred_spread``. ``DATABASE_URL`` goes for the same reason: a replay
    must not be able to reach production Postgres, whose contents move.

    ``FMP_API_KEY`` is then set to a dummy because callers format the key into
    log lines and a missing key changes those code paths.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[str, Any]] = []

    def __enter__(self) -> "pinned_env":
        for k, v in PINNED_ENV.items():
            self._set(k, v)
        for k in SECRET_ENV_VARS:
            self._set(k, None)
        self._set("FMP_API_KEY", "golden-replay-no-network")
        return self

    def __exit__(self, *exc) -> None:
        for k, old in reversed(self._undo):
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old

    def _set(self, key: str, value: str | None) -> None:
        self._undo.append((key, os.environ.get(key)))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
