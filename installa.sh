#!/usr/bin/env bash
# Installazione di EsaMiX su Linux o macOS: crea l'ambiente virtuale Python e installa
# l'app al suo interno, senza toccare nulla al di fuori di questa cartella.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 non risulta installato. Vedi il README per le istruzioni di installazione."
    exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

echo ""
echo "Installazione completata. Per avviare l'app esegui: ./avvia.sh"
