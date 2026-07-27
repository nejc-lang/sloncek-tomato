@echo off
REM Zgradi samostojno .exe za GUI klienta (gui/main.py) s PyInstaller.
REM Zagnati na Windows, iz mape gui\ ali od koderkoli (skripta sama poišče
REM svojo lokacijo prek %~dp0). Vse, kar skripta ustvari - virtualno okolje,
REM build\ in dist\ - nastane znotraj gui\ mape.
REM
REM Vse odvisnosti se namestijo samodejno, ČE ŠE NISO nameščene:
REM - Python 3.11 sam (preko winget, če `python` ni najden v PATH),
REM - PySide6, httpx, python-dotenv, pyinstaller (preko pip v virtualnem
REM   okolju gui\.venv-build) - `pip install` privzeto ne prenaša/ne
REM   ponovno namešča paketov, ki so že zadovoljeni v pravilni razlici, zato
REM   je ponoven zagon te skripte hiter in ne dela nič, če je vse že na mestu.

setlocal
set "GUI_DIR=%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    where winget >nul 2>nul
    if errorlevel 1 (
        echo NAPAKA: python ni na voljo v PATH, winget pa ni na voljo za samodejno namestitev.
        echo Namestite Python 3.11+ za Windows rocno: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo Python ni najden - namescam Python 3.11 preko winget ...
    winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo NAPAKA: samodejna namestitev Pythona preko winget ni uspela.
        echo Namestite Python 3.11+ za Windows rocno: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    REM winget namestitev posodobi PATH samo v registru - osvezimo ga v tej seji,
    REM da lahko takoj uporabimo novo namescen "python" brez ponovnega zagona okna.
    for /f "skip=2 tokens=1,2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%C"
    for /f "skip=2 tokens=1,2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%C"
    set "PATH=%SYS_PATH%;%USER_PATH%;%PATH%"
    where python >nul 2>nul
    if errorlevel 1 (
        echo NAPAKA: Python je nameščen, a ga ta ukazna vrstica ne vidi. Odprite novo ukazno okno in znova zaženite to skripto.
        pause
        exit /b 1
    )
)

if not exist "%GUI_DIR%.venv-build" (
    echo Ustvarjam virtualno okolje gui\.venv-build ...
    python -m venv "%GUI_DIR%.venv-build"
)

call "%GUI_DIR%.venv-build\Scripts\activate.bat"

python -m pip install --upgrade pip
pip install -r "%GUI_DIR%requirements.txt"
pip install pyinstaller

if exist "%GUI_DIR%dist\DN-PREVZEMNICA-Tomato-GUI.exe" del /f /q "%GUI_DIR%dist\DN-PREVZEMNICA-Tomato-GUI.exe"

pyinstaller --noconfirm --distpath "%GUI_DIR%dist" --workpath "%GUI_DIR%build" "%GUI_DIR%gui.spec"

if errorlevel 1 (
    echo NAPAKA: PyInstaller gradnja ni uspela.
    pause
    exit /b 1
)

echo.
echo Zgrajeno: gui\dist\DN-PREVZEMNICA-Tomato-GUI.exe
if /i "%~1" neq "--no-pause" pause
