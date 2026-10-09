"""Registro presenze di un appello: una spunta "presente" per ogni iscritto, da usare per
fare l'appello in aula prima ancora di correggere. Non tocca i risultati (voto/assente/
ritirato): quando l'appello si chiude in aula, si "chiude" anche questo registro, e solo
allora chi non risulta presente (né già valutato) viene segnato 'assente' in risultati."""
from typing import Optional

from quizesame import config, db
from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service
from quizesame.services import esportazione as esportazione_service


def list_presenti(tag: str, appello_id: int) -> set[str]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return {
            r["matricola"] for r in
            conn.execute("SELECT matricola FROM appello_presenze WHERE appello_id=?", (appello_id,))
        }
    finally:
        conn.close()


def segna_presente(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO appello_presenze (matricola, appello_id) VALUES (?,?) "
            "ON CONFLICT(matricola, appello_id) DO NOTHING",
            (matricola, appello_id),
        )
        conn.commit()
    finally:
        conn.close()


def rimuovi_presente(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("DELETE FROM appello_presenze WHERE matricola=? AND appello_id=?", (matricola, appello_id))
        conn.commit()
    finally:
        conn.close()


def _con_risultato(conn, appello_id: int) -> dict[str, str]:
    """matricola -> valore di risultati.esito ('voto'/'assente'/'ritirato'/'rifiutato')."""
    return {
        r["matricola"]: r["esito"] for r in
        conn.execute("SELECT matricola, esito FROM risultati WHERE appello_id=?", (appello_id,))
    }


def riepilogo(tag: str, appello_id: int) -> Optional[dict]:
    """None se per questo appello non c'è nessun elenco iscritti (né file della segreteria
    né iscritti aggiunti a mano): il registro presenze si basa su quell'elenco. Uno
    studente già valutato (o già segnato assente/ritirato) conta come presente a
    prescindere dalla spunta, e non è più possibile togliergliela: ha comunque già un
    esito per questo appello ('esito', usato per distinguere chi risulta "assente" o
    "ritirato" da chi ha davvero un voto)."""
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is None:
        return None
    presenti = list_presenti(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        con_risultato = _con_risultato(conn, appello_id)
    finally:
        conn.close()
    elenco = [
        {
            **s,
            "presente": s["matricola"] in presenti or s["matricola"] in con_risultato,
            "bloccato": s["matricola"] in con_risultato,
            "esito": con_risultato.get(s["matricola"]),
        }
        for s in iscritti
    ]
    n_presenti = sum(1 for s in elenco if s["presente"])
    return {"iscritti": elenco, "n_iscritti": len(elenco), "n_presenti": n_presenti}


def _segna_assenti(tag: str, appello_id: int, elenco: list[dict], escludi: set[str]) -> dict:
    """Segna 'assente' ogni studente di `elenco` senza già un risultato e non in
    `escludi`. Le matricole non ancora registrate come studenti di questo corso
    (possibile per righe del file della segreteria mai importate) non possono ricevere un
    risultato: sono ritornate a parte, da registrare a mano prima di poterle segnare."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        registrati = {r["matricola"] for r in conn.execute("SELECT matricola FROM studenti")}
        con_risultato = _con_risultato(conn, appello_id)
    finally:
        conn.close()

    segnati_assenti = 0
    non_registrati = []
    errori = []
    for s in elenco:
        matricola = s["matricola"]
        if matricola in escludi or matricola in con_risultato:
            continue
        if matricola not in registrati:
            non_registrati.append(s)
            continue
        try:
            correzione_service.segna_esito_speciale(tag, appello_id, matricola, "assente")
            segnati_assenti += 1
        except Exception as e:
            # un imprevisto su un singolo studente (es. una condizione di corsa con una
            # correzione fatta nel frattempo) non deve impedire di segnare comunque
            # assenti tutti gli altri.
            errori.append({**s, "errore": str(e)})
    return {"segnati_assenti": segnati_assenti, "non_registrati": non_registrati, "errori": errori}


def chiudi(tag: str, appello_id: int) -> dict:
    """Segna 'assente' (in risultati) ogni iscritto non spuntato presente e senza già un
    risultato: da fare una volta sola, quando l'appello è finito e non arriva più nessuno."""
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is None:
        raise ValueError("Nessun elenco iscritti caricato per questo appello")
    corsi_service.verifica_appello_aperto(tag, appello_id)
    esito = _segna_assenti(tag, appello_id, iscritti, list_presenti(tag, appello_id))
    corsi_service.update_appello(tag, appello_id, presenze_chiuse=True)
    return esito


def elenco_da_valutare(tag: str, appello_id: int, ammessi: Optional[list[dict]] = None) -> Optional[list[dict]]:
    """Gli studenti attesi a questa prova: l'elenco iscritti (file della segreteria o
    aggiunti a mano) o, se manca, gli ammessi passati dal chiamante (prove di un
    raggruppamento). None se non c'è nessun elenco a cui fare riferimento."""
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is not None:
        return iscritti
    return ammessi


def non_valutati(tag: str, appello_id: int, ammessi: Optional[list[dict]] = None) -> Optional[list[dict]]:
    """Studenti attesi (vedi elenco_da_valutare) ancora senza nessun risultato, esclusi
    quelli con una correzione in bozza (la si sta facendo). None se non c'è un elenco."""
    elenco = elenco_da_valutare(tag, appello_id, ammessi)
    if elenco is None:
        return None
    bozze = {b["matricola"] for b in correzione_service.list_bozze(tag, appello_id)}
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        con_risultato = _con_risultato(conn, appello_id)
    finally:
        conn.close()
    return [s for s in elenco if s["matricola"] not in con_risultato and s["matricola"] not in bozze]


def segna_assenti_non_valutati(tag: str, appello_id: int, ammessi: Optional[list[dict]] = None) -> dict:
    """Segna 'assente' tutti gli studenti attesi ancora senza un risultato (presenti
    spuntati compresi): per chiudere la correzione quando i compiti consegnati sono
    stati tutti corretti. Chi ha una correzione in bozza non viene toccato."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    elenco = elenco_da_valutare(tag, appello_id, ammessi)
    if elenco is None:
        raise ValueError("Nessun elenco iscritti per questa prova: non so chi segnare come assente")
    bozze = {b["matricola"] for b in correzione_service.list_bozze(tag, appello_id)}
    return _segna_assenti(tag, appello_id, elenco, bozze)


def riapri(tag: str, appello_id: int) -> None:
    corsi_service.update_appello(tag, appello_id, presenze_chiuse=False)
