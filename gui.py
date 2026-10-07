import customtkinter as ctk
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

from config_store import (
    DEFAULT_IDLE_COLOR, IDLE_BEHAVIORS, KEYRING_PLACEHOLDER, KEYRING_SERVICE, MAX_TRANSITION_SECONDS,
    normalize_idle_behavior, parse_rgb, parse_transition_seconds, read_config, resolve_password, write_config,
)
from version import __version__

# Passed by the Windows startup shortcut: start syncing straight away, hidden in the tray
BACKGROUND_FLAG = "--background"
AUTOSTART_SHORTCUT_NAME = "DesktopLEDSyncGUI.lnk"
MAX_LOG_LINES = 500

# --- Windows 11 (Fluent) colors, as (light mode, dark mode) ---
ACCENT = ("#005FB8", "#60CDFF")
ACCENT_HOVER = ("#196EBF", "#5AB9E6")
TEXT_ON_ACCENT = ("#FFFFFF", "#0A0A0A")  # Not pure black: that is see-through on a Mica window
TEXT = ("#1B1B1B", "#FFFFFF")
TEXT_SECONDARY = ("#5F5F5F", "#C5C5C5")
WINDOW_BG = ("#F3F3F3", "#202020")
CARD_BG = ("#FBFBFB", "#2B2B2B")
CARD_STROKE = ("#E5E5E5", "#1D1D1D")
CONTROL_BG = ("#FEFEFE", "#2D2D2D")
CONTROL_HOVER = ("#F5F5F5", "#383838")
CONTROL_STROKE = ("#E0E0E0", "#3A3A3A")
SUBTLE_HOVER = ("#EAEAEA", "#383838")
TRACK = ("#8A8A8A", "#9A9A9A")
SUCCESS = ("#0F7B0F", "#6CCB5F")
CAUTION = ("#9D5D00", "#FCE100")
CRITICAL = ("#C42B1C", "#FF99A4")
# With Mica on, everything Tk paints pure black shows the backdrop through it
MICA_KEY_COLOR = "#000000"

ICON_INFO = "\uE946"  # Segoe Fluent Icons / Segoe MDL2 Assets glyph


def apply_fluent_theme():
    """Restyle CustomTkinter's default widgets after Windows 11's Fluent design."""
    theme = ctk.ThemeManager.theme
    theme["CTk"]["fg_color"] = WINDOW_BG
    theme["CTkToplevel"]["fg_color"] = WINDOW_BG
    theme["CTkFrame"].update(corner_radius=8, border_width=0, fg_color=CARD_BG,
                             top_fg_color=CARD_BG, border_color=CARD_STROKE)
    theme["CTkLabel"]["text_color"] = TEXT
    theme["CTkButton"].update(corner_radius=4, border_width=1, fg_color=CONTROL_BG, hover_color=CONTROL_HOVER,
                              border_color=CONTROL_STROKE, text_color=TEXT, text_color_disabled=TEXT_SECONDARY)
    theme["CTkEntry"].update(corner_radius=4, border_width=1, fg_color=CONTROL_BG, border_color=CONTROL_STROKE,
                             text_color=TEXT, placeholder_text_color=TEXT_SECONDARY)
    theme["CTkCheckBox"].update(corner_radius=4, border_width=1, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                border_color=TEXT_SECONDARY, checkmark_color=TEXT_ON_ACCENT, text_color=TEXT)
    theme["CTkSwitch"].update(border_width=3, fg_color=TRACK, progress_color=ACCENT,
                              button_color=("#FFFFFF", "#1B1B1B"), button_hover_color=("#F5F5F5", "#101010"),
                              text_color=TEXT)
    theme["CTkSlider"].update(border_width=7, fg_color=TRACK, progress_color=ACCENT,
                              button_color=ACCENT, button_hover_color=ACCENT_HOVER)
    theme["CTkOptionMenu"].update(corner_radius=4, fg_color=CONTROL_BG, button_color=CONTROL_BG,
                                  button_hover_color=CONTROL_HOVER, text_color=TEXT)
    theme["DropdownMenu"].update(fg_color=("#F9F9F9", "#2C2C2C"), hover_color=SUBTLE_HOVER, text_color=TEXT)
    theme["CTkScrollbar"].update(button_color=("#C2C2C2", "#5A5A5A"), button_hover_color=TRACK)
    theme["CTkTextbox"].update(corner_radius=8, border_width=1, fg_color=CARD_BG, border_color=CARD_STROKE,
                               text_color=TEXT, scrollbar_button_color=("#C2C2C2", "#5A5A5A"),
                               scrollbar_button_hover_color=TRACK)


