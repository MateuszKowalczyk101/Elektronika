@echo off
REM ============================================================
REM  Buduje GeigerApp.exe (jeden plik, z ikona).
REM  Uruchom z "Anaconda Prompt" (albo innej konsoli, w ktorej dziala "python"):
REM      build_exe.bat
REM  Wynik: dist\GeigerApp.exe
REM  Ten sam skrypt buduje .exe na GitHubie (GitHub Actions) - wtedy nie czeka na klawisz.
REM ============================================================
setlocal
cd /d "%~dp0"
set "BLAD=0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [BLAD] Nie znaleziono Pythona. Uruchom ten plik z "Anaconda Prompt".
    set "BLAD=1"
    goto :koniec
)

REM Stary pakiet "pathlib" z PyPI psuje PyInstallera - trzeba go usunac recznie.
python -m pip show pathlib >nul 2>&1
if not errorlevel 1 (
    echo [BLAD] Zainstalowany jest przestarzaly pakiet "pathlib", ktory psuje PyInstallera.
    echo        Usun go poleceniem:   conda remove pathlib
    echo        (albo: pip uninstall pathlib^) i uruchom build_exe.bat ponownie.
    echo        Bez uprawnien administratora uzyj osobnego srodowiska - patrz README.
    set "BLAD=1"
    goto :koniec
)

echo Instaluje/aktualizuje paczki potrzebne do budowania...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [BLAD] Instalacja paczek nie powiodla sie.
    set "BLAD=1"
    goto :koniec
)

echo Buduje GeigerApp.exe (to potrwa 1-3 minuty)...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name GeigerApp ^
    --icon "assets\geiger.ico" ^
    --add-data "assets\geiger.png;assets" ^
    --collect-all nidaqmx ^
    geiger_gui.py
if errorlevel 1 (
    echo [BLAD] Budowanie nie powiodlo sie - zobacz komunikaty powyzej.
    set "BLAD=1"
    goto :koniec
)

echo.
echo Gotowe: %cd%\dist\GeigerApp.exe
echo Skopiuj ten plik (albo skrot do niego) na pulpit.

:koniec
echo.
if not defined CI pause
endlocal & exit /b %BLAD%
