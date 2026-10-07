"""
Windows 11 (Fluent) look for CustomTkinter: colors, the Mica backdrop, and the controls
CustomTkinter has no close match for (combo box with a flyout, toggle switch).

How Mica works here: Tk paints the window pure black, and DWM shows the backdrop through it.
Every other color Tk paints is *added* to the backdrop rather than drawn over it, so in dark
mode the colors below are given as "desired color minus backdrop". This is also how Windows 11
builds its own dark surfaces (faint white layers over Mica). Dark text can't be drawn that way,
so light mode keeps Mica only in the title bar and paints the window with solid colors.
"""
import ctypes
import sys
from types import SimpleNamespace

import customtkinter as ctk
from PIL import Image, ImageDraw

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) glyphs
ICON_CHEVRON_DOWN = ""
ICON_CHEVRON_UP = ""

MICA_KEY_COLOR = "#000000"
DARK_MICA_BRIGHTNESS = 0x1E  # Roughly how bright the dark Mica backdrop is

# Desired on-screen colors, as (light mode, dark mode)
_BASE = {
    "window":                ("#F3F3F3", "#202020"),
    "card":                  ("#FBFBFB", "#2B2B2B"),
    "card_stroke":           ("#E5E5E5", "#1D1D1D"),
    "control":               ("#FEFEFE", "#373737"),  # Buttons, boxes and inputs inside a card
    "control_hover":         ("#F6F6F6", "#3C3C3C"),
    "control_stroke":        ("#E5E5E5", "#3F3F3F"),
    "window_control":        ("#FBFBFB", "#2D2D2D"),  # The same, directly on the window
    "window_control_hover":  ("#F6F6F6", "#323232"),
    "window_control_stroke": ("#E5E5E5", "#353535"),
    "input_focused":         ("#FFFFFF", "#1F1F1F"),
    "subtle_hover":          ("#F2F2F2", "#323232"),
    "text":                  ("#1B1B1B", "#FFFFFF"),
    "text_secondary":        ("#5D5D5D", "#C5C5C5"),
    "strong_stroke":         ("#868686", "#9A9A9A"),
    "scrollbar":             ("#8A8A8A", "#5D5D5D"),
    "scrollbar_hover":       ("#5D5D5D", "#8A8A8A"),
    "success":               ("#0F7B0F", "#6CCB5F"),
    "caution":               ("#9D5D00", "#FCE100"),
    "critical":              ("#C42B1C", "#FF99A4"),
}
# Popup windows don't get Mica, so these are always solid
_FLYOUT = {
    "flyout":        ("#F9F9F9", "#2C2C2C"),
    "flyout_hover":  ("#F0F0F0", "#383838"),
    "flyout_stroke": ("#E5E5E5", "#3A3A3A"),
    "flyout_text":   ("#1B1B1B", "#FFFFFF"),
}
DEFAULT_ACCENT = ("#005FB8", "#60CDFF")

# The active palette: each attribute is a (light, dark) color tuple. Filled in by configure().
C = SimpleNamespace()


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _over_mica(color):
    """The color to paint so it appears as `color` once DWM adds the dark Mica backdrop."""
    # Never exactly black, which would turn see-through
    return _hex(tuple(max(c - DARK_MICA_BRIGHTNESS, 1) for c in _rgb(color)))


def _mix(color, other, amount):
    a, b = _rgb(color), _rgb(other)
    return _hex(tuple(round(x * amount + y * (1 - amount)) for x, y in zip(a, b)))


