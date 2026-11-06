"""Backup di un singolo appello: tutti i suoi dati (impostazioni, esercizi assegnati,
blocchi e compiti, iscritti, presenze, aule, risultati, bozze, revisioni...) in un file
JSON da scaricare, e il ripristino dello stesso file nello stesso appello, che ne
sostituisce i dati (dopo una conferma esplicita). I file generati (PDF, .tex) non ci sono:
si rigenerano. Gli studenti coinvolti viaggiano col backup e, se mancano nel corso, si
riaggiungono; gli esercizi devono essere ancora nella banca del corso."""
import base64
import json
from datetime import datetime

from quizesame import config, db
from quizesame.services import corsi as corsi_service

FORMATO = "esamix-appello"
VERSIONE = 1

# tabelle con una colonna appello_id, nell'ordine in cui si reinseriscono (prima quelle a
# cui le altre fanno riferimento); si cancellano nell'ordine inverso
TABELLE = [
    "appello_esercizi", "appello_revisione_esercizi", "blocchi", "compiti", "appello_aule",
    "aula_assegnazioni", "ammissioni_manuali", "appello_segreteria_csv", "appello_moodle_file",
    "appello_iscritti_manuali", "appello_iscritti_rimossi", "appello_eccezioni_matricola",
    "appello_presenze", "esercizio_sospetti", "risultati", "bozze_correzione",
]


