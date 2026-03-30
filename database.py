"""
SQLite database for all sprinkler agent data.

Single source of truth for zones, schedules, watering history, and settings.
Replaces: config.py (zone data), history.py (watering log), schedules.py (custom schedules).

On first run, creates the database and seeds it with zone definitions and default settings.
Migrates existing watering_log.json and custom_schedules.json if present.
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "sprinkler.db"

# Austin TX timezone for display formatting
AUSTIN_TZ = timezone(timedelta(hours=-5))  # CDT


def get_conn():
    """Open a connection with row-factory and foreign key support."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema + seeding
# ---------------------------------------------------------------------------

def init_db():
    """Create tables if needed, seed initial data, migrate JSON files."""
    conn = get_conn()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS zones (
            zone_number INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            entity_id TEXT NOT NULL,
            wired INTEGER DEFAULT 0,
            new_planting INTEGER DEFAULT 0,
            plant_type TEXT,
            sprinkler_type TEXT,
            location TEXT,
            default_duration_minutes INTEGER DEFAULT 10,
            zen16_number INTEGER,
            relay_number INTEGER,
            flow_rate_gpm REAL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS schedules (
            name TEXT PRIMARY KEY,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS schedule_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_name TEXT NOT NULL REFERENCES schedules(name) ON DELETE CASCADE,
            zone_number INTEGER NOT NULL REFERENCES zones(zone_number),
            minutes INTEGER NOT NULL,
            run_order INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS watering_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            event_type TEXT NOT NULL,
            zone_number INTEGER,
            zone_name TEXT,
            duration_minutes INTEGER,
            schedule_name TEXT,
            weather_temp_f REAL,
            weather_condition TEXT,
            weather_rain_mm REAL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT
        );
    """)

    # Seed zones if table is empty
    if conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 0:
        _seed_zones(conn)

    # Seed settings if table is empty
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        _seed_settings(conn)

    # Migrate existing JSON data (one-time, skipped if tables already have data)
    _migrate_json_data(conn)

    conn.commit()
    conn.close()


def _seed_zones(conn):
    """Insert the initial 12 zone definitions."""
    zones = [
        (1, "Front Beds & Trees",
         "Front yard flower beds and bubblers. Monterrey Oak (2), Crape Myrtle, "
         "Texas Sage (5), Ligustrum (5), Carolina Cherries (5), Pride of Barbados. "
         "All installed ~3 weeks ago.",
         "switch.sprinkler_zone_1", 1, 1, "trees_shrubs", "bubblers",
         "front_beds", 8, 1, 2),
        (2, "Front Lawn Right",
         "Front yard right side lawn. ~10 sprayers + 1 needing repair. "
         "Zoysia Palisades sod installed ~3 weeks ago. Some overspray onto walkway.",
         "switch.sprinkler_zone_2", 1, 1, "new_sod", "sprayers",
         "front_right", 12, 1, 1),
        (3, "Front Lawn Left",
         "Front yard left side lawn. 9 sprayers. "
         "Zoysia Palisades sod installed ~3 weeks ago.",
         "switch.sprinkler_zone_3", 1, 1, "new_sod", "sprayers",
         "front_left", 12, 1, 3),
        (4, "Backyard Left Front",
         "Backyard left front area. 3 sprayers. Mainly weeds and live oaks.",
         "switch.sprinkler_zone_4", 0, 0, "lawn", "sprayers",
         "backyard_left_front", 10, 2, 1),
        (5, "Backyard Far Left Front",
         "Backyard far left front. 3 long rotating sprayers. Live oaks and weeds.",
         "switch.sprinkler_zone_5", 0, 0, "lawn", "rotating",
         "backyard_far_left_front", 10, 2, 2),
        (6, "Backyard Far Left Back",
         "Backyard far left back. 3 long rotating sprayers. Live oaks and weeds.",
         "switch.sprinkler_zone_6", 0, 0, "lawn", "rotating",
         "backyard_far_left_back", 10, 2, 3),
        (7, "Backyard Left Flowerbeds",
         "Backyard left flowerbeds along retaining wall. Live oaks and weeds.",
         "switch.sprinkler_zone_7", 0, 0, "flowerbeds", "sprayers",
         "backyard_left_flowerbeds", 8, 3, 1),
        (8, "Backyard Left Back Fence",
         "Backyard left back fence and along retaining wall.",
         "switch.sprinkler_zone_8", 0, 0, "lawn", "sprayers",
         "backyard_left_back", 10, 3, 2),
        (9, "Right Walkway (Eliminate)",
         "Right side and upper walkway. Candidate for elimination.",
         "switch.sprinkler_zone_9", 0, 0, "lawn", "sprayers",
         "right_walkway", 0, 3, 3),
        (10, "Right Side Back",
         "Right side yard back. 5 long rotating sprayers. Mainly weeds and live oaks.",
         "switch.sprinkler_zone_10", 0, 0, "lawn", "rotating",
         "right_back", 10, 3, 4),
        (11, "Right Side Middle",
         "Right side yard middle. 5 long rotating sprayers. Mainly weeds and live oaks.",
         "switch.sprinkler_zone_11", 0, 0, "lawn", "rotating",
         "right_middle", 10, 3, 5),
        (12, "Right Side Front",
         "Right side yard front. 9 sprayers. Mainly weeds and live oaks.",
         "switch.sprinkler_zone_12", 0, 0, "lawn", "sprayers",
         "right_front", 10, 3, 6),
    ]
    conn.executemany(
        """INSERT INTO zones
           (zone_number, name, description, entity_id, wired, new_planting,
            plant_type, sprinkler_type, location, default_duration_minutes,
            zen16_number, relay_number)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        zones,
    )


