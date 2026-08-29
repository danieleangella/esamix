"""Banco esercizi. Gli esercizi vivono nel database del corso (tabelle esercizi /
esercizio_varianti / appello_esercizi): si creano dalla pagina di un appello (e restano
nella banca del corso, riusabili su altri appelli) oppure si importano una tantum da una
cartella del vecchio formato a file Python (legacy/esami.py, testi.py + testoN.py)."""
import importlib.util
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from quizesame import config, db


@dataclass
class Variante:
    id: Optional[int]
    testo: str
    risposte: list[str]  # risposte[0] = corretta, il resto = sbagliate


@dataclass
class Esercizio:
    id: int
    nome: Optional[str]
    note: Optional[str]
    argomento: Optional[str] = None
    varianti: list[Variante] = field(default_factory=list)
    assegnato_il: Optional[str] = None  # valorizzato solo da list_esercizi_appello
    obbligatorio: bool = False  # valorizzato solo da list_esercizi_appello


def _row_to_esercizio(conn, row, assegnato_il: Optional[str] = None, obbligatorio: bool = False) -> Esercizio:
    varianti_rows = conn.execute(
        "SELECT id, testo, risposte_json FROM esercizio_varianti WHERE esercizio_id=? ORDER BY id",
        (row["id"],),
    ).fetchall()
    varianti = [Variante(id=v["id"], testo=v["testo"], risposte=json.loads(v["risposte_json"])) for v in varianti_rows]
    return Esercizio(
        id=row["id"], nome=row["nome"], note=row["note"], argomento=row["argomento"], varianti=varianti,
        assegnato_il=assegnato_il, obbligatorio=obbligatorio,
    )


def _varianti_di(conn, esercizio_ids: list[int]) -> dict[int, list[Variante]]:
    """Carica le varianti di più esercizi in un'unica query (invece di una per esercizio):
    con una banca di centinaia di esercizi, una query per esercizio rende la pagina lenta."""
    if not esercizio_ids:
        return {}
    segnaposto = ",".join("?" * len(esercizio_ids))
    rows = conn.execute(
        f"SELECT esercizio_id, id, testo, risposte_json FROM esercizio_varianti "
        f"WHERE esercizio_id IN ({segnaposto}) ORDER BY esercizio_id, id",
        esercizio_ids,
    ).fetchall()
    per_esercizio: dict[int, list[Variante]] = {eid: [] for eid in esercizio_ids}
    for r in rows:
        per_esercizio[r["esercizio_id"]].append(
            Variante(id=r["id"], testo=r["testo"], risposte=json.loads(r["risposte_json"]))
        )
    return per_esercizio


def list_esercizi(tag: str) -> list[Esercizio]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute("SELECT * FROM esercizi ORDER BY id").fetchall()
        varianti = _varianti_di(conn, [r["id"] for r in rows])
        return [
            Esercizio(id=r["id"], nome=r["nome"], note=r["note"], argomento=r["argomento"], varianti=varianti[r["id"]])
            for r in rows
        ]
    finally:
        conn.close()


def list_argomenti(tag: str) -> list[str]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT DISTINCT argomento FROM esercizi WHERE argomento IS NOT NULL AND argomento != '' ORDER BY argomento"
        ).fetchall()
        return [r["argomento"] for r in rows]
    finally:
        conn.close()


def get_esercizio(tag: str, esercizio_id: int) -> Optional[Esercizio]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute("SELECT * FROM esercizi WHERE id=?", (esercizio_id,)).fetchone()
        return _row_to_esercizio(conn, row) if row else None
    finally:
        conn.close()


