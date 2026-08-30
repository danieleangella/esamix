import csv
import io
from dataclasses import dataclass
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service


@dataclass
class Studente:
    matricola: str
    nome: str
    cognome: str
    laurea_ctype: Optional[str]
    laurea_nome: Optional[str] = None


def _clean(value: Optional[str]) -> str:
    if value is None:
        return ""
    return value.strip().lstrip("﻿")


def _row_to_studente(row) -> Studente:
    return Studente(
        matricola=row["matricola"], nome=row["nome"], cognome=row["cognome"],
        laurea_ctype=row["laurea_ctype"], laurea_nome=row["laurea_nome"] if "laurea_nome" in row.keys() else None,
    )


def list_studenti(tag: str, search: Optional[str] = None) -> list[Studente]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        query = (
            "SELECT s.*, cl.nome AS laurea_nome FROM studenti s "
            "LEFT JOIN corsi_laurea cl ON cl.ctype = s.laurea_ctype "
        )
        params: tuple = ()
        if search:
            query += "WHERE s.matricola LIKE ? OR s.nome LIKE ? OR s.cognome LIKE ? "
            like = f"%{search}%"
            params = (like, like, like)
        query += "ORDER BY s.cognome, s.nome"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_studente(r) for r in rows]
    finally:
        conn.close()


def esporta_csv(studenti: list[Studente], stato_esame: dict) -> bytes:
    """CSV di un elenco di studenti (già filtrato dal chiamante) con lo stato aggregato
    dell'esame nel corso: usata sia per l'esportazione completa sia per quella di un
    sottoinsieme filtrato (es. solo chi deve ancora sostenerlo)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["Matricola", "Cognome", "Nome", "Corso di laurea", "Stato esame", "Voto"])
    etichette = {"verbalizzato": "esame superato", "da_verbalizzare": "da verbalizzare"}
    for s in studenti:
        stato = stato_esame.get(s.matricola)
        writer.writerow([
            s.matricola, s.cognome, s.nome, s.laurea_nome or "",
            etichette.get(stato["stato"], "deve ancora sostenerlo") if stato else "deve ancora sostenerlo",
            stato["voto"] if stato else "",
        ])
    return buffer.getvalue().encode("utf-8-sig")


def _risultati_studente(tag: str, matricola: str) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT a.id AS appello_id, a.nome AS appello_nome, r.voto, r.esito, r.richiede_orale, "
            "r.orale_svolto, r.esito_orale, r.verbalizzato, r.data_verbalizzazione "
            "FROM risultati r JOIN appelli a ON a.id = r.appello_id "
            "WHERE r.matricola=? ORDER BY a.data IS NULL, a.data, a.id",
            (matricola,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cerca_globale(query: str, limite: int = 15) -> list[dict]:
    """Studenti (deduplicati per matricola) che combaciano con `query` in almeno un
    corso, per l'autocompletamento della ricerca in homepage — click su un suggerimento
    porta alla pagina di riepilogo dello studente, che aggrega comunque tutti i corsi."""
    query = query.strip()
    if not query:
        return []
    trovati: dict[str, dict] = {}
    for corso in corsi_service.list_corsi():
        for s in list_studenti(corso.tag, search=query):
            if s.matricola not in trovati:
                trovati[s.matricola] = {"matricola": s.matricola, "nome": s.nome, "cognome": s.cognome}
                if len(trovati) >= limite:
                    return list(trovati.values())
    return list(trovati.values())


def riepilogo_globale(matricola: str) -> list[dict]:
    """Tutti i corsi (anni accademici) in cui questa matricola compare, con i suoi
    risultati in ciascuno: per la pagina di riepilogo di uno studente, raggiungibile
    cliccando sulla matricola/nome da qualunque elenco studenti."""
    matricola = _clean(matricola)
    riepilogo = []
    for corso in corsi_service.list_corsi():
        s = get_studente(corso.tag, matricola)
        if s is None:
            continue
        riepilogo.append({
            "corso_tag": corso.tag, "corso_nome": corso.nome, "corso_anno": corso.anno,
            "nome": s.nome, "cognome": s.cognome, "risultati": _risultati_studente(corso.tag, matricola),
        })
    return riepilogo


def list_non_superati(tag: str) -> list[Studente]:
    """Studenti di questo corso che non hanno ancora un risultato verbalizzato, cioè non
    hanno ancora superato definitivamente l'esame: candidati sensati da reimportare in un
    nuovo corso/anno accademico (chi ha già verbalizzato non dovrebbe ripetere l'esame)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT s.* FROM studenti s WHERE NOT EXISTS ("
            "  SELECT 1 FROM risultati r WHERE r.matricola = s.matricola AND r.verbalizzato = 1"
            ") ORDER BY s.cognome, s.nome"
        ).fetchall()
        return [_row_to_studente(r) for r in rows]
    finally:
        conn.close()


