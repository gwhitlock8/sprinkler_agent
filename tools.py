"""
LangChain tools that the agent can call.

Each tool is a Python async function wrapped with @tool.
The docstrings are what the LLM reads to decide when to use each tool — keep them clear.
"""

import asyncio
import json
from datetime import datetime, timezone
from langchain_core.tools import tool

from ha_client import ha
from weather import get_weather_forecast
from config import ZONES, SAFETY
from history import append_event, get_recent_events, get_last_run_for_zone, format_local_time
from schedules import get_all_schedules, save_schedule, remove_schedule, BUILTIN_NAMES


def _zone_info(zone_num: int) -> str:
    """Helper: return a short description of a zone for error messages."""
    z = ZONES.get(zone_num)
    if not z:
        return f"Zone {zone_num} (not configured)"
    return f"Zone {zone_num} ({z['name']})"


async def _sync_schedules_to_ha():
    """Push current schedule list to the HA dashboard display helper."""
    all_scheds = get_all_schedules()
    parts = []
    for name, sched in all_scheds.items():
        tag = "" if name in BUILTIN_NAMES else " \u2605"
        zones_str = ", ".join(f"Z{s['zone']}:{s['minutes']}m" for s in sched["zones"])
        parts.append(f"{name}{tag} ({zones_str})")
    text = " | ".join(parts)
    await ha.update_text_helper("input_text.sprinkler_schedules_display", text)


# ---------------------------------------------------------------------------
# TOOL: Get zone status
# ---------------------------------------------------------------------------

@tool
async def get_zone_status(zone_number: int) -> str:
    """
    Check whether a specific sprinkler zone is currently ON or OFF.
    Use this to answer questions like 'Is zone 2 running?' or 'What's active right now?'
    Pass zone_number as an integer (1-12).
    """
    zone = ZONES.get(zone_number)
    if not zone:
        return f"Zone {zone_number} is not configured."
    if not zone["wired"]:
        return (
            f"Zone {zone_number} ({zone['name']}) is not yet wired to a ZEN16 relay. "
            "It cannot be controlled until wired."
        )

    state = await ha.is_on(zone["entity_id"])
    status = "ON (running)" if state else "OFF"
    return f"Zone {zone_number} ({zone['name']}): {status}"


@tool
async def get_all_zones_status() -> str:
    """
    Get the current ON/OFF status of all configured sprinkler zones.
    Use this when the user asks 'What's running?' or 'Show me all zones'.
    """
    lines = []
    for zone_num, zone in ZONES.items():
        if not zone["wired"]:
            lines.append(f"  Zone {zone_num:2d} ({zone['name']}): NOT WIRED")
            continue
        state = await ha.is_on(zone["entity_id"])
        status = "ON  ✓" if state else "off"
        lines.append(f"  Zone {zone_num:2d} ({zone['name']}): {status}")
    return "Zone Status:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# TOOL: Run a single zone
# ---------------------------------------------------------------------------

