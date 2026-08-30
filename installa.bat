@echo off
REM Installazione di EsaMiX su Windows: crea l'ambiente virtuale Python e installa
REM l'app al suo interno, senza toccare nulla al di fuori di questa cartella.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python non risulta installato. Vedi il README per le istruzioni di installazione.
    pause
    exit /b 1
)

python -m venv .venv
call .venv\Scripts\activate.bat
pip install --upgrade pip
pip install -e .

echo.
echo Installazione completata. Per avviare l'app esegui: esamix.bat
pause
