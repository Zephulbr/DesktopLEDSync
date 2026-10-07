import asyncio
import colorsys
from plugp100.common.credentials import AuthCredential
# plugp100 5.2 renamed the 'plugp100.new' package to 'plugp100.devices'
try:
    from plugp100.devices.device_factory import connect, DeviceConnectConfiguration
    from plugp100.devices.components.light_component import LightComponent
except ImportError:
    from plugp100.new.device_factory import connect, DeviceConnectConfiguration
    from plugp100.new.components.light_component import LightComponent

from config_store import resolve_password
from . import LightProvider

CONNECT_TIMEOUT_SECONDS = 15


def _raise_on_failure(result):
    """plugp100 reports many errors as a returned Failure instead of raising, so surface them."""
    get_or_raise = getattr(result, "get_or_raise", None)
    if callable(get_or_raise):
        get_or_raise()


class TapoProvider(LightProvider):
    """
    Provider for TP-Link Tapo smart lights.
    Requires 'plugp100' library version 5.
    """
    def __init__(self, config, log=None):
        super().__init__(config, log)
        creds = config.get("credentials") or {}
        self.username = creds.get("username")

        try:
            self.password = resolve_password(creds)
        except Exception as e:
            self.password = None
            self._log("error", f"[Tapo] Failed to load password from keyring: {e}")

        if not self.username or not self.password:
            raise ValueError("Tapo provider requires an account email and password.")

        self.device = None
        self.light_component = None

    async def connect(self):
        """Asynchronously authenticate and connect to the Tapo light."""
        await self.close()
        self._log("info", f"[Tapo] Connecting to {self.ip_address}...")
        credentials = AuthCredential(self.username, self.password)
        dev_config = DeviceConnectConfiguration(self.ip_address, credentials=credentials)
        self.device = await asyncio.wait_for(connect(dev_config), CONNECT_TIMEOUT_SECONDS)

        # Some devices like the L920 might not be auto-detected by V5's factory,
        # so we inject the authenticated client directly into a new LightComponent.
        self.light_component = LightComponent(self.device.client)
        self._log("ok", f"[Tapo] Connected to device at {self.ip_address}")

    async def close(self):
        client = getattr(self.device, "client", None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                pass
        self.device = None
        self.light_component = None

    async def _run_with_reconnect(self, action):
        """Run a device action, reconnecting once if the Tapo session has expired or dropped."""
        if self.light_component is None:
            await self.connect()
        try:
            await action()
        except Exception as e:
            self._log("info", f"[Tapo] Command failed ({e}), reconnecting...")
            await self.connect()
            await action()

    async def set_color(self, rgb_tuple, match_brightness=False):
        """Asynchronously send the color command."""
        r, g, b = rgb_tuple

        # Special case: (0, 0, 0) means "turn off"
        if r == 0 and g == 0 and b == 0:
            await self.turn_off()
            return

        # Convert RGB to HSV. Tapo uses hue + saturation for color control.
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

        hue = int(h * 360)
        saturation = int(s * 100)

        if match_brightness:
            brightness = max(10, int(v * 100))  # Derived from album art value
        else:
            brightness = 100  # Always full brightness

        async def apply():
            _raise_on_failure(await self.light_component.turn_on())  # Wake up if previously turned off
            _raise_on_failure(await self.light_component.set_hue_saturation(hue, saturation))
            _raise_on_failure(await self.light_component.set_brightness(brightness))

        await self._run_with_reconnect(apply)
        self._log("info", f"[Tapo] Set HSV({hue}°, {saturation}%, {brightness}%)")

    async def turn_off(self):
        async def apply():
            _raise_on_failure(await self.light_component.turn_off())

        await self._run_with_reconnect(apply)
        self._log("info", "[Tapo] Lights turned off (idle).")
