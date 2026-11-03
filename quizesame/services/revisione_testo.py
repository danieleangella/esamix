"""Revisione del testo dopo la prova: un esercizio rivelatosi sbagliato si annulla (vale il
punteggio pieno per tutti), uno obbligatorio rivelatosi troppo difficile diventa
facoltativo (non svolgerlo non rende più insufficiente il compito). Prima di applicarla si
vede come cambia il voto di ciascuno studente; applicata, ricalcola i voti già registrati,
tranne i verbalizzati e quelli cambiati a mano. Un'eventualità rara: tutto sta qui, e il
calcolo del voto ne tiene conto in correzione.calcola_voto."""
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import correzione as correzione_service

REVISIONI = {
    "annullato": "annullato: punteggio pieno per tutti",
    "facoltativo": "non più obbligatorio",
}


def _posizioni_per_compito(conn, appello_id: int) -> dict[int, list[dict]]:
    """Le posizioni di ogni compito dell'appello, senza la revisione (la si applica dopo)."""
    posizioni: dict[int, list[dict]] = {}
    for p in conn.execute(
        "SELECT ce.compito_id, ce.posizione, ce.obbligatorio, ce.aperta, ce.esercizio_id FROM compito_esercizi ce "
        "JOIN compiti c ON c.id = ce.compito_id WHERE c.appello_id=? ORDER BY ce.posizione",
        (appello_id,),
    ):
        posizioni.setdefault(p["compito_id"], []).append(dict(p))
    return posizioni


def revisione_attuale(tag: str, appello_id: int) -> dict[int, str]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return {
            r["esercizio_id"]: r["revisione"] for r in conn.execute(
                "SELECT esercizio_id, revisione FROM appello_revisione_esercizi WHERE appello_id=?", (appello_id,))
        }
    finally:
        conn.close()


