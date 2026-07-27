# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec za GUI klienta (gui/main.py) - onefile, brez konzole (Qt6 GUI).
# Zagnati z: pyinstaller --noconfirm gui\gui.spec  (glej build_exe.bat)
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # gui/ -> koren projekta

a = Analysis(
    [str(ROOT / "gui" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="DN-PREVZEMNICA-Tomato-GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
