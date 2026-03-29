"""
Watering history log — reads and writes a local JSON file.

Each entry records one watering event (a single zone run or a skipped event).
The agent reads this to answer questions like "when did zone 2 last run?"

Log file location: watering_log.json in the same directory as this file.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG_FILE = Path(__file__).parent / "watering_log.json"

# Central timezone offset for Austin TX (CST = UTC-6, CDT = UTC-5)
# We store UTC in the file and display local time in responses.
AUSTIN_TZ = timezone(timedelta(hours=-5))  # CDT (daylight saving)


def _load() -> list[dict]:
    """Load the full log from disk. Returns empty list if file doesn't exist."""
    if not LOG_FILE.exists():
        return []
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]):
    """Save the full log to disk."""
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def append_event(event: dict):
    """
    Append a single event to the log.

    Event fields:
      - timestamp_utc: ISO 8601 UTC string (auto-added if missing)
      - event_type: "zone_run", "schedule_run", "zone_skipped", "manual_stop"
      - zone: int or None
      - zone_name: str or None
      - duration_minutes: int or None
      - schedule_name: str or None
      - notes: str or None (e.g. "skipped — rain expected")
    """
    if "timestamp_utc" not in event:
        event["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

    entries = _load()
    entries.append(event)

    # Cap log at 500 entries to avoid unbounded growth
    if len(entries) > 500:
        entries = entries[-500:]

    _save(entries)


def get_recent_events(days: int = 7) -> list[dict]:
    """Return events from the last N days, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = _load()
    recent = [
        e for e in entries
        if datetime.fromisoformat(e["timestamp_utc"]) >= cutoff
    ]
    return list(reversed(recent))


def get_last_run_for_zone(zone_number: int) -> dict | None:
    """Return the most recent zone_run event for a specific zone, or None."""
    entries = _load()
    for entry in reversed(entries):
        if entry.get("event_type") == "zone_run" and entry.get("zone") == zone_number:
            return entry
    return None


def format_local_time(timestamp_utc: str) -> str:
    """Convert a UTC ISO string to a readable Austin local time string."""
    dt = datetime.fromisoformat(timestamp_utc).astimezone(AUSTIN_TZ)
    return dt.strftime("%a %b %-d at %-I:%M %p CDT")
