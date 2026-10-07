import customtkinter as ctk
import tkinter
from tkinter import colorchooser, messagebox
import tkinter.font as tkfont
import os
import threading
import sys
import queue
import ctypes
from ctypes import wintypes
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import winshell
import win32com.client
import keyring

import fluent
from fluent import C
from config_store import (
    DEFAULT_IDLE_COLOR, IDLE_BEHAVIORS, KEYRING_PLACEHOLDER, KEYRING_SERVICE,
    normalize_idle_behavior, parse_rgb, parse_transition_seconds, read_config, resolve_password, write_config,
)
from version import __version__

# Passed by the Windows startup shortcut: start syncing straight away, hidden in the tray
BACKGROUND_FLAG = "--background"
AUTOSTART_SHORTCUT_NAME = "DesktopLEDSyncGUI.lnk"
MAX_LOG_LINES = 500
TRANSITION_CHOICES = (0, 0.25, 0.5, 1, 1.5, 2, 3, 5)
MICA_CHECK_INTERVAL_MS = 3000

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) glyphs
ICON_INFO = "\uE946"
ICON_LIGHTBULB = "\uEA80"
ICON_NETWORK = "\uE968"
ICON_ACCOUNT = "\uE77B"
ICON_PAUSE = "\uE769"
ICON_COLOR = "\uE790"
ICON_BRIGHTNESS = "\uE706"
ICON_CLOSE = "\uE8BB"
ICON_POWER = "\uE7E8"
ICON_LOG = "\uE81C"
ICON_BACKDROP = "\uE771"

ctk.set_appearance_mode("System")  # Follows Windows Dark/Light mode
ctk.set_default_color_theme("blue")


def format_transition(seconds):
    return f"{seconds:g} s" if seconds > 0 else "Off"


