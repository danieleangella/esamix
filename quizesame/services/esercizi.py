"""Banco esercizi. Gli esercizi vivono nel database del corso (tabelle esercizi /
esercizio_varianti / appello_esercizi): si creano dalla pagina di un appello (e restano
nella banca del corso, riusabili su altri appelli) oppure si importano una tantum da una
cartella del vecchio formato a file Python (legacy/esami.py, testi.py + testoN.py)."""
import importlib.util
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service


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
    difficolta: Optional[int] = None  # 1-3 stelline, opzionale
    soluzione: Optional[str] = None  # se non vuota, va nel foglio di riferimento
    aperta: bool = False  # domanda aperta: nessuna risposta a scelta multipla, punteggio manuale
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
        id=row["id"], nome=row["nome"], note=row["note"], argomento=row["argomento"],
        difficolta=row["difficolta"], soluzione=row["soluzione"], aperta=bool(row["aperta"]),
        varianti=varianti, assegnato_il=assegnato_il, obbligatorio=obbligatorio,
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
            Esercizio(
                id=r["id"], nome=r["nome"], note=r["note"], argomento=r["argomento"],
                difficolta=r["difficolta"], soluzione=r["soluzione"], aperta=bool(r["aperta"]),
                varianti=varianti[r["id"]],
            )
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


def get_esercizio_su_connessione(conn, esercizio_id: int) -> Optional[Esercizio]:
    """Come get_esercizio, ma riusando una connessione già aperta dal chiamante: usata
    dove va letto un esercizio per volta dentro un ciclo già scoped su una connessione
    (es. le statistiche di un appello), per non aprirne una nuova ad ogni giro."""
    row = conn.execute("SELECT * FROM esercizi WHERE id=?", (esercizio_id,)).fetchone()
    return _row_to_esercizio(conn, row) if row else None


def get_esercizio(tag: str, esercizio_id: int) -> Optional[Esercizio]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return get_esercizio_su_connessione(conn, esercizio_id)
    finally:
        conn.close()


def trova_duplicati(tag: str) -> list[list[Esercizio]]:
    """Raggruppa gli esercizi della banca del corso che hanno esattamente lo stesso testo
    e le stesse risposte in ogni variante: gruppi con più di un esercizio sono probabili
    doppioni (es. importati più volte), da ripulire tenendone solo uno."""
    gruppi: dict[frozenset, list[Esercizio]] = {}
    for e in list_esercizi(tag):
        firma = _firma_varianti([{"testo": v.testo, "risposte": v.risposte} for v in e.varianti])
        gruppi.setdefault(firma, []).append(e)
    return [g for g in gruppi.values() if len(g) > 1]


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


def utilizzo_in_altri_corsi(tag: str, esercizio_id: int) -> list[dict]:
    """Un esercizio importato da un altro corso (o esportato e riusato altrove) diventa
    una copia indipendente, con un id nuovo, nel database del corso di destinazione: non
    esiste un id condiviso tra le copie per risalire direttamente a dove altro è usato.
    Cerca quindi, in tutti gli altri corsi, esercizi con lo stesso testo e le stesse
    risposte in ogni variante (stessa firma usata per i doppioni) e ritorna a quali
    appelli di quei corsi sono assegnati, per segnalarlo in anteprima."""
    esercizio = get_esercizio(tag, esercizio_id)
    if esercizio is None:
        return []
    firma_target = _firma_varianti([{"testo": v.testo, "risposte": v.risposte} for v in esercizio.varianti])
    risultati = []
    for corso in corsi_service.list_corsi():
        if corso.tag == tag:
            continue
        for e in list_esercizi(corso.tag):
            firma = _firma_varianti([{"testo": v.testo, "risposte": v.risposte} for v in e.varianti])
            if firma != firma_target:
                continue
            for appello_id in appelli_che_usano(corso.tag, e.id):
                appello = corsi_service.get_appello(corso.tag, appello_id)
                if appello:
                    risultati.append({"corso_nome": corso.nome, "corso_tag": corso.tag, "appello_nome": appello.nome})
    return risultati