def read_windows_accent():
    """The user's Windows accent color as (light mode shade, dark mode shade), or None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as key:
            palette, _ = winreg.QueryValueEx(key, "AccentPalette")
        # Eight RGBA colors from lightest to darkest; Windows uses Dark 1 in light mode and Light 2 in dark mode
        shades = [_hex(palette[i * 4:i * 4 + 3]) for i in range(8)]
        return shades[4], shades[1]
    except Exception:
        return None


def configure(mica):
    """Build the palette, for a window that does (mica=True) or doesn't use Mica in dark mode."""
    colors = dict(_BASE)
    accent = read_windows_accent() or DEFAULT_ACCENT
    colors["accent"] = accent
    colors["accent_hover"] = (_mix(accent[0], _BASE["window"][0], 0.9), _mix(accent[1], _BASE["window"][1], 0.9))
    colors["text_on_accent"] = ("#FFFFFF", "#000000")

    for name, (light, dark) in colors.items():
        if mica:
            dark = _over_mica(dark)
        setattr(C, name, (light, dark))
    C.window = (_BASE["window"][0], MICA_KEY_COLOR if mica else _BASE["window"][1])

    for name, value in _FLYOUT.items():
        setattr(C, name, value)
    C.accent_solid = accent
    C.mica = mica


def exact(color):
    """A (light, dark) pair that shows `color` itself on screen, such as a preview swatch."""
    return (color, _over_mica(color) if C.mica else color)


def apply_theme(font_family):
    """Restyle CustomTkinter's default widgets with the active palette. Call before creating widgets."""
    theme = ctk.ThemeManager.theme
    theme["CTkFont"].update(family=font_family, size=14, weight="normal")
    theme["CTk"]["fg_color"] = C.window
    theme["CTkToplevel"]["fg_color"] = C.window
    theme["CTkFrame"].update(corner_radius=8, border_width=0, fg_color=C.card, top_fg_color=C.card,
                             border_color=C.card_stroke)
    theme["CTkLabel"]["text_color"] = C.text
    theme["CTkButton"].update(corner_radius=4, border_width=1, fg_color=C.control, hover_color=C.control_hover,
                              border_color=C.control_stroke, text_color=C.text, text_color_disabled=C.text_secondary)
    theme["CTkEntry"].update(corner_radius=4, border_width=1, fg_color=C.control, border_color=C.control_stroke,
                             text_color=C.text, placeholder_text_color=C.text_secondary)
    theme["CTkCheckBox"].update(corner_radius=4, border_width=1, fg_color=C.accent, hover_color=C.accent_hover,
                                border_color=C.strong_stroke, checkmark_color=C.text_on_accent, text_color=C.text)
    theme["CTkScrollbar"].update(corner_radius=1000, border_spacing=4, button_color=C.scrollbar,
                                 button_hover_color=C.scrollbar_hover)
    theme["CTkTextbox"].update(corner_radius=0, border_width=0, fg_color=C.card, text_color=C.text,
                               scrollbar_button_color=C.scrollbar, scrollbar_button_hover_color=C.scrollbar_hover)


# --- Windows (DWM) helpers ---
class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_SYSTEMBACKDROP_TYPE = 38      # Windows 11 22H2 and later
DWMSBT_MAINWINDOW = 2
DWMWA_MICA_EFFECT = 1029            # Undocumented equivalent on the first Windows 11 release


def _hwnd(window):
    window.update_idletasks()
    return ctypes.windll.user32.GetParent(window.winfo_id())


def _set_attribute(hwnd, attribute, value):
    value = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0


def mica_supported():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def enable_mica(window):
    """Turn on the Mica backdrop for a window. Returns False where it isn't available."""
    if not mica_supported():
        return False
    try:
        hwnd = _hwnd(window)
        if sys.getwindowsversion().build >= 22523:
            return _set_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
        return _set_attribute(hwnd, DWMWA_MICA_EFFECT, 1)
    except Exception:
        return False


def show_mica_in_client_area(window, show):
    """Let the backdrop show through the window's black pixels (dark mode), or keep it to the title bar."""
    try:
        extent = -1 if show else 0
        margins = _MARGINS(extent, extent, extent, extent)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(_hwnd(window), ctypes.byref(margins))
    except Exception:
        pass


