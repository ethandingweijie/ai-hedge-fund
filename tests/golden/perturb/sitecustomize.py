"""Deliberate 10% multiple perturbation — Phase 0 acceptance check.

NOT part of the product. This file exists so the golden suite can be proved to
actually fail: the acceptance bar for Phase 0 is that a deliberate 10% change
to a valuation multiple is caught with a readable diff, and a suite that has
never been seen to fail is a suite nobody trusts.

Loaded by the replay subprocess via ``PYTHONPATH`` — Python imports
``sitecustomize`` during interpreter start-up, before ``src`` is touched, so
patching ``src.data.sector_profiles.get_sector_peer_multiples`` here is picked
up by ``dcf_agent``'s module-level ``from ... import`` at line 71.

    PYTHONPATH=tests/golden/perturb pytest tests/test_golden_valuations.py

Every numeric peer multiple the engine resolves is scaled by 1.10. The
underscore-prefixed provenance keys ``_stamp`` attaches are left alone — they
are strings describing where a multiple came from, and changing them would
test the diff renderer rather than the valuation.
"""
import sys

FACTOR = 1.10


def _scale(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("_"):
                out[k] = v                      # provenance, not a multiple
            elif isinstance(v, bool):
                out[k] = v
            elif isinstance(v, (int, float)):
                out[k] = round(v * FACTOR, 6)
            else:
                out[k] = _scale(v)
        return out
    return obj


def _install():
    try:
        from src.data import sector_profiles as sp
    except Exception as exc:                    # noqa: BLE001
        print(f"[perturb] could not import sector_profiles: {exc}", file=sys.stderr)
        return
    if getattr(sp, "_GOLDEN_PERTURBED", False):
        return
    original = sp.get_sector_peer_multiples

    def perturbed(*args, **kwargs):
        return _scale(original(*args, **kwargs))

    perturbed.__name__ = original.__name__
    perturbed.__doc__ = (original.__doc__ or "") + "\n[perturbed x1.10]"
    sp.get_sector_peer_multiples = perturbed
    sp._GOLDEN_PERTURBED = True


_install()
