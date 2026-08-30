from datetime import date

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services.correzione import GiaVerbalizzato


def list_idonei(tag: str, appello_id: int) -> list[dict]:
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.verbalizzato=0 AND r.voto >= ? "
            "AND NOT (r.richiede_orale=1 AND r.orale_svolto=0) "
            "AND r.valutazione_sospesa=0 "
            "ORDER BY s.cognome, s.nome",
            (appello_id, votomin),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verbalizza(
    tag: str, appello_id: int, matricola: str, voto: int | None = None, data: str | None = None,
) -> None:
    data = data or date.today().isoformat()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT voto, verbalizzato FROM risultati WHERE matricola=? AND appello_id=?",
            (matricola, appello_id),
        ).fetchone()
        if row is None:
            raise ValueError("Nessun risultato registrato per questo studente in questo appello")
        if row["verbalizzato"]:
            raise GiaVerbalizzato("Questo risultato è già stato verbalizzato")

        voto_finale = voto if voto is not None else row["voto"]
        conn.execute(
            "UPDATE risultati SET voto=?, verbalizzato=1, data_verbalizzazione=? "
            "WHERE matricola=? AND appello_id=?",
            (voto_finale, data, matricola, appello_id),
        )
        conn.commit()
    finally:
        conn.close()