# Configure the modern look of the window
ctk.set_appearance_mode("System")  # Follows Windows Dark/Light mode
ctk.set_default_color_theme("blue")
apply_fluent_theme()


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def enable_mica(window):
    """
    Give a window the Windows 11 Mica backdrop. Returns False where it isn't available
    (Windows 10, or DWM refused), in which case the window keeps its solid background.
    """
    if sys.platform != "win32" or sys.getwindowsversion().build < 22000:
        return False
    DWMWA_SYSTEMBACKDROP_TYPE = 38      # Windows 11 22H2 and later
    DWMSBT_AUTO, DWMSBT_MAINWINDOW = 0, 2
    DWMWA_MICA_EFFECT = 1029            # Undocumented equivalent on the first Windows 11 release
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dwmapi = ctypes.windll.dwmapi

        def set_attribute(attribute, value):
            value = ctypes.c_int(value)
            return dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0

        if sys.getwindowsversion().build >= 22523:
            enabled = set_attribute(DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
        else:
            enabled = set_attribute(DWMWA_MICA_EFFECT, 1)
        if not enabled:
            return False

        # Extend the backdrop under the whole window so it shows wherever Tk paints black
        margins = _MARGINS(-1, -1, -1, -1)
        if dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) != 0:
            set_attribute(DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_AUTO)
            return False
        return True
    except Exception:
        return False


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

        self._setup_fonts()
        self.use_mica = bool(settings.get("mica_background", True))
        self.apply_backdrop(self)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=(20, 8), padx=24, fill="x")
        ctk.CTkLabel(header, text="Desktop LED Sync", font=self.font_title, anchor="w").pack(fill="x")
        ctk.CTkLabel(header, text=f"Sync Windows media to your smart lights  ·  Version {__version__}",
                     font=self.font_body, text_color=TEXT_SECONDARY, anchor="w").pack(fill="x")

        # --- Log Panel and Footer (packed first so they stay at the bottom) ---
        self.log_box = ctk.CTkTextbox(self, height=110, state="disabled", wrap="word", font=self.font_mono)
        self.log_box.pack(side="bottom", pady=(0, 20), padx=24, fill="x")
        self._log_colors_mode = None
        self._apply_log_colors()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", pady=(8, 10), padx=24, fill="x")

        self.start_button = ctk.CTkButton(footer, width=150, height=34, font=self.font_strong,
                                          command=self.toggle_sync)
        self.start_button.pack(side="right")
        self._show_start_button()

        self.status_dot = ctk.CTkLabel(footer, text="●", text_color=TEXT_SECONDARY, width=16)
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(footer, text="Not running", text_color=TEXT_SECONDARY, anchor="w",
                                        font=self.font_body)
        self.status_text.pack(side="left", padx=(6, 0), fill="x", expand=True)

        # --- Settings (Scrollable so it always fits on screen) ---
        self.settings_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.settings_frame.pack(padx=(24, 12), fill="both", expand=True)
        page = self.settings_frame

        # Lights section
        self._add_section_header(page, "Lights", first=True)

        card = self._add_card(page, "Light brand", "The kind of lights to control",
            help_text="Select the brand of your LED strip.\nTapo: Official TP-Link Tapo lights.\nWLED: Custom ESP8266/ESP32 WLED controllers.")
        # We will parse the providers folder automatically in the future, but for now hardcode
        self.provider_var = ctk.StringVar(value=self.app_config.get("provider", "tapo"))
        self.provider_dropdown = self._add_dropdown(card, ["tapo", "wled"], self.provider_var)
        self._place_trailing(self.provider_dropdown.master)

        card = self._add_card(page, "IP address", "Where your lights are on the local network", wide=True,
            help_text="For Tapo: Open Tapo App -> Device Settings -> Device Info -> IP Address (e.g. 192.168.1.100).\n\nFor WLED: Look in your router's connected devices, or use the WLED app.")
        self.ip_entry = ctk.CTkEntry(card, placeholder_text="e.g. 192.168.1.100", height=32, font=self.font_body)
        self._fill_entry(self.ip_entry, self.app_config.get("ip_address", ""))
        self._place_below(self.ip_entry)

        card = self._add_card(page, "Tapo account", "Needed to sign in to Tapo lights. Leave blank for WLED.", wide=True,
            help_text="Enter the email address and password of the Tapo account your lights are registered to. "
                      "They are required for local authentication.\n\nThe password is stored in Windows Credential Manager.\n(Leave blank if using WLED)")
        self.user_entry = ctk.CTkEntry(card, placeholder_text="Account email", height=32, font=self.font_body)
        self._fill_entry(self.user_entry, saved_creds.get("username", ""))
        self._place_below(self.user_entry, row=1)

        self.pass_entry = ctk.CTkEntry(card, show="•", placeholder_text="Password", height=32, font=self.font_body)

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
        self.apply_btn = ctk.CTkButton(apply_row, text="Save & apply", width=120, height=32,
                                       font=self.font_body, command=self.refresh_sync)
        self.apply_btn.pack(side="right")
        ctk.CTkLabel(apply_row, text="Light changes apply when you save.\nEverything else saves instantly.",
                     font=self.font_caption, text_color=TEXT_SECONDARY, justify="left", anchor="w").pack(side="left")

        # Colors section
        self._add_section_header(page, "Colors")

        card = self._add_card(page, "When music pauses", "What the lights do when nothing is playing",
            help_text="Default Color: Switch to a specific solid color when music stops.\nTurn Off: Power off the lights entirely.\nDo Nothing: Keep the lights displaying the very last album art color.")
        # Handles migration from old snake_case values
        self.idle_var = ctk.StringVar(value=normalize_idle_behavior(settings.get("idle_behavior", "Default Color")))
        self.idle_dropdown = self._add_dropdown(card, list(IDLE_BEHAVIORS), self.idle_var,
                                                command=self.on_idle_behavior_change)
        self._place_trailing(self.idle_dropdown.master)

        # Idle color row (swatch + entry + color picker button), shown only for 'Default Color'
        self.idle_color_row_frame = ctk.CTkFrame(card, fg_color="transparent")
        ctk.CTkLabel(self.idle_color_row_frame, text="Idle color (R,G,B)", font=self.font_body).pack(side="left")

        self.color_pick_btn = ctk.CTkButton(self.idle_color_row_frame, text="Pick…", width=70, height=32,
                                            font=self.font_body, command=self.open_color_picker)
        self.color_pick_btn.pack(side="right")

        self.idle_color_entry = ctk.CTkEntry(self.idle_color_row_frame, placeholder_text="255,200,100",
                                             width=110, height=32, font=self.font_body)
        saved_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
        self.idle_color_entry.insert(0, f"{saved_color[0]},{saved_color[1]},{saved_color[2]}")
        self.idle_color_entry.pack(side="right", padx=(0, 6))
        self.idle_color_entry.bind("<Return>", lambda _: self.save_settings())
        self.idle_color_entry.bind("<FocusOut>", lambda _: self._save_if_idle_color_changed())
        self.idle_color_entry.bind("<KeyRelease>", lambda _: self._update_idle_swatch())

        self.idle_swatch = ctk.CTkFrame(self.idle_color_row_frame, width=32, height=32, corner_radius=4,
                                        border_width=1, border_color=CONTROL_STROKE)
        self.idle_swatch.pack(side="right", padx=(0, 6))
        self._update_idle_swatch()
        self._update_idle_color_visibility(self.idle_var.get())

        card = self._add_card(page, "Color fade", "How long the lights take to blend into a new color",
            help_text="When the color changes, the lights fade smoothly from the old color to the new one "
                      "over this many seconds.\nSet it to 0 to switch colors instantly.")
        fade_row = ctk.CTkFrame(card, fg_color="transparent")
        self.transition_var = ctk.DoubleVar(value=parse_transition_seconds(settings.get("transition_seconds")))
        self.transition_slider = ctk.CTkSlider(
            fade_row, from_=0, to=MAX_TRANSITION_SECONDS, number_of_steps=int(MAX_TRANSITION_SECONDS * 4),
            variable=self.transition_var, width=130, command=lambda _: self._update_transition_label()
        )
        self.transition_slider.pack(side="left")
        self.transition_slider.bind("<ButtonRelease-1>", lambda _: self._save_if_transition_changed())
        self.transition_label = ctk.CTkLabel(fade_row, width=44, anchor="e", font=self.font_body)
        self.transition_label.pack(side="left", padx=(6, 0))
        self._update_transition_label()
        self._place_trailing(fade_row)

        card = self._add_card(page, "Match album art brightness", "Dim the lights for darker artwork")
        self.match_brightness_var = ctk.BooleanVar(value=settings.get("match_brightness", False))
        self.match_brightness_switch = self._add_switch(card, self.match_brightness_var, self.save_settings)

        # App section
        self._add_section_header(page, "App")

        card = self._add_card(page, "When closing the window", "Keep syncing in the tray, or quit",
            help_text="Ask what to do: Show a prompt to select minimize or exit.\nMinimize to Tray: Keep syncing in background.\nExit App: Completely quit the program.")
        self.close_bh_var = ctk.StringVar(value=settings.get("close_behavior", "Ask what to do"))
        self.close_bh_dropdown = self._add_dropdown(card, ["Ask what to do", "Minimize to Tray", "Exit App"],
                                                    self.close_bh_var, command=lambda _: self.save_settings())
        self._place_trailing(self.close_bh_dropdown.master)

        card = self._add_card(page, "Start with Windows", "Sync in the background from the moment you sign in")
        self.autostart_var = ctk.BooleanVar(value=self.check_if_autostart_enabled())
        self.autostart_switch = self._add_switch(card, self.autostart_var, self.on_autostart_toggle)

        ctk.CTkFrame(page, fg_color="transparent", height=8).pack()

        self.stop_event = None      # threading.Event for the current engine run
        self.engine_thread = None
        self.tray_icon = None
        self._status_is_error = False

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

        # Every widget created from here on uses the body font by default
        ctk.ThemeManager.theme["CTkFont"].update(family=body, size=14, weight="normal")
        self.font_body = ctk.CTkFont(family=body, size=14)
        self.font_caption = ctk.CTkFont(family=body, size=12)
        self.font_strong = ctk.CTkFont(family=semibold or body, size=14, weight="normal" if semibold else "bold")
        self.font_title = ctk.CTkFont(family=display or body, size=28, weight="normal" if display else "bold")
        self.font_mono = ctk.CTkFont(family=pick("Cascadia Mono", "Consolas") or body, size=12)
        icon_family = pick("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.font_icon = ctk.CTkFont(family=icon_family, size=14) if icon_family else None

    def apply_backdrop(self, window):
        """Give a window the Mica backdrop if this PC supports it (and it isn't switched off in config.json)."""
        if self.use_mica and enable_mica(window):
            window.configure(fg_color=MICA_KEY_COLOR)

    def _add_section_header(self, parent, text, first=False):
        ctk.CTkLabel(parent, text=text, font=self.font_strong, anchor="w").pack(
            fill="x", padx=2, pady=(0 if first else 18, 6))

    def _add_card(self, parent, title, description="", help_text=None, wide=False):
        """
        A Windows 11 settings card: a title and description on the left. Put a control on the
        right with _place_trailing, or underneath (wide=True) with _place_below.
        """
        card = ctk.CTkFrame(parent, border_width=1)
        card.pack(fill="x", pady=(0, 4))
        card.grid_columnconfigure(0, weight=1)

        text = ctk.CTkFrame(card, fg_color="transparent")
        text.grid(row=0, column=0, sticky="w", padx=(16, 12), pady=(12, 8 if wide else 12))
        title_row = ctk.CTkFrame(text, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text=title, font=self.font_body, height=20).pack(side="left")
        if help_text:
            self.create_help_button(title_row, title, help_text).pack(side="left", padx=(4, 0))
        if description:
            ctk.CTkLabel(text, text=description, font=self.font_caption, text_color=TEXT_SECONDARY, height=16,
                         justify="left", anchor="w", wraplength=400 if wide else 210).pack(anchor="w")
        return card

    @staticmethod
    def _fill_entry(entry, value):
        # Inserting even an empty string hides CustomTkinter's placeholder text
        if value:
            entry.insert(0, value)

    @staticmethod
    def _place_trailing(widget):
        widget.grid(row=0, column=1, sticky="e", padx=(0, 16), pady=12)

    @staticmethod
    def _place_below(widget, row=1):
        widget.grid(row=row, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))

    def _add_dropdown(self, card, values, variable, command=None):
        """An option menu with a 1px outline, like a Windows 11 combo box. Place it via its .master."""
        outline = ctk.CTkFrame(card, fg_color=CONTROL_STROKE, corner_radius=5)
        menu = ctk.CTkOptionMenu(outline, values=values, variable=variable, command=command, width=170, height=30,
                                 font=self.font_body, dropdown_font=self.font_body, bg_color=CONTROL_STROKE,
                                 dynamic_resizing=False)
        menu.pack(padx=1, pady=1)
        return menu

    def _add_switch(self, card, variable, command):
        switch = ctk.CTkSwitch(card, text="", variable=variable, command=command,
                               width=40, switch_width=40, switch_height=20)
        self._place_trailing(switch)
        return switch

    def _show_start_button(self, running=False):
        if running:
            self.start_button.configure(text="Stop syncing", fg_color=CONTROL_BG, hover_color=CONTROL_HOVER,
                                        border_color=CONTROL_STROKE, text_color=TEXT)
        else:
            self.start_button.configure(text="Start syncing", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                        border_color=ACCENT, text_color=TEXT_ON_ACCENT)

    def _apply_log_colors(self):
        """Tk text tags can't follow light/dark mode by themselves, so recolor them when it changes."""
        mode = 1 if ctk.get_appearance_mode() == "Dark" else 0
        if mode == self._log_colors_mode:
            return
        self._log_colors_mode = mode
        self.log_box.tag_config("error", foreground=CRITICAL[mode])
        self.log_box.tag_config("ok", foreground=SUCCESS[mode])
        self.log_box.tag_config("info", foreground=TEXT_SECONDARY[mode])

    def load_config(self):
        try:
            return read_config()
        except Exception:
            return {"provider": "tapo", "ip_address": "", "credentials": {}, "settings": {}}

    def create_help_button(self, parent, title, message):
        """Helper to create a small info button that shows a messagebox."""
        return ctk.CTkButton(
            parent, text=ICON_INFO if self.font_icon else "?", font=self.font_icon or self.font_caption,
            width=24, height=24, border_width=0, fg_color="transparent", hover_color=SUBTLE_HOVER,
            text_color=TEXT_SECONDARY, command=lambda: messagebox.showinfo(title, message)
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
            self.idle_swatch.configure(fg_color="#{:02x}{:02x}{:02x}".format(*color))

    def _update_idle_color_visibility(self, value):
        """Show the idle color picker only when 'Default Color' is selected."""
        if value == "Default Color":
            self._place_below(self.idle_color_row_frame)
        else:
            self.idle_color_row_frame.grid_forget()

    def _update_transition_label(self):
        seconds = self.transition_var.get()
        self.transition_label.configure(text=f"{seconds:g} s" if seconds > 0 else "Off")

    def _save_if_transition_changed(self):
        saved = parse_transition_seconds(self.app_config.get("settings", {}).get("transition_seconds"))
        if self.transition_var.get() != saved:
            self.save_settings()

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
        settings["transition_seconds"] = round(self.transition_var.get(), 2)

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
        self.set_status("Connecting...", CAUTION)
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
        self.set_status("Stopped", TEXT_SECONDARY)
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
            self.set_status("Stopped", TEXT_SECONDARY)

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

    def poll_queues(self):
        """Apply log messages and UI calls posted by other threads. Re-schedules itself every 100ms."""
        self._apply_log_colors()
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                if level == "ok":
                    self.set_status(message, SUCCESS)
                    self.append_log("✔ " + message, "ok")
                elif level == "error":
                    self.set_status("Error — see log", CRITICAL, is_error=True)
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
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=ACCENT, text_color=TEXT_ON_ACCENT,
                      command=minimize).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(btn_frame, text="Exit", height=32, font=self.font_body,
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
