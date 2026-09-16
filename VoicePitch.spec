# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for VoicePitch.

Build a one-file Windows executable with:

    pyinstaller VoicePitch.spec

The resulting exe is written to  dist/VoicePitch.exe  and runs standalone with
no Python installation required on the target machine.

Notes:
  * librosa / soundfile / scipy pull in data files and lazily-imported
    submodules that PyInstaller cannot always detect automatically, so we
    collect them explicitly below.
  * For MP3 / M4A decoding, ffmpeg must be reachable at runtime.  See README
    ("Bundling ffmpeg") for how to drop ffmpeg.exe next to the app.
"""
from PyInstaller.utils.hooks import (collect_data_files,
                                     collect_dynamic_libs,
                                     collect_submodules)

datas = []
binaries = []
hiddenimports = []

for pkg in ("librosa", "soundfile", "sklearn", "scipy"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

for pkg in ("soundfile", "scipy", "sklearn", "numpy"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# librosa & pyqtgraph do a lot of lazy importing.
for pkg in ("librosa", "sklearn", "sklearn.utils", "scipy.signal",
            "scipy.special", "pyqtgraph", "sounddevice", "soundfile"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        hiddenimports.append(pkg)

# audioread backends used by librosa for compressed formats
hiddenimports += ["audioread", "audioread.ffdec", "audioread.rawread",
                  "lazy_loader"]


block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PyQt6", "IPython", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="VoicePitch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed GUI app (no console window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # set to "assets/icon.ico" if you add one
)
