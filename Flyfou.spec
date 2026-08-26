# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/Anira/Desktop/Projects/Flyfou/flyfou_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['PIL._tkinter_finder', 'pydirectinput', 'keyboard', 'win32gui', 'win32ui', 'win32con', 'mss.windows'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'pytest', 'IPython', 'notebook', 'PySide6', 'PyQt5', 'PyQt6', 'setuptools', 'pip'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Flyfou',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