def appelli_che_usano(tag: str, esercizio_id: int) -> list[int]:
    """Appelli a cui questo esercizio della banca è assegnato: modificarne testo/varianti
    può richiedere di rigenerare i blocchi già generati su ciascuno di essi."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT appello_id FROM appello_esercizi WHERE esercizio_id=?", (esercizio_id,)
        ).fetchall()
        return [r["appello_id"] for r in rows]
    finally:
        conn.close()


def aggiorna_esercizio(
    tag: str, esercizio_id: int, nome: str, note: str, varianti: list[dict], argomento: str = "",
) -> Esercizio:
    """Sostituisce nome/note/argomento e tutte le varianti di un esercizio della banca già
    esistente. Le vecchie varianti vengono cancellate e ricreate: i loro id cambiano, quindi
    va chiamata solo dopo aver protetto/rigenerato gli appelli che lo usano (vedi
    appelli_che_usano) se hanno già blocchi di compiti generati."""
    varianti = _valida_varianti(varianti)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "UPDATE esercizi SET nome=?, argomento=?, note=? WHERE id=?",
            (nome or None, argomento.strip() or None, note or None, esercizio_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Esercizio #{esercizio_id} non trovato")
        conn.execute("DELETE FROM esercizio_varianti WHERE esercizio_id=?", (esercizio_id,))
        for v in varianti:
            conn.execute(
                "INSERT INTO esercizio_varianti (esercizio_id, testo, risposte_json) VALUES (?,?,?)",
                (esercizio_id, v["testo"].strip(), json.dumps(v["risposte"])),
            )
        conn.commit()
    finally:
        conn.close()
    return get_esercizio(tag, esercizio_id)


def _valida_varianti(varianti: list[dict]) -> list[dict]:
    varianti = [v for v in varianti if v.get("testo", "").strip()]
    if not varianti:
        raise ValueError("Serve almeno una variante con un testo")
    for v in varianti:
        risposte = [r for r in v.get("risposte", []) if r and r.strip()]
        if len(risposte) < 2:
            raise ValueError("Ogni variante richiede la risposta corretta e almeno una sbagliata")
        v["risposte"] = risposte
    return varianti


def create_esercizio(tag: str, nome: str, note: str, varianti: list[dict], argomento: str = "") -> Esercizio:
    """varianti: [{"testo": ..., "risposte": [corretta, sbagliata1, ...]}, ...]"""
    varianti = _valida_varianti(varianti)

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "INSERT INTO esercizi (nome, argomento, note) VALUES (?, ?, ?)",
            (nome or None, argomento.strip() or None, note or None),
        )
        esercizio_id = cur.lastrowid
        for v in varianti:
            conn.execute(
                "INSERT INTO esercizio_varianti (esercizio_id, testo, risposte_json) VALUES (?,?,?)",
                (esercizio_id, v["testo"].strip(), json.dumps(v["risposte"])),
            )
        conn.commit()
    finally:
        conn.close()
    return get_esercizio(tag, esercizio_id)


def elimina_esercizio(tag: str, esercizio_id: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        in_uso = conn.execute(
            "SELECT count(*) FROM appello_esercizi WHERE esercizio_id=?", (esercizio_id,)
        ).fetchone()[0]
        if in_uso:
            raise ValueError("Questo esercizio è assegnato a un appello: rimuovilo prima dall'appello")
        conn.execute("DELETE FROM esercizio_varianti WHERE esercizio_id=?", (esercizio_id,))
        conn.execute("DELETE FROM esercizi WHERE id=?", (esercizio_id,))
        conn.commit()
    finally:
        conn.close()


def list_esercizi_appello(tag: str, appello_id: int) -> list[Esercizio]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT e.*, ae.data_assegnazione, ae.obbligatorio FROM appello_esercizi ae "
            "JOIN esercizi e ON e.id = ae.esercizio_id "
            "WHERE ae.appello_id=? ORDER BY ae.ordine, ae.rowid",
            (appello_id,),
        ).fetchall()
        varianti = _varianti_di(conn, [r["id"] for r in rows])
        return [
            Esercizio(
                id=r["id"], nome=r["nome"], note=r["note"], argomento=r["argomento"], varianti=varianti[r["id"]],
                assegnato_il=r["data_assegnazione"], obbligatorio=bool(r["obbligatorio"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def assegna_a_appello(tag: str, appello_id: int, esercizio_id: int, obbligatorio: bool = False) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        ordine = conn.execute(
            "SELECT COALESCE(MAX(ordine), 0) + 1 FROM appello_esercizi WHERE appello_id=?", (appello_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO appello_esercizi (appello_id, esercizio_id, ordine, obbligatorio) VALUES (?,?,?,?)",
            (appello_id, esercizio_id, ordine, obbligatorio),
        )
        conn.commit()
    finally:
        conn.close()


def sposta_esercizio(tag: str, appello_id: int, esercizio_id: int, delta: int) -> None:
    """Sposta un esercizio assegnato su (delta=-1) o giù (delta=+1) nell'ordine di
    visualizzazione/stampa. L'ordine non incide sul compito dello studente (gli esercizi
    lì vengono comunque rimescolati), solo sull'elenco e sul foglio di riferimento."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        righe = conn.execute(
            "SELECT esercizio_id FROM appello_esercizi WHERE appello_id=? ORDER BY ordine, rowid",
            (appello_id,),
        ).fetchall()
        ids = [r["esercizio_id"] for r in righe]
        if esercizio_id not in ids:
            return
        idx = ids.index(esercizio_id)
        nuovo_idx = idx + delta
        if 0 <= nuovo_idx < len(ids):
            ids[idx], ids[nuovo_idx] = ids[nuovo_idx], ids[idx]
        for posizione, eid in enumerate(ids):
            conn.execute(
                "UPDATE appello_esercizi SET ordine=? WHERE appello_id=? AND esercizio_id=?",
                (posizione, appello_id, eid),
            )
        conn.commit()
    finally:
        conn.close()


