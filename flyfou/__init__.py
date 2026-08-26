"""Flyfou — a screen-capture auto-farm bot for Flyff, with a GUI setup wizard.

The package is split so the automation core never imports tkinter:

    flyfou.winutil    window enumeration, client rects, DPI, focus
    flyfou.capture    screen grabbing
    flyfou.vision     template matching, HP-bar reading, colour detection
    flyfou.profile    named profiles (YAML on disk, fractional geometry)
    flyfou.inputs     focus-guarded mouse/keyboard output
    flyfou.bot        the state machine
    flyfou.gui.*      everything tkinter
"""

APP_NAME = "Flyfou"
VERSION = "2.0.0"
