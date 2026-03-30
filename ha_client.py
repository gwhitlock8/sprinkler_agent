"""
Home Assistant REST API client.

Uses the HA Long-Lived Access Token to read entity states and call services.
All calls are async (non-blocking) so the agent stays responsive.
"""

import os
import httpx
from typing import Any


class HAClient:
    """Thin async wrapper around the Home Assistant REST API."""

    def __init__(self):
        self.base_url = os.getenv("HA_URL", "http://localhost:8123").rstrip("/")
        self.token = os.getenv("HA_TOKEN", "")
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # State reading
    # ------------------------------------------------------------------

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        """
        Return the full state object for an entity.
        Returns {"state": "unavailable"} if the entity doesn't exist.
        """
        url = f"{self.base_url}/api/states/{entity_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers)
        if resp.status_code == 404:
            return {"entity_id": entity_id, "state": "unavailable"}
        resp.raise_for_status()
        return resp.json()

    async def is_on(self, entity_id: str) -> bool:
        """True if the switch entity is currently 'on'."""
        data = await self.get_state(entity_id)
        return data.get("state") == "on"

    async def get_all_zone_states(self, entity_ids: list[str]) -> dict[str, str]:
        """Return {entity_id: state} for a list of zone switches."""
        results = {}
        for eid in entity_ids:
            data = await self.get_state(eid)
            results[eid] = data.get("state", "unknown")
        return results

    # ------------------------------------------------------------------
    # Switch control
    # ------------------------------------------------------------------

    async def turn_on(self, entity_id: str) -> bool:
        """Turn a switch on. Returns True on success."""
        return await self._call_service("switch", "turn_on", entity_id)

    async def turn_off(self, entity_id: str) -> bool:
        """Turn a switch off. Returns True on success."""
        return await self._call_service("switch", "turn_off", entity_id)

    async def _call_service(self, domain: str, service: str, entity_id: str) -> bool:
        url = f"{self.base_url}/api/services/{domain}/{service}"
        payload = {"entity_id": entity_id}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
        return resp.status_code in (200, 201)

    # ------------------------------------------------------------------
    # Input number helpers (zone durations stored in HA)
    # ------------------------------------------------------------------

    async def set_input_number(self, helper_id: str, value: float) -> bool:
        """Set an input_number helper value (e.g., zone duration)."""
        url = f"{self.base_url}/api/services/input_number/set_value"
        payload = {"entity_id": helper_id, "value": value}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
        return resp.status_code in (200, 201)

    async def get_input_number(self, helper_id: str) -> float | None:
        """Read an input_number helper value."""
        data = await self.get_state(helper_id)
        try:
            return float(data.get("state", 0))
        except (TypeError, ValueError):
            return None

    async def update_last_run(self, zone_number: int) -> bool:
        """Record the current time as the last-run timestamp for a zone."""
        from datetime import datetime
        url = f"{self.base_url}/api/services/input_datetime/set_datetime"
        payload = {
            "entity_id": f"input_datetime.sprinkler_zone_{zone_number}_last_run",
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
        return resp.status_code in (200, 201)

    async def update_text_helper(self, helper_id: str, text: str) -> bool:
        """Set an input_text helper value (truncated to 255 chars)."""
        url = f"{self.base_url}/api/services/input_text/set_value"
        payload = {"entity_id": helper_id, "value": text[:255]}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
        return resp.status_code in (200, 201)


# Singleton — imported by tools.py
ha = HAClient()
