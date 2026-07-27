# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec za GUI klienta (gui/main.py) - onefile, brez konzole (Qt6 GUI).
# Zagnati z: pyinstaller --noconfirm gui\gui.spec  (glej build_exe.bat)
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # gui/ -> koren projekta

a = Analysis(
    [str(ROOT / "gui" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # Vseh 6 okvirjev (ne samo ikona) - uporabljajo se tudi za animacijo
    # slončka po koncu obdelave (glej gui/main.py, ANIMATION_FRAME_PATHS).
    datas=[
        (str(p), "pic-sloncek")
        for p in sorted((ROOT / "gui" / "pic-sloncek").glob("flying_elephant_frame_*.png"))
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Ikona .exe (in posledično bližnjic, ki privzeto povzamejo ikono tarče) -
# prva slika slončka (glej gui/pic-sloncek/app_icon.ico, generirana iz
# flying_elephant_frame_1.png).
ICON_PATH = str(ROOT / "gui" / "pic-sloncek" / "app_icon.ico")
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
    icon=ICON_PATH,
)