def _valore(v):
    """I contenuti binari (file caricati) in base64, per stare nel JSON."""
    if isinstance(v, (bytes, bytearray)):
        return {"__base64__": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def _da_valore(v):
    if isinstance(v, dict) and "__base64__" in v:
        return base64.b64decode(v["__base64__"])
    return v


def _colonne(conn, tabella: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({tabella})")]


def crea_backup(tag: str, appello_id: int) -> tuple[str, bytes]:
    """(nome del file, contenuto JSON) con tutti i dati dell'appello."""
    appello = corsi_service.get_appello(tag, appello_id)
    if appello is None:
        raise ValueError("Appello non trovato")
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        dati = {"appelli": [dict(r) for r in conn.execute("SELECT * FROM appelli WHERE id=?", (appello_id,))]}
        for t in TABELLE:
            dati[t] = [{k: _valore(v) for k, v in dict(r).items()}
                       for r in conn.execute(f"SELECT * FROM {t} WHERE appello_id=?", (appello_id,))]
        dati["compito_esercizi"] = [dict(r) for r in conn.execute(
            "SELECT ce.* FROM compito_esercizi ce JOIN compiti c ON c.id = ce.compito_id WHERE c.appello_id=?",
            (appello_id,))]
        matricole = sorted({r["matricola"] for t in TABELLE for r in dati[t] if r.get("matricola")})
        dati["studenti"] = [dict(r) for r in conn.execute(
            f"SELECT * FROM studenti WHERE matricola IN ({','.join('?' * len(matricole))})", matricole)] if matricole else []
    finally:
        conn.close()
    contenuto = {
        "formato": FORMATO, "versione": VERSIONE, "corso": tag, "appello_id": appello_id,
        "appello_nome": appello.nome, "creato_il": datetime.now().isoformat(timespec="seconds"), "tabelle": dati,
    }
    nome = f"backup-{tag}-{appello.slug}-{datetime.now().strftime('%Y-%m-%d')}.json"
    return nome, json.dumps(contenuto, ensure_ascii=False, indent=1).encode("utf-8")


def leggi_backup(tag: str, appello_id: int, contenuto: bytes) -> dict:
    """Controlla che il file sia un backup di questo appello e ne riassume il contenuto
    (per la pagina di conferma), confrontato con i dati attuali."""
    try:
        b = json.loads(contenuto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Il file non è un backup di un appello (JSON non leggibile)")
    if not isinstance(b, dict) or b.get("formato") != FORMATO:
        raise ValueError("Il file non è un backup di un appello di EsaMiX")
    if b.get("corso") != tag or b.get("appello_id") != appello_id:
        raise ValueError(f"Il backup è di un altro appello ({b.get('appello_nome')}, corso {b.get('corso')}): "
                         "si può reinserire solo nell'appello da cui è stato scaricato")
    t = b["tabelle"]
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        attuali = {
            "risultati": conn.execute("SELECT COUNT(*) FROM risultati WHERE appello_id=?", (appello_id,)).fetchone()[0],
            "compiti": conn.execute("SELECT COUNT(*) FROM compiti WHERE appello_id=?", (appello_id,)).fetchone()[0],
            "bozze": conn.execute("SELECT COUNT(*) FROM bozze_correzione WHERE appello_id=?", (appello_id,)).fetchone()[0],
        }
        esercizi = {r[0] for r in conn.execute("SELECT id FROM esercizi")}
    finally:
        conn.close()
    richiesti = {r["esercizio_id"] for r in t.get("appello_esercizi", []) + t.get("compito_esercizi", [])}
    mancanti = sorted(richiesti - esercizi)
    return {
        "appello_nome": b.get("appello_nome"), "creato_il": b.get("creato_il"),
        "nel_backup": {"risultati": len(t.get("risultati", [])), "compiti": len(t.get("compiti", [])),
                       "bozze": len(t.get("bozze_correzione", [])), "esercizi": len(t.get("appello_esercizi", [])),
                       "presenze": len(t.get("appello_presenze", []))},
        "attuali": attuali, "esercizi_mancanti": mancanti,
    }


def ripristina(tag: str, appello_id: int, contenuto: bytes) -> None:
    """Sostituisce tutti i dati dell'appello con quelli del backup, in un'unica
    transazione: se qualcosa va storto non cambia niente."""
    riepilogo = leggi_backup(tag, appello_id, contenuto)
    if riepilogo["esercizi_mancanti"]:
        raise ValueError("Mancano nella banca del corso gli esercizi " + ", ".join(map(str, riepilogo["esercizi_mancanti"]))
                         + ": senza, i compiti del backup non si possono ricostruire")
    t = json.loads(contenuto.decode("utf-8"))["tabelle"]
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("BEGIN")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        # studenti che non ci sono più nel corso: si riaggiungono (quelli presenti restano come sono)
        for s in t.get("studenti", []):
            colonne = [c for c in _colonne(conn, "studenti") if c in s]
            conn.execute(f"INSERT OR IGNORE INTO studenti ({','.join(colonne)}) VALUES ({','.join('?' * len(colonne))})",
                         [s[c] for c in colonne])
        # via i dati attuali dell'appello (le righe dei compiti passano dai compiti)
        conn.execute("DELETE FROM compito_esercizi WHERE compito_id IN (SELECT id FROM compiti WHERE appello_id=?)",
                     (appello_id,))
        for tabella in reversed(TABELLE):
            conn.execute(f"DELETE FROM {tabella} WHERE appello_id=?", (appello_id,))
        # l'appello stesso: si aggiornano le sue impostazioni
        a = (t.get("appelli") or [None])[0]
        if a:
            colonne = [c for c in _colonne(conn, "appelli") if c in a and c != "id"]
            conn.execute(f"UPDATE appelli SET {', '.join(c + '=?' for c in colonne)} WHERE id=?",
                         [a[c] for c in colonne] + [appello_id])
        for tabella in TABELLE[:4] + ["compito_esercizi"] + TABELLE[4:]:
            righe = t.get(tabella, [])
            if not righe:
                continue
            esistenti = _colonne(conn, tabella)
            for r in righe:
                colonne = [c for c in esistenti if c in r]
                conn.execute(f"INSERT INTO {tabella} ({','.join(colonne)}) VALUES ({','.join('?' * len(colonne))})",
                             [_da_valore(r[c]) for c in colonne])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
