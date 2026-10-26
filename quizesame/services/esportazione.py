"""Gestione del file "Lista Studenti Esame" caricato dalla segreteria per un appello: si
carica una volta sola (scheda "Compiti d'esame"), da cui si ricava sia l'elenco degli
iscritti (numero, elenco stampabile per fare l'appello, controllo in correzione) sia,
su richiesta, il file compilato da riproporre alla segreteria con le colonne "Esito" e
"Domande d'esame" compilate. Si compilano solo quelle due colonne per gli studenti già
corretti, lasciando invariato il resto del file (comprese le altre colonne e gli
studenti senza un risultato)."""
import csv
import io
from pathlib import Path
from typing import Optional

import openpyxl
import xlrd

from quizesame import config, db
from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service
from quizesame.services import studenti as studenti_service


def _decodifica(contenuto: bytes) -> tuple[str, str]:
    for codifica in ("utf-8-sig", "cp1252"):
        try:
            return contenuto.decode(codifica), codifica
        except UnicodeDecodeError:
            continue
    raise ValueError("Non riconosco la codifica del file: attesi UTF-8 o Windows-1252")


def _normalizza_cella(valore) -> str:
    if valore is None:
        return ""
    if isinstance(valore, float) and valore.is_integer():
        return str(int(valore))  # es. una matricola letta come numero (7034532.0 -> "7034532")
    return str(valore).strip()


def _righe_da_xlsx(contenuto: bytes) -> list[list[str]]:
    cartella = openpyxl.load_workbook(io.BytesIO(contenuto), data_only=True, read_only=True)
    try:
        foglio = cartella.worksheets[0]
        return [[_normalizza_cella(v) for v in riga] for riga in foglio.iter_rows(values_only=True)]
    finally:
        cartella.close()


def _righe_da_xls(contenuto: bytes) -> list[list[str]]:
    foglio = xlrd.open_workbook(file_contents=contenuto).sheet_by_index(0)
    return [
        [_normalizza_cella(foglio.cell_value(riga, colonna)) for colonna in range(foglio.ncols)]
        for riga in range(foglio.nrows)
    ]


def _righe_e_codifica(nome_file: str, contenuto: bytes) -> tuple[list[list[str]], str]:
    """Il file della segreteria può arrivare come CSV oppure come foglio Excel (.xls/.xlsx):
    in entrambi i casi si ottiene la stessa lista di righe di stringhe, così il resto della
    logica (ricerca dell'intestazione, colonne 'Matricola'/'Esito') resta identica a valle,
    indipendentemente dal formato caricato."""
    estensione = Path(nome_file or "").suffix.lower()
    if estensione == ".xlsx":
        return _righe_da_xlsx(contenuto), "utf-8"
    if estensione == ".xls":
        return _righe_da_xls(contenuto), "utf-8"
    testo, codifica = _decodifica(contenuto)
    return list(csv.reader(io.StringIO(testo))), codifica


def _trova_intestazione(righe: list[list[str]]) -> int:
    indice = next((i for i, r in enumerate(righe) if "Matricola" in r and "Esito" in r), None)
    if indice is None:
        raise ValueError("Non trovo nel file la riga di intestazione con le colonne 'Matricola' ed 'Esito'")
    return indice


def _esito_per_export(risultato: Optional[dict], votomin: int, annullato_come: str = "insufficiente") -> Optional[str]:
    """None significa "non scrivere nulla" (nessuna valutazione ancora completata o
    confermata): lo studente resta come nel file caricato. Un compito annullato vale
    insufficiente ("0") o ritirato ("RIT") secondo l'impostazione del corso."""
    if risultato is None:
        return None
    if risultato["esito"] == "assente":
        return "ASS"
    if risultato["esito"] == "ritirato":
        return "RIT"
    if risultato["esito"] == "annullato":
        return "RIT" if annullato_come == "ritirato" else "0"
    if risultato["esito"] != "voto":
        return None
    if risultato["valutazione_sospesa"]:
        return None
    if risultato["richiede_orale"] and not risultato["orale_svolto"]:
        return None
    if risultato["orale_svolto"] and risultato["esito_orale"] == "assente":
        return "ASS"
    if risultato["orale_svolto"] and risultato["esito_orale"] == "insufficiente":
        return "0"
    voto = risultato["voto"]
    if voto is None:
        return None
    if voto < votomin:
        return "0"
    if voto > 30:
        return "31"
    return str(voto)


