"""
File-locked JSON state read/write helpers.

These functions protect read-modify-write JSON files from concurrent corruption
when multiple pipeline runs execute simultaneously. Uses filelock for
cross-platform file locking (works on both POSIX and Windows).

Usage:
    from src.utils.json_state import load_json_locked, save_json_locked, update_json_locked

    # Simple read
    data = load_json_locked("src/data/trade_log.json", default=[])

    # Simple write
    save_json_locked("src/data/regime_state.json", {"regime": "bull"})

    # Atomic read-modify-write (safest for appends)
    def _append(entry):
        entries = load_json_locked(path, default=[])
        entries.append(entry)
        return entries
    update_json_locked(path, _append)
"""

import json
import os
from typing import Any, Callable

from filelock import FileLock


def _lock_path(path: str) -> str:
    """Return the lock file path for a given data file."""
    return path + ".lock"


def load_json_locked(path: str, default: Any = None) -> Any:
    """Load JSON from a file with a shared lock.

    Returns `default` if the file doesn't exist or is corrupt.
    """
    lock = FileLock(_lock_path(path), timeout=10)
    with lock:
        if not os.path.exists(path):
            return default if default is not None else {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default if default is not None else {}


def save_json_locked(path: str, data: Any, indent: int = 2) -> None:
    """Save data as JSON to a file with an exclusive lock.

    Creates parent directories if they don't exist.
    """
    lock = FileLock(_lock_path(path), timeout=10)
    with lock:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)


def update_json_locked(
    path: str,
    mutate: Callable[[Any], Any],
    default: Any = None,
    indent: int = 2,
) -> Any:
    """Atomic read-modify-write of a JSON file.

    Reads the current content, applies `mutate(data)`, and writes the result
    back — all under a single lock so concurrent callers are serialized.

    Args:
        path: Path to the JSON file.
        mutate: Function that takes the current data and returns the new data.
        default: Value to pass to `mutate` if the file doesn't exist.
        indent: JSON indentation level.

    Returns:
        The new data that was written.
    """
    lock = FileLock(_lock_path(path), timeout=10)
    with lock:
        # Read
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = default if default is not None else {}
        else:
            data = default if default is not None else {}

        # Mutate
        new_data = mutate(data)

        # Write
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=indent)

        return new_data