def aggiorna_esercizio(
    tag: str, esercizio_id: int, nome: str, note: str, varianti: list[dict], argomento: str = "",
    difficolta: Optional[int] = None, soluzione: str = "", aperta: bool = False,
) -> Esercizio:
    """Sostituisce nome/note/argomento e tutte le varianti di un esercizio della banca già
    esistente. Le vecchie varianti vengono cancellate e ricreate: i loro id cambiano, quindi
    va chiamata solo dopo aver protetto/rigenerato gli appelli che lo usano (vedi
    appelli_che_usano) se hanno già blocchi di compiti generati."""
    varianti = _valida_varianti(varianti, aperta)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "UPDATE esercizi SET nome=?, argomento=?, note=?, difficolta=?, soluzione=?, aperta=? WHERE id=?",
            (nome or None, argomento.strip() or None, note or None, difficolta, soluzione.strip() or None, aperta, esercizio_id),
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


def _valida_varianti(varianti: list[dict], aperta: bool = False) -> list[dict]:
    varianti = [v for v in varianti if v.get("testo", "").strip()]
    if not varianti:
        raise ValueError("Serve almeno una variante con un testo")
    for v in varianti:
        if aperta:
            v["risposte"] = []
            continue
        risposte = [r for r in v.get("risposte", []) if r and r.strip()]
        if len(risposte) < 2:
            raise ValueError("Ogni variante richiede la risposta corretta e almeno una sbagliata")
        v["risposte"] = risposte
    return varianti


def create_esercizio(
    tag: str, nome: str, note: str, varianti: list[dict], argomento: str = "",
    difficolta: Optional[int] = None, soluzione: str = "", aperta: bool = False,
) -> Esercizio:
    """varianti: [{"testo": ..., "risposte": [corretta, sbagliata1, ...]}, ...] (risposte
    ignorate se aperta=True: una domanda aperta non ha scelte multiple)."""
    varianti = _valida_varianti(varianti, aperta)

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "INSERT INTO esercizi (nome, argomento, note, difficolta, soluzione, aperta) VALUES (?,?,?,?,?,?)",
            (nome or None, argomento.strip() or None, note or None, difficolta, soluzione.strip() or None, aperta),
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
        in_compiti = conn.execute(
            "SELECT count(*) FROM compito_esercizi WHERE esercizio_id=?", (esercizio_id,)
        ).fetchone()[0]
        if in_compiti:
            raise ValueError("Questo esercizio compare in compiti già generati: non può essere eliminato")
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
                id=r["id"], nome=r["nome"], note=r["note"], argomento=r["argomento"],
                difficolta=r["difficolta"], soluzione=r["soluzione"], aperta=bool(r["aperta"]),
                varianti=varianti[r["id"]],
                assegnato_il=r["data_assegnazione"], obbligatorio=bool(r["obbligatorio"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def assegna_a_appello(tag: str, appello_id: int, esercizio_id: int, obbligatorio: bool = False) -> None:
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
    difficolta: Optional[int] = None, soluzione: str = "", aperta: bool = False,
) -> Esercizio:
    esercizio = create_esercizio(
        tag, nome, note, varianti, argomento=argomento,
        difficolta=difficolta, soluzione=soluzione, aperta=aperta,
    )
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
                "difficolta": e.difficolta, "soluzione": e.soluzione, "aperta": e.aperta,
                "varianti": [{"testo": v.testo, "risposte": list(v.risposte)} for v in e.varianti],
            }
            for e in esercizi
        ],
    }


def _firma_varianti(varianti: list[dict]) -> frozenset:
    """Un esercizio è considerato identico a un altro se hanno lo stesso insieme di
    varianti (stesso testo e stesse risposte in ciascuna, indipendentemente dall'ordine):
    usata per segnalare i doppioni prima di importare, non per bloccare l'importazione —
    la decisione se duplicare comunque resta al docente."""
    return frozenset((v.get("testo", "").strip(), tuple(v.get("risposte") or [])) for v in varianti)