@tool
async def run_zone(zone_number: int, minutes: int) -> str:
    """
    Turn on a specific sprinkler zone for a given number of minutes, then turn it off.
    This is the primary tool for watering a single zone.

    SAFETY: Will refuse if another zone is already running.
    Will cap duration at 30 minutes maximum.

    Args:
        zone_number: integer 1-12
        minutes: how long to run (1-30)
    """
    zone = ZONES.get(zone_number)
    if not zone:
        return f"Error: Zone {zone_number} does not exist."

    if not zone["wired"]:
        return (
            f"Zone {zone_number} ({zone['name']}) is not yet wired. "
            "Cannot activate unwired zones."
        )

    # Safety cap
    max_mins = SAFETY["max_zone_duration_minutes"]
    if minutes > max_mins:
        minutes = max_mins
        note = f" (capped at {max_mins} min safety limit)"
    else:
        note = ""

    if minutes <= 0:
        return f"Zone {zone_number} has 0 minutes configured — skipped."

    # Safety check: is any other zone already on?
    active = []
    for znum, z in ZONES.items():
        if z["wired"] and znum != zone_number:
            if await ha.is_on(z["entity_id"]):
                active.append(f"Zone {znum} ({z['name']})")

    if active:
        return (
            f"Safety block: Cannot start Zone {zone_number} because {', '.join(active)} "
            "is already running. Turn it off first or use stop_all_zones."
        )

    # Turn on
    success = await ha.turn_on(zone["entity_id"])
    if not success:
        return f"Error: Failed to turn on Zone {zone_number}. Check HA connectivity."

    # Wait for duration (blocking inside async — fine for short durations)
    await asyncio.sleep(minutes * 60)

    # Turn off
    await ha.turn_off(zone["entity_id"])

    # Update HA last-run helper for dashboard display
    await ha.update_last_run(zone_number)

    # Log the event
    append_event({
        "event_type": "zone_run",
        "zone": zone_number,
        "zone_name": zone["name"],
        "duration_minutes": minutes,
        "schedule_name": None,
        "notes": note.strip() if note else None,
    })

    return (
        f"Zone {zone_number} ({zone['name']}) ran for {minutes} minute(s){note} and "
        "has been turned off."
    )


# ---------------------------------------------------------------------------
# TOOL: Stop zones
# ---------------------------------------------------------------------------

@tool
async def stop_zone(zone_number: int) -> str:
    """
    Immediately turn off a specific sprinkler zone.
    Use when asked to 'stop zone 2' or 'turn off zone 1'.
    """
    zone = ZONES.get(zone_number)
    if not zone:
        return f"Error: Zone {zone_number} does not exist."
    if not zone["wired"]:
        return f"Zone {zone_number} is not wired — nothing to stop."

    await ha.turn_off(zone["entity_id"])
    return f"Zone {zone_number} ({zone['name']}) has been turned off."


@tool
async def stop_all_zones() -> str:
    """
    Emergency stop: immediately turn off ALL sprinkler zones.
    Use when the user says 'stop everything', 'emergency stop', or 'turn off all zones'.
    """
    turned_off = []
    for zone_num, zone in ZONES.items():
        if zone["wired"]:
            await ha.turn_off(zone["entity_id"])
            turned_off.append(f"Zone {zone_num}")

    return f"All zones stopped: {', '.join(turned_off)}."


# ---------------------------------------------------------------------------
# TOOL: Run a schedule
# ---------------------------------------------------------------------------

@tool
async def run_schedule(schedule_name: str) -> str:
    """
    Run a named watering schedule — a sequence of zones, one at a time.
    All schedules are user-created. Use list_schedules to see what's available.
    Zones run sequentially with a brief pause between each.
    """
    all_schedules = get_all_schedules()
    sched = all_schedules.get(schedule_name)
    if not sched:
        available = ", ".join(all_schedules.keys())
        return f"Schedule '{schedule_name}' not found. Available: {available}"

    # Safety: nothing should be on before we start
    for zone_num, zone in ZONES.items():
        if zone["wired"] and await ha.is_on(zone["entity_id"]):
            return (
                f"Safety block: Zone {zone_num} ({zone['name']}) is already running. "
                "Stop all zones before running a schedule."
            )

    results = []
    for step in sched["zones"]:
        znum = step["zone"]
        mins = step["minutes"]
        zone = ZONES.get(znum)

        if not zone or not zone["wired"]:
            results.append(f"Zone {znum}: skipped (not wired)")
            continue
        if mins <= 0:
            results.append(f"Zone {znum}: skipped (0 minutes)")
            continue

        # Cap duration
        mins = min(mins, SAFETY["max_zone_duration_minutes"])

        success = await ha.turn_on(zone["entity_id"])
        if not success:
            results.append(f"Zone {znum} ({zone['name']}): FAILED to turn on")
            continue

        await asyncio.sleep(mins * 60)
        await ha.turn_off(zone["entity_id"])
        await ha.update_last_run(znum)
        results.append(f"Zone {znum} ({zone['name']}): ran {mins} min ✓")

        # Log each zone run within the schedule
        append_event({
            "event_type": "zone_run",
            "zone": znum,
            "zone_name": zone["name"],
            "duration_minutes": mins,
            "schedule_name": schedule_name,
            "notes": None,
        })

        # Brief pause between zones
        await asyncio.sleep(SAFETY["inter_zone_delay_seconds"])

    summary = "\n".join(f"  {r}" for r in results)
    return f"Schedule '{schedule_name}' complete:\n{summary}"


