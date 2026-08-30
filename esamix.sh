#!/usr/bin/env bash
# Avvio di EsaMiX su Linux o macOS (dopo aver eseguito ./installa.sh almeno una volta).
set -e
cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then
    echo "Ambiente virtuale non trovato: esegui prima ./installa.sh"
    exit 1
fi

source .venv/bin/activate
quizesame
