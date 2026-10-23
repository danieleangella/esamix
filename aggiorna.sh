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

# solo i file del programma modificati bloccano l'aggiornamento: i file nuovi non tracciati
# (dati dei corsi, registro, file temporanei, copie in conflitto di Dropbox) non c'entrano
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Ci sono modifiche non salvate in alcuni file del programma (non i tuoi corsi):"
    git status --short --untracked-files=no
    echo ""
    echo "Aggiornamento annullato per non perderle. Se non ti servono, puoi scartarle con:"
    echo "  git checkout -- ."
    echo "e poi rilanciare questo script."
    exit 1
fi

echo "Scarico l'ultima versione..."
if ! git pull --ff-only; then
    echo ""
    echo "Aggiornamento non riuscito: git pull ha restituito un errore (vedi sopra)."
    echo "Se dice che i rami sono divergenti, la copia locale ha modifiche salvate con git che"
    echo "non sono su GitHub: chiedi aiuto prima di procedere, per non perderle."
    exit 1
fi

if [ -f .venv/bin/activate ]; then
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
    if ! pip install -e . --upgrade; then
        echo ""
        echo "Aggiornamento non riuscito: la reinstallazione delle dipendenze ha dato un errore (vedi sopra)."
        exit 1
    fi
else
    echo "Ambiente virtuale non trovato: esegui prima ./installa.sh"
    exit 1
fi

echo ""
echo "Aggiornamento completato. Se EsaMiX era aperto, chiudilo (Ctrl+C) e riavvialo con: ./esamix.sh"