def carica_csv_segreteria(tag: str, appello_id: int, nome_file: str, contenuto: bytes) -> int:
    """Valida e salva il file (sostituendo un eventuale caricamento precedente per lo
    stesso appello). Accetta sia un CSV sia un foglio Excel (.xls/.xlsx, come spesso
    esportato dal sistema della segreteria): in entrambi i casi viene salvato come CSV,
    così il resto del programma (elenco iscritti, compilazione per il riesportazione) non
    deve distinguere il formato originale. Ritorna il numero di studenti iscritti trovati
    nel file."""
    righe, codifica = _righe_e_codifica(nome_file, contenuto)
    indice_intestazione = _trova_intestazione(righe)
    idx_matricola = righe[indice_intestazione].index("Matricola")
    n_iscritti = sum(
        1 for r in righe[indice_intestazione + 1:] if len(r) > idx_matricola and r[idx_matricola].strip()
    )

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(righe)
    testo = buffer.getvalue()

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO appello_segreteria_csv (appello_id, nome_file, contenuto, codifica) VALUES (?,?,?,?) "
            "ON CONFLICT(appello_id) DO UPDATE SET nome_file=excluded.nome_file, contenuto=excluded.contenuto, "
            "codifica=excluded.codifica, caricato_il=CURRENT_TIMESTAMP",
            (appello_id, nome_file, testo, codifica),
        )
        # un file nuovo è un elenco nuovo: chi era stato tolto dal precedente vi ricompare
        conn.execute("DELETE FROM appello_iscritti_rimossi WHERE appello_id=?", (appello_id,))
        conn.commit()
    finally:
        conn.close()
    return n_iscritti


