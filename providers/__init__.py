class LightProvider:
    """Base class that all specific LED light providers must inherit from."""

    # True if set_color can fade by itself. Otherwise the engine fades by sending in-between colors.
    supports_transitions = False

    def __init__(self, config, log=None):
        self.config = config
        self.ip_address = config.get("ip_address")
        self.credentials = config.get("credentials", {})
        self.settings = config.get("settings", {})
        self._log_fn = log

        if not self.ip_address or self.ip_address == "YOUR_LED_STRIP_IP":
            raise ValueError("Invalid IP address in config.json")

    def _log(self, level, message):
        """Forward log messages to the engine's logger, or print if there is none."""
        if self._log_fn is not None:
            self._log_fn(level, message)
        else:
            print(message)

    async def connect(self):
        """Handle any necessary local authentication or handshakes (e.g., Tapo login)."""
        pass

    async def set_color(self, rgb_tuple, match_brightness=False, transition=0.0):
        """
        Send the command to change the light color. Raise an exception on failure.
        :param rgb_tuple: A tuple of (Red, Green, Blue) from 0-255.
        :param match_brightness: Scale the light's brightness to the color's value
                                 instead of using full brightness.
        :param transition: Seconds to fade over. Only used when supports_transitions is True.
        """
        raise NotImplementedError("Each provider must implement the set_color method.")

    async def turn_off(self):
        """Power the lights off. Raise an exception on failure."""
        raise NotImplementedError("Each provider must implement the turn_off method.")

    async def close(self):
        """Release any open connections."""
        pass