def esercizi_prova(tag: str, appello_id: int) -> list[dict]:
    """Gli esercizi finiti nei compiti della prova, nell'ordine del testo, con la
    revisione attuale ("annullato", "facoltativo" o None)."""
    revisione = revisione_attuale(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        righe = conn.execute(
            "SELECT e.id, e.nome, MAX(ce.obbligatorio) AS obbligatorio, MAX(ce.aperta) AS aperta, "
            "MIN(ce.posizione) AS prima FROM compito_esercizi ce JOIN compiti c ON c.id = ce.compito_id "
            "JOIN esercizi e ON e.id = ce.esercizio_id WHERE c.appello_id=? GROUP BY e.id ORDER BY prima",
            (appello_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r["id"], "nome": r["nome"] or f"Esercizio #{r['id']}", "obbligatorio": bool(r["obbligatorio"]),
         "aperta": bool(r["aperta"]), "revisione": revisione.get(r["id"])}
        for r in righe
    ]


def _valida(tag: str, appello_id: int, nuova: dict[int, Optional[str]]) -> dict[int, str]:
    esercizi = {e["id"]: e for e in esercizi_prova(tag, appello_id)}
    valida = {}
    for esercizio_id, revisione in nuova.items():
        if not revisione:
            continue
        e = esercizi.get(esercizio_id)
        if e is None:
            raise ValueError(f"L'esercizio #{esercizio_id} non è nei compiti di questa prova")
        if revisione not in REVISIONI:
            raise ValueError(f"Revisione non valida: {revisione}")
        if revisione == "facoltativo" and not e["obbligatorio"]:
            raise ValueError(f"«{e['nome']}» non era obbligatorio: non c'è niente da rendere facoltativo")
        valida[esercizio_id] = revisione
    return valida


def _con_revisione(posizioni: list[dict], revisione: dict[int, str]) -> list[dict]:
    return [{**p, "revisione": revisione.get(p["esercizio_id"])} for p in posizioni]


def analisi(tag: str, appello_id: int, nuova: dict[int, Optional[str]]) -> dict:
    """Come cambierebbe il voto scritto di ogni compito registrato passando dalla revisione
    attuale a `nuova` ({esercizio_id: "annullato" | "facoltativo" | None}). Ogni voce ha il
    voto attuale e quello nuovo, e "stato": "ricalcolato", "verbalizzato" o "a_mano" (voto
    finale scelto dal docente: non si tocca, si mostra solo quello che verrebbe). Nel
    riepilogo, quanti aumentano o diminuiscono e chi diventa sufficiente o insufficiente."""
    nuova = _valida(tag, appello_id, nuova)
    attuale = revisione_attuale(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        risultati = conn.execute(
            "SELECT r.*, c.soluzioni, s.nome, s.cognome FROM risultati r "
            "JOIN compiti c ON c.id = r.compito_id JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.esito IN ('voto', 'rifiutato') "
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        posizioni_per_compito = _posizioni_per_compito(conn, appello_id)
    finally:
        conn.close()

    voci = []
    for r in risultati:
        posizioni = posizioni_per_compito.get(r["compito_id"])
        if not posizioni:
            continue  # compito senza esercizi collegati (dati storici): la revisione non lo tocca
        soluzioni, risposte = r["soluzioni"], r["risposte"] or ""
        punteggi = correzione_service._json_posizioni(r["punteggi_obbligatori"])
        prima = correzione_service.calcola_voto(
            corso, votomin, soluzioni, risposte, _con_revisione(posizioni, attuale), punteggi)
        dopo = correzione_service.calcola_voto(
            corso, votomin, soluzioni, risposte, _con_revisione(posizioni, nuova), punteggi)
        voto_a_mano = bool(r["voto_modificato"]) and not r["valutazione_sospesa"] and r["voto_scritto"] is not None
        stato = "verbalizzato" if r["verbalizzato"] else ("a_mano" if voto_a_mano else "ricalcolato")
        voto_attuale = r["voto_scritto"] if r["voto_scritto"] is not None else prima.voto
        voto_nuovo = dopo.voto if stato == "ricalcolato" else voto_attuale
        voci.append({
            "matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"], "stato": stato,
            "voto_attuale": voto_attuale, "voto_nuovo": voto_nuovo, "voto_calcolato": dopo.voto,
            "differenza": voto_nuovo - voto_attuale,
            "promosso": stato == "ricalcolato" and voto_attuale < votomin <= voto_nuovo,
            "bocciato": stato == "ricalcolato" and voto_nuovo < votomin <= voto_attuale,
            "insufficiente_dopo": dopo.insufficiente,
            "non_piu_insufficiente_per_obbligatorio": bool(r["insufficiente_per_obbligatorio"]) and not dopo.insufficiente,
            "sospesa": bool(r["valutazione_sospesa"]), "orale_svolto": bool(r["orale_svolto"]),
            "cambierebbe": stato != "ricalcolato" and dopo.voto != prima.voto,
        })
    ricalcolati = [v for v in voci if v["stato"] == "ricalcolato"]
    return {
        "voci": voci, "votomin": votomin, "revisione": nuova,
        "aumentano": sum(1 for v in ricalcolati if v["differenza"] > 0),
        "diminuiscono": sum(1 for v in ricalcolati if v["differenza"] < 0),
        "invariati": sum(1 for v in ricalcolati if v["differenza"] == 0),
        "promossi": [v for v in voci if v["promosso"]],
        "bocciati": [v for v in voci if v["bocciato"]],
        "esclusi": [v for v in voci if v["stato"] != "ricalcolato" and v["cambierebbe"]],
        "cambia_qualcosa": nuova != attuale,
    }


def applica(tag: str, appello_id: int, nuova: dict[int, Optional[str]]) -> dict:
    """Salva la revisione e ricalcola i voti scritti come mostrato da analisi(): non tocca
    i verbalizzati né i voti cambiati a mano; con l'orale già svolto cambia solo il voto
    scritto di riferimento. Solo a correzione aperta: chiudendola si rigenerano l'elenco
    dei risultati e, per le prove di un raggruppamento, i risultati complessivi."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    appello = corsi_service.get_appello(tag, appello_id)
    if appello.correzione_scritti_conclusa:
        raise ValueError("La correzione di questa prova è conclusa: riaprila dalla scheda Correzione per applicare la revisione")
    esito = analisi(tag, appello_id, nuova)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("DELETE FROM appello_revisione_esercizi WHERE appello_id=?", (appello_id,))
        conn.executemany(
            "INSERT INTO appello_revisione_esercizi (appello_id, esercizio_id, revisione) VALUES (?,?,?)",
            [(appello_id, esercizio_id, revisione) for esercizio_id, revisione in esito["revisione"].items()],
        )
        for v in esito["voci"]:
            if v["stato"] != "ricalcolato":
                continue
            conn.execute(
                "UPDATE risultati SET voto_scritto=?, voto=CASE WHEN orale_svolto=1 THEN voto ELSE ? END, "
                "insufficiente_per_obbligatorio=? WHERE appello_id=? AND matricola=?",
                (v["voto_nuovo"], v["voto_nuovo"], v["insufficiente_dopo"], appello_id, v["matricola"]),
            )
        conn.commit()
    finally:
        conn.close()
    return esito

