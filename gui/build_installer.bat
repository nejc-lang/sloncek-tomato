@echo off
REM Zgradi celoten namestitveni paket (.exe): najprej PyInstaller .exe
REM (build_exe.bat), nato Inno Setup namestitveni program (setup.iss).
REM Zagnati na Windows. Predpogoj: Inno Setup 6 nameščen
REM (https://jrsoftware.org/isinfo.php) - iskan v privzetih namestitvenih
REM poteh, ali nastavite spremenljivko ISCC_PATH na pot do ISCC.exe.

setlocal
cd /d "%~dp0"

call build_exe.bat
if errorlevel 1 exit /b 1

set "ISCC="
if defined ISCC_PATH if exist "%ISCC_PATH%" set "ISCC=%ISCC_PATH%"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo NAPAKA: ISCC.exe ^(Inno Setup Compiler^) ni bilo mogoce najti.
    echo Namestite Inno Setup 6 (https://jrsoftware.org/isinfo.php) ali nastavite
    echo spremenljivko ISCC_PATH na polno pot do ISCC.exe.
    exit /b 1
)

"%ISCC%" setup.iss
if errorlevel 1 (
    echo NAPAKA: Inno Setup gradnja ni uspela.
    exit /b 1
)

echo.
echo Namestitveni paket: ..\dist_installer\DN-PREVZEMNICA-Tomato-GUI-Setup.exe (relativno na koren projekta)