# ---------------------------------------------------------------------------
# TOOL: Weather check
# ---------------------------------------------------------------------------

@tool
async def check_weather() -> str:
    """
    Get current weather conditions and a watering recommendation for today.
    Use this before running schedules, or when the user asks about weather.
    Returns temperature, rainfall, and whether watering is recommended.
    """
    try:
        w = await get_weather_forecast()
        return (
            f"Weather Report:\n"
            f"  Condition: {w['current_condition']}\n"
            f"  Temperature: {w['current_temp_f']}°F\n"
            f"  Rain last hour: {w['rain_last_hour_mm']} mm\n"
            f"  Rain forecast (next 24h): {w['rain_next_24h_mm']} mm\n"
            f"\nRecommendation: {w['recommendation']}"
        )
    except Exception as e:
        return f"Could not fetch weather: {e}. Proceed with manual judgment."


# ---------------------------------------------------------------------------
# TOOL: Zone info
# ---------------------------------------------------------------------------

@tool
def get_zone_info(zone_number: int) -> str:
    """
    Get details about a specific zone: what it waters, plant types, wiring status,
    and default run time. Use when asked 'what is zone 3?' or 'tell me about zone 1'.
    """
    zone = ZONES.get(zone_number)
    if not zone:
        return f"Zone {zone_number} is not configured (valid range: 1-12)."

    wired_str = "Yes — wired and ready" if zone["wired"] else "No — ZEN16 not yet wired"
    new_str = "Yes (needs frequent watering)" if zone.get("new_planting") else "No"

    return (
        f"Zone {zone_number}: {zone['name']}\n"
        f"  Description: {zone['description']}\n"
        f"  HA Entity: {zone['entity_id']}\n"
        f"  Wired: {wired_str}\n"
        f"  New planting: {new_str}\n"
        f"  Default duration: {zone['default_duration_minutes']} min\n"
        f"  ZEN16 #{zone['zen16']}, Relay {zone['relay']}"
    )


# ---------------------------------------------------------------------------
# TOOL: Watering history
# ---------------------------------------------------------------------------

@tool
def get_watering_history(days: int = 7) -> str:
    """
    Get a summary of recent watering events from the local log.
    Use when asked 'when did zone 2 last run?', 'what did I water this week?',
    'did I water yesterday?', or any question about past watering activity.

    Args:
        days: how many days back to look (default 7, max 30)
    """
    days = min(days, 30)
    events = get_recent_events(days)

    if not events:
        return f"No watering events recorded in the last {days} days."

    lines = [f"Watering history — last {days} days ({len(events)} events):\n"]
    for e in events:
        time_str = format_local_time(e["timestamp_utc"])
        if e["event_type"] == "zone_run":
            sched = f" (part of '{e['schedule_name']}')" if e.get("schedule_name") else ""
            lines.append(
                f"  {time_str}: Zone {e['zone']} ({e['zone_name']}) — "
                f"{e['duration_minutes']} min{sched}"
            )
        elif e["event_type"] == "zone_skipped":
            lines.append(f"  {time_str}: Zone {e.get('zone', '?')} skipped — {e.get('notes', '')}")
        elif e["event_type"] == "manual_stop":
            lines.append(f"  {time_str}: Manual stop — {e.get('notes', '')}")

    return "\n".join(lines)


