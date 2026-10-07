import customtkinter as ctk
from tkinter import colorchooser, messagebox
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
    DEFAULT_IDLE_COLOR, IDLE_BEHAVIORS, KEYRING_PLACEHOLDER, KEYRING_SERVICE,
    normalize_idle_behavior, parse_rgb, read_config, resolve_password, write_config,
)

# Configure the modern look of the window
ctk.set_appearance_mode("System")  # Follows Windows Dark/Light mode
ctk.set_default_color_theme("blue")

# Passed by the Windows startup shortcut: start syncing straight away, hidden in the tray
BACKGROUND_FLAG = "--background"
AUTOSTART_SHORTCUT_NAME = "DesktopLEDSyncGUI.lnk"
MAX_LOG_LINES = 500

class DesktopLEDSyncGUI(ctk.CTk):
    def __init__(self, start_in_background=False):
        super().__init__()

        self.title("Desktop LED Sync")
        self.geometry("520x720")
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

        # Title Label
        self.title_label = ctk.CTkLabel(self, text="Desktop LED Sync", font=ctk.CTkFont(size=24, weight="bold"))
        self.title_label.pack(pady=(20, 5))

        self.subtitle_label = ctk.CTkLabel(self, text="Sync Windows Media to Smart Lights", text_color="gray")
        self.subtitle_label.pack(pady=(0, 20))

        # --- Settings Frame (Scrollable so it always fits on screen) ---
        self.settings_frame = ctk.CTkScrollableFrame(self, height=500)
        self.settings_frame.pack(pady=10, padx=20, fill="x")

        # 1. Light Provider Dropdown
        prov_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        prov_label_frame.pack(pady=(20, 0), padx=20, fill="x")
        self.provider_label = ctk.CTkLabel(prov_label_frame, text="Light Brand / Provider:", anchor="w")
        self.provider_label.pack(side="left")
        self.create_help_button(prov_label_frame, "Light Brand",
            "Select the brand of your LED strip.\nTapo: Official TP-Link Tapo lights.\nWLED: Custom ESP8266/ESP32 WLED controllers.").pack(side="left", padx=5)

        # We will parse the providers folder automatically in the future, but for now hardcode
        self.provider_var = ctk.StringVar(value=self.app_config.get("provider", "tapo"))
        self.provider_dropdown = ctk.CTkOptionMenu(self.settings_frame, values=["tapo", "wled"], variable=self.provider_var)
        self.provider_dropdown.pack(pady=(5, 15), padx=20, fill="x")

        # 2. IP Address
        ip_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        ip_label_frame.pack(pady=(5, 0), padx=20, fill="x")
        self.ip_label = ctk.CTkLabel(ip_label_frame, text="LED Strip IP Address:", anchor="w")
        self.ip_label.pack(side="left")
        self.create_help_button(ip_label_frame, "IP Address Help",
            "For Tapo: Open Tapo App -> Device Settings -> Device Info -> IP Address (e.g. 192.168.1.100).\n\nFor WLED: Look in your router's connected devices, or use the WLED app.").pack(side="left", padx=5)

        self.ip_entry = ctk.CTkEntry(self.settings_frame, placeholder_text="e.g. 192.168.1.100")
        self.ip_entry.insert(0, self.app_config.get("ip_address", ""))
        self.ip_entry.pack(pady=(5, 15), padx=20, fill="x")

        # 3. Username / Email (For Tapo etc)
        user_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        user_label_frame.pack(pady=(5, 0), padx=20, fill="x")
        self.user_label = ctk.CTkLabel(user_label_frame, text="Tapo Account Email:", anchor="w")
        self.user_label.pack(side="left")
        self.create_help_button(user_label_frame, "Account Email",
            "Enter the email address used for your Tapo account. This is required for local authentication.\n(Leave blank if using WLED)").pack(side="left", padx=5)

        self.user_entry = ctk.CTkEntry(self.settings_frame, placeholder_text="Leave blank for WLED")
        self.user_entry.insert(0, saved_creds.get("username", ""))
        self.user_entry.pack(pady=(5, 15), padx=20, fill="x")

        # 4. Password
        pass_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        pass_label_frame.pack(pady=(5, 0), padx=20, fill="x")
        self.pass_label = ctk.CTkLabel(pass_label_frame, text="Tapo Account Password:", anchor="w")
        self.pass_label.pack(side="left")
        self.create_help_button(pass_label_frame, "Account Password",
            "Enter your Tapo account password.\n(Leave blank if using WLED)").pack(side="left", padx=5)

        self.pass_entry = ctk.CTkEntry(self.settings_frame, show="*", placeholder_text="Leave blank for WLED")

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

        self.pass_entry.insert(0, saved_pwd)
        self.pass_entry.pack(pady=(5, 10), padx=20, fill="x")

        # 4b. Save & Apply Button
        self.apply_btn = ctk.CTkButton(
            self.settings_frame, text=" Save & Apply",
            fg_color="#1a5a8a", hover_color="#134466",
            command=self.refresh_sync
        )
        self.apply_btn.pack(pady=(0, 20), padx=20, fill="x")

        # 5. Idle Behavior
        idle_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        idle_label_frame.pack(pady=(5, 0), padx=20, fill="x")
        self.idle_label = ctk.CTkLabel(idle_label_frame, text="When Music Pauses:", anchor="w")
        self.idle_label.pack(side="left")
        self.create_help_button(idle_label_frame, "Idle Behavior",
            "Default Color: Switch to a specific solid color when music stops.\nTurn Off: Power off the lights entirely.\nDo Nothing: Keep the lights displaying the very last album art color.").pack(side="left", padx=5)

        # Handles migration from old snake_case values
        self.idle_var = ctk.StringVar(value=normalize_idle_behavior(settings.get("idle_behavior", "Default Color")))
        self.idle_dropdown = ctk.CTkOptionMenu(
            self.settings_frame,
            values=list(IDLE_BEHAVIORS),
            variable=self.idle_var,
            command=self.on_idle_behavior_change
        )
        self.idle_dropdown.pack(pady=(5, 10), padx=20, fill="x")

        # 6. Idle Color row (entry + color picker button side by side)
        self.idle_color_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.idle_color_label = ctk.CTkLabel(self.idle_color_label_frame, text="Idle Color:", anchor="w")
        self.idle_color_label.pack(side="left")
        self.create_help_button(self.idle_color_label_frame, "Idle Color",
            "The color your lights will revert to when playback stops.\nValues are RGB (Red, Green, Blue) from 0-255.").pack(side="left", padx=5)

        self.idle_color_row_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")

        self.idle_color_entry = ctk.CTkEntry(self.idle_color_row_frame, placeholder_text="255,200,100")
        saved_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
        self.idle_color_entry.insert(0, f"{saved_color[0]},{saved_color[1]},{saved_color[2]}")
        self.idle_color_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.idle_color_entry.bind("<Return>", lambda _: self.save_settings())
        self.idle_color_entry.bind("<FocusOut>", lambda _: self._save_if_idle_color_changed())

        self.color_pick_btn = ctk.CTkButton(
            self.idle_color_row_frame, text=" Pick", width=80,
            command=self.open_color_picker
        )
        self.color_pick_btn.pack(side="left")

        # 7. Close Behavior
        self.close_bh_label_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.close_bh_label_frame.pack(pady=(5, 0), padx=20, fill="x")
        self.close_bh_label = ctk.CTkLabel(self.close_bh_label_frame, text="When Closing App:", anchor="w")
        self.close_bh_label.pack(side="left")
        self.create_help_button(self.close_bh_label_frame, "Close Behavior",
            "Ask what to do: Show a prompt to select minimize or exit.\nMinimize to Tray: Keep syncing in background.\nExit App: Completely quit the program.").pack(side="left", padx=5)

        self.close_bh_var = ctk.StringVar(value=settings.get("close_behavior", "Ask what to do"))
        self.close_bh_dropdown = ctk.CTkOptionMenu(
            self.settings_frame,
            values=["Ask what to do", "Minimize to Tray", "Exit App"],
            variable=self.close_bh_var,
            command=lambda _: self.save_settings()
        )
        self.close_bh_dropdown.pack(pady=(5, 10), padx=20, fill="x")

        # Show the idle color row only for 'Default Color' (placed above the close behavior section)
        self._update_idle_color_visibility(self.idle_var.get())

        # 8. Match Album Art Brightness
        self.match_brightness_var = ctk.BooleanVar(value=settings.get("match_brightness", False))
        self.match_brightness_switch = ctk.CTkSwitch(
            self.settings_frame,
            text="Match album art brightness",
            variable=self.match_brightness_var,
            command=self.save_settings
        )
        self.match_brightness_switch.pack(pady=(5, 5), padx=20, fill="x")

        # 9. Start With Windows Toggle
        self.autostart_var = ctk.BooleanVar(value=self.check_if_autostart_enabled())
        self.autostart_switch = ctk.CTkSwitch(
            self.settings_frame, text="Run in background when PC starts",
            variable=self.autostart_var, command=self.on_autostart_toggle
        )
        self.autostart_switch.pack(pady=(10, 10), padx=20, fill="x")

        # --- Action Buttons (two separate rows) ---
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(10, 2), padx=20, fill="x")

        self.start_button = ctk.CTkButton(btn_row, text=" Start Syncing",
                                          fg_color="#2d7a2d", hover_color="#1f5c1f",
                                          command=self.toggle_sync)
        self.start_button.pack(side="left", fill="x", expand=True)

        # Hint label
        ctk.CTkLabel(self, text="💡 Network settings require Apply, others save instantly",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(pady=(0, 4))

        self.stop_event = None      # threading.Event for the current engine run
        self.engine_thread = None
        self.tray_icon = None
        self._status_is_error = False

        # --- Status Bar ---
        status_row = ctk.CTkFrame(self, fg_color="transparent")
        status_row.pack(pady=(0, 5), padx=20, fill="x")

        self.status_dot = ctk.CTkLabel(status_row, text="●", text_color="gray", width=20)
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(status_row, text="Not running", text_color="gray", anchor="w")
        self.status_text.pack(side="left", padx=(4, 0))

        # --- Log Panel ---
        self.log_box = ctk.CTkTextbox(self, height=120, state="disabled", wrap="word")
        self.log_box.pack(pady=(0, 15), padx=20, fill="x")
        self.log_box.tag_config("error", foreground="#ff6b6b")
        self.log_box.tag_config("ok", foreground="#6bff8e")
        self.log_box.tag_config("info", foreground="#aaaaaa")

        for message, tag in startup_messages:
            self.append_log(message, tag)

        self._upgrade_autostart_shortcut()
        self.after(100, self.poll_queues)

        if start_in_background:
            self.after(0, self._start_in_background)

    def load_config(self):
        try:
            return read_config()
        except Exception:
            return {"provider": "tapo", "ip_address": "", "credentials": {}, "settings": {}}

    def create_help_button(self, parent, title, message):
        """Helper to create a small '?' button that shows a messagebox."""
        return ctk.CTkButton(
            parent, text="?", width=20, height=20, corner_radius=10,
            fg_color="gray", hover_color="darkgray",
            command=lambda: messagebox.showinfo(title, message)
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

    def _update_idle_color_visibility(self, value):
        """Show the idle color picker only when 'Default Color' is selected."""
        if value == "Default Color":
            self.idle_color_label_frame.pack(pady=(5, 0), padx=20, fill="x",
                                             before=self.close_bh_label_frame)
            self.idle_color_row_frame.pack(pady=(5, 10), padx=20, fill="x",
                                           before=self.close_bh_label_frame)
        else:
            self.idle_color_label_frame.pack_forget()
            self.idle_color_row_frame.pack_forget()

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

        idle_color = parse_rgb(self.idle_color_entry.get())
        if idle_color is None:
            idle_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
            self.append_log("Idle color must be three numbers from 0-255, e.g. 255,200,100. "
                            f"Keeping {idle_color[0]},{idle_color[1]},{idle_color[2]}.", "error")
        settings["idle_color"] = list(idle_color)

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
        self.start_button.configure(text=" Stop Syncing", fg_color="#7a2d2d", hover_color="#5c1f1f")
        self.set_status("Connecting...", "#f0a500")
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
        self.start_button.configure(text=" Start Syncing", fg_color="#2d7a2d", hover_color="#1f5c1f")
        self.set_status("Stopped", "gray")
        self.append_log("Syncing stopped.", "info")

    def refresh_sync(self):
        """Save settings and restart the engine if it was running."""
        self.save_settings()
        if self.is_engine_running():
            self.stop_event.set()
            self.append_log("Refreshing — restarting engine with new settings...", "info")
            self.start_engine(save=False)  # Waits for the old engine to exit first
        else:
            self.append_log("Settings saved. Press ▶ Start to begin syncing.", "info")

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
        self.start_button.configure(text=" Start Syncing", fg_color="#2d7a2d", hover_color="#1f5c1f")
        if not self._status_is_error:
            self.set_status("Stopped", "gray")

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
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                if level == "ok":
                    self.set_status(message, "#6bff8e")
                    self.append_log("✔ " + message, "ok")
                elif level == "error":
                    self.set_status("Error — see log", "#ff6b6b", is_error=True)
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
        dialog.geometry("300x170")
        dialog.resizable(False, False)
        dialog.grab_set()  # Modal — blocks the main window

        # Center over parent
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 300) // 2
        y = self.winfo_y() + (self.winfo_height() - 170) // 2
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(dialog, text="What would you like to do?",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(18, 8))

        # Checkbox
        remember_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(dialog, text="Remember my choice", variable=remember_var, font=ctk.CTkFont(size=11)).pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 14), padx=20, fill="x")

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

        ctk.CTkButton(btn_frame, text="Minimize to Tray", command=minimize).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(btn_frame, text="Exit", fg_color="#7a2d2d", hover_color="#5c1f1f",
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
        self.tray_icon = pystray.Icon("DesktopLEDSync", image, "Desktop LED Sync", menu)
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