def importa_da_altro_corso(tag: str, tag_sorgente: str, matricole: list[str]) -> "ImportReport":
    """Importa studenti scelti da un altro corso, solo se non hanno ancora superato
    l'esame in quello di origine. Stesso comportamento verso le matricole già presenti
    dell'import da CSV: aggiorna se già presenti, altrimenti inserisce. Porta con sé anche
    l'eventuale obbligo di orale (impostato in un appello del corso di origine), così
    resta valido anche nel nuovo corso/anno."""
    report = ImportReport()
    non_superati = {s.matricola: s for s in list_non_superati(tag_sorgente)}
    conn_sorgente = db.get_connection(config.corso_db_path(tag_sorgente))
    try:
        orali_obbligatori = {
            r["matricola"]: dict(r) for r in conn_sorgente.execute("SELECT * FROM orale_obbligatorio")
        }
    finally:
        conn_sorgente.close()

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for matricola in matricole:
            s = non_superati.get(_clean(matricola))
            if s is None:
                report.saltati += 1
                report.errori.append(f"Matricola {matricola}: non trovata, o ha già superato l'esame nel corso di origine")
                continue
            existing = conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (s.matricola,)).fetchone()
            conn.execute(
                "INSERT INTO studenti (matricola, nome, cognome) VALUES (?,?,?) "
                "ON CONFLICT(matricola) DO UPDATE SET nome=excluded.nome, cognome=excluded.cognome",
                (s.matricola, s.nome, s.cognome),
            )
            if existing:
                report.aggiornati += 1
            else:
                report.inseriti += 1
            orale = orali_obbligatori.get(s.matricola)
            if orale:
                conn.execute(
                    "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
                    "ON CONFLICT(matricola) DO NOTHING",
                    (s.matricola, orale["motivazione"], f"importato dal corso '{tag_sorgente}'"),
                )
        conn.commit()
    finally:
        conn.close()
    return report


def cerca_in_tutti_i_corsi(query: str) -> list[dict]:
    """Cerca uno studente (per matricola, nome o cognome) in tutti i corsi, con i suoi
    risultati per ciascuno. Utile perché la stessa persona può comparire in più corsi
    (anni accademici) diversi, ognuno un database indipendente."""
    risultati = []
    for corso in corsi_service.list_corsi():
        for s in list_studenti(corso.tag, search=query):
            risultati.append({
                "corso_tag": corso.tag, "corso_nome": corso.nome, "corso_anno": corso.anno,
                "matricola": s.matricola, "nome": s.nome, "cognome": s.cognome,
                "laurea_nome": s.laurea_nome, "risultati": _risultati_studente(corso.tag, s.matricola),
            })
    return risultati


def get_studente(tag: str, matricola: str) -> Optional[Studente]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT s.*, cl.nome AS laurea_nome FROM studenti s "
            "LEFT JOIN corsi_laurea cl ON cl.ctype = s.laurea_ctype WHERE s.matricola=?",
            (matricola,),
        ).fetchone()
        return _row_to_studente(row) if row else None
    finally:
        conn.close()


