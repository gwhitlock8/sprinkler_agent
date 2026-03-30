"""
Zone configuration and agent settings.

IMPORTANT: Verify entity IDs in Home Assistant before running.
Go to HA → Settings → Devices & Services → Entities and search "sprinkler" or "zen16".
Update the entity_id values below to match what you see.
"""

# ---------------------------------------------------------------------------
# ZONE DEFINITIONS
# ---------------------------------------------------------------------------
# Each zone maps to a ZEN16 relay switch in Home Assistant.
#
# ZEN16 #1 (wired):
#   Relay 1 → Zone 2 (front lawn right)
#   Relay 2 → Zone 1 (front beds/trees)
#   Relay 3 → Zone 3 (front lawn left)
#
# ZEN16 #2 and #3: not yet wired (zones 4-12 marked wired=False)

ZONES: dict[int, dict] = {
    1: {
        "name": "Front Beds & Trees",
        "description": (
            "Front yard flower beds and bubblers. Monterrey Oak (2), Crape Myrtle, "
            "Texas Sage (5), Ligustrum (5), Carolina Cherries (5), Pride of Barbados. "
            "All installed ~3 weeks ago — needs consistent moisture."
        ),
        "entity_id": "switch.sprinkler_zone_1",   # ZEN16 #1, Relay 2  ← VERIFY IN HA
        "wired": True,
        "new_planting": True,
        # New shrubs/trees: water daily, shorter cycles to avoid root rot
        "default_duration_minutes": 8,
        "plant_type": "trees_shrubs",
        "zen16": 1, "relay": 2,
    },
    2: {
        "name": "Front Lawn Right",
        "description": (
            "Front yard right side lawn. ~10 sprayers + 1 needing repair. "
            "Zoysia Palisades sod installed ~3 weeks ago. Some overspray onto walkway."
        ),
        "entity_id": "switch.sprinkler_zone_2",   # ZEN16 #1, Relay 1  ← VERIFY IN HA
        "wired": True,
        "new_planting": True,
        # New sod: water 2-3× daily short cycles while establishing
        "default_duration_minutes": 12,
        "plant_type": "new_sod",
        "zen16": 1, "relay": 1,
    },
    3: {
        "name": "Front Lawn Left",
        "description": (
            "Front yard left side lawn. 9 sprayers. "
            "Zoysia Palisades sod installed ~3 weeks ago."
        ),
        "entity_id": "switch.sprinkler_zone_3",   # ZEN16 #1, Relay 3  ← VERIFY IN HA
        "wired": True,
        "new_planting": True,
        "default_duration_minutes": 12,
        "plant_type": "new_sod",
        "zen16": 1, "relay": 3,
    },
    4: {
        "name": "Backyard Left Front",
        "description": "Backyard left front area. 3 sprayers. Mainly weeds and live oaks.",
        "entity_id": "switch.sprinkler_zone_4",   # ZEN16 #2, Relay 1  ← NOT YET WIRED
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 2, "relay": 1,
    },
    5: {
        "name": "Backyard Far Left Front",
        "description": "Backyard far left front. 3 long rotating sprayers. Live oaks and weeds.",
        "entity_id": "switch.sprinkler_zone_5",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 2, "relay": 2,
    },
    6: {
        "name": "Backyard Far Left Back",
        "description": "Backyard far left back. 3 long rotating sprayers. Live oaks and weeds.",
        "entity_id": "switch.sprinkler_zone_6",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 2, "relay": 3,
    },
    7: {
        "name": "Backyard Left Flowerbeds",
        "description": "Backyard left flowerbeds along retaining wall. Live oaks and weeds.",
        "entity_id": "switch.sprinkler_zone_7",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 8,
        "plant_type": "flowerbeds",
        "zen16": 3, "relay": 1,
    },
    8: {
        "name": "Backyard Left Back Fence",
        "description": "Backyard left back fence and along retaining wall.",
        "entity_id": "switch.sprinkler_zone_8",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 3, "relay": 2,
    },
    9: {
        "name": "Right Walkway (Eliminate)",
        "description": "Right side and upper walkway. Candidate for elimination.",
        "entity_id": "switch.sprinkler_zone_9",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 0,   # Disabled — planned for elimination
        "plant_type": "lawn",
        "zen16": 3, "relay": 3,
    },
    10: {
        "name": "Right Side Back",
        "description": "Right side yard back. 5 long rotating sprayers. Mainly weeds and live oaks.",
        "entity_id": "switch.sprinkler_zone_10",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 3, "relay": 4,   # ZEN16 only has 3 relays — may need 4th unit
    },
    11: {
        "name": "Right Side Middle",
        "description": "Right side yard middle. 5 long rotating sprayers. Mainly weeds and live oaks.",
        "entity_id": "switch.sprinkler_zone_11",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 3, "relay": 5,
    },
    12: {
        "name": "Right Side Front",
        "description": "Right side yard front. 9 sprayers. Mainly weeds and live oaks.",
        "entity_id": "switch.sprinkler_zone_12",
        "wired": False,
        "new_planting": False,
        "default_duration_minutes": 10,
        "plant_type": "lawn",
        "zen16": 3, "relay": 6,
    },
}

# ---------------------------------------------------------------------------
# WATERING SCHEDULE PRESETS
# ---------------------------------------------------------------------------
# These run zones sequentially (one at a time — safety requirement).
# Durations are in minutes and override zone defaults when specified.

SCHEDULES: dict[str, dict] = {
    # Built-in schedules removed — all schedules are created and managed
    # dynamically via the agent (stored in custom_schedules.json).
}

# ---------------------------------------------------------------------------
# WEATHER THRESHOLDS
# ---------------------------------------------------------------------------
WEATHER = {
    # Skip watering if this much rain (mm) is expected in next 24h
    "skip_if_rain_mm": 6.0,
    # Reduce duration by 30% if temp below this °F (cool/overcast)
    "reduce_if_temp_below_f": 65,
    # Increase duration by 20% if temp above this °F (hot day)
    "increase_if_temp_above_f": 95,
}

# ---------------------------------------------------------------------------
# SAFETY
# ---------------------------------------------------------------------------
SAFETY = {
    # Hard maximum any single zone will run (HA automation enforces this too)
    "max_zone_duration_minutes": 30,
    # Delay in seconds between zones in a schedule (let pressure equalize)
    "inter_zone_delay_seconds": 5,
}
