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


def verbalizza_multipli(tag: str, appello_id: int, matricole: list[str], data: str | None = None) -> int:
    """Verbalizza in blocco gli studenti scelti (dalla lista degli idonei, con la
    possibilità di deselezionarne alcuni prima di confermare): un matricola non più
    idoneo nel frattempo (es. verbalizzato da un'altra scheda nel frattempo) viene
    semplicemente ignorato invece di far fallire l'intera operazione."""
    idonei = {r["matricola"] for r in list_idonei(tag, appello_id)}
    n = 0
    for matricola in matricole:
        if matricola not in idonei:
            continue
        verbalizza(tag, appello_id, matricola, data=data)
        n += 1
    return n


def list_verbalizzati(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.verbalizzato=1 "
            "ORDER BY s.cognome, s.nome",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def annulla_verbalizzazione(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "UPDATE risultati SET verbalizzato=0, data_verbalizzazione=NULL "
            "WHERE matricola=? AND appello_id=? AND verbalizzato=1",
            (matricola, appello_id),
        )
        if cur.rowcount == 0:
            raise ValueError("Nessun risultato verbalizzato trovato per questo studente in questo appello")
        conn.commit()
    finally:
        conn.close()