def crea_studente(tag: str, matricola: str, nome: str, cognome: str, laurea_ctype: Optional[str] = None) -> None:
    """A differenza di upsert_studente, rifiuta la matricola se già assegnata a qualcun
    altro: usata dal form "Aggiungi studente", dove un inserimento con matricola sbagliata
    non deve sovrascrivere in silenzio lo studente già presente."""
    matricola = _clean(matricola)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        esistente = conn.execute(
            "SELECT nome, cognome FROM studenti WHERE matricola=?", (matricola,)
        ).fetchone()
        if esistente:
            raise ValueError(
                f"La matricola {matricola} è già assegnata a {esistente['cognome']} {esistente['nome']}"
            )
        conn.execute(
            "INSERT INTO studenti (matricola, nome, cognome, laurea_ctype) VALUES (?,?,?,?)",
            (matricola, _clean(nome), _clean(cognome), laurea_ctype or None),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_studente(tag: str, matricola: str, nome: str, cognome: str, laurea_ctype: Optional[str] = None) -> None:
    matricola = _clean(matricola)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO studenti (matricola, nome, cognome, laurea_ctype) VALUES (?,?,?,?) "
            "ON CONFLICT(matricola) DO UPDATE SET nome=excluded.nome, cognome=excluded.cognome, "
            "laurea_ctype=excluded.laurea_ctype",
            (matricola, _clean(nome), _clean(cognome), laurea_ctype or None),
        )
        conn.commit()
    finally:
        conn.close()


def aggiorna_studente(tag: str, matricola: str, nome: str, cognome: str, nuova_matricola: Optional[str] = None) -> None:
    matricola = _clean(matricola)
    nuova_matricola = _clean(nuova_matricola) if nuova_matricola else matricola
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if nuova_matricola != matricola:
            if conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (nuova_matricola,)).fetchone():
                raise ValueError(f"Esiste già uno studente con matricola '{nuova_matricola}'")
            # la matricola è chiave primaria referenziata da risultati e orale_obbligatorio:
            # defer_foreign_keys rimanda il controllo dei vincoli a fine transazione, così si
            # può aggiornare prima la tabella genitore e poi le tabelle figlie in sicurezza.
            conn.execute("PRAGMA defer_foreign_keys = ON")
        cur = conn.execute(
            "UPDATE studenti SET nome=?, cognome=?, matricola=? WHERE matricola=?",
            (_clean(nome), _clean(cognome), nuova_matricola, matricola),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Studente con matricola '{matricola}' non trovato")
        if nuova_matricola != matricola:
            conn.execute("UPDATE risultati SET matricola=? WHERE matricola=?", (nuova_matricola, matricola))
            conn.execute("UPDATE orale_obbligatorio SET matricola=? WHERE matricola=?", (nuova_matricola, matricola))
        conn.commit()
    finally:
        conn.close()


def elimina_studente(tag: str, matricola: str) -> None:
    """Rifiuta la cancellazione se lo studente ha già risultati registrati: in quel caso
    andrebbe corretta la matricola con aggiorna_studente, non persa la storia dei voti."""
    matricola = _clean(matricola)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if conn.execute("SELECT 1 FROM risultati WHERE matricola=?", (matricola,)).fetchone():
            raise ValueError(
                f"Lo studente {matricola} ha già risultati registrati: non può essere eliminato "
                "(se la matricola è sbagliata, correggila invece di eliminarlo)"
            )
        conn.execute("DELETE FROM orale_obbligatorio WHERE matricola=?", (matricola,))
        cur = conn.execute("DELETE FROM studenti WHERE matricola=?", (matricola,))
        if cur.rowcount == 0:
            raise ValueError(f"Studente con matricola '{matricola}' non trovato")
        conn.commit()
    finally:
        conn.close()


@dataclass
class ImportReport:
    inseriti: int = 0
    aggiornati: int = 0
    saltati: int = 0
    errori: list[str] = None

    def __post_init__(self):
        if self.errori is None:
            self.errori = []


_SINONIMI_MATRICOLA = ["matricola", "id", "codice", "numero matricola", "n. matricola", "n matricola", "student id"]
_SINONIMI_NOME = ["nome", "first name", "firstname"]
_SINONIMI_COGNOME = ["cognome", "last name", "lastname", "surname"]
_SINONIMI_LAUREA = ["laurea", "corso di laurea", "laurea_ctype", "ctype"]


def _rileva_intestazione(testo: str) -> bool:
    """File diversi possono avere colonne in ordine diverso, o colonne in più: prima di
    importare si chiede sempre all'utente quale colonna usare per cosa (vedi
    anteprima_csv), ma per proporre un abbinamento già corretto serve prima capire se la
    prima riga è un'intestazione (nomi di colonna) o già un dato."""
    try:
        return csv.Sniffer().has_header(testo[:4096])
    except csv.Error:
        return False


def _indovina_colonna(intestazioni: list[str], sinonimi: list[str]) -> Optional[int]:
    for i, h in enumerate(intestazioni):
        if h.strip().lower() in sinonimi:
            return i
    return None


def _righe_csv(testo: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(testo)) if any(cell.strip() for cell in r)]


