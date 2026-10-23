#!/usr/bin/env bash
# Avvio di EsaMiX su Linux o macOS (dopo aver eseguito ./installa.sh almeno una volta).
set -e
cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then
    echo "Ambiente virtuale non trovato: esegui prima ./installa.sh"
    exit 1
fi

# se il Python di sistema è stato aggiornato (es. 3.12 → 3.14) l'ambiente virtuale non vede più
# i pacchetti installati con la versione precedente: lo ricreo da capo
venv_ver=$(sed -n 's/^version *= *\([0-9]*\.[0-9]*\).*/\1/p' .venv/pyvenv.cfg 2>/dev/null)
py_ver=$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
if [ "$venv_ver" != "$py_ver" ]; then
    echo "Python è cambiato (${venv_ver:-?} → ${py_ver:-non trovato}): ricreo l'ambiente virtuale..."
    rm -rf .venv
    ./installa.sh
fi

source .venv/bin/activate
quizesame