def anteprima_importa_json(tag: str, dati: dict) -> list[dict]:
    """Per ogni esercizio del file da importare, controlla se è identico (stesso testo e
    stesse risposte in ogni variante) a uno già presente nella banca del corso: prepara
    l'elenco mostrato per la conferma prima di creare davvero qualcosa."""
    esercizi_input = dati.get("esercizi")
    if not isinstance(esercizi_input, list):
        raise ValueError("File non valido: manca la lista 'esercizi'")
    esistenti = list_esercizi(tag)
    firme_esistenti = {
        _firma_varianti([{"testo": v.testo, "risposte": v.risposte} for v in e.varianti]): e
        for e in esistenti
    }
    risultato = []
    for e in esercizi_input:
        varianti = e.get("varianti") or []
        duplicato = firme_esistenti.get(_firma_varianti(varianti))
        risultato.append({
            "nome": e.get("nome") or "", "argomento": e.get("argomento") or "", "note": e.get("note") or "",
            "obbligatorio": bool(e.get("obbligatorio")), "difficolta": e.get("difficolta"),
            "soluzione": e.get("soluzione") or "", "aperta": bool(e.get("aperta")), "varianti": varianti,
            "duplicato_di": (duplicato.nome or f"#{duplicato.id}") if duplicato else None,
        })
    return risultato


# --- Importazione da file di testo semplice: pensato per essere preparato a mano o da
# un'IA a partire da un compito esistente (vedi la guida, sezione "Importare un compito
# da un unico file"). A differenza del JSON non richiede di raddoppiare le barre
# rovesciate del LaTeX, che è l'errore più frequente nei file JSON scritti da un'IA. ---

_MARCATORE = re.compile(r"^\s*#{2,}\s*(ESERCIZIO|VARIANTE|SOLUZIONE)\b", re.IGNORECASE)
_RISPOSTA = re.compile(r"^\s*([+-])\s+(.*\S)\s*$")
_CAMPI_ESERCIZIO = {"nome", "argomento", "note", "difficolta", "aperta", "obbligatorio"}
_SI = {"si", "sì", "s", "yes", "true", "1", "x"}
_NO = {"no", "n", "false", "0", ""}


def _parse_si_no(valore: str, campo: str, riga: int) -> bool:
    v = valore.strip().lower()
    if v in _SI:
        return True
    if v in _NO:
        return False
    raise ValueError(f"Riga {riga}: '{campo}' deve essere sì o no (trovato '{valore.strip()}')")