def anteprima_csv(file_bytes: bytes) -> dict:
    """Analizza un CSV appena caricato: rileva se ha una riga di intestazione e propone
    automaticamente quali colonne usare per matricola/nome/cognome/laurea in base al nome
    delle colonne (se presente); l'utente conferma o corregge la proposta (comprese
    eventuali righe iniziali da ignorare) prima che qualunque dato venga scritto (vedi
    verifica_import_csv)."""
    testo = file_bytes.decode("utf-8-sig")
    righe = _righe_csv(testo)
    if not righe:
        return {
            "csv_testo": testo, "colonne": [], "n_colonne": 0, "ha_intestazione": False,
            "righe_anteprima": [], "totale_righe": 0,
            "matricola_idx": None, "nome_idx": None, "cognome_idx": None, "laurea_idx": None,
        }
    ha_intestazione = _rileva_intestazione(testo)
    n_colonne = max(len(r) for r in righe)
    if ha_intestazione:
        intestazioni = (righe[0] + [""] * n_colonne)[:n_colonne]
        righe_dati = righe[1:]
    else:
        intestazioni = [f"Colonna {i + 1}" for i in range(n_colonne)]
        righe_dati = righe

    return {
        "csv_testo": testo, "colonne": intestazioni, "n_colonne": n_colonne, "ha_intestazione": ha_intestazione,
        "righe_anteprima": [(r + [""] * n_colonne)[:n_colonne] for r in righe_dati[:8]],
        "totale_righe": len(righe_dati),
        "matricola_idx": _indovina_colonna(intestazioni, _SINONIMI_MATRICOLA) if ha_intestazione else (0 if n_colonne > 0 else None),
        "nome_idx": _indovina_colonna(intestazioni, _SINONIMI_NOME) if ha_intestazione else (1 if n_colonne > 1 else None),
        "cognome_idx": _indovina_colonna(intestazioni, _SINONIMI_COGNOME) if ha_intestazione else (2 if n_colonne > 2 else None),
        "laurea_idx": _indovina_colonna(intestazioni, _SINONIMI_LAUREA) if ha_intestazione else None,
    }