class DesktopLEDSyncGUI(ctk.CTk):
    def __init__(self, start_in_background=False):
        super().__init__()

        self.title(f"Desktop LED Sync {__version__}")
        self.geometry("520x760")
        self.resizable(False, False)
        if start_in_background:
            self.withdraw()

        # Intercept the 'X' close button to hide the window instead
        self.protocol('WM_DELETE_WINDOW', self.hide_window)

        # Load existing config or defaults
        # (named app_config because Tk widgets already have a config() method)
        self.app_config = self.load_config()
        settings = self.app_config.setdefault("settings", {})
        saved_creds = self.app_config.get("credentials") or {}

        # Thread-safe queues: log messages from the core engine, and UI calls from other threads
        self.log_queue = queue.Queue()
        self.ui_calls = queue.Queue()
        startup_messages = []

        # Windows 11 styling: pick the colors for Mica (or a solid background), then build the widgets
        self._setup_fonts()
        self._windows = [self]  # This window and its dialogs, which share the backdrop
        self.mica_allowed = ctk.BooleanVar(value=bool(settings.get("mica_background", True)))
        self._mica_reason = fluent.mica_unavailable_reason()
        self.mica = self.mica_allowed.get() and self._mica_reason is None and fluent.enable_mica(self)
        fluent.configure(self.mica)
        fluent.apply_theme(self.font_body.cget("family"))
        self.configure(fg_color=C.window)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=(20, 8), padx=24, fill="x")
        ctk.CTkLabel(header, text="Desktop LED Sync", font=self.font_title, anchor="w").pack(fill="x")
        ctk.CTkLabel(header, text=f"Sync Windows media to your smart lights  ·  Version {__version__}",
                     font=self.font_body, text_color=C.text_secondary, anchor="w").pack(fill="x")

        # --- Activity log and footer (packed first so they stay at the bottom) ---
        self._build_log_expander(expanded=bool(settings.get("log_expanded", False)))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", pady=(8, 8), padx=24, fill="x")

        self.start_button = ctk.CTkButton(footer, width=150, height=32, font=self.font_body,
                                          command=self.toggle_sync)
        self.start_button.pack(side="right")
        self._show_start_button()

        self.status_dot = ctk.CTkLabel(footer, text="●", text_color=C.text_secondary, width=16)
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(footer, text="Not running", text_color=C.text_secondary, anchor="w",
                                        font=self.font_body)
        self.status_text.pack(side="left", padx=(6, 0), fill="x", expand=True)

        # --- Settings (Scrollable so it always fits on screen) ---
        self.settings_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.settings_frame.pack(padx=(24, 12), fill="both", expand=True)
        self.settings_frame._scrollbar.configure(width=12)  # Thin, like Windows 11 scrollbars
        page = self.settings_frame

        # Lights section
        self._add_section_header(page, "Lights", first=True)

        card = self._add_card(page, "Light brand", "The kind of lights to control", icon=ICON_LIGHTBULB,
            help_text="Select the brand of your LED strip.\nTapo: Official TP-Link Tapo lights.\nWLED: Custom ESP8266/ESP32 WLED controllers.")
        # We will parse the providers folder automatically in the future, but for now hardcode
        self.provider_var = ctk.StringVar(value=self.app_config.get("provider", "tapo"))
        self.provider_dropdown = self._add_dropdown(card, ["tapo", "wled"], self.provider_var)

        card = self._add_card(page, "IP address", "Where your lights are on the local network", wide=True,
            icon=ICON_NETWORK,
            help_text="For Tapo: Open Tapo App -> Device Settings -> Device Info -> IP Address (e.g. 192.168.1.100).\n\nFor WLED: Look in your router's connected devices, or use the WLED app.")
        self.ip_entry = self._add_entry(card, placeholder_text="e.g. 192.168.1.100")
        self._fill_entry(self.ip_entry, self.app_config.get("ip_address", ""))
        self._place_below(self.ip_entry)

        card = self._add_card(page, "Tapo account", "Needed to sign in to Tapo lights. Leave blank for WLED.", wide=True,
            icon=ICON_ACCOUNT,
            help_text="Enter the email address and password of the Tapo account your lights are registered to. "
                      "They are required for local authentication.\n\nThe password is stored in Windows Credential Manager.\n(Leave blank if using WLED)")
        self.user_entry = self._add_entry(card, placeholder_text="Account email")
        self._fill_entry(self.user_entry, saved_creds.get("username", ""))
        self._place_below(self.user_entry, row=1)

        self.pass_entry = self._add_entry(card, show="•", placeholder_text="Password")

        # The password currently stored in Credential Manager, so saves only touch it when it changes
        self._keyring_password = None
        self._keyring_read_failed = False
        saved_pwd = saved_creds.get("password", "")
        if saved_pwd == KEYRING_PLACEHOLDER:
            try:
                self._keyring_password = resolve_password(saved_creds)
            except Exception as e:
                self._keyring_read_failed = True
                startup_messages.append((f"Could not read password from Windows Credential Manager: {e}", "error"))
            saved_pwd = self._keyring_password or ""

        self._fill_entry(self.pass_entry, saved_pwd)
        self._place_below(self.pass_entry, row=2)

        # Save & Apply (network settings only take effect when the engine reconnects)
        apply_row = ctk.CTkFrame(page, fg_color="transparent")
        apply_row.pack(fill="x", pady=(4, 0))
        self.apply_btn = ctk.CTkButton(apply_row, text="Save & apply", width=120, height=32, font=self.font_body,
                                       fg_color=C.window_control, hover_color=C.window_control_hover,
                                       border_color=C.window_control_stroke, command=self.refresh_sync)
        self.apply_btn.pack(side="right")
        ctk.CTkLabel(apply_row, text="Light changes apply when you save.\nEverything else saves instantly.",
                     font=self.font_caption, text_color=C.text_secondary, justify="left", anchor="w").pack(side="left")

        # Colors section
        self._add_section_header(page, "Colors")

        card = self._add_card(page, "When music pauses", "What the lights do when nothing is playing", icon=ICON_PAUSE,
            help_text="Default Color: Switch to a specific solid color when music stops.\nTurn Off: Power off the lights entirely.\nDo Nothing: Keep the lights displaying the very last album art color.")
        # Handles migration from old snake_case values
        self.idle_var = ctk.StringVar(value=normalize_idle_behavior(settings.get("idle_behavior", "Default Color")))
        self.idle_dropdown = self._add_dropdown(card, list(IDLE_BEHAVIORS), self.idle_var,
                                                command=self.on_idle_behavior_change)

        # Idle color row (swatch + entry + color picker button), shown only for 'Default Color'
        self.idle_color_row_frame = ctk.CTkFrame(card, fg_color="transparent")
        ctk.CTkLabel(self.idle_color_row_frame, text="Idle color (R,G,B)", font=self.font_body).pack(side="left")

        self.color_pick_btn = ctk.CTkButton(self.idle_color_row_frame, text="Pick…", width=70, height=32,
                                            font=self.font_body, command=self.open_color_picker)
        self.color_pick_btn.pack(side="right")

        self.idle_color_entry = self._add_entry(self.idle_color_row_frame, placeholder_text="255,200,100", width=110)
        saved_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
        self.idle_color_entry.insert(0, f"{saved_color[0]},{saved_color[1]},{saved_color[2]}")
        self.idle_color_entry.pack(side="right", padx=(0, 6))
        self.idle_color_entry.bind("<Return>", lambda _: self.save_settings())
        self.idle_color_entry.bind("<FocusOut>", lambda _: self._save_if_idle_color_changed())
        self.idle_color_entry.bind("<KeyRelease>", lambda _: self._update_idle_swatch())

        self.idle_swatch = ctk.CTkFrame(self.idle_color_row_frame, width=32, height=32, corner_radius=4,
                                        border_width=1, border_color=C.control_stroke)
        self.idle_swatch.pack(side="right", padx=(0, 6))
        self._update_idle_swatch()
        self._update_idle_color_visibility(self.idle_var.get())

        card = self._add_card(page, "Color fade", "How long the lights take to blend into a new color", icon=ICON_COLOR,
            help_text="When the color changes, the lights fade smoothly from the old color to the new one "
                      "over this many seconds.\nChoose Off to switch colors instantly.")
        saved_transition = parse_transition_seconds(settings.get("transition_seconds"))
        self._transition_choices = {format_transition(s): s for s in sorted({*TRANSITION_CHOICES, saved_transition})}
        self.transition_var = ctk.StringVar(value=format_transition(saved_transition))
        self.transition_dropdown = self._add_dropdown(card, list(self._transition_choices), self.transition_var,
                                                      command=lambda _: self.save_settings())

        card = self._add_card(page, "Match album art brightness", "Dim the lights for darker artwork",
                              icon=ICON_BRIGHTNESS)
        self.match_brightness_var = ctk.BooleanVar(value=settings.get("match_brightness", False))
        self.match_brightness_switch = self._add_switch(card, self.match_brightness_var, self.save_settings)

        # App section
        self._add_section_header(page, "App")

        card = self._add_card(page, "When closing the window", "Keep syncing in the tray, or quit", icon=ICON_CLOSE,
            help_text="Ask what to do: Show a prompt to select minimize or exit.\nMinimize to Tray: Keep syncing in background.\nExit App: Completely quit the program.")
        self.close_bh_var = ctk.StringVar(value=settings.get("close_behavior", "Ask what to do"))
        self.close_bh_dropdown = self._add_dropdown(card, ["Ask what to do", "Minimize to Tray", "Exit App"],
                                                    self.close_bh_var, command=lambda _: self.save_settings())

        card = self._add_card(page, "Start with Windows", "Sync in the background from the moment you sign in",
                              icon=ICON_POWER)
        self.autostart_var = ctk.BooleanVar(value=self.check_if_autostart_enabled())
        self.autostart_switch = self._add_switch(card, self.autostart_var, self.on_autostart_toggle)

        if fluent.mica_supported():
            card = self._add_card(page, "Mica background", "Turn off if the window looks black", icon=ICON_BACKDROP,
                help_text="Lets your desktop wallpaper tint the window, like Windows 11's own apps.\n\n"
                          "The app already switches to a solid background when Windows can't draw Mica "
                          "(transparency effects off, battery saver, Remote Desktop, high contrast). "
                          "Turn this off if the window still looks black, which some graphics drivers "
                          "and virtual machines cause.")
            self.mica_switch = self._add_switch(card, self.mica_allowed, self.on_mica_toggle)

        ctk.CTkFrame(page, fg_color="transparent", height=8).pack()

        self.stop_event = None      # threading.Event for the current engine run
        self.engine_thread = None
        self.tray_icon = None
        self._status_is_error = False

        # Follow Windows switching between light and dark mode, and Mica becoming (un)available
        self._on_appearance_change(ctk.get_appearance_mode())
        ctk.AppearanceModeTracker.add(self._on_appearance_change, self)
        if self.mica_allowed.get() and self._mica_reason and fluent.mica_supported():
            startup_messages.append((f"Using a solid background because {self._mica_reason}.", "info"))
        self.after(MICA_CHECK_INTERVAL_MS, self._watch_mica)

        for message, tag in startup_messages:
            self.append_log(message, tag)

        self._upgrade_autostart_shortcut()
        self.after(100, self.poll_queues)

        if start_in_background:
            self.after(0, self._start_in_background)

    # --- Styling ---
    def _setup_fonts(self):
        """Use Windows 11's Segoe UI Variable where available, falling back to the Windows 10 fonts."""
        families = set(tkfont.families(self))

        def pick(*candidates):
            return next((family for family in candidates if family in families), None)

        body = pick("Segoe UI Variable Text", "Segoe UI") or ctk.ThemeManager.theme["CTkFont"]["family"]
        semibold = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
        display = pick("Segoe UI Variable Display Semibold", "Segoe UI Semibold")

        self.font_body = ctk.CTkFont(family=body, size=14)
        self.font_caption = ctk.CTkFont(family=body, size=12)
        self.font_strong = ctk.CTkFont(family=semibold or body, size=14, weight="normal" if semibold else "bold")
        self.font_title = ctk.CTkFont(family=display or body, size=28, weight="normal" if display else "bold")
        self.font_mono = ctk.CTkFont(family=pick("Cascadia Mono", "Consolas") or body, size=12)
        icon_family = pick("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.font_icon = ctk.CTkFont(family=icon_family, size=16) if icon_family else None
        self.font_icon_small = ctk.CTkFont(family=icon_family, size=12) if icon_family else None

    def apply_backdrop(self, window):
        """Give a dialog the same backdrop as the main window."""
        self._windows.append(window)
        if self.mica and fluent.enable_mica(window):
            fluent.show_mica_in_client_area(window, ctk.get_appearance_mode() == "Dark")
        window.configure(fg_color=C.window)

    def _on_appearance_change(self, mode):
        """Mica only shows through the window in dark mode (see fluent.py), so switch it with the theme."""
        dark = mode == "Dark"
        self._windows = [w for w in self._windows if w.winfo_exists()]
        for window in self._windows:
            fluent.show_mica_in_client_area(window, self.mica and dark)
        index = 1 if dark else 0
        self.log_box.tag_config("error", foreground=C.critical[index])
        self.log_box.tag_config("ok", foreground=C.success[index])
        self.log_box.tag_config("info", foreground=C.text_secondary[index])

    def _watch_mica(self):
        """Windows stops drawing Mica in some situations (see fluent.mica_unavailable_reason), so keep checking."""
        try:
            reason = fluent.mica_unavailable_reason()
            if reason != self._mica_reason:
                self._mica_reason = reason
                wanted = self.mica_allowed.get() and reason is None
                if wanted != self.mica:
                    if self.mica_allowed.get():
                        self.append_log(f"Using a solid background because {reason}." if reason
                                        else "Mica background is available again.", "info")
                    self._set_mica(wanted)
        finally:
            self.after(MICA_CHECK_INTERVAL_MS, self._watch_mica)

    def on_mica_toggle(self):
        self._set_mica(self.mica_allowed.get() and self._mica_reason is None)
        self.save_settings()

    def _set_mica(self, on):
        """Switch between Mica and a solid background while the app is running."""
        self._windows = [w for w in self._windows if w.winfo_exists()]
        if on == self.mica or (on and not all(fluent.enable_mica(w) for w in self._windows)):
            return
        self.mica = on
        dark = ctk.get_appearance_mode() == "Dark"
        if not on:
            for window in self._windows:
                fluent.show_mica_in_client_area(window, False)
        changes = fluent.configure(on)
        fluent.apply_theme(self.font_body.cget("family"))
        fluent.recolor(self._windows, changes)
        if on:
            for window in self._windows:
                fluent.show_mica_in_client_area(window, dark)
        # Colors that aren't stored on a widget option
        self._on_appearance_change(ctk.get_appearance_mode())
        self._update_idle_swatch()
        self._show_start_button(self.is_engine_running())

    def _add_section_header(self, parent, text, first=False):
        ctk.CTkLabel(parent, text=text, font=self.font_strong, anchor="w").pack(
            fill="x", padx=2, pady=(0 if first else 18, 6))

    def _add_card(self, parent, title, description="", help_text=None, wide=False, icon=None):
        """
        A Windows 11 settings card: an icon, then a title and description on the left. Put a control
        on the right with _place_trailing, or underneath (wide=True) with _place_below.
        """
        card = ctk.CTkFrame(parent, border_width=1)
        card.pack(fill="x", pady=(0, 4))
        card.grid_columnconfigure(1, weight=1)

        if icon and self.font_icon:
            ctk.CTkLabel(card, text=icon, font=self.font_icon, width=20).grid(
                row=0, column=0, padx=(16, 0), pady=(12, 8 if wide else 12))
        text = ctk.CTkFrame(card, fg_color="transparent")
        text.grid(row=0, column=1, sticky="w", padx=(16, 12), pady=(12, 8 if wide else 12))
        title_row = ctk.CTkFrame(text, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text=title, font=self.font_body, height=20).pack(side="left")
        if help_text:
            self.create_help_button(title_row, title, help_text).pack(side="left", padx=(4, 0))
        if description:
            ctk.CTkLabel(text, text=description, font=self.font_caption, text_color=C.text_secondary, height=16,
                         justify="left", anchor="w", wraplength=380 if wide else 190).pack(anchor="w")
        return card

    @staticmethod
    def _fill_entry(entry, value):
        # Inserting even an empty string hides CustomTkinter's placeholder text
        if value:
            entry.insert(0, value)

    @staticmethod
    def _place_trailing(widget):
        widget.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=12)

    @staticmethod
    def _place_below(widget, row=1):
        widget.grid(row=row, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 12))

    def _add_dropdown(self, card, values, variable, command=None):
        combo = fluent.ComboBox(card, values, variable, command=command, width=170, font=self.font_body,
                                icon_font=self.font_icon_small)
        self._place_trailing(combo)
        return combo

    def _add_switch(self, card, variable, command):
        toggle = fluent.Toggle(card, variable, command, font=self.font_body)
        self._place_trailing(toggle)
        return toggle

    def _add_entry(self, parent, **kwargs):
        """A text box that gets Windows 11's accent underline and darker fill while focused."""
        entry = ctk.CTkEntry(parent, height=32, font=self.font_body, **kwargs)
        underline = tkinter.Frame(entry, height=2, borderwidth=0, highlightthickness=0)

        def on_focus(focused):
            entry.configure(fg_color=C.input_focused if focused else C.control)
            if focused:
                underline.configure(bg=C.accent[1 if ctk.get_appearance_mode() == "Dark" else 0])
                underline.place(x=1, rely=1, y=-1, relwidth=1, width=-2, anchor="sw")
            else:
                underline.place_forget()

        entry.bind("<FocusIn>", lambda _: on_focus(True), add="+")
        entry.bind("<FocusOut>", lambda _: on_focus(False), add="+")
        return entry

    def _build_log_expander(self, expanded):
        """The activity log, as a Windows 11 expander card that shows the latest message when collapsed."""
        self.log_card = ctk.CTkFrame(self, border_width=1)
        self.log_card.pack(side="bottom", pady=(0, 20), padx=24, fill="x")
        self.log_card.grid_columnconfigure(1, weight=1)

        header = self.log_header = ctk.CTkFrame(self.log_card, fg_color="transparent", corner_radius=6)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=2, pady=2)
        header.grid_columnconfigure(1, weight=1)
        widgets = [header]
        if self.font_icon:
            widgets.append(ctk.CTkLabel(header, text=ICON_LOG, font=self.font_icon, width=20))
            widgets[-1].grid(row=0, column=0, padx=(14, 0), pady=10)
        text = ctk.CTkFrame(header, fg_color="transparent")
        text.grid(row=0, column=1, sticky="ew", padx=(16, 12), pady=10)
        title = ctk.CTkLabel(text, text="Activity log", font=self.font_body, height=20, anchor="w")
        title.pack(fill="x")
        self.log_summary = ctk.CTkLabel(text, text="No activity yet", font=self.font_caption,
                                        text_color=C.text_secondary, height=16, anchor="w")
        self.log_summary.pack(fill="x")
        self.log_chevron = ctk.CTkLabel(header, width=32, height=32, corner_radius=4,
                                        font=self.font_icon_small or self.font_body)
        self.log_chevron.grid(row=0, column=2, padx=(0, 10))
        widgets += [text, title, self.log_summary, self.log_chevron]

        def set_hover(hovering):
            header.configure(fg_color=C.subtle_hover if hovering else "transparent")
        self._log_hover = fluent.Hover(header, widgets, set_hover)
        for widget in widgets:
            widget.bind("<Button-1>", lambda _: self.toggle_log(), add="+")

        self.log_divider = ctk.CTkFrame(self.log_card, height=1, corner_radius=0, fg_color=C.card_stroke)
        self.log_box = ctk.CTkTextbox(self.log_card, height=110, state="disabled", wrap="word", font=self.font_mono)
        self.log_expanded = None
        self._show_log(expanded)

    def _show_log(self, expanded):
        self.log_expanded = expanded
        if self.font_icon_small:
            self.log_chevron.configure(text=fluent.ICON_CHEVRON_UP if expanded else fluent.ICON_CHEVRON_DOWN)
        else:
            self.log_chevron.configure(text="▴" if expanded else "▾")
        if expanded:
            self.log_summary.pack_forget()
            self.log_divider.grid(row=1, column=0, columnspan=3, sticky="ew", padx=1)
            self.log_box.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(12, 4), pady=(6, 8))
            self.log_box.see("end")
        else:
            self.log_summary.pack(fill="x")
            self.log_divider.grid_forget()
            self.log_box.grid_forget()

    def toggle_log(self):
        self._show_log(not self.log_expanded)
        # Remember the choice without the usual "Settings saved." message
        self.app_config.setdefault("settings", {})["log_expanded"] = self.log_expanded
        try:
            write_config(self.app_config)
        except Exception:
            pass

    def _show_start_button(self, running=False):
        if running:
            self.start_button.configure(text="Stop syncing", fg_color=C.window_control,
                                        hover_color=C.window_control_hover, border_color=C.window_control_stroke,
                                        text_color=C.text)
        else:
            self.start_button.configure(text="Start syncing", fg_color=C.accent, hover_color=C.accent_hover,
                                        border_color=C.accent, text_color=C.text_on_accent)

    def load_config(self):
        try:
            return read_config()
        except Exception:
            return {"provider": "tapo", "ip_address": "", "credentials": {}, "settings": {}}

    def create_help_button(self, parent, title, message):
        """Helper to create a small info button that shows a messagebox."""
        return ctk.CTkButton(
            parent, text=ICON_INFO if self.font_icon else "?", font=self.font_icon or self.font_caption,
            width=24, height=24, border_width=0, fg_color="transparent", hover_color=C.subtle_hover,
            text_color=C.text_secondary, command=lambda: messagebox.showinfo(title, message)
        )

    # --- Auto-Start ---
    def _autostart_lnk_path(self):
        return os.path.join(winshell.startup(), AUTOSTART_SHORTCUT_NAME)

    def check_if_autostart_enabled(self):
        """Check if shortcut exists in user's startup folder"""
        return os.path.exists(self._autostart_lnk_path())

    def manage_autostart(self):
        """Create or remove Windows startup shortcut based on toggle"""
        lnk_path = self._autostart_lnk_path()

        if self.autostart_var.get():
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(lnk_path)
            if getattr(sys, 'frozen', False):
                # Compiled EXE: point the shortcut straight at it
                shortcut.Targetpath = sys.executable
                shortcut.Arguments = BACKGROUND_FLAG
                shortcut.WorkingDirectory = os.path.dirname(sys.executable)
            else:
                # Running from source: use pythonw.exe so no console window appears
                script = os.path.abspath(__file__)
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                shortcut.Targetpath = pythonw if os.path.exists(pythonw) else sys.executable
                shortcut.Arguments = f'"{script}" {BACKGROUND_FLAG}'
                shortcut.WorkingDirectory = os.path.dirname(script)
            shortcut.save()
            self.append_log("Added Windows startup shortcut.", "info")
        else:
            if os.path.exists(lnk_path):
                os.remove(lnk_path)
                self.append_log("Removed Windows startup shortcut.", "info")

    def on_autostart_toggle(self):
        try:
            self.manage_autostart()
        except Exception as e:
            self.append_log(f"Could not update Windows startup shortcut: {e}", "error")
            self.autostart_var.set(self.check_if_autostart_enabled())

    def _upgrade_autostart_shortcut(self):
        """Shortcuts made by older versions don't start syncing in the background; rewrite them once."""
        if not self.autostart_var.get():
            return
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            if BACKGROUND_FLAG not in shell.CreateShortCut(self._autostart_lnk_path()).Arguments:
                self.manage_autostart()
        except Exception as e:
            self.append_log(f"Could not update Windows startup shortcut: {e}", "error")

    def _start_in_background(self):
        self._do_minimize_to_tray()
        self.start_engine()

    # --- Settings ---
    def open_color_picker(self):
        """Open the native Windows color picker and populate the entry."""
        # Build initial color tuple from whatever is currently in the entry
        current = parse_rgb(self.idle_color_entry.get()) or DEFAULT_IDLE_COLOR
        initial = "#{:02x}{:02x}{:02x}".format(*current)

        result = colorchooser.askcolor(color=initial, title="Choose Idle Light Color")
        if result and result[0]:
            r, g, b = (int(c) for c in result[0])
            self.idle_color_entry.delete(0, "end")
            self.idle_color_entry.insert(0, f"{r},{g},{b}")
            self.save_settings()

    def _update_idle_swatch(self):
        """Preview the idle color next to its entry (unchanged while the entry isn't a valid color)."""
        color = parse_rgb(self.idle_color_entry.get())
        if color is not None:
            self.idle_swatch.configure(fg_color=fluent.exact("#{:02x}{:02x}{:02x}".format(*color)))

    def _update_idle_color_visibility(self, value):
        """Show the idle color picker only when 'Default Color' is selected."""
        if value == "Default Color":
            self._place_below(self.idle_color_row_frame)
        else:
            self.idle_color_row_frame.grid_forget()

    def on_idle_behavior_change(self, value):
        self._update_idle_color_visibility(value)
        self.save_settings()

    def _save_if_idle_color_changed(self):
        saved = parse_rgb(self.app_config.get("settings", {}).get("idle_color"))
        if parse_rgb(self.idle_color_entry.get()) != saved:
            self.save_settings()

    def _build_credentials(self):
        """Build the credentials block for config.json, keeping the password in Credential Manager."""
        user = self.user_entry.get().strip()
        pwd = self.pass_entry.get()
        old_creds = self.app_config.get("credentials") or {}
        old_user = old_creds.get("username", "")
        old_in_keyring = old_creds.get("password") == KEYRING_PLACEHOLDER

        if user and pwd:
            if pwd != self._keyring_password or user != old_user or not old_in_keyring:
                try:
                    keyring.set_password(KEYRING_SERVICE, user, pwd)
                except Exception as e:
                    # Never fall back to writing the password into config.json in plaintext
                    self.append_log(f"Could not save password to Windows Credential Manager: {e}", "error")
                    return dict(old_creds)
                self._keyring_password = pwd
            creds = {"username": user, "password": KEYRING_PLACEHOLDER}
        elif user:
            creds = {"username": user, "password": ""}
        else:
            if pwd:
                self.append_log("Enter the account email so the password can be saved.", "error")
            creds = {}

        if user and not pwd and user == old_user and old_in_keyring and self._keyring_read_failed:
            # The field is only blank because Credential Manager couldn't be read at startup
            return dict(old_creds)

        # Don't leave the previous account's password behind in Credential Manager
        if old_in_keyring and old_user and (old_user != user or creds.get("password") != KEYRING_PLACEHOLDER):
            try:
                keyring.delete_password(KEYRING_SERVICE, old_user)
            except Exception:
                pass
            if creds.get("password") != KEYRING_PLACEHOLDER:
                self._keyring_password = None

        return creds

    def save_settings(self):
        """Save all GUI fields to config.json. Works while syncing is running."""
        self.app_config["provider"] = self.provider_var.get()
        self.app_config["ip_address"] = self.ip_entry.get().strip()
        self.app_config["credentials"] = self._build_credentials()

        settings = self.app_config.setdefault("settings", {})
        settings["idle_behavior"] = self.idle_var.get()
        settings["match_brightness"] = self.match_brightness_var.get()
        settings["close_behavior"] = self.close_bh_var.get()
        settings["mica_background"] = self.mica_allowed.get()
        settings["transition_seconds"] = self._transition_choices.get(self.transition_var.get(), 0)

        idle_color = parse_rgb(self.idle_color_entry.get())
        if idle_color is None:
            idle_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
            self.append_log("Idle color must be three numbers from 0-255, e.g. 255,200,100. "
                            f"Keeping {idle_color[0]},{idle_color[1]},{idle_color[2]}.", "error")
        settings["idle_color"] = list(idle_color)
        self._update_idle_swatch()

        try:
            write_config(self.app_config)
        except Exception as e:
            self.append_log(f"Failed to save settings: {e}", "error")
            return
        self.append_log("Settings saved.", "info")

    # --- Sync Engine Control ---
    def is_engine_running(self):
        return self.stop_event is not None and not self.stop_event.is_set()

    def toggle_sync(self):
        """Start the sync engine, or stop it if already running."""
        if self.is_engine_running():
            self.stop_engine()
        else:
            self.start_engine()

    def start_engine(self, save=True):
        if self.is_engine_running():
            return
        if save:
            self.save_settings()
        self.stop_event = threading.Event()
        self._show_start_button(running=True)
        self.set_status("Connecting...", C.caution)
        self.append_log("Connecting to device...", "info")
        self._launch_when_previous_stopped(self.stop_event)

    def _launch_when_previous_stopped(self, stop_event):
        """Start the engine thread, first waiting for any previous run to finish shutting down."""
        if stop_event.is_set():
            return  # Stopped again before it got going
        if self.engine_thread is not None and self.engine_thread.is_alive():
            self.after(100, lambda: self._launch_when_previous_stopped(stop_event))
            return
        self.engine_thread = threading.Thread(target=self.launch_core, args=(stop_event,), daemon=True)
        self.engine_thread.start()

    def stop_engine(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self._show_start_button()
        self.set_status("Stopped", C.text_secondary)
        self.append_log("Syncing stopped.", "info")

    def refresh_sync(self):
        """Save settings and restart the engine if it was running."""
        self.save_settings()
        if self.is_engine_running():
            self.stop_event.set()
            self.append_log("Refreshing — restarting engine with new settings...", "info")
            self.start_engine(save=False)  # Waits for the old engine to exit first
        else:
            self.append_log("Settings saved. Press Start syncing to begin.", "info")

    def launch_core(self, stop_event):
        """Runs on the engine thread."""
        try:
            import core
            core.log_queue = self.log_queue
            core.run(stop_event)
        except BaseException as e:
            self.log_queue.put(("error", f"Core engine crashed: {e}"))
        finally:
            self.ui_calls.put(lambda: self._on_engine_exit(stop_event))

    def _on_engine_exit(self, stop_event):
        # A newer run may already have started; only reset the controls for the current one
        if stop_event is not self.stop_event or stop_event.is_set():
            return
        stop_event.set()
        self._show_start_button()
        if not self._status_is_error:
            self.set_status("Stopped", C.text_secondary)

    # --- Status & Log ---
    def set_status(self, text, color, is_error=False):
        """Update the status dot and label. Call on the UI thread only."""
        self._status_is_error = is_error
        self.status_dot.configure(text_color=color)
        self.status_text.configure(text=text, text_color=color)

    def append_log(self, message, tag="info"):
        """Append a colored line to the log textbox, dropping the oldest lines past MAX_LOG_LINES."""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n", tag)
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

        # The collapsed log shows the latest message
        summary = message.strip()
        if len(summary) > 60:
            summary = summary[:59] + "…"
        self.log_summary.configure(text=summary, text_color=C.critical if tag == "error" else C.text_secondary)

    def poll_queues(self):
        """Apply log messages and UI calls posted by other threads. Re-schedules itself every 100ms."""
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                if level == "ok":
                    self.set_status(message, C.success)
                    self.append_log("✔ " + message, "ok")
                elif level == "error":
                    self.set_status("Error — see log", C.critical, is_error=True)
                    self.append_log("✖ " + message, "error")
                else:
                    self.append_log("  " + message, "info")
        except queue.Empty:
            pass

        while True:
            try:
                call = self.ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                call()
            except Exception as e:
                self.append_log(f"UI error: {e}", "error")

        self.after(100, self.poll_queues)

    # --- System Tray Logic ---
    def create_image(self):
        # Generate a simple 64x64 colored square icon dynamically for the tray
        image = Image.new('RGB', (64, 64), color=(50, 150, 255))
        dc = ImageDraw.Draw(image)
        dc.rectangle((16, 16, 48, 48), fill=(255, 255, 255))
        return image

    def hide_window(self):
        """Ask the user what to do when the X button is pressed."""
        behavior = self.app_config.get("settings", {}).get("close_behavior", "Ask what to do")
        if behavior == "Minimize to Tray":
            self._do_minimize_to_tray()
            return
        elif behavior == "Exit App":
            self.exit_app()
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Close")
        dialog.geometry("340x180")
        dialog.resizable(False, False)
        self.apply_backdrop(dialog)
        dialog.grab_set()  # Modal — blocks the main window

        # Center over parent
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 340) // 2
        y = self.winfo_y() + (self.winfo_height() - 180) // 2
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(dialog, text="What would you like to do?", font=self.font_strong,
                     anchor="w").pack(pady=(20, 10), padx=24, fill="x")

        # Checkbox
        remember_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(dialog, text="Remember my choice", variable=remember_var, font=self.font_body,
                        checkbox_width=20, checkbox_height=20).pack(pady=(0, 16), padx=24, anchor="w")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 20), padx=24, fill="x")

        def minimize():
            if remember_var.get():
                self.close_bh_var.set("Minimize to Tray")
                self.save_settings()
            dialog.destroy()
            self._do_minimize_to_tray()

        def exit_app():
            if remember_var.get():
                self.close_bh_var.set("Exit App")
                self.save_settings()
            dialog.destroy()
            self.exit_app()

        ctk.CTkButton(btn_frame, text="Minimize to tray", height=32, font=self.font_body,
                      fg_color=C.accent, hover_color=C.accent_hover, border_color=C.accent, text_color=C.text_on_accent,
                      command=minimize).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(btn_frame, text="Exit", height=32, font=self.font_body, fg_color=C.window_control,
                      hover_color=C.window_control_hover, border_color=C.window_control_stroke,
                      command=exit_app).pack(side="left", fill="x", expand=True)

    def _do_minimize_to_tray(self):
        self.withdraw()
        if self.tray_icon is not None:
            return
        image = self.create_image()
        menu = pystray.Menu(
            item('Show', self.show_window, default=True),
            item('Quit', self.quit_window)
        )
        self.tray_icon = pystray.Icon("DesktopLEDSync", image, f"Desktop LED Sync {__version__}", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    # The tray callbacks run on pystray's thread, so hand the work to the Tk thread
    def show_window(self, icon, item):
        self.ui_calls.put(self._restore_from_tray)

    def quit_window(self, icon, item):
        self.ui_calls.put(self.exit_app)

    def _stop_tray_icon(self):
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None

    def _restore_from_tray(self):
        self._stop_tray_icon()
        self.deiconify()
        self.lift()
        self.focus_force()

    def exit_app(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self._stop_tray_icon()
        self.quit()

if __name__ == "__main__":
    # --- Single Instance Guard ---
    # Create a named Windows mutex. If it already exists, another copy is running.
    ERROR_ALREADY_EXISTS = 183
    MUTEX_NAME = "DesktopLEDSync_SingleInstanceMutex"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        # Briefly show a Tk root just to display the messagebox, then exit
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Already Running",
            "Desktop LED Sync is already running!"
        )
        root.destroy()
        sys.exit(0)

    app = DesktopLEDSyncGUI(start_in_background=BACKGROUND_FLAG in sys.argv[1:])
    app.mainloop()

    # Release the mutex when the app closes cleanly
    kernel32.CloseHandle(mutex)
