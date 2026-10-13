"""Registro delle azioni svolte nell'app e degli aggiornamenti installati: un file
`registro.jsonl` accanto alle cartelle dei corsi, una riga JSON per evento, solo in
aggiunta (mai riscritto). Le azioni vengono registrate in automatico dal middleware in
main.py (ogni modulo inviato, con il messaggio di esito mostrato al docente); gli
aggiornamenti all'avvio, confrontando versione e commit con quelli dell'avvio precedente."""
import json
import threading
from datetime import datetime
from typing import Optional

from quizesame import config

PATH = config.DATA_ROOT / "registro.jsonl"
_lock = threading.Lock()


def registra(tipo: str, azione: str, esito: str = "", tag: str = "", appello_id: Optional[int] = None,
             esercizio_id: Optional[int] = None, gravita: str = "success", **extra) -> None:
    """`tipo`: "azione" | "aggiornamento" | "ia"; `gravita`: success | warning | error.
    Non deve mai far fallire l'operazione che sta registrando."""
    voce = {
        "quando": datetime.now().isoformat(timespec="seconds"), "tipo": tipo, "azione": azione,
        "esito": esito, "tag": tag, "appello_id": appello_id, "esercizio_id": esercizio_id,
        "gravita": gravita, **extra,
    }
    try:
        with _lock:
            config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
            with PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(voce, ensure_ascii=False) + "\n")
    except OSError:
        pass


def leggi(limite: int = 2000) -> list[dict]:
    """Le voci più recenti per prime."""
    if not PATH.exists():
        return []
    try:
        righe = PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    voci = []
    for riga in reversed(righe[-limite:]):
        try:
            voci.append(json.loads(riga))
        except json.JSONDecodeError:
            continue
    return voci


def registra_avvio(versione: Optional[str], commit: Optional[str], novita: Optional[list[str]] = None) -> None:
    """Registra un aggiornamento se versione o commit sono cambiati rispetto all'ultimo
    avvio registrato (non registra ogni avvio, per non riempire il registro)."""
    precedente = next((v for v in leggi(5000) if v.get("tipo") == "aggiornamento"), None)
    if precedente and precedente.get("versione") == versione and precedente.get("commit") == commit:
        return
    if precedente is None:
        azione = f"Prima registrazione: versione {versione or '?'}"
    elif precedente.get("versione") != versione:
        azione = f"Aggiornamento dalla versione {precedente.get('versione') or '?'} alla {versione or '?'}"
    else:
        azione = f"Aggiornamento del codice (versione {versione or '?'})"
    registra("aggiornamento", azione, esito="; ".join(novita or []), versione=versione, commit=commit,
             commit_precedente=(precedente or {}).get("commit"))


def commit_corrente() -> Optional[str]:
    from quizesame.services import aggiornamenti
    try:
        return aggiornamenti._git("rev-parse", "--short", "HEAD") or None
    except Exception:
        return None


def novita_tra(da: Optional[str], a: Optional[str]) -> list[str]:
    """I titoli dei commit tra due versioni del codice (le novità dell'aggiornamento)."""
    if not da or not a or da == a:
        return []
    from quizesame.services import aggiornamenti
    try:
        return [r for r in aggiornamenti._git("log", "--format=%s", f"{da}..{a}").splitlines() if r][:30]
    except Exception:
        return []
