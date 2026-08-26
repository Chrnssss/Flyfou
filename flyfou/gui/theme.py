"""Colours and ttk styling."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#1b1c22"
PANEL = "#23252e"
RAISED = "#2c2f3a"
LINE = "#3a3e4c"
FG = "#e9eaf0"
MUTED = "#9296a8"
ACCENT = "#5b9dff"
GOOD = "#46cf94"
WARN = "#f0b429"
BAD = "#f2685f"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_HUGE = ("Segoe UI Semibold", 22)
FONT_MONO = ("Consolas", 9)

LEVEL_COLORS = {"info": MUTED, "good": GOOD, "warn": WARN}


def apply(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, font=FONT, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Raised.TFrame", background=RAISED)

    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("Panel.TLabel", background=PANEL, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=FONT_SMALL)
    style.configure("Title.TLabel", background=BG, foreground=FG, font=FONT_TITLE)
    style.configure("Huge.TLabel", background=PANEL, foreground=FG, font=FONT_HUGE)
    style.configure("Good.TLabel", background=BG, foreground=GOOD, font=FONT_SMALL)
    style.configure("Warn.TLabel", background=BG, foreground=WARN, font=FONT_SMALL)
    style.configure("Bad.TLabel", background=BG, foreground=BAD, font=FONT_SMALL)

    style.configure(
        "TButton", background=RAISED, foreground=FG, font=FONT,
        padding=(14, 7), relief="flat", focuscolor=RAISED,
    )
    style.map("TButton",
              background=[("disabled", "#25272f"), ("active", LINE)],
              foreground=[("disabled", "#5c6070")])

    style.configure("Accent.TButton", background=ACCENT, foreground="#0d1220", font=FONT_BOLD)
    style.map("Accent.TButton",
              background=[("active", "#7fb4ff"), ("disabled", LINE)],
              foreground=[("disabled", MUTED)])

    style.configure("Danger.TButton", background="#4a2b2b", foreground="#ffd7d3")
    style.map("Danger.TButton",
              background=[("disabled", "#25272f"), ("active", "#5f3535")],
              foreground=[("disabled", "#5c6070")])

    style.configure("Link.TButton", background=BG, foreground=ACCENT, font=FONT_SMALL, padding=(4, 2))
    style.map("Link.TButton", background=[("active", BG)], foreground=[("active", FG)])

    # clam draws a 3D bevel from lightcolor/darkcolor; without flattening them
    # every field gets a white highlight edge.
    bevel = dict(bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.configure("TEntry", fieldbackground=RAISED, foreground=FG,
                    insertcolor=FG, padding=6, **bevel)
    style.configure("TSpinbox", fieldbackground=RAISED, foreground=FG,
                    background=RAISED, arrowcolor=FG, padding=4, **bevel)
    style.configure("TCombobox", fieldbackground=RAISED, background=RAISED,
                    foreground=FG, arrowcolor=FG, padding=6, **bevel)
    # clam ignores the plain fieldbackground once a combobox is readonly, and
    # leaves the selection highlight on, so both have to be mapped by state.
    style.map("TCombobox",
              fieldbackground=[("readonly", RAISED), ("disabled", PANEL)],
              background=[("readonly", RAISED), ("active", LINE)],
              foreground=[("disabled", MUTED)],
              selectbackground=[("readonly", RAISED), ("focus", RAISED)],
              selectforeground=[("readonly", FG), ("focus", FG)],
              arrowcolor=[("disabled", MUTED)])
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#0d1220")

    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("Panel.TCheckbutton", background=PANEL, foreground=FG)
    style.map("Panel.TCheckbutton", background=[("active", PANEL)])

    # background is the slider knob here, not the widget backdrop, and clam
    # slices the knob in half with grip lines unless gripcount is zeroed.
    style.configure("TScale", background=ACCENT, troughcolor=RAISED, gripcount=0, **bevel)
    style.map("TScale", background=[("active", "#7fb4ff"), ("disabled", LINE)])
    style.configure("Panel.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=RAISED, bordercolor=RAISED, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Good.Horizontal.TProgressbar", background=GOOD,
                    troughcolor=RAISED, bordercolor=RAISED, lightcolor=GOOD, darkcolor=GOOD)
    style.configure("Warn.Horizontal.TProgressbar", background=WARN,
                    troughcolor=RAISED, bordercolor=RAISED, lightcolor=WARN, darkcolor=WARN)
    style.configure("Bad.Horizontal.TProgressbar", background=BAD,
                    troughcolor=RAISED, bordercolor=RAISED, lightcolor=BAD, darkcolor=BAD)

    style.configure("TSeparator", background=LINE)
    # On something this narrow the default bevel is most of the widget, so the
    # scrollbar reads as solid white until lightcolor/darkcolor are flattened.
    style.configure("Vertical.TScrollbar", background=RAISED, troughcolor=BG,
                    arrowcolor=MUTED, bordercolor=BG, lightcolor=RAISED, darkcolor=RAISED)
    style.map("Vertical.TScrollbar", background=[("active", LINE)],
              arrowcolor=[("disabled", LINE)])


def hex_color(rgb) -> str:
    r, g, b = (int(max(0, min(255, c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def readable_on(rgb) -> str:
    r, g, b = rgb
    return "#101014" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#f5f5fa"
