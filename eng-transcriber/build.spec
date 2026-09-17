# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 스펙 — Windows .exe 빌드.

빌드:  pyinstaller build.spec
결과:  dist/EngTranscriber/EngTranscriber.exe  (onedir 권장 — CUDA DLL 이 많아 onefile 은 비권장)

주의:
- faster-whisper/ctranslate2 및 (설치했다면) nvidia-* CUDA DLL 이 자동 수집되도록
  collect_all 을 사용한다.
- ffmpeg 는 imageio-ffmpeg 가 번들하는 실행 파일을 사용한다.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

for pkg in ("faster_whisper", "ctranslate2", "yt_dlp", "imageio_ffmpeg"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# NVIDIA CUDA 런타임 DLL (설치되어 있으면 포함)
for pkg in ("nvidia.cublas", "nvidia.cudnn"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("openai")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EngTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # GUI 앱 → 콘솔 창 없음
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EngTranscriber",
)