def get_segreteria_csv(tag: str, appello_id: int) -> Optional[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT * FROM appello_segreteria_csv WHERE appello_id=?", (appello_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def elimina_segreteria_csv(tag: str, appello_id: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("DELETE FROM appello_segreteria_csv WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM appello_iscritti_rimossi WHERE appello_id=?", (appello_id,))
        conn.commit()
    finally:
        conn.close()


def list_iscritti_manuali(tag: str, appello_id: int) -> list[dict]:
    """Iscritti aggiunti a mano uno per uno (da soli, o in aggiunta a quelli del file
    della segreteria): stessa forma dell'elenco derivato dal CSV."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT s.matricola, s.nome, s.cognome, s.dsa, s.dsa_tipo, s.dsa_note FROM appello_iscritti_manuali im "
            "JOIN studenti s ON s.matricola = im.matricola "
            "WHERE im.appello_id=? ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        return [
            {
                "matricola": r["matricola"], "cognome": r["cognome"], "nome": r["nome"],
                "dsa": bool(r["dsa"]), "dsa_tipo": studenti_service.tipo_dsa(r["dsa"], r["dsa_tipo"]),
                "dsa_note": r["dsa_note"] or "", "registrato": True,
            }
            for r in rows
        ]
    finally:
        conn.close()


def aggiungi_iscritto_manuale(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        studente = conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (matricola,)).fetchone()
        if studente is None:
            raise ValueError(f"Nessuno studente con matricola '{matricola}' in questo corso")
        conn.execute(
            "INSERT INTO appello_iscritti_manuali (matricola, appello_id) VALUES (?,?) "
            "ON CONFLICT(matricola, appello_id) DO NOTHING",
            (matricola, appello_id),
        )
        # se era stato tolto dall'elenco del file della segreteria, ci torna
        conn.execute("DELETE FROM appello_iscritti_rimossi WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
    finally:
        conn.close()


# --- Prenotazioni esportate da Moodle (es. "Iscrizione primo parziale.csv"): colonne
# "First name", "Last name", "Email address", "ID number" (in italiano "Nome", "Cognome",
# "Indirizzo email", "Codice identificativo"). L'ID number è la matricola: le righe
# diventano iscritti aggiunti a mano, registrando nel corso chi non c'è ancora. ---------

_COLONNE_MOODLE = {
    "matricola": ("id number", "codice identificativo", "numero identificativo", "matricola"),
    "nome": ("first name", "nome"),
    "cognome": ("last name", "cognome"),
    "email": ("email address", "indirizzo email", "email"),
}


def anteprima_iscritti_moodle(tag: str, appello_id: int, nome_file: str, contenuto: bytes) -> list[dict]:
    """Legge il file (CSV o Excel) e ritorna una riga per studente con lo stato rispetto
    al corso: "registrato" (matricola già nel corso), "nuovo" (verrà registrato),
    "da_verificare" (ID non numerico: spesso un docente o un account di prova, quindi
    deselezionato di default) o "senza_matricola" (non importabile). "gia_iscritto"
    segnala chi è già nell'elenco iscritti di questo appello."""
    righe, _ = _righe_e_codifica(nome_file, contenuto)
    indice, colonne = None, {}
    for i, riga in enumerate(righe):
        normalizzate = [c.strip().lower() for c in riga]
        trovate = {
            campo: next((normalizzate.index(n) for n in nomi if n in normalizzate), None)
            for campo, nomi in _COLONNE_MOODLE.items()
        }
        if trovate["matricola"] is not None and trovate["cognome"] is not None:
            indice, colonne = i, trovate
            break
    if indice is None:
        raise ValueError(
            "Non trovo nel file le colonne di Moodle 'ID number' e 'Last name' "
            "(o 'Codice identificativo' e 'Cognome')"
        )

    def cella(riga: list[str], campo: str) -> str:
        idx = colonne.get(campo)
        return riga[idx].strip() if idx is not None and idx < len(riga) else ""

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        registrati = {r["matricola"] for r in conn.execute("SELECT matricola FROM studenti")}
        gia_iscritti = {
            r["matricola"] for r in conn.execute(
                "SELECT matricola FROM appello_iscritti_manuali WHERE appello_id=?", (appello_id,)
            )
        }
    finally:
        conn.close()

    risultato, viste = [], set()
    for riga in righe[indice + 1:]:
        if not any(c.strip() for c in riga):
            continue
        matricola = cella(riga, "matricola")
        if matricola in viste:
            continue  # stessa persona prenotata due volte: conta una sola volta
        if matricola:
            viste.add(matricola)
        if not matricola:
            stato = "senza_matricola"
        elif matricola in registrati:
            stato = "registrato"
        elif not matricola.isdigit():
            stato = "da_verificare"
        else:
            stato = "nuovo"
        risultato.append({
            "matricola": matricola, "nome": cella(riga, "nome"), "cognome": cella(riga, "cognome"),
            "email": cella(riga, "email"), "stato": stato, "gia_iscritto": matricola in gia_iscritti,
        })
    return risultato


def importa_iscritti_moodle(tag: str, appello_id: int, studenti: list[dict], sostituisci: bool = False) -> dict:
    """Registra nel corso gli studenti scelti che non ci sono ancora (nome e cognome dal
    file) e li aggiunge agli iscritti di questo appello. Con `sostituisci`, prima toglie
    dall'elenco iscritti chi non è tra gli scelti (es. prenotazioni annullate su Moodle).
    Ritorna i conteggi per il messaggio di conferma."""
    scelti = [s for s in studenti if s.get("matricola")]
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        registrati = {r["matricola"] for r in conn.execute("SELECT matricola FROM studenti")}
        nuovi = [s for s in scelti if s["matricola"] not in registrati]
        conn.executemany(
            "INSERT INTO studenti (matricola, nome, cognome) VALUES (?,?,?)",
            [(s["matricola"], s.get("nome") or "", s.get("cognome") or "") for s in nuovi],
        )
        rimossi = 0
        if sostituisci:
            prima = conn.total_changes
            segnaposti = ",".join("?" * len(scelti)) or "''"
            conn.execute(
                f"DELETE FROM appello_iscritti_manuali WHERE appello_id=? AND matricola NOT IN ({segnaposti})",
                (appello_id, *[s["matricola"] for s in scelti]),
            )
            rimossi = conn.total_changes - prima
        prima = conn.total_changes
        conn.executemany(
            "INSERT INTO appello_iscritti_manuali (matricola, appello_id) VALUES (?,?) "
            "ON CONFLICT(matricola, appello_id) DO NOTHING",
            [(s["matricola"], appello_id) for s in scelti],
        )
        aggiunti = conn.total_changes - prima
        conn.commit()
    finally:
        conn.close()
    return {"aggiunti": aggiunti, "registrati": len(nuovi), "rimossi": rimossi}


def iscrivi_tutti_gli_studenti(tag: str, appello_id: int, matricola_minima: str = "") -> int:
    """Aggiunge agli iscritti manuali di questo appello tutti gli studenti del corso (con
    matricola >= `matricola_minima`, se data): per le prove a cui è ammesso l'intero
    corso, senza un file della segreteria. Chi è fuori dal filtro sul cognome della prova
    non viene aggiunto (altrimenti finirebbe tra gli esclusi da confermare uno per uno).
    Ritorna quanti ne sono stati aggiunti."""
    appello = corsi_service.get_appello(tag, appello_id)
    filtro_cognome = appello.cognome_iscritti if appello else None
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        matricole = [
            r["matricola"] for r in conn.execute("SELECT matricola, cognome FROM studenti")
            if corsi_service.matricola_ammessa(r["matricola"], matricola_minima.strip() or None)
            and corsi_service.cognome_ammesso(r["cognome"], filtro_cognome)
        ]
        prima = conn.total_changes
        conn.executemany(
            "INSERT INTO appello_iscritti_manuali (matricola, appello_id) VALUES (?,?) "
            "ON CONFLICT(matricola, appello_id) DO NOTHING",
            [(m, appello_id) for m in matricole],
        )
        conn.commit()
        return conn.total_changes - prima
    finally:
        conn.close()


def rimuovi_iscritto(tag: str, appello_id: int, matricola: str) -> None:
    """Toglie uno studente dall'elenco iscritti, comunque ci sia entrato: se aggiunto a
    mano lo si cancella, se viene dal file della segreteria lo si segna come tolto (il
    file salvato resta intero)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "DELETE FROM appello_iscritti_manuali WHERE matricola=? AND appello_id=?", (matricola, appello_id)
        )
        if conn.execute("SELECT 1 FROM appello_segreteria_csv WHERE appello_id=?", (appello_id,)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO appello_iscritti_rimossi (appello_id, matricola) VALUES (?,?)",
                (appello_id, matricola),
            )
        conn.commit()
    finally:
        conn.close()


def list_iscritti(tag: str, appello_id: int) -> Optional[list[dict]]:
    """None se non è stato caricato nessun file per questo appello e non è stato aggiunto
    a mano nessuno studente. Il file della segreteria, quando presente, ha sempre la
    precedenza sull'elenco aggiunto a mano. L'elenco (matricola/cognome/nome/dsa/dsa_note/
    registrato) è ordinato per cognome poi nome: i dati anagrafici vengono dall'anagrafica
    del corso quando lo studente è già censito ('registrato': True), altrimenti (solo per
    il CSV, per uno studente mai importato in questo corso) dalle colonne Cognome/Nome del
    file stesso, se presenti ('registrato': False — non può ricevere un risultato, la cui
    matricola è vincolata a esistere già in 'studenti'). Una matricola ripetuta più volte
    nel file conta una sola volta (prima occorrenza). Se l'appello ha una matricola
    minima per gli iscritti, chi ha una matricola inferiore è escluso dall'elenco (ma resta
    nel file salvato, che torna intero alla segreteria). Lo stesso per il filtro sul cognome
    (es. solo A-D). Chi il docente ha ammesso in deroga resta comunque."""
    elenco = _list_iscritti_senza_filtro(tag, appello_id)
    if elenco is None:
        return None
    appello = corsi_service.get_appello(tag, appello_id)
    if not appello or not (appello.matricola_minima_iscritti or appello.cognome_iscritti):
        return elenco
    ammessi_in_deroga = {m for m, ammesso in _decisioni_filtro(tag, appello_id).items() if ammesso}
    return [s for s in elenco if not _motivi_esclusione(s, appello) or s["matricola"] in ammessi_in_deroga]


def _motivi_esclusione(studente: dict, appello) -> list[str]:
    """Perché uno studente non passa i filtri sugli iscritti di `appello` (vuoto se li passa)."""
    motivi = []
    if not corsi_service.matricola_ammessa(studente["matricola"], appello.matricola_minima_iscritti):
        motivi.append(f"matricola < {appello.matricola_minima_iscritti}")
    if not corsi_service.cognome_ammesso(studente.get("cognome") or "", appello.cognome_iscritti):
        motivi.append(f"cognome fuori {appello.cognome_iscritti}")
    return motivi


def _decisioni_filtro(tag: str, appello_id: int) -> dict[str, bool]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return {
            r["matricola"]: bool(r["ammesso"]) for r in conn.execute(
                "SELECT matricola, ammesso FROM appello_eccezioni_matricola WHERE appello_id=?", (appello_id,)
            )
        }
    finally:
        conn.close()


def list_esclusi_dal_filtro(tag: str, appello_id: int) -> list[dict]:
    """Iscritti che non passano i filtri di questo appello (matricola sotto la soglia,
    cognome fuori dall'intervallo), ognuno con i "motivi" e la decisione del docente:
    "decisione" è "ammesso" (eccezione: resta tra gli iscritti), "escluso" (esclusione
    confermata) o None (ancora da confermare). Vuoto se nessun filtro è attivo. I dati
    DSA/L104 servono a decidere con cognizione di causa."""
    appello = corsi_service.get_appello(tag, appello_id)
    elenco = _list_iscritti_senza_filtro(tag, appello_id)
    if not appello or not elenco or not (appello.matricola_minima_iscritti or appello.cognome_iscritti):
        return []
    decisioni = _decisioni_filtro(tag, appello_id)
    esclusi = []
    for s in elenco:
        motivi = _motivi_esclusione(s, appello)
        if not motivi:
            continue
        decisione = decisioni.get(s["matricola"])
        esclusi.append({
            **s, "motivi": motivi, "decisione": None if decisione is None else ("ammesso" if decisione else "escluso"),
        })
    return esclusi


def decidi_eccezione_filtro(tag: str, appello_id: int, matricola: str, decisione: str) -> None:
    """`decisione`: "ammesso" (eccezione al filtro), "escluso" (conferma l'esclusione)
    oppure "" (torna da confermare)."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if decisione in ("ammesso", "escluso"):
            conn.execute(
                "INSERT INTO appello_eccezioni_matricola (appello_id, matricola, ammesso) VALUES (?,?,?) "
                "ON CONFLICT(appello_id, matricola) DO UPDATE SET ammesso=excluded.ammesso",
                (appello_id, matricola, decisione == "ammesso"),
            )
        else:
            conn.execute(
                "DELETE FROM appello_eccezioni_matricola WHERE appello_id=? AND matricola=?", (appello_id, matricola)
            )
        conn.commit()
    finally:
        conn.close()


def _list_iscritti_senza_filtro(tag: str, appello_id: int) -> Optional[list[dict]]:
    """Gli iscritti del file della segreteria (tolti quelli rimossi a mano) più quelli
    aggiunti a mano, questi ultimi con "aggiunto": True; senza file, solo quelli a mano."""
    riga_csv = get_segreteria_csv(tag, appello_id)
    manuali = [{**s, "aggiunto": True} for s in list_iscritti_manuali(tag, appello_id)]
    if riga_csv is None:
        return manuali or None
    righe = list(csv.reader(io.StringIO(riga_csv["contenuto"])))
    indice_intestazione = _trova_intestazione(righe)
    intestazione = righe[indice_intestazione]
    idx_matricola = intestazione.index("Matricola")
    idx_cognome = intestazione.index("Cognome") if "Cognome" in intestazione else None
    idx_nome = intestazione.index("Nome") if "Nome" in intestazione else None

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        anagrafica = {
            r["matricola"]: r for r in conn.execute("SELECT matricola, nome, cognome, dsa, dsa_tipo, dsa_note FROM studenti")
        }
        rimossi = {r["matricola"] for r in conn.execute(
            "SELECT matricola FROM appello_iscritti_rimossi WHERE appello_id=?", (appello_id,)
        )}
    finally:
        conn.close()

    iscritti = []
    matricole_viste = set(rimossi)
    for r in righe[indice_intestazione + 1:]:
        if len(r) <= idx_matricola or not r[idx_matricola].strip():
            continue
        matricola = r[idx_matricola].strip()
        if matricola in matricole_viste:
            continue
        matricole_viste.add(matricola)
        studente = anagrafica.get(matricola)
        if studente:
            iscritti.append({
                "matricola": matricola, "cognome": studente["cognome"], "nome": studente["nome"],
                "dsa": bool(studente["dsa"]), "dsa_tipo": studenti_service.tipo_dsa(studente["dsa"], studente["dsa_tipo"]),
                "dsa_note": studente["dsa_note"] or "", "registrato": True,
            })
        else:
            iscritti.append({
                "matricola": matricola,
                "cognome": r[idx_cognome].strip() if idx_cognome is not None and len(r) > idx_cognome else "",
                "nome": r[idx_nome].strip() if idx_nome is not None and len(r) > idx_nome else "",
                "dsa": False, "dsa_tipo": "", "dsa_note": "", "registrato": False,
            })
    nel_file = {s["matricola"] for s in iscritti}
    iscritti += [s for s in manuali if s["matricola"] not in nel_file]
    iscritti.sort(key=lambda s: (s["cognome"], s["nome"]))
    return iscritti


def numero_iscritti(tag: str, appello_id: int) -> Optional[int]:
    """Numero di iscritti: dal file caricato se presente (ha sempre la precedenza),
    altrimenti dal numero inserito a mano nelle impostazioni dell'appello, altrimenti
    None (non ancora indicato)."""
    iscritti = list_iscritti(tag, appello_id)
    if iscritti is not None:
        return len(iscritti)
    appello = corsi_service.get_appello(tag, appello_id)
    return appello.iscritti_manuale if appello else None


def compila_export_voti(
    tag: str, appello_id: int, domande_esame: Optional[str] = None,
) -> tuple[str, str, list[dict], list[dict]]:
    """Compila il file caricato in precedenza per questo appello (vedi
    carica_csv_segreteria) con "Esito" (voto, ASS, RIT o 0 per insufficiente) e "Domande
    d'esame" (`domande_esame`, scelto tra quelli salvati nelle impostazioni del corso; se
    None, il primo), per gli studenti già corretti.

    Ritorna (testo compilato, codifica del file caricato, elenco degli studenti
    compilati [{"matricola","nome","cognome","voto_label"}], elenco "extra" con la stessa
    forma — studenti con una valutazione da esportare la cui matricola non compare nel
    file, da aggiungere a mano sul sistema della segreteria — il chiamante decide se
    scaricarlo subito o mostrare prima una conferma, es. per il raggruppamento o quando
    ci sono studenti extra da segnalare)."""
    riga_csv = get_segreteria_csv(tag, appello_id)
    if riga_csv is None:
        raise ValueError(
            "Nessun file caricato per questo appello: caricalo dalla scheda 'Compiti d'esame'."
        )
    righe = list(csv.reader(io.StringIO(riga_csv["contenuto"])))
    indice_intestazione = _trova_intestazione(righe)

    intestazione = righe[indice_intestazione]
    idx_matricola = intestazione.index("Matricola")
    idx_esito = intestazione.index("Esito")
    idx_domande = intestazione.index("Domande d'esame") if "Domande d'esame" in intestazione else None

    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    risultati = {r["matricola"]: r for r in correzione_service.list_risultati(tag, appello_id)}

    compilati = []
    matricole_nel_file = set()
    for riga in righe[indice_intestazione + 1:]:
        if len(riga) <= idx_matricola or not riga[idx_matricola].strip():
            continue
        matricola = riga[idx_matricola].strip()
        matricole_nel_file.add(matricola)

        risultato = risultati.get(matricola)
        esito = _esito_per_export(risultato, votomin, corso.annullato_come)
        if esito is None:
            continue
        riga[idx_esito] = esito
        if idx_domande is not None:
            riga[idx_domande] = corso.domande_esame if domande_esame is None else domande_esame
        compilati.append({
            "matricola": matricola, "nome": risultato["nome"], "cognome": risultato["cognome"],
            "voto_label": esito,
        })

    # Studenti con una valutazione da esportare ma la cui matricola non compare affatto
    # nel file della segreteria (es. corretti dopo che il file era già stato scaricato, o
    # iscritti con matricola diversa da quella censita): il file compilato non ha nessuna
    # riga in cui scriverli, quindi vanno segnalati per l'aggiunta a mano sul sistema
    # della segreteria, non solo silenziosamente ignorati.
    extra = []
    for matricola, risultato in risultati.items():
        if matricola in matricole_nel_file:
            continue
        esito = _esito_per_export(risultato, votomin, corso.annullato_come)
        if esito is None:
            continue
        extra.append({
            "matricola": matricola, "nome": risultato["nome"], "cognome": risultato["cognome"],
            "voto_label": esito,
        })
    extra.sort(key=lambda s: (s["cognome"], s["nome"]))

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(righe)
    return buffer.getvalue(), riga_csv["codifica"], compilati, extra