def round_corners(window):
    """Ask Windows 11 to round a borderless popup's corners. Returns False where it can't."""
    if not mica_supported():
        return False
    try:
        return _set_attribute(_hwnd(window), DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
    except Exception:
        return False


def _is_inside(widget, x_root, y_root):
    return (widget.winfo_rootx() <= x_root < widget.winfo_rootx() + widget.winfo_width()
            and widget.winfo_rooty() <= y_root < widget.winfo_rooty() + widget.winfo_height())


class Hover:
    """
    Tracks the pointer over `area` and its child widgets, calling on_change(hovering) when it
    enters or leaves. (Tk sends a Leave event when the pointer merely moves onto a child.)
    """

    def __init__(self, area, widgets, on_change):
        self.area = area
        self.on_change = on_change
        self.hovering = False
        for widget in widgets:
            widget.bind("<Enter>", lambda _: self.set(True), add="+")
            widget.bind("<Leave>", lambda _: area.after(1, self.check), add="+")

    def check(self):
        try:
            x, y = self.area.winfo_pointerxy()
            self.set(_is_inside(self.area, x, y))
        except Exception:
            pass

    def set(self, hovering):
        if hovering != self.hovering:
            self.hovering = hovering
            self.on_change(hovering)


class ComboBox(ctk.CTkFrame):
    """A Windows 11 combo box: the value with a chevron, opening a rounded flyout list."""

    ITEM_HEIGHT = 34
    ITEM_GAP = 2
    PADDING = 4

    def __init__(self, master, values, variable, command=None, width=200, font=None, icon_font=None,
                 on_window=False):
        self._fill = C.window_control if on_window else C.control
        self._fill_hover = C.window_control_hover if on_window else C.control_hover
        super().__init__(master, width=width, height=32, corner_radius=4, border_width=1, fg_color=self._fill,
                         border_color=C.window_control_stroke if on_window else C.control_stroke)
        self.values = list(values)
        self.variable = variable
        self.command = command
        self._font = font
        self._flyout = None

        self.grid_propagate(False)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._text = ctk.CTkLabel(self, textvariable=variable, font=font, anchor="w", height=20)
        self._text.grid(row=0, column=0, sticky="ew", padx=(11, 4))
        self._chevron = ctk.CTkLabel(self, text=ICON_CHEVRON_DOWN if icon_font else "▾", width=16, height=20,
                                     font=icon_font, text_color=C.text_secondary)
        self._chevron.grid(row=0, column=1, padx=(0, 10))

        widgets = (self, self._text, self._chevron)
        self._hover = Hover(self, widgets, lambda hovering: self.configure(
            fg_color=self._fill_hover if hovering or self._flyout else self._fill))
        for widget in widgets:
            widget.bind("<Button-1>", self._open, add="+")

    def _open(self, _event=None):
        if self._flyout is not None:
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        current = self.variable.get()
        selected = self.values.index(current) if current in self.values else 0
        pitch = self.ITEM_HEIGHT + self.ITEM_GAP

        flyout = self._flyout = ctk.CTkToplevel(self, fg_color=C.flyout)
        flyout.withdraw()
        flyout.overrideredirect(True)
        rounded = round_corners(flyout)
        body = ctk.CTkFrame(flyout, fg_color=C.flyout, corner_radius=0,
                            border_width=0 if rounded else 1, border_color=C.flyout_stroke)
        body.pack(fill="both", expand=True)

        for index, value in enumerate(self.values):
            is_selected = index == selected
            item = ctk.CTkButton(
                body, text=value, anchor="w", height=self.ITEM_HEIGHT, corner_radius=4, border_width=0,
                border_spacing=8, font=self._font, text_color=C.flyout_text,
                fg_color=C.flyout_hover if is_selected else "transparent", hover_color=C.flyout_hover,
                command=lambda v=value: self._choose(v),
            )
            item.pack(fill="x", padx=self.PADDING,
                      pady=(self.PADDING if index == 0 else self.ITEM_GAP,
                            self.PADDING if index == len(self.values) - 1 else 0))
            if is_selected:
                # The accent "pill" Windows 11 puts next to the current choice
                ctk.CTkFrame(item, width=3, height=16, corner_radius=2, fg_color=C.accent_solid,
                             bg_color=C.flyout_hover).place(x=0, rely=0.5, anchor="w")

        # Line the current choice up over the box, like Windows does, but keep the list on screen
        width = self.winfo_width() / scaling + 2 * self.PADDING
        height = 2 * self.PADDING + len(self.values) * pitch - self.ITEM_GAP
        x = round(self.winfo_rootx() - self.PADDING * scaling)
        y = self.winfo_rooty() + (self.winfo_height() - self.ITEM_HEIGHT * scaling) / 2 \
            - (self.PADDING + selected * pitch) * scaling
        y = round(min(max(y, 0), self.winfo_screenheight() - height * scaling))
        flyout.geometry(f"{round(width)}x{round(height)}+{x}+{y}")

        flyout.deiconify()
        flyout.lift()
        flyout.focus_force()
        flyout.grab_set()
        flyout.bind("<Escape>", lambda _: self._close())
        flyout.bind("<ButtonPress>", self._on_flyout_press)
        flyout.bind("<FocusOut>", lambda _: flyout.after(50, self._close_if_unfocused))

    def _on_flyout_press(self, event):
        if self._flyout is not None and not _is_inside(self._flyout, event.x_root, event.y_root):
            self._close()

    def _close_if_unfocused(self):
        if self._flyout is not None and self._flyout.focus_get() is None:
            self._close()

    def _close(self):
        flyout, self._flyout = self._flyout, None
        if flyout is not None:
            try:
                flyout.grab_release()
                flyout.destroy()
            except Exception:
                pass
        self._hover.check()

    def _choose(self, value):
        changed = value != self.variable.get()
        self.variable.set(value)
        self._close()
        if changed and self.command is not None:
            self.command(value)


def _toggle_image(on, hover, mode):
    """Draw a Windows 11 toggle switch (40x20, at 2x for sharpness) in light (0) or dark (1) mode."""
    scale = 8
    width, height = 40 * scale, 20 * scale
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    knob = (14 if hover else 12) * scale // 2
    center_y = height // 2
    if on:
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=_rgb(C.accent[mode]))
        center_x = width - height // 2
        knob_color = _rgb(C.text_on_accent[mode])
    else:
        fill = _rgb(C.subtle_hover[mode]) + (255,) if hover else (0, 0, 0, 0)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=fill,
                               outline=_rgb(C.strong_stroke[mode]), width=scale)
        center_x = height // 2
        knob_color = _rgb(C.strong_stroke[mode])
    draw.ellipse((center_x - knob, center_y - knob, center_x + knob, center_y + knob), fill=knob_color)
    return image.resize((80, 40), Image.LANCZOS)


class Toggle(ctk.CTkFrame):
    """A Windows 11 toggle switch with its On/Off label, bound to a BooleanVar."""

    def __init__(self, master, variable, command=None, font=None):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.variable = variable
        self.command = command
        self._images = {
            (on, hover): ctk.CTkImage(_toggle_image(on, hover, 0), _toggle_image(on, hover, 1), size=(40, 20))
            for on in (False, True) for hover in (False, True)
        }
        self._label = ctk.CTkLabel(self, font=font, width=28, anchor="e")
        self._label.pack(side="left", padx=(0, 12))
        self._switch = ctk.CTkLabel(self, text="", width=40, height=20)
        self._switch.pack(side="left")

        self._hover = Hover(self, (self, self._label, self._switch), lambda _: self._refresh())
        for widget in (self._label, self._switch):
            widget.bind("<Button-1>", self._toggle, add="+")
        variable.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()

    def _refresh(self):
        on = bool(self.variable.get())
        self._label.configure(text="On" if on else "Off")
        self._switch.configure(image=self._images[(on, self._hover.hovering)])
