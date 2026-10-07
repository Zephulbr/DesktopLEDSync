import requests
import asyncio
import colorsys
from . import LightProvider

class WLEDProvider(LightProvider):
    """
    Provider for WLED smart lights.
    WLED uses a completely open, unauthenticated REST API.
    """
    supports_transitions = True

    def __init__(self, config, log=None):
        super().__init__(config, log)
        self.api_url = f"http://{self.ip_address}/json/state"
        self.info_url = f"http://{self.ip_address}/json/info"

    async def connect(self):
        """Verify the WLED device is reachable by fetching its info endpoint."""
        try:
            response = await asyncio.to_thread(requests.get, self.info_url, timeout=3)
            if response.status_code == 200:
                info = response.json()
                name = info.get("name", "WLED Device")
                version = info.get("ver", "unknown")
                self._log("ok", f"Connected to WLED: '{name}' (v{version}) at {self.ip_address}")
            else:
                raise ConnectionError(f"Unexpected status code: {response.status_code}")
        except Exception as e:
            raise ConnectionError(f"Could not reach WLED device at {self.ip_address}: {e}")

    async def set_color(self, rgb_tuple, match_brightness=False, transition=0.0):
        """Send the JSON payload to change the light color."""
        r, g, b = rgb_tuple

        # "tt" is a fade for this request only, in deciseconds (WLED's own default is left alone)
        payload = {"on": True, "tt": round(transition * 10)}

        if match_brightness and (r or g or b):
            # Send the hue at full value and let WLED's master brightness carry the album art's value,
            # with the same 10% floor the Tapo provider uses
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(h, s, 1.0))
            payload["bri"] = max(26, round(v * 255))
        # Otherwise leave "bri" out so the brightness set in WLED itself is kept

        payload["seg"] = [{"id": 0, "col": [[r, g, b]]}]
        await self._post_state(payload)

    async def turn_off(self):
        await self._post_state({"on": False})
        self._log("info", "[WLED] Lights turned off (idle).")

    async def _post_state(self, payload):
        try:
            response = await asyncio.to_thread(
                requests.post, self.api_url, json=payload, timeout=2
            )
        except Exception as e:
            raise ConnectionError(f"[WLED] Failed to reach {self.ip_address}: {e}") from e
        if response.status_code != 200:
            raise ConnectionError(f"[WLED] Error setting state (HTTP {response.status_code})")
