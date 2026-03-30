"""
LangChain tools that the agent can call.

Each tool is a Python async function wrapped with @tool.
The docstrings are what the LLM reads to decide when to use each tool — keep them clear.
"""

import asyncio
import json
from datetime import date
from langchain_core.tools import tool

from ha_client import ha
from weather import get_weather_forecast
from database import (
    get_zone, get_all_zones, get_wired_zones, update_zone,
    get_schedule, get_all_schedules_db, save_schedule_db, delete_schedule_db,
    log_watering_event, get_recent_events, get_last_run_for_zone,
    get_setting_int, get_setting_float, format_local_time,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zone_label(zone_num):
    """Short label for error messages."""
    z = get_zone(zone_num)
    if not z:
        return "Zone " + str(zone_num) + " (not configured)"
    return "Zone " + str(zone_num) + " (" + z["name"] + ")"


async def _capture_weather():
    """Fetch current weather for logging alongside watering events. Fails silently."""
    try:
        w = await get_weather_forecast()
        return {
            "weather_temp_f": w.get("current_temp_f"),
            "weather_condition": w.get("current_condition"),
            "weather_rain_mm": w.get("rain_last_hour_mm"),
        }
    except Exception:
        return {"weather_temp_f": None, "weather_condition": None, "weather_rain_mm": None}


async def _sync_schedules_to_ha():
    """Push current schedule list to the HA dashboard display helper."""
    all_scheds = get_all_schedules_db()
    parts = []
    for name, sched in all_scheds.items():
        zone_parts = []
        for s in sched["zones"]:
            zone_parts.append("Z" + str(s["zone"]) + ":" + str(s["minutes"]) + "m")
        parts.append(name + " (" + ", ".join(zone_parts) + ")")
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
    zone = get_zone(zone_number)
    if not zone:
        return "Zone " + str(zone_number) + " is not configured."
    if not zone["wired"]:
        return (
            "Zone " + str(zone_number) + " (" + zone["name"] + ") is not yet wired "
            "to a ZEN16 relay. It cannot be controlled until wired."
        )

    state = await ha.is_on(zone["entity_id"])
    status = "ON (running)" if state else "OFF"
    return "Zone " + str(zone_number) + " (" + zone["name"] + "): " + status


@tool
async def get_all_zones_status() -> str:
    """
    Get the current ON/OFF status of all configured sprinkler zones.
    Use this when the user asks 'What's running?' or 'Show me all zones'.
    """
    lines = []
    for zone in get_all_zones():
        zn = zone["zone_number"]
        if not zone["wired"]:
            lines.append("  Zone " + str(zn) + " (" + zone["name"] + "): NOT WIRED")
            continue
        state = await ha.is_on(zone["entity_id"])
        status = "ON" if state else "off"
        lines.append("  Zone " + str(zn) + " (" + zone["name"] + "): " + status)
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
    Will cap duration at the max_zone_duration_minutes setting (default 30).

    Args:
        zone_number: integer 1-12
        minutes: how long to run (1-30)
    """
    zone = get_zone(zone_number)
    if not zone:
        return "Error: Zone " + str(zone_number) + " does not exist."
    if not zone["wired"]:
        return (
            "Zone " + str(zone_number) + " (" + zone["name"] + ") is not yet wired. "
            "Cannot activate unwired zones."
        )

    # Safety cap
    max_mins = get_setting_int("max_zone_duration_minutes", 30)
    note = ""
    if minutes > max_mins:
        minutes = max_mins
        note = " (capped at " + str(max_mins) + " min safety limit)"
    if minutes <= 0:
        return "Zone " + str(zone_number) + " has 0 minutes configured — skipped."

    # Safety check: is any other wired zone already on?
    active = []
    for z in get_wired_zones():
        if z["zone_number"] != zone_number:
            if await ha.is_on(z["entity_id"]):
                active.append("Zone " + str(z["zone_number"]) + " (" + z["name"] + ")")
    if active:
        return (
            "Safety block: Cannot start Zone " + str(zone_number) + " because "
            + ", ".join(active) + " is already running. Turn it off first or use stop_all_zones."
        )

    # Turn on
    success = await ha.turn_on(zone["entity_id"])
    if not success:
        return "Error: Failed to turn on Zone " + str(zone_number) + ". Check HA connectivity."

    await asyncio.sleep(minutes * 60)

    # Turn off
    await ha.turn_off(zone["entity_id"])
    await ha.update_last_run(zone_number)

    # Capture weather and log
    wx = await _capture_weather()
    log_watering_event(
        event_type="zone_run",
        zone_number=zone_number,
        zone_name=zone["name"],
        duration_minutes=minutes,
        notes=note.strip() if note else None,
        **wx,
    )

    return (
        "Zone " + str(zone_number) + " (" + zone["name"] + ") ran for "
        + str(minutes) + " minute(s)" + note + " and has been turned off."
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
    zone = get_zone(zone_number)
    if not zone:
        return "Error: Zone " + str(zone_number) + " does not exist."
    if not zone["wired"]:
        return "Zone " + str(zone_number) + " is not wired — nothing to stop."

    await ha.turn_off(zone["entity_id"])
    return "Zone " + str(zone_number) + " (" + zone["name"] + ") has been turned off."


@tool
async def stop_all_zones() -> str:
    """
    Emergency stop: immediately turn off ALL sprinkler zones.
    Use when the user says 'stop everything', 'emergency stop', or 'turn off all zones'.
    """
    turned_off = []
    for z in get_wired_zones():
        await ha.turn_off(z["entity_id"])
        turned_off.append("Zone " + str(z["zone_number"]))
    return "All zones stopped: " + ", ".join(turned_off) + "."


# ---------------------------------------------------------------------------
# TOOL: Run a schedule
# ---------------------------------------------------------------------------

@tool
async def run_schedule(schedule_name: str) -> str:
    """
    Run a named watering schedule — a sequence of zones, one at a time.
    Use list_schedules to see what's available.
    Zones run sequentially with a brief pause between each.
    """
    sched = get_schedule(schedule_name)
    if not sched:
        all_s = get_all_schedules_db()
        available = ", ".join(all_s.keys()) if all_s else "none"
        return "Schedule '" + schedule_name + "' not found. Available: " + available

    # Safety: nothing should be on
    for z in get_wired_zones():
        if await ha.is_on(z["entity_id"]):
            return (
                "Safety block: Zone " + str(z["zone_number"]) + " (" + z["name"]
                + ") is already running. Stop all zones before running a schedule."
            )

    max_mins = get_setting_int("max_zone_duration_minutes", 30)
    delay = get_setting_int("inter_zone_delay_seconds", 5)
    results = []

    for step in sched["zones"]:
        znum = step["zone"]
        mins = step["minutes"]
        zone = get_zone(znum)

        if not zone or not zone["wired"]:
            results.append("Zone " + str(znum) + ": skipped (not wired)")
            continue
        if mins <= 0:
            results.append("Zone " + str(znum) + ": skipped (0 minutes)")
            continue

        mins = min(mins, max_mins)

        success = await ha.turn_on(zone["entity_id"])
        if not success:
            results.append("Zone " + str(znum) + " (" + zone["name"] + "): FAILED to turn on")
            continue

        await asyncio.sleep(mins * 60)
        await ha.turn_off(zone["entity_id"])
        await ha.update_last_run(znum)
        results.append("Zone " + str(znum) + " (" + zone["name"] + "): ran " + str(mins) + " min")

        wx = await _capture_weather()
        log_watering_event(
            event_type="zone_run",
            zone_number=znum,
            zone_name=zone["name"],
            duration_minutes=mins,
            schedule_name=schedule_name,
            **wx,
        )

        await asyncio.sleep(delay)

    summary = "\n".join("  " + r for r in results)
    return "Schedule '" + schedule_name + "' complete:\n" + summary


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
            "Weather Report:\n"
            "  Condition: " + str(w["current_condition"]) + "\n"
            "  Temperature: " + str(w["current_temp_f"]) + " F\n"
            "  Rain last hour: " + str(w["rain_last_hour_mm"]) + " mm\n"
            "  Rain forecast (next 24h): " + str(w["rain_next_24h_mm"]) + " mm\n"
            "\nRecommendation: " + w["recommendation"]
        )
    except Exception as e:
        return "Could not fetch weather: " + str(e) + ". Proceed with manual judgment."


# ---------------------------------------------------------------------------
# TOOL: Zone info
# ---------------------------------------------------------------------------

@tool
def get_zone_info(zone_number: int) -> str:
    """
    Get full details about a specific zone: description, plant types, sprinkler type,
    location, wiring status, default run time, flow rate, and notes.
    Use when asked 'what is zone 3?' or 'tell me about zone 1'.
    """
    zone = get_zone(zone_number)
    if not zone:
        return "Zone " + str(zone_number) + " is not configured (valid range: 1-12)."

    wired_str = "Yes — wired and ready" if zone["wired"] else "No — ZEN16 not yet wired"
    new_str = "Yes (needs frequent watering)" if zone["new_planting"] else "No"
    flow = (str(zone["flow_rate_gpm"]) + " GPM") if zone["flow_rate_gpm"] else "Not set"
    notes = zone["notes"] if zone["notes"] else "None"

    return (
        "Zone " + str(zone_number) + ": " + zone["name"] + "\n"
        "  Description: " + (zone["description"] or "None") + "\n"
        "  Location: " + (zone["location"] or "Unknown") + "\n"
        "  Plant type: " + (zone["plant_type"] or "Unknown") + "\n"
        "  Sprinkler type: " + (zone["sprinkler_type"] or "Unknown") + "\n"
        "  HA Entity: " + zone["entity_id"] + "\n"
        "  Wired: " + wired_str + "\n"
        "  New planting: " + new_str + "\n"
        "  Default duration: " + str(zone["default_duration_minutes"]) + " min\n"
        "  Flow rate: " + flow + "\n"
        "  ZEN16 #" + str(zone["zen16_number"]) + ", Relay " + str(zone["relay_number"]) + "\n"
        "  Notes: " + notes
    )


# ---------------------------------------------------------------------------
# TOOL: Update zone info
# ---------------------------------------------------------------------------

@tool
def update_zone_info(zone_number: int, updates_json: str) -> str:
    """
    Update metadata for a specific zone. Use when the user says things like:
    - 'zone 1 is now established, remove the new planting flag'
    - 'add a note to zone 2 that the sprayer near the walkway needs repair'
    - 'zone 2 has bubblers, not sprayers'
    - 'set the flow rate for zone 1 to 3.5 GPM'

    Args:
        zone_number: integer 1-12
        updates_json: JSON object with fields to update. Allowed fields:
            name, description, wired (0 or 1), new_planting (0 or 1),
            plant_type, sprinkler_type, location, default_duration_minutes,
            flow_rate_gpm, notes

    Example: '{"new_planting": 0, "notes": "Sod is now established"}'
    """
    zone = get_zone(zone_number)
    if not zone:
        return "Error: Zone " + str(zone_number) + " does not exist."

    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return "Error: updates_json must be valid JSON."

    if not isinstance(updates, dict) or not updates:
        return "Error: updates_json must be a non-empty JSON object."

    success = update_zone(zone_number, **updates)
    if success:
        changed = ", ".join(str(k) + "=" + str(v) for k, v in updates.items())
        return "Zone " + str(zone_number) + " updated: " + changed
    else:
        return "No valid fields to update. Allowed: name, description, plant_type, sprinkler_type, location, default_duration_minutes, wired, new_planting, flow_rate_gpm, notes"


# ---------------------------------------------------------------------------
# TOOL: Watering history
# ---------------------------------------------------------------------------

@tool
def get_watering_history(days: int = 7) -> str:
    """
    Get a summary of recent watering events from the log.
    Use when asked 'when did zone 2 last run?', 'what did I water this week?',
    'did I water yesterday?', or any question about past watering activity.
    Includes weather conditions at the time of each run when available.

    Args:
        days: how many days back to look (default 7, max 30)
    """
    days = min(days, 30)
    events = get_recent_events(days)

    if not events:
        return "No watering events recorded in the last " + str(days) + " days."

    lines = ["Watering history — last " + str(days) + " days (" + str(len(events)) + " events):\n"]
    for e in events:
        time_str = format_local_time(e["timestamp_utc"])
        if e["event_type"] == "zone_run":
            sched = ""
            if e.get("schedule_name"):
                sched = " (part of '" + e["schedule_name"] + "')"
            weather = ""
            if e.get("weather_temp_f") is not None:
                weather = " [" + str(e["weather_temp_f"]) + "F"
                if e.get("weather_condition"):
                    weather += ", " + e["weather_condition"]
                weather += "]"
            lines.append(
                "  " + time_str + ": Zone " + str(e["zone_number"]) + " ("
                + (e["zone_name"] or "?") + ") — "
                + str(e["duration_minutes"]) + " min" + sched + weather
            )
        elif e["event_type"] == "zone_skipped":
            lines.append(
                "  " + time_str + ": Zone " + str(e.get("zone_number", "?"))
                + " skipped — " + (e.get("notes") or "")
            )
        elif e["event_type"] == "manual_stop":
            lines.append(
                "  " + time_str + ": Manual stop — " + (e.get("notes") or "")
            )

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
    zone = get_zone(zone_number)
    zone_name = zone["name"] if zone else "Zone " + str(zone_number)

    if not event:
        return "No watering history found for Zone " + str(zone_number) + " (" + zone_name + ")."

    time_str = format_local_time(event["timestamp_utc"])
    sched = ""
    if event.get("schedule_name"):
        sched = " (part of '" + event["schedule_name"] + "')"
    weather = ""
    if event.get("weather_temp_f") is not None:
        weather = ". Weather at that time: " + str(event["weather_temp_f"]) + "F"
        if event.get("weather_condition"):
            weather += ", " + event["weather_condition"]
    return (
        "Zone " + str(zone_number) + " (" + zone_name + ") last ran " + time_str
        + " for " + str(event["duration_minutes"]) + " minutes" + sched + weather + "."
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
    - 'Should I adjust my schedules given recent weather?'
    - 'It has been hot — should I water more?'
    - 'Evaluate my watering schedules'
    - 'Are my schedules right for this time of year?'

    After calling this tool, reason about what changes (if any) are appropriate
    based on the weather data, plant types, season, and Central Texas climate knowledge.
    Then PROPOSE the changes to the user in plain language and wait for their approval
    before saving anything with create_schedule.
    """
    # Weather
    try:
        w = await get_weather_forecast()
        weather_section = (
            "Current weather in Austin TX:\n"
            "  Condition: " + str(w["current_condition"]) + "\n"
            "  Temperature: " + str(w["current_temp_f"]) + "F\n"
            "  Rain last hour: " + str(w["rain_last_hour_mm"]) + " mm\n"
            "  Rain forecast next 24h: " + str(w["rain_next_24h_mm"]) + " mm\n"
            "  Watering recommendation: " + w["recommendation"]
        )
    except Exception as e:
        weather_section = "Weather unavailable: " + str(e)

    # Season
    month = date.today().month
    if month in (12, 1, 2):
        season = "Winter"
    elif month in (3, 4, 5):
        season = "Spring"
    elif month in (6, 7, 8, 9):
        season = "Summer (extreme heat season)"
    else:
        season = "Fall"
    season_section = "Current season: " + season + " (month " + str(month) + ")"

    # All schedules with zone details
    all_schedules = get_all_schedules_db()
    sched_lines = ["Current schedules:"]
    for sname, sched in all_schedules.items():
        sched_lines.append("\n  Schedule: " + sname)
        sched_lines.append("  Description: " + (sched.get("description") or "none"))
        for step in sched["zones"]:
            znum = step["zone"]
            mins = step["minutes"]
            zone = get_zone(znum)
            if zone:
                plant = zone.get("plant_type") or "unknown"
                sprinkler = zone.get("sprinkler_type") or "unknown"
                new = " [NEW PLANTING]" if zone.get("new_planting") else ""
                sched_lines.append(
                    "    Zone " + str(znum) + " (" + zone["name"] + ") — "
                    + str(mins) + " min — " + plant + " / " + sprinkler + new
                )
            else:
                sched_lines.append(
                    "    Zone " + str(znum) + " — " + str(mins) + " min"
                )

    if not all_schedules:
        sched_lines.append("  No schedules configured.")

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
                      Zones run in the order listed.

    Example user request: "Create a schedule called evening_sod that runs zone 2 for 8 min and zone 3 for 8 min"
    Example zones_config: '[{"zone": 2, "minutes": 8}, {"zone": 3, "minutes": 8}]'
    """
    name = name.strip().replace(" ", "_").lower()
    if not name:
        return "Error: schedule name cannot be empty."

    try:
        zones = json.loads(zones_config)
    except json.JSONDecodeError:
        return (
            "Error: zones_config must be valid JSON. Example: "
            '[{"zone": 2, "minutes": 8}, {"zone": 3, "minutes": 10}]'
        )

    if not isinstance(zones, list) or not zones:
        return "Error: zones_config must be a non-empty JSON array."

    max_mins = get_setting_int("max_zone_duration_minutes", 30)
    cleaned = []
    for step in zones:
        if not isinstance(step, dict) or "zone" not in step or "minutes" not in step:
            return "Error: each zone entry must have 'zone' and 'minutes' keys."
        znum = int(step["zone"])
        mins = int(step["minutes"])
        if not get_zone(znum):
            return "Error: Zone " + str(znum) + " is not configured."
        if mins <= 0:
            return "Error: minutes must be > 0 for zone " + str(znum) + "."
        mins = min(mins, max_mins)
        cleaned.append({"zone": znum, "minutes": mins})

    save_schedule_db(name, description, cleaned)
    await _sync_schedules_to_ha()

    zone_parts = []
    for s in cleaned:
        zone_parts.append("Zone " + str(s["zone"]) + " (" + str(s["minutes"]) + " min)")
    zone_summary = ", ".join(zone_parts)

    return (
        "Schedule '" + name + "' saved.\n"
        "  Description: " + description + "\n"
        "  Zones: " + zone_summary + "\n"
        "Run it anytime by saying 'run schedule " + name + "'."
    )


@tool
def list_schedules() -> str:
    """
    List all available watering schedules.
    Use when asked 'what schedules do I have?', 'list my schedules', or 'what presets exist?'
    """
    all_schedules = get_all_schedules_db()
    if not all_schedules:
        return "No schedules configured. Ask me to create one."

    lines = ["Available schedules:\n"]
    for sname, sched in all_schedules.items():
        desc = sched.get("description") or "No description."
        zone_parts = []
        for s in sched["zones"]:
            zone_parts.append("Zone " + str(s["zone"]) + " " + str(s["minutes"]) + "min")
        lines.append("  " + sname)
        lines.append("    " + desc)
        lines.append("    Zones: " + ", ".join(zone_parts))
    return "\n".join(lines)


@tool
async def delete_schedule(schedule_name: str) -> str:
    """
    Delete a watering schedule permanently.
    Use when asked to 'delete schedule', 'remove schedule', or 'get rid of [schedule name]'.

    Args:
        schedule_name: the exact name of the schedule to delete
    """
    deleted = delete_schedule_db(schedule_name)

    if deleted:
        await _sync_schedules_to_ha()
        return "Schedule '" + schedule_name + "' has been deleted."
    else:
        all_schedules = get_all_schedules_db()
        available = ", ".join(all_schedules.keys()) if all_schedules else "none"
        return "Schedule '" + schedule_name + "' not found. Available: " + available


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
    update_zone_info,
    get_watering_history,
    get_last_zone_run,
    evaluate_schedules,
    create_schedule,
    list_schedules,
    delete_schedule,
]
