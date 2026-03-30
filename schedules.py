"""
User-created watering schedules — reads and writes custom_schedules.json.

Built-in schedules live in config.py (SCHEDULES dict) and cannot be modified here.
Custom schedules created via WhatsApp are stored in this file and persist across restarts.

Custom schedules take precedence over built-in schedules if names collide.
"""

import json
from pathlib import Path

SCHEDULES_FILE = Path(__file__).parent / "custom_schedules.json"

# No built-in schedules — all schedules are user-created and deletable
BUILTIN_NAMES: set[str] = set()


def _load() -> dict:
    """Load custom schedules from disk. Returns empty dict if file doesn't exist."""
    if not SCHEDULES_FILE.exists():
        return {}
    try:
        with open(SCHEDULES_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(schedules: dict):
    """Save custom schedules to disk."""
    with open(SCHEDULES_FILE, "w") as f:
        json.dump(schedules, f, indent=2)


def get_custom_schedules() -> dict:
    """Return all user-created schedules."""
    return _load()


def get_all_schedules() -> dict:
    """
    Return merged dict of built-in + custom schedules.
    Custom schedules override built-ins if they share a name.
    """
    from config import SCHEDULES
    merged = dict(SCHEDULES)
    merged.update(_load())
    return merged


def save_schedule(name: str, description: str, zones: list[dict]) -> None:
    """
    Save a custom schedule.

    Args:
        name: schedule key (e.g. 'evening_sod')
        description: human-readable description
        zones: list of {"zone": int, "minutes": int} dicts
    """
    custom = _load()
    custom[name] = {
        "description": description,
        "zones": zones,
        "custom": True,  # flag so we know it was user-created
    }
    _save(custom)


def remove_schedule(name: str) -> bool:
    """
    Delete a custom schedule. Returns True if deleted, False if not found.
    Raises ValueError if trying to delete a built-in schedule.
    """
    if name in BUILTIN_NAMES:
        raise ValueError(f"'{name}' is a built-in schedule and cannot be deleted.")
    custom = _load()
    if name not in custom:
        return False
    del custom[name]
    _save(custom)
    return True
