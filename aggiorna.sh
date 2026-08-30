#!/usr/bin/env bash
# Aggiornamento di EsaMiX all'ultima versione disponibile su GitHub: scarica il codice
# nuovo con git e reinstalla le eventuali dipendenze cambiate, senza toccare corsi/ né
# altri dati locali (non tracciati da git).
set -e
cd "$(dirname "$0")"

if ! command -v git >/dev/null 2>&1; then
    echo "Git non risulta installato. Vedi il README per le istruzioni di installazione."
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Ci sono modifiche non salvate nella cartella del programma (file del codice, non i tuoi corsi):"
    git status --short
    echo ""
    echo "Aggiornamento annullato per non perderle. Se non ti servono, puoi scartarle con:"
    echo "  git checkout -- ."
    echo "e poi rilanciare questo script."
    exit 1
fi

echo "Scarico l'ultima versione..."
git pull

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
    pip install -e . --upgrade
else
    echo "Ambiente virtuale non trovato: esegui prima ./installa.sh"
    exit 1
fi

echo ""
echo "Aggiornamento completato. Per avviare l'app esegui: ./esamix.sh"
