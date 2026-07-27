@echo off
REM Zgradi samostojno .exe za GUI klienta (gui/main.py) s PyInstaller.
REM Zagnati na Windows, iz korena projekta ali iz gui\ (skripta sama poišče
REM koren projekta glede na svojo lokacijo).
REM
REM Odvisnosti (PySide6, httpx, python-dotenv, pyinstaller) se namestijo
REM samodejno, ČE ŠE NISO nameščene - `pip install` privzeto ne prenaša/ne
REM ponovno namešča paketov, ki so že zadovoljeni v pravilni razlici, zato
REM je ponoven zagon te skripte hiter in ne dela nič, če je vse že na mestu.

setlocal
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo NAPAKA: python ni na voljo v PATH. Namestite Python 3.11+ za Windows.
    exit /b 1
)

if not exist ".venv-build" (
    echo Ustvarjam virtualno okolje .venv-build ...
    python -m venv .venv-build
)

call ".venv-build\Scripts\activate.bat"

python -m pip install --upgrade pip
pip install -r requirements-gui.txt
pip install pyinstaller

if exist "dist\DN-PREVZEMNICA-Tomato-GUI.exe" del /f /q "dist\DN-PREVZEMNICA-Tomato-GUI.exe"

pyinstaller --noconfirm gui\gui.spec

if errorlevel 1 (
    echo NAPAKA: PyInstaller gradnja ni uspela.
    exit /b 1
)

echo.
echo Zgrajeno: dist\DN-PREVZEMNICA-Tomato-GUI.exe
