@echo off
setlocal
REM Installazione di EsaMiX su Windows: crea l'ambiente virtuale Python e installa
REM l'app al suo interno, senza toccare nulla al di fuori di questa cartella.
REM Usa sempre il Python dell'ambiente per percorso (non "activate"), cosi' funziona
REM anche da PowerShell e con Python non aggiunto al PATH.
cd /d "%~dp0"
set "VPY=.venv\Scripts\python.exe"

REM ambiente gia' presente (ad esempio da un'installazione interrotta): lo riusa se
REM funziona, altrimenti lo ricrea
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>nul
    if not errorlevel 1 goto installa
    echo L'ambiente virtuale esistente non funziona: lo ricreo.
    rmdir /s /q .venv
)

REM cerca un Python 3.10 o successivo: prima il lanciatore "py" (installato da
REM python.org anche senza PATH), poi "python" e "python3". La prova esegue davvero
REM Python, cosi' scarta il finto python.exe del Microsoft Store.
set "PY="
call :prova py -3
if not defined PY call :prova python
if not defined PY call :prova python3
if not defined PY (
    echo Non trovo Python 3.10 o successivo.
    echo Installalo da https://www.python.org/downloads/ spuntando "Add python.exe to PATH",
    echo poi chiudi questa finestra e rilancia installa.bat. Vedi il README per i dettagli.
    pause
    exit /b 1
)
echo Uso Python: %PY%
%PY% -m venv .venv
if errorlevel 1 (
    echo.
    echo Creazione dell'ambiente virtuale non riuscita ^(vedi sopra^).
    pause
    exit /b 1
)

:installa
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -e .
if errorlevel 1 (
    echo.
    echo Installazione non riuscita: pip ha restituito un errore ^(vedi sopra^).
    pause
    exit /b 1
)
if not exist .venv\Scripts\quizesame.exe (
    echo.
    echo Installazione incompleta: manca .venv\Scripts\quizesame.exe ^(vedi i messaggi sopra^).
    pause
    exit /b 1
)

echo.
echo Installazione completata. Per avviare l'app esegui: esamix.bat
pause
exit /b 0

:prova
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