@tool
def get_last_zone_run(zone_number: int) -> str:
    """
    Find out when a specific zone last ran and for how long.
    Use when asked 'when did zone 1 last run?' or 'has zone 3 run today?'

    Args:
        zone_number: integer 1-12
    """
    event = get_last_run_for_zone(zone_number)
    zone = ZONES.get(zone_number)
    zone_name = zone["name"] if zone else f"Zone {zone_number}"

    if not event:
        return f"No watering history found for Zone {zone_number} ({zone_name})."

    time_str = format_local_time(event["timestamp_utc"])
    sched = f" (part of '{event['schedule_name']}')" if event.get("schedule_name") else ""
    return (
        f"Zone {zone_number} ({zone_name}) last ran {time_str} "
        f"for {event['duration_minutes']} minutes{sched}."
    )


# ---------------------------------------------------------------------------
# TOOL: Evaluate schedules against weather
# ---------------------------------------------------------------------------

@tool
async def evaluate_schedules() -> str:
    """
    Pull together everything needed to evaluate whether current watering schedules
    should be adjusted: current weather conditions, forecasted rain, temperature,
    and the full details of all active schedules with zone plant types.

    Use this when the user asks:
    - "Should I adjust my schedules given recent weather?"
    - "It's been hot — should I water more?"
    - "Evaluate my watering schedules"
    - "Are my schedules right for this time of year?"

    After calling this tool, reason about what changes (if any) are appropriate
    based on the weather data, plant types, season, and Central Texas climate knowledge.
    Then PROPOSE the changes to the user in plain language and wait for their approval
    before saving anything with create_schedule.
    """
    from datetime import date

    # --- Weather ---
    try:
        w = await get_weather_forecast()
        weather_section = (
            f"Current weather in Austin TX:\n"
            f"  Condition: {w['current_condition']}\n"
            f"  Temperature: {w['current_temp_f']}F\n"
            f"  Rain last hour: {w['rain_last_hour_mm']} mm\n"
            f"  Rain forecast next 24h: {w['rain_next_24h_mm']} mm\n"
            f"  Watering recommendation: {w['recommendation']}"
        )
    except Exception as e:
        weather_section = f"Weather unavailable: {e}"

    # --- Current date / season context ---
    month = date.today().month
    if month in (12, 1, 2):
        season = "Winter"
    elif month in (3, 4, 5):
        season = "Spring"
    elif month in (6, 7, 8, 9):
        season = "Summer (extreme heat season)"
    else:
        season = "Fall"
    season_section = f"Current season: {season} (month {month})"

    # --- All schedules with zone details ---
    all_schedules = get_all_schedules()
    sched_lines = ["Current schedules:"]
    for sname, sched in all_schedules.items():
        tag = "(built-in)" if sname in BUILTIN_NAMES else "(custom)"
        sched_lines.append(f"\n  Schedule: {sname} {tag}")
        sched_lines.append(f"  Description: {sched.get('description', 'none')}")
        for step in sched["zones"]:
            znum = step["zone"]
            mins = step["minutes"]
            zone = ZONES.get(znum, {})
            plant_type = zone.get("plant_type", "unknown")
            new = " [NEW PLANTING]" if zone.get("new_planting") else ""
            sched_lines.append(
                f"    Zone {znum} ({zone.get('name', '?')}) — {mins} min — {plant_type}{new}"
            )

    schedules_section = "\n".join(sched_lines)

    return "\n\n".join([season_section, weather_section, schedules_section])


# ---------------------------------------------------------------------------
# TOOL: Schedule management
# ---------------------------------------------------------------------------

