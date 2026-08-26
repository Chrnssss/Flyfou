"""Small shared widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .. import inputs
from . import theme


class Card(ttk.Frame):
    """A padded panel with an optional heading."""

    def __init__(self, master, title: str = "", **kwargs):
        super().__init__(master, style="Panel.TFrame", padding=14, **kwargs)
        self.body = self
        if title:
            ttk.Label(self, text=title, style="Panel.TLabel", font=theme.FONT_BOLD).pack(
                anchor="w", pady=(0, 8)
            )
            self.body = ttk.Frame(self, style="Panel.TFrame")
            self.body.pack(fill="both", expand=True)


class Chip(ttk.Label):
    """Short status text that changes colour with its level.

    `wrap` is a pixel width; without one Tk clips a long message mid-word
    instead of wrapping it, which is exactly when the text matters most.
    """

    def __init__(self, master, text: str = "", level: str = "info", panel: bool = False,
                 wrap: int = 0):
        self._panel = panel
        super().__init__(master, text=text, font=theme.FONT_SMALL, justify="left",
                         wraplength=wrap, background=theme.PANEL if panel else theme.BG)
        self.set(text, level)

    def set(self, text: str, level: str = "info") -> None:
        colours = {"info": theme.MUTED, "good": theme.GOOD, "warn": theme.WARN, "bad": theme.BAD}
        self.configure(text=text, foreground=colours.get(level, theme.MUTED),
                       background=theme.PANEL if self._panel else theme.BG)


class LabelledScale(ttk.Frame):
    """A slider with a plain-language value readout."""

    def __init__(self, master, text: str, from_: float, to: float, value: float,
                 formatter: Callable[[float], str], on_change: Optional[Callable[[float], None]] = None,
                 step: Optional[float] = None, panel: bool = True):
        style = "Panel.TFrame" if panel else "TFrame"
        label_style = "Panel.TLabel" if panel else "TLabel"
        super().__init__(master, style=style)
        self._formatter = formatter
        self._on_change = on_change
        self._step = step

        header = ttk.Frame(self, style=style)
        header.pack(fill="x")
        ttk.Label(header, text=text, style=label_style).pack(side="left")
        self.value_label = ttk.Label(header, text=formatter(value), style=label_style,
                                     font=theme.FONT_BOLD, foreground=theme.ACCENT)
        self.value_label.pack(side="right")

        self.var = tk.DoubleVar(value=value)
        scale = ttk.Scale(self, from_=from_, to=to, variable=self.var,
                          orient="horizontal", command=self._changed)
        scale.pack(fill="x", pady=(4, 0))

    def _changed(self, _event=None) -> None:
        value = self.var.get()
        if self._step:
            value = round(value / self._step) * self._step
        self.value_label.configure(text=self._formatter(value))
        if self._on_change:
            self._on_change(value)

    def get(self) -> float:
        value = self.var.get()
        return round(value / self._step) * self._step if self._step else value


class KeyCaptureButton(ttk.Button):
    """Click, then press a key — used for skills and hotkeys so nobody types key names."""

    def __init__(self, master, value: str = "", on_capture: Optional[Callable[[str], None]] = None,
                 placeholder: str = "Click, then press a key"):
        super().__init__(master, command=self._arm)
        self._value = value
        self._on_capture = on_capture
        self._placeholder = placeholder
        self._armed = False
        self._bindings = []
        self._render()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._render()

    def _render(self) -> None:
        if self._armed:
            self.configure(text="Press any key…  (Esc to cancel)", style="Accent.TButton")
        else:
            text = inputs.display_name(self._value) if self._value else self._placeholder
            self.configure(text=text, style="TButton")

    def _arm(self) -> None:
        if self._armed:
            self._disarm()
            return
        self._armed = True
        self._render()
        self.focus_set()
        self._bindings = [self.bind("<KeyPress>", self._captured)]

    def _disarm(self) -> None:
        self._armed = False
        for binding in self._bindings:
            self.unbind("<KeyPress>", binding)
        self._bindings = []
        self._render()

    def _captured(self, event) -> str:
        if event.keysym == "Escape":
            self._disarm()
            return "break"
        key = inputs.from_keysym(event.keysym)
        if key is None:
            return "break"
        self._value = key
        self._disarm()
        if self._on_capture:
            self._on_capture(key)
        return "break"


class ScrollFrame(ttk.Frame):
    """Vertically scrollable container; put content in `.inner`."""

    def __init__(self, master, height: int = 260, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, height=height, bd=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.inner.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def clear(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()


def separator(master, pady: int = 12) -> ttk.Separator:
    line = ttk.Separator(master, orient="horizontal")
    line.pack(fill="x", pady=pady)
    return line
