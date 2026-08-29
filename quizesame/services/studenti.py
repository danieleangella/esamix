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


def _risultati_studente(tag: str, matricola: str) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT a.nome AS appello_nome, r.voto, r.verbalizzato, r.data_verbalizzazione "
            "FROM risultati r JOIN appelli a ON a.id = r.appello_id "
            "WHERE r.matricola=? ORDER BY a.data IS NULL, a.data, a.id",
            (matricola,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


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


def aggiorna_studente(tag: str, matricola: str, nome: str, cognome: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "UPDATE studenti SET nome=?, cognome=? WHERE matricola=?",
            (_clean(nome), _clean(cognome), _clean(matricola)),
        )
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


def importa_csv(tag: str, file_bytes: bytes) -> ImportReport:
    """Formato atteso: matricola,nome,cognome[,laurea_ctype]. Robusto a BOM UTF-8."""
    text = file_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    report = ImportReport()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for i, row in enumerate(reader, start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) < 3:
                report.errori.append(f"Riga {i}: colonne insufficienti ({row})")
                report.saltati += 1
                continue
            matricola = _clean(row[0])
            nome = _clean(row[1])
            cognome = _clean(row[2])
            laurea_ctype = _clean(row[3]) if len(row) > 3 and row[3].strip() else None
            if not matricola:
                report.errori.append(f"Riga {i}: matricola mancante")
                report.saltati += 1
                continue
            existing = conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (matricola,)).fetchone()
            conn.execute(
                "INSERT INTO studenti (matricola, nome, cognome, laurea_ctype) VALUES (?,?,?,?) "
                "ON CONFLICT(matricola) DO UPDATE SET nome=excluded.nome, cognome=excluded.cognome",
                (matricola, nome, cognome, laurea_ctype),
            )
            if existing:
                report.aggiornati += 1
            else:
                report.inseriti += 1
        conn.commit()
    finally:
        conn.close()
    return report