def _estrai_righe_csv(
    csv_testo: str, righe_da_ignorare: int, matricola_idx: int, nome_idx: int, cognome_idx: int,
    laurea_idx: Optional[int] = None,
) -> list[dict]:
    righe = _righe_csv(csv_testo)[righe_da_ignorare:]
    max_idx = max(i for i in (matricola_idx, nome_idx, cognome_idx, laurea_idx) if i is not None)
    estratte = []
    for numero, row in enumerate(righe, start=1):
        if len(row) <= max_idx:
            estratte.append({"numero_riga": numero, "errore": f"colonne insufficienti ({row})"})
            continue
        matricola = _clean(row[matricola_idx])
        if not matricola:
            estratte.append({"numero_riga": numero, "errore": "matricola mancante"})
            continue
        estratte.append({
            "numero_riga": numero, "matricola": matricola,
            "nome": _clean(row[nome_idx]), "cognome": _clean(row[cognome_idx]),
            "laurea_ctype": _clean(row[laurea_idx]) if laurea_idx is not None and row[laurea_idx].strip() else None,
            "errore": None,
        })
    return estratte


def verifica_import_csv(
    tag: str, csv_testo: str, righe_da_ignorare: int, matricola_idx: int, nome_idx: int, cognome_idx: int,
    laurea_idx: Optional[int] = None,
) -> dict:
    """Estrae e classifica ogni riga (nuovo studente / aggiornamento di uno già presente /
    errore) senza scrivere nulla nel database: usata per mostrare all'utente esattamente
    cosa succederà, con l'elenco completo, prima di confermare l'importazione vera."""
    righe = _estrai_righe_csv(csv_testo, righe_da_ignorare, matricola_idx, nome_idx, cognome_idx, laurea_idx)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        esistenti = {
            r["matricola"]: r for r in conn.execute("SELECT matricola, nome, cognome FROM studenti")
        }
    finally:
        conn.close()
    nuovi = aggiornati = errori = 0
    for r in righe:
        if r.get("errore"):
            errori += 1
            r["stato"] = "errore"
        elif r["matricola"] in esistenti:
            aggiornati += 1
            r["stato"] = "aggiornamento"
            r["nome_attuale"] = esistenti[r["matricola"]]["nome"]
            r["cognome_attuale"] = esistenti[r["matricola"]]["cognome"]
        else:
            nuovi += 1
            r["stato"] = "nuovo"
    return {"righe": righe, "nuovi": nuovi, "aggiornati": aggiornati, "errori": errori}


def importa_csv_mappato(
    tag: str, csv_testo: str, righe_da_ignorare: int, matricola_idx: int, nome_idx: int, cognome_idx: int,
    laurea_idx: Optional[int] = None, righe_incluse: Optional[set[int]] = None,
) -> ImportReport:
    """Scrive nel database le righe con la mappatura di colonne scelta e confermata
    dall'utente (vedi verifica_import_csv per l'anteprima mostrata prima di questa).
    `righe_incluse`, se indicato, limita l'importazione ai soli numeri di riga presenti
    (l'utente può deselezionarne alcune nella pagina di conferma prima di importare)."""
    righe = _estrai_righe_csv(csv_testo, righe_da_ignorare, matricola_idx, nome_idx, cognome_idx, laurea_idx)
    report = ImportReport()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for r in righe:
            if righe_incluse is not None and r["numero_riga"] not in righe_incluse:
                report.saltati += 1
                continue
            if r.get("errore"):
                report.errori.append(f"Riga {r['numero_riga']}: {r['errore']}")
                report.saltati += 1
                continue
            existing = conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (r["matricola"],)).fetchone()
            conn.execute(
                "INSERT INTO studenti (matricola, nome, cognome, laurea_ctype) VALUES (?,?,?,?) "
                "ON CONFLICT(matricola) DO UPDATE SET nome=excluded.nome, cognome=excluded.cognome, "
                "laurea_ctype=excluded.laurea_ctype",
                (r["matricola"], r["nome"], r["cognome"], r["laurea_ctype"]),
            )
            if existing:
                report.aggiornati += 1
            else:
                report.inseriti += 1
        conn.commit()
    finally:
        conn.close()
    return report
