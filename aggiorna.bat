@echo off
REM Aggiornamento di EsaMiX all'ultima versione disponibile su GitHub: scarica il codice
REM nuovo con git e reinstalla le eventuali dipendenze cambiate, senza toccare corsi\ ne
REM altri dati locali (non tracciati da git).
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Git non risulta installato. Vedi il README per le istruzioni di installazione.
    pause
    exit /b 1
)

REM solo i file del programma modificati bloccano l'aggiornamento: i file nuovi non
REM tracciati (dati dei corsi, registro, file temporanei) non c'entrano
set MODIFICHE=
for /f "delims=" %%i in ('git status --porcelain --untracked-files=no') do set MODIFICHE=1
if defined MODIFICHE (
    echo Ci sono modifiche non salvate in alcuni file del programma ^(non i tuoi corsi^):
    git status --short --untracked-files=no
    echo.
    echo Aggiornamento annullato per non perderle. Se non ti servono, puoi scartarle con:
    echo   git checkout -- .
    echo e poi rilanciare questo script.
    pause
    exit /b 1
)

echo Scarico l'ultima versione...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Aggiornamento non riuscito: git pull ha restituito un errore ^(vedi sopra^).
    echo Se dice che i rami sono divergenti, la copia locale ha modifiche salvate con git che
    echo non sono su GitHub: chiedi aiuto prima di procedere, per non perderle.
    pause
    exit /b 1
)

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
    pip install -e . --upgrade
    if errorlevel 1 (
        echo.
        echo Aggiornamento non riuscito: la reinstallazione delle dipendenze ha dato un errore ^(vedi sopra^).
        pause
        exit /b 1
    )
) else (
    echo Ambiente virtuale non trovato: esegui prima installa.bat
    pause
    exit /b 1
)

echo.
echo Aggiornamento completato. Se EsaMiX era aperto, chiudilo e riavvialo con: esamix.bat
pause
