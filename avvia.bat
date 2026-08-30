@echo off
REM Avvio di EsaMiX su Windows (dopo aver eseguito installa.bat almeno una volta).
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo Ambiente virtuale non trovato: esegui prima installa.bat
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
quizesame
pause