def imposta_obbligatorio(tag: str, appello_id: int, esercizio_id: int, obbligatorio: bool) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "UPDATE appello_esercizi SET obbligatorio=? WHERE appello_id=? AND esercizio_id=?",
            (obbligatorio, appello_id, esercizio_id),
        )
        conn.commit()
    finally:
        conn.close()


def rimuovi_da_appello(tag: str, appello_id: int, esercizio_id: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "DELETE FROM appello_esercizi WHERE appello_id=? AND esercizio_id=?", (appello_id, esercizio_id)
        )
        conn.commit()
    finally:
        conn.close()


def crea_e_assegna(
    tag: str, appello_id: int, nome: str, note: str, varianti: list[dict],
    obbligatorio: bool = False, argomento: str = "",
) -> Esercizio:
    esercizio = create_esercizio(tag, nome, note, varianti, argomento=argomento)
    assegna_a_appello(tag, appello_id, esercizio.id, obbligatorio=obbligatorio)
    return esercizio


# --- Esportazione/importazione in JSON, per condividere gli esercizi di un appello con
# altri colleghi (file autonomo, senza riferimenti agli id di questo database) ----------

def esporta_esercizi_appello(tag: str, appello_id: int) -> dict:
    esercizi = list_esercizi_appello(tag, appello_id)
    return {
        "esercizi": [
            {
                "nome": e.nome, "argomento": e.argomento, "note": e.note, "obbligatorio": e.obbligatorio,
                "varianti": [{"testo": v.testo, "risposte": list(v.risposte)} for v in e.varianti],
            }
            for e in esercizi
        ],
    }


def importa_json(tag: str, appello_id: int, dati: dict) -> int:
    """Importa gli esercizi esportati con esporta_esercizi_appello (da un altro corso, o
    condivisi da un collega): li crea nella banca di questo corso e li assegna subito a
    questo appello."""
    esercizi = dati.get("esercizi")
    if not isinstance(esercizi, list):
        raise ValueError("File non valido: manca la lista 'esercizi'")
    n = 0
    for e in esercizi:
        crea_e_assegna(
            tag, appello_id, nome=e.get("nome") or "", note=e.get("note") or "",
            varianti=e.get("varianti") or [], obbligatorio=bool(e.get("obbligatorio")),
            argomento=e.get("argomento") or "",
        )
        n += 1
    return n


# --- Import una tantum dal vecchio formato a file Python (legacy/esami.py) ------------

def carica_esercizi_da_cartella(esercizi_dir: str):
    """Carica il modulo testi.py di una cartella esercizi (vecchio formato) con un nome
    di modulo univoco, per evitare collisioni nella cache di sys.modules quando più
    cartelle vengono lette nello stesso processo server."""
    path = Path(esercizi_dir) / "testi.py"
    if not path.exists():
        raise FileNotFoundError(f"File testi.py non trovato in {esercizi_dir}")
    module_name = f"quizesame_esercizi_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(esercizi_dir).resolve()))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(Path(esercizi_dir).resolve()))
        except ValueError:
            pass
    return module.esercizi


def importa_da_altro_corso(tag: str, tag_sorgente: str, esercizio_ids: list[int]) -> int:
    """Copia esercizi (con le loro varianti) dalla banca di un altro corso in quella del
    corso corrente. Ogni corso resta un database indipendente e autosufficiente: 'collegare'
    due banche significa importare una copia degli esercizi scelti, non condividerli dal
    vivo — le modifiche successive su una copia non si propagano all'altra."""
    n = 0
    for esercizio_id in esercizio_ids:
        esercizio = get_esercizio(tag_sorgente, esercizio_id)
        if esercizio is None:
            continue
        varianti = [{"testo": v.testo, "risposte": list(v.risposte)} for v in esercizio.varianti]
        create_esercizio(
            tag, nome=esercizio.nome, note=f"Importato dal corso '{tag_sorgente}'", varianti=varianti,
            argomento=esercizio.argomento or "",
        )
        n += 1
    return n


def importa_da_cartella_legacy(tag: str, esercizi_dir: str, appello_id: Optional[int] = None) -> int:
    """Importa gli esercizi di una cartella nel vecchio formato (testi.py/testoN.py) nella
    banca esercizi del corso, opzionalmente assegnandoli subito a un appello."""
    esercizi_legacy = carica_esercizi_da_cartella(esercizi_dir)
    n = 0
    for varianti_legacy in esercizi_legacy:
        varianti = [{"testo": testo, "risposte": list(risposte)} for testo, risposte in varianti_legacy]
        esercizio = create_esercizio(tag, nome=None, note=f"Importato da {esercizi_dir}", varianti=varianti)
        if appello_id is not None:
            assegna_a_appello(tag, appello_id, esercizio.id)
        n += 1
    return n