def _seed_settings(conn):
    """Insert default safety and weather settings."""
    settings = [
        ("max_zone_duration_minutes", "30", "Hard max any single zone will run"),
        ("inter_zone_delay_seconds", "5", "Pause between zones in a schedule"),
        ("skip_if_rain_mm", "6.0", "Skip watering if rain >= this in 24h"),
        ("reduce_if_temp_below_f", "65", "Reduce duration 30% below this temp"),
        ("increase_if_temp_above_f", "95", "Increase duration 20% above this temp"),
    ]
    conn.executemany(
        "INSERT INTO settings (key, value, description) VALUES (?, ?, ?)",
        settings,
    )


def _migrate_json_data(conn):
    """One-time migration of watering_log.json and custom_schedules.json."""
    base = Path(__file__).parent

    # Migrate watering log
    log_file = base / "watering_log.json"
    if log_file.exists():
        if conn.execute("SELECT COUNT(*) FROM watering_events").fetchone()[0] == 0:
            try:
                with open(log_file, "r") as f:
                    events = json.load(f)
                for e in events:
                    conn.execute(
                        """INSERT INTO watering_events
                           (timestamp_utc, event_type, zone_number, zone_name,
                            duration_minutes, schedule_name, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (e.get("timestamp_utc"), e.get("event_type"), e.get("zone"),
                         e.get("zone_name"), e.get("duration_minutes"),
                         e.get("schedule_name"), e.get("notes")),
                    )
            except (json.JSONDecodeError, OSError):
                pass

    # Migrate custom schedules
    sched_file = base / "custom_schedules.json"
    if sched_file.exists():
        if conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] == 0:
            try:
                with open(sched_file, "r") as f:
                    schedules = json.load(f)
                for name, sched in schedules.items():
                    conn.execute(
                        "INSERT INTO schedules (name, description) VALUES (?, ?)",
                        (name, sched.get("description", "")),
                    )
                    for i, step in enumerate(sched.get("zones", [])):
                        conn.execute(
                            """INSERT INTO schedule_zones
                               (schedule_name, zone_number, minutes, run_order)
                               VALUES (?, ?, ?, ?)""",
                            (name, step["zone"], step["minutes"], i + 1),
                        )
            except (json.JSONDecodeError, OSError):
                pass


# ---------------------------------------------------------------------------
# Zone operations
# ---------------------------------------------------------------------------

def get_zone(zone_number):
    """Return a zone as a dict, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM zones WHERE zone_number = ?", (zone_number,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_zones():
    """Return all zones as a list of dicts, ordered by zone number."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM zones ORDER BY zone_number").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_wired_zones():
    """Return only wired zones as a list of dicts."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM zones WHERE wired = 1 ORDER BY zone_number"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_zone(zone_number, **kwargs):
    """
    Update one or more fields on a zone. Returns True if updated.
    Only whitelisted columns can be changed.
    """
    allowed = {
        "name", "description", "entity_id", "wired", "new_planting",
        "plant_type", "sprinkler_type", "location", "default_duration_minutes",
        "zen16_number", "relay_number", "flow_rate_gpm", "notes",
    }
    filtered = {}
    for k, v in kwargs.items():
        if k in allowed:
            filtered[k] = v
    if not filtered:
        return False

    parts = []
    values = []
    for k, v in filtered.items():
        parts.append(k + " = ?")
        values.append(v)
    values.append(zone_number)

    conn = get_conn()
    cursor = conn.execute(
        "UPDATE zones SET " + ", ".join(parts) + " WHERE zone_number = ?",
        values,
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


# ---------------------------------------------------------------------------
# Schedule operations
# ---------------------------------------------------------------------------

def get_schedule(name):
    """Return a schedule dict with 'zones' list, or None."""
    conn = get_conn()
    sched = conn.execute(
        "SELECT * FROM schedules WHERE name = ?", (name,)
    ).fetchone()
    if sched is None:
        conn.close()
        return None
    rows = conn.execute(
        """SELECT zone_number, minutes FROM schedule_zones
           WHERE schedule_name = ? ORDER BY run_order""",
        (name,),
    ).fetchall()
    conn.close()
    return {
        "name": sched["name"],
        "description": sched["description"],
        "zones": [{"zone": r["zone_number"], "minutes": r["minutes"]} for r in rows],
    }


def get_all_schedules_db():
    """Return all schedules as a dict keyed by name."""
    conn = get_conn()
    scheds = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
    result = {}
    for s in scheds:
        rows = conn.execute(
            """SELECT zone_number, minutes FROM schedule_zones
               WHERE schedule_name = ? ORDER BY run_order""",
            (s["name"],),
        ).fetchall()
        result[s["name"]] = {
            "description": s["description"],
            "zones": [{"zone": r["zone_number"], "minutes": r["minutes"]} for r in rows],
        }
    conn.close()
    return result


def save_schedule_db(name, description, zones):
    """Create or replace a schedule. zones: list of {"zone": int, "minutes": int}."""
    conn = get_conn()
    conn.execute("DELETE FROM schedule_zones WHERE schedule_name = ?", (name,))
    conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
    conn.execute(
        "INSERT INTO schedules (name, description) VALUES (?, ?)",
        (name, description),
    )
    for i, step in enumerate(zones):
        conn.execute(
            """INSERT INTO schedule_zones
               (schedule_name, zone_number, minutes, run_order)
               VALUES (?, ?, ?, ?)""",
            (name, step["zone"], step["minutes"], i + 1),
        )
    conn.commit()
    conn.close()


def delete_schedule_db(name):
    """Delete a schedule. Returns True if it existed."""
    conn = get_conn()
    cursor = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


# ---------------------------------------------------------------------------
# Watering event operations
# ---------------------------------------------------------------------------

def log_watering_event(event_type, zone_number=None, zone_name=None,
                       duration_minutes=None, schedule_name=None,
                       weather_temp_f=None, weather_condition=None,
                       weather_rain_mm=None, notes=None):
    """Log a watering event with optional weather context."""
    conn = get_conn()
    conn.execute(
        """INSERT INTO watering_events
           (timestamp_utc, event_type, zone_number, zone_name, duration_minutes,
            schedule_name, weather_temp_f, weather_condition, weather_rain_mm, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), event_type, zone_number,
         zone_name, duration_minutes, schedule_name,
         weather_temp_f, weather_condition, weather_rain_mm, notes),
    )
    conn.commit()
    conn.close()


def get_recent_events(days=7):
    """Return events from the last N days, newest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM watering_events
           WHERE timestamp_utc >= ? ORDER BY timestamp_utc DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_run_for_zone(zone_number):
    """Return the most recent zone_run event for a zone, or None."""
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM watering_events
           WHERE event_type = 'zone_run' AND zone_number = ?
           ORDER BY timestamp_utc DESC LIMIT 1""",
        (zone_number,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Settings operations
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    """Get a setting value as a string."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_setting_int(key, default=0):
    """Get a setting as an int."""
    val = get_setting(key)
    if val is None:
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def get_setting_float(key, default=0.0):
    """Get a setting as a float."""
    val = get_setting(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def set_setting(key, value, description=None):
    """Create or update a setting."""
    conn = get_conn()
    if description:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, description) VALUES (?, ?, ?)",
            (key, str(value), description),
        )
    else:
        existing = conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
        if existing:
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)", (key, str(value))
            )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def format_local_time(timestamp_utc):
    """Convert a UTC ISO string to a readable Austin local time string."""
    dt = datetime.fromisoformat(timestamp_utc).astimezone(AUSTIN_TZ)
    return dt.strftime("%a %b %d at %I:%M %p CDT").replace(" 0", " ")
