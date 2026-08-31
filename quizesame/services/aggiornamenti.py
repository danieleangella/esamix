"""Controllo (best-effort, non bloccante) se è disponibile un aggiornamento del codice
su GitHub: usato per l'avviso in homepage. Non deve mai far fallire la homepage — se git
non è installato, la cartella non è un repository, non c'è connessione, o qualunque altro
errore, il controllo fallisce silenziosamente e l'avviso semplicemente non compare."""
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parent.parent.parent
INTERVALLO_CONTROLLO = timedelta(hours=6)

_cache: dict = {"controllato_il": None, "disponibile": False}


def _git(*args: str, timeout: float = 4.0) -> str:
    risultato = subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=True, text=True, timeout=timeout, check=True,
    )
    return risultato.stdout.strip()


def aggiornamento_disponibile() -> bool:
    """True se il ramo main locale è indietro rispetto a origin/main. Il risultato è
    tenuto in cache per qualche ora, per non fare un git fetch (accesso di rete) a ogni
    caricamento della homepage."""
    ora = datetime.now()
    if _cache["controllato_il"] is not None and ora - _cache["controllato_il"] < INTERVALLO_CONTROLLO:
        return _cache["disponibile"]
    disponibile, _ = _controlla()
    return disponibile


def _controlla() -> tuple[bool, Optional[str]]:
    """Esegue il controllo (git fetch + confronto con origin/main) e aggiorna la cache.
    Restituisce (disponibile, errore): errore è None se il controllo è andato a buon
    fine, altrimenti un messaggio leggibile sul perché non è stato possibile controllare."""
    disponibile = False
    errore = None
    try:
        _git("fetch", "--quiet")
        dietro = int(_git("rev-list", "--count", "HEAD..origin/main") or "0")
        disponibile = dietro > 0
    except FileNotFoundError:
        errore = "git non è installato o non è nel PATH"
    except subprocess.CalledProcessError as e:
        errore = (e.stderr or str(e)).strip()
    except subprocess.TimeoutExpired:
        errore = "timeout durante il controllo (problema di connessione?)"
    except Exception as e:
        errore = str(e)
    _cache["controllato_il"] = datetime.now()
    _cache["disponibile"] = disponibile
    return disponibile, errore


def forza_controllo() -> tuple[bool, Optional[str]]:
    """Come aggiornamento_disponibile, ma ignora la cache: usato dal bottone "Controlla
    aggiornamenti" nelle impostazioni, dove l'utente si aspetta un controllo immediato."""
    return _controlla()