@tool
async def create_schedule(name: str, description: str, zones_config: str) -> str:
    """
    Create a new named watering schedule and save it permanently.
    Use when the user asks to 'create a schedule', 'save a schedule', or 'make a new watering plan'.

    Args:
        name: short identifier for the schedule, no spaces (e.g. 'evening_sod', 'weekend_full').
              Use lowercase with underscores.
        description: one sentence describing when/why to use this schedule.
        zones_config: JSON array of zone/duration pairs. Format:
                      '[{"zone": 2, "minutes": 8}, {"zone": 3, "minutes": 10}]'
                      Zones run in the order listed. Only include wired zones (1-3 currently).

    Example user request: "Create a schedule called evening_sod that runs zone 2 for 8 min and zone 3 for 8 min"
    Example zones_config: '[{"zone": 2, "minutes": 8}, {"zone": 3, "minutes": 8}]'
    """
    import json

    # Validate name
    name = name.strip().replace(" ", "_").lower()
    if not name:
        return "Error: schedule name cannot be empty."

    # Parse zones
    try:
        zones = json.loads(zones_config)
    except json.JSONDecodeError:
        return (
            "Error: zones_config must be valid JSON. Example: "
            '[{"zone": 2, "minutes": 8}, {"zone": 3, "minutes": 10}]'
        )

    if not isinstance(zones, list) or not zones:
        return "Error: zones_config must be a non-empty JSON array."

    # Validate each zone entry
    max_mins = SAFETY["max_zone_duration_minutes"]
    cleaned = []
    for step in zones:
        if not isinstance(step, dict) or "zone" not in step or "minutes" not in step:
            return f"Error: each zone entry must have 'zone' and 'minutes' keys. Got: {step}"
        znum = int(step["zone"])
        mins = int(step["minutes"])
        if znum not in ZONES:
            return f"Error: Zone {znum} is not configured."
        if mins <= 0:
            return f"Error: minutes must be > 0 for zone {znum}."
        mins = min(mins, max_mins)
        cleaned.append({"zone": znum, "minutes": mins})

    save_schedule(name, description, cleaned)
    await _sync_schedules_to_ha()

    zone_summary = ", ".join(f"Zone {s['zone']} ({s['minutes']} min)" for s in cleaned)
    return (
        f"Schedule '{name}' saved.\n"
        f"  Description: {description}\n"
        f"  Zones: {zone_summary}\n"
        f"Run it anytime by saying 'run schedule {name}'."
    )


@tool
def list_schedules() -> str:
    """
    List all available watering schedules — both built-in presets and user-created custom ones.
    Use when asked 'what schedules do I have?', 'list my schedules', or 'what presets exist?'
    """
    all_schedules = get_all_schedules()
    if not all_schedules:
        return "No schedules configured."

    lines = ["Available schedules:\n"]
    for sname, sched in all_schedules.items():
        tag = "(built-in)" if sname in BUILTIN_NAMES else "(custom)"
        desc = sched.get("description", "No description.")
        zone_parts = [f"Zone {s['zone']} {s['minutes']}min" for s in sched["zones"]]
        lines.append(f"  {sname} {tag}")
        lines.append(f"    {desc}")
        lines.append(f"    Zones: {', '.join(zone_parts)}")
    return "\n".join(lines)


@tool
async def delete_schedule(schedule_name: str) -> str:
    """
    Delete a custom (user-created) watering schedule permanently.
    Built-in schedules (morning_new_sod, midday_new_sod, full_front) cannot be deleted.
    Use when asked to 'delete schedule', 'remove schedule', or 'get rid of [schedule name]'.

    Args:
        schedule_name: the exact name of the schedule to delete
    """
    try:
        deleted = remove_schedule(schedule_name)
    except ValueError as e:
        return f"Cannot delete: {e}"

    if deleted:
        await _sync_schedules_to_ha()
        return f"Schedule '{schedule_name}' has been deleted."
    else:
        all_schedules = get_all_schedules()
        available = ", ".join(all_schedules.keys())
        return f"Schedule '{schedule_name}' not found. Available: {available}"


# ---------------------------------------------------------------------------
# All tools for the agent
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    get_zone_status,
    get_all_zones_status,
    run_zone,
    stop_zone,
    stop_all_zones,
    run_schedule,
    check_weather,
    get_zone_info,
    get_watering_history,
    get_last_zone_run,
    evaluate_schedules,
    create_schedule,
    list_schedules,
    delete_schedule,
]
