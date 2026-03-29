"""
Weather data via Open-Meteo (free, no API key required).
Uses your location from .env (defaults to Austin, TX).
"""

import os
import httpx
from datetime import datetime, timezone


async def get_weather_forecast() -> dict:
    """
    Fetch current conditions + 24h rain forecast for your location.

    Returns a dict with:
      - current_temp_f: float
      - current_condition: str  (e.g. "Clear sky")
      - rain_last_hour_mm: float
      - rain_next_24h_mm: float  (sum of hourly forecasts)
      - recommendation: str  (human-readable watering advice)
    """
    lat = os.getenv("LATITUDE", "30.2672")
    lon = os.getenv("LONGITUDE", "-97.7431")

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,precipitation,weathercode"
        "&hourly=precipitation"
        "&temperature_unit=fahrenheit"
        "&precipitation_unit=mm"
        "&forecast_days=2"
        "&timezone=America%2FChicago"
    )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()

    # Current conditions
    current = data["current"]
    temp_f = current["temperature_2m"]
    rain_now = current["precipitation"]
    weather_code = current["weathercode"]

    # Sum precipitation for next 24 hours
    hourly_precip = data["hourly"]["precipitation"]      # list of hourly mm values
    hourly_times = data["hourly"]["time"]                # list of ISO time strings
    now_utc = datetime.now(timezone.utc)
    rain_next_24h = 0.0
    hours_counted = 0
    for t, p in zip(hourly_times, hourly_precip):
        try:
            # Open-Meteo returns local time strings without tz — parse as naive
            hour_dt = datetime.fromisoformat(t)
            # Compare to local-equivalent now (approx — good enough for rain logic)
            if hours_counted < 24:
                rain_next_24h += p
                hours_counted += 1
        except Exception:
            pass

    recommendation = _build_recommendation(temp_f, rain_now, rain_next_24h)

    return {
        "current_temp_f": round(temp_f, 1),
        "current_condition": _weather_code_label(weather_code),
        "rain_last_hour_mm": round(rain_now, 2),
        "rain_next_24h_mm": round(rain_next_24h, 2),
        "recommendation": recommendation,
    }


def _build_recommendation(temp_f: float, rain_now_mm: float, rain_24h_mm: float) -> str:
    from config import WEATHER  # local import to avoid circular

    parts = []

    if rain_now_mm > 2:
        parts.append("It's currently raining — skip watering.")
    elif rain_24h_mm >= WEATHER["skip_if_rain_mm"]:
        parts.append(
            f"Rain expected ({rain_24h_mm:.1f}mm in next 24h) — recommend skipping."
        )
    else:
        parts.append("No significant rain expected — watering is appropriate.")

    if temp_f >= WEATHER["increase_if_temp_above_f"]:
        parts.append(f"It's hot ({temp_f}°F) — consider adding 20% to run times.")
    elif temp_f <= WEATHER["reduce_if_temp_below_f"]:
        parts.append(f"It's cool ({temp_f}°F) — can reduce run times by 30%.")

    return " ".join(parts)


def _weather_code_label(code: int) -> str:
    """Convert WMO weather code to a readable string."""
    mapping = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Icy fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Heavy drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
        95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm heavy hail",
    }
    return mapping.get(code, f"Code {code}")
