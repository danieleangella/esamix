"""Verifiche con l'IA salvate: ogni verifica completata resta in un file JSON nella
cartella `verifiche_ia` del corso (quindi anche nei backup), per poterla rivedere in
seguito con tutti i commenti del modello, non solo con l'esito per esercizio salvato
nel database."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from quizesame import config

_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def _cartella(tag: str) -> Path:
    return config.corso_dir(tag) / "verifiche_ia"


def _percorso(tag: str, verifica_id: str) -> Optional[Path]:
    if not _ID_RE.match(verifica_id or ""):
        return None
    return _cartella(tag) / f"{verifica_id}.json"


def salva(tag: str, ambito: dict, risultati: list[dict], conf: dict, conteggi: dict) -> str:
    """Salva una verifica appena fatta; ritorna il suo identificativo. Di ogni esercizio
    tiene solo id, nome e tipo (l'esercizio può essere modificato o eliminato in seguito:
    la verifica salvata mostra com'era al momento)."""
    cartella = _cartella(tag)
    cartella.mkdir(parents=True, exist_ok=True)
    adesso = datetime.now()
    base = adesso.strftime("%Y%m%d-%H%M%S")
    verifica_id, n = base, 1
    while (cartella / f"{verifica_id}.json").exists():
        n += 1
        verifica_id = f"{base}-{n}"
    dati = {
        "id": verifica_id, "quando": adesso.isoformat(timespec="seconds"),
        "titolo": ambito["titolo"], "contesto": ambito["contesto"],
        "appello_id": ambito["appello"].id if ambito.get("appello") else None,
        "url": ambito["url"], "torna": ambito["torna"], "torna_etichetta": ambito["torna_etichetta"],
        "conf": {"nome": conf.get("nome", ""), "modello": conf.get("modello", "")}, "conteggi": conteggi,
        "risultati": [
            {**{k: v for k, v in r.items() if k != "esercizio"},
             "esercizio": {"id": r["esercizio"].id, "nome": r["esercizio"].nome, "aperta": bool(r["esercizio"].aperta)}}
            for r in risultati
        ],
    }
    (cartella / f"{verifica_id}.json").write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    return verifica_id


def carica(tag: str, verifica_id: str) -> Optional[dict]:
    p = _percorso(tag, verifica_id)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def contenuto_file(tag: str, verifica_id: str) -> Optional[bytes]:
    p = _percorso(tag, verifica_id)
    return p.read_bytes() if p is not None and p.exists() else None


def elenco(tag: str, appello_id: Optional[int] = None, esercizio_id: Optional[int] = None) -> list[dict]:
    """Le verifiche salvate del corso, dalla più recente, in forma riassunta (senza i
    risultati). Con `appello_id` solo quelle fatte sugli esercizi di quell'appello, con
    `esercizio_id` solo quelle che comprendono quell'esercizio."""
    cartella = _cartella(tag)
    if not cartella.exists():
        return []
    voci = []
    for p in sorted(cartella.glob("*.json"), reverse=True):
        if not _ID_RE.match(p.stem):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if appello_id is not None and d.get("appello_id") != appello_id:
            continue
        if esercizio_id is not None and not any(r["esercizio"]["id"] == esercizio_id for r in d.get("risultati", [])):
            continue
        voci.append({k: d.get(k) for k in ("id", "quando", "titolo", "appello_id", "conf", "conteggi")}
                    | {"n_esercizi": len(d.get("risultati", []))})
    return voci


def ultima(tag: str, appello_id: int) -> Optional[dict]:
    voci = elenco(tag, appello_id=appello_id)
    return voci[0] if voci else None


def elimina(tag: str, verifica_id: str) -> bool:
    p = _percorso(tag, verifica_id)
    if p is None or not p.exists():
        return False
    p.unlink()
    return True
