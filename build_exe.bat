@echo off
REM ============================================================
REM  Buduje GeigerApp.exe (jeden plik, z ikona).
REM  Uruchom z "Anaconda Prompt" (albo innej konsoli, w ktorej dziala "python"):
REM      build_exe.bat
REM  Wynik: dist\GeigerApp.exe
REM ============================================================
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [BLAD] Nie znaleziono Pythona. Uruchom ten plik z "Anaconda Prompt".
    goto :koniec
)

REM Stary pakiet "pathlib" z PyPI psuje PyInstallera - trzeba go usunac recznie.
python -m pip show pathlib >nul 2>&1
if not errorlevel 1 (
    echo [BLAD] Zainstalowany jest przestarzaly pakiet "pathlib", ktory psuje PyInstallera.
    echo        Usun go poleceniem:   conda remove pathlib
    echo        (albo: pip uninstall pathlib^) i uruchom build_exe.bat ponownie.
    goto :koniec
)

echo Instaluje/aktualizuje paczki potrzebne do budowania...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [BLAD] Instalacja paczek nie powiodla sie.
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
    goto :koniec
)

echo.
echo Gotowe: %cd%\dist\GeigerApp.exe
echo Skopiuj ten plik (albo skrot do niego) na pulpit.

:koniec
echo.
pause
endlocal
