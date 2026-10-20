@echo off
REM Avvio di EsaMiX su Windows (dopo aver eseguito installa.bat almeno una volta).
REM Lancia il programma dell'ambiente virtuale per percorso, senza "activate".
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Ambiente virtuale non trovato: esegui prima installa.bat
    pause
    exit /b 1
)
if not exist .venv\Scripts\quizesame.exe (
    echo Installazione incompleta: esegui di nuovo installa.bat
    pause
    exit /b 1
)

.venv\Scripts\quizesame.exe
pause