def parse_testo_esercizi(testo: str) -> dict:
    """Converte il formato testuale in {"esercizi": [...]}, la stessa struttura del file
    JSON di esportazione, così da riusare anteprima e conferma dell'import JSON. Gli
    errori riportano il numero di riga, per poterli correggere (o farli correggere
    all'IA) senza cercare a tentativi."""
    esercizi: list[dict] = []
    corrente: Optional[dict] = None
    sezione = None  # "intestazione" | "variante" | "soluzione"
    variante: Optional[dict] = None

    for n, riga in enumerate(testo.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        # recinti di codice markdown (```), che un'IA aggiunge spesso intorno al file
        if riga.strip().startswith("```"):
            continue
        m = _MARCATORE.match(riga)
        if m:
            tipo = m.group(1).upper()
            if tipo == "ESERCIZIO":
                corrente = {"riga": n, "campi": {}, "varianti": [], "soluzione": []}
                esercizi.append(corrente)
                sezione = "intestazione"
            elif corrente is None:
                raise ValueError(f"Riga {n}: '### {tipo}' prima di qualunque '### ESERCIZIO'")
            elif tipo == "VARIANTE":
                variante = {"riga": n, "testo": [], "risposte": []}
                corrente["varianti"].append(variante)
                sezione = "variante"
            else:
                sezione = "soluzione"
            continue
        if corrente is None:
            continue  # eventuale preambolo prima del primo esercizio: ignorato

        if sezione == "intestazione":
            if not riga.strip():
                continue
            if ":" not in riga:
                raise ValueError(
                    f"Riga {n}: attesa una riga 'campo: valore' (o '### VARIANTE'), trovato '{riga.strip()}'"
                )
            campo, valore = riga.split(":", 1)
            campo = campo.strip().lower().replace("à", "a")
            if campo not in _CAMPI_ESERCIZIO:
                raise ValueError(
                    f"Riga {n}: campo '{campo}' sconosciuto (ammessi: {', '.join(sorted(_CAMPI_ESERCIZIO))})"
                )
            corrente["campi"][campo] = (valore.strip(), n)
        elif sezione == "variante":
            r = _RISPOSTA.match(riga)
            if r:
                variante["risposte"].append((r.group(1), r.group(2)))
            elif variante["risposte"] and riga.strip():
                raise ValueError(
                    f"Riga {n}: dopo le risposte (righe che iniziano con '+' o '-') è atteso un "
                    f"nuovo '### VARIANTE', '### SOLUZIONE' o '### ESERCIZIO', trovato '{riga.strip()}'"
                )
            else:
                variante["testo"].append(riga)
        else:
            corrente["soluzione"].append(riga)

    if not esercizi:
        raise ValueError("Nessun esercizio trovato: ogni esercizio deve iniziare con una riga '### ESERCIZIO'")

    risultato = []
    for e in esercizi:
        campi = e["campi"]
        valori = {c: v for c, (v, _) in campi.items()}
        etichetta = f"Esercizio a riga {e['riga']}"
        if not e["varianti"]:
            raise ValueError(f"{etichetta}: serve almeno un '### VARIANTE' con il testo")

        if "aperta" in campi:
            aperta = _parse_si_no(campi["aperta"][0], "aperta", campi["aperta"][1])
        else:
            # senza indicazione esplicita, un esercizio senza nessuna risposta è una domanda aperta
            aperta = all(not v["risposte"] for v in e["varianti"])
        obbligatorio = _parse_si_no(campi["obbligatorio"][0], "obbligatorio", campi["obbligatorio"][1]) \
            if "obbligatorio" in campi else False

        difficolta = None
        if valori.get("difficolta", ""):
            try:
                difficolta = int(valori.get("difficolta", ""))
            except ValueError:
                difficolta = 0
            if difficolta not in (1, 2, 3):
                raise ValueError(f"Riga {campi['difficolta'][1]}: 'difficolta' deve essere 1, 2 o 3")

        varianti = []
        for v in e["varianti"]:
            testo_variante = "\n".join(v["testo"]).strip()
            if not testo_variante:
                raise ValueError(f"Variante a riga {v['riga']}: manca il testo")
            corrette = [t for segno, t in v["risposte"] if segno == "+"]
            sbagliate = [t for segno, t in v["risposte"] if segno == "-"]
            if aperta:
                if v["risposte"]:
                    raise ValueError(
                        f"Variante a riga {v['riga']}: una domanda aperta non deve avere risposte '+'/'-'"
                    )
                risposte = []
            else:
                if len(corrette) != 1:
                    raise ValueError(
                        f"Variante a riga {v['riga']}: serve esattamente una risposta corretta '+' (trovate {len(corrette)})"
                    )
                if not sbagliate:
                    raise ValueError(f"Variante a riga {v['riga']}: serve almeno una risposta sbagliata '-'")
                risposte = corrette + sbagliate
            varianti.append({"testo": testo_variante, "risposte": risposte})

        risultato.append({
            "nome": valori.get("nome", ""), "argomento": valori.get("argomento", ""), "note": valori.get("note", ""),
            "obbligatorio": obbligatorio, "difficolta": difficolta, "aperta": aperta,
            "soluzione": "\n".join(e["soluzione"]).strip(), "varianti": varianti,
        })
    return {"esercizi": risultato}


def leggi_file_esercizi(contenuto: str, nome_file: str = "") -> dict:
    """Riconosce se il contenuto caricato/incollato è il JSON di esportazione o il formato
    testuale, e lo converte nella struttura comune {"esercizi": [...]}."""
    contenuto = contenuto.lstrip("﻿")
    if nome_file.lower().endswith(".json") or contenuto.lstrip().startswith("{"):
        try:
            return json.loads(contenuto)
        except json.JSONDecodeError as e:
            raise ValueError(f"File JSON non valido (riga {e.lineno}): {e.msg}")
    return parse_testo_esercizi(contenuto)


def importa_json(tag: str, appello_id: int, esercizi: list[dict]) -> int:
    """Crea nella banca di questo corso e assegna subito all'appello gli esercizi scelti
    dal docente nella pagina di conferma (vedi anteprima_importa_json): a differenza delle
    versioni precedenti non importa più l'intero file alla cieca."""
    n = 0
    for e in esercizi:
        crea_e_assegna(
            tag, appello_id, nome=e.get("nome") or "", note=e.get("note") or "",
            varianti=e.get("varianti") or [], obbligatorio=bool(e.get("obbligatorio")),
            argomento=e.get("argomento") or "", difficolta=e.get("difficolta"),
            soluzione=e.get("soluzione") or "", aperta=bool(e.get("aperta")),
        )
        n += 1
    return n


def importa_json_banca(tag: str, esercizi: list[dict]) -> int:
    """Come importa_json, ma crea gli esercizi solo nella banca del corso senza
    assegnarli a nessun appello: per l'import dalla scheda Esercizi del corso, non
    legata a un appello specifico."""
    n = 0
    for e in esercizi:
        create_esercizio(
            tag, nome=e.get("nome") or "", note=e.get("note") or "",
            varianti=e.get("varianti") or [], argomento=e.get("argomento") or "",
            difficolta=e.get("difficolta"), soluzione=e.get("soluzione") or "", aperta=bool(e.get("aperta")),
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


def list_esercizi_tutti_i_corsi(escludi_tag: str = "") -> list[tuple]:
    """Tutti gli esercizi di tutti i corsi, come coppie (corso, esercizio): le banche
    restano separate (un database per corso), questo è solo un elenco unico per
    sfogliarle e filtrarle insieme, ad es. per riusare un esercizio di un anno precedente."""
    risultato = []
    for corso in corsi_service.list_corsi():
        if corso.tag == escludi_tag:
            continue
        try:
            esercizi = list_esercizi(corso.tag)
        except Exception:
            continue  # un database illeggibile non deve nascondere tutti gli altri
        risultato.extend((corso, e) for e in esercizi)
    return risultato


def copia_da_altro_corso(tag: str, tag_sorgente: str, esercizio_id: int) -> tuple[Optional[int], bool]:
    """Copia un esercizio (varianti, soluzione, difficoltà, tipo compresi) dalla banca di
    un altro corso in quella del corso `tag`: le banche restano indipendenti, le modifiche
    successive su una copia non si propagano all'altra. Se nella banca di destinazione
    c'è già un esercizio identico (stesse varianti e risposte) riusa quello invece di
    creare un doppione. Ritorna (id nella banca di destinazione, era_già_presente);
    (None, False) se l'esercizio sorgente non esiste."""
    esercizio = get_esercizio(tag_sorgente, esercizio_id)
    if esercizio is None:
        return None, False
    varianti = [{"testo": v.testo, "risposte": list(v.risposte)} for v in esercizio.varianti]
    firma = _firma_varianti(varianti)
    for esistente in list_esercizi(tag):
        if _firma_varianti([{"testo": v.testo, "risposte": v.risposte} for v in esistente.varianti]) == firma:
            return esistente.id, True
    sorgente = corsi_service.get_corso(tag_sorgente)
    provenienza = f"Copiato da {sorgente.nome}" + (f" ({sorgente.anno})" if sorgente.anno else f" [{tag_sorgente}]")
    note = f"{esercizio.note}\n{provenienza}" if esercizio.note else provenienza
    nuovo = create_esercizio(
        tag, nome=esercizio.nome or "", note=note, varianti=varianti, argomento=esercizio.argomento or "",
        difficolta=esercizio.difficolta, soluzione=esercizio.soluzione or "", aperta=esercizio.aperta,
    )
    return nuovo.id, False


def importa_in_appello_da_altri_corsi(
    tag: str, appello_id: int, selezionati: list[tuple[str, int]], obbligatorio: bool = False,
) -> tuple[int, int]:
    """Copia nella banca del corso (vedi copia_da_altro_corso) e assegna subito al
    compito gli esercizi scelti da altri corsi/anni. `selezionati`: [(tag_sorgente,
    esercizio_id), ...]. Ritorna (assegnati, di cui già presenti nella banca)."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    assegnati = gia_presenti = 0
    for tag_sorgente, esercizio_id in selezionati:
        if tag_sorgente == tag:
            nuovo_id, presente = esercizio_id, True
        else:
            nuovo_id, presente = copia_da_altro_corso(tag, tag_sorgente, esercizio_id)
        if nuovo_id is None:
            continue
        assegna_a_appello(tag, appello_id, nuovo_id, obbligatorio=obbligatorio)
        assegnati += 1
        gia_presenti += presente
    return assegnati, gia_presenti


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
