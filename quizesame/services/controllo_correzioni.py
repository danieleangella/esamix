"""Controllo delle correzioni già fatte: ricalcola ogni voto scritto dalle risposte
registrate e lo confronta con quello salvato, e cerca le domande la cui risposta
"corretta" sembra sospetta (scelta da meno studenti di una sbagliata). Non modifica
nulla: segnala soltanto, perché il docente ricontrolli."""
import json
from collections import Counter, defaultdict

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services.correzione import valuta_con_override


def _ricalcola(corso, soluzioni: str, risposte: str, punteggi: dict[int, int]) -> tuple[int, int]:
    """Il voto ricalcolato in due modi: ignorando maiuscole e minuscole (come la vecchia
    CLI e come la correzione attuale), e con la convenzione usata a mano nei corsi
    importati per gli esercizi obbligatori, dove una lettera minuscola in una stringa per
    il resto maiuscola è una risposta giusta con svolgimento non accettato (= sbagliata)."""
    args = (soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota)
    base = valuta_con_override(*args, override=punteggi)
    minuscole = dict(punteggi)
    if any(ch.isupper() for ch in risposte if ch.upper() != "X"):
        for i, ch in enumerate(risposte):
            if ch.islower() and ch != "x" and i not in minuscole:
                minuscole[i] = corso.risposta_sbagliata
    return base, valuta_con_override(*args, override=minuscole)


def _differenze(soluzioni: str, risposte: str) -> list[dict]:
    return [{"posizione": i + 1, "data": r, "corretta": s} for i, (s, r) in enumerate(zip(soluzioni, risposte))]


def controlla_appello(tag: str, appello, corso=None, conn=None) -> dict:
    corso = corso or corsi_service.get_corso(tag)
    propria = conn is None
    conn = conn or db.get_connection(config.corso_db_path(tag))
    try:
        votomin = corsi_service.effective_votomin(corso, appello)
        righe = conn.execute(
            "SELECT r.*, c.codice, c.soluzioni, s.nome, s.cognome FROM risultati r "
            "JOIN compiti c ON c.id = r.compito_id LEFT JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.risposte IS NOT NULL AND r.esito IN ('voto', 'rifiutato') "
            "ORDER BY s.cognome, s.nome", (appello.id,)
        ).fetchall()
        anomalie, controllati = [], 0
        for r in righe:
            soluzioni, risposte = r["soluzioni"] or "", r["risposte"] or ""
            registrato = r["voto_scritto"] if r["voto_scritto"] is not None else (
                None if (r["richiede_orale"] or r["orale_svolto"]) else r["voto"])
            studente = {"matricola": r["matricola"], "nome": r["nome"] or "", "cognome": r["cognome"] or "",
                        "codice": r["codice"], "risposte": risposte, "soluzioni": soluzioni, "registrato": registrato}
            if len(risposte) != len(soluzioni):
                anomalie.append({**studente, "tipo": "lunghezza", "ricalcolato": None,
                                 "spiegazione": f"Registrate {len(risposte)} risposte, ma il compito ne ha {len(soluzioni)}: "
                                                "probabilmente una risposta è stata saltata o aggiunta trascrivendo."})
                continue
            if registrato is None:
                continue
            controllati += 1
            punteggi = {int(k): v for k, v in json.loads(r["punteggi_obbligatori"] or "{}").items()}
            base, con_minuscole = _ricalcola(corso, soluzioni, risposte, punteggi)
            atteso = {base, con_minuscole}
            if r["insufficiente_per_obbligatorio"]:
                atteso |= {min(v, votomin - 1) for v in atteso}
            if registrato in atteso:
                continue
            diff = registrato - base
            passo_vuota = corso.risposta_sbagliata - corso.risposta_vuota
            if passo_vuota and diff % passo_vuota == 0 and diff // passo_vuota > 0 and "X" in risposte.upper():
                n = diff // passo_vuota
                spiegazione = (f"Differenza di {diff:+d}: sembra che {'una risposta vuota sia stata contata' if n == 1 else f'{n} risposte vuote siano state contate'} "
                               "come sbagliata, o che le risposte registrate non siano quelle usate per il voto.")
            elif diff > 0 and diff % (corso.risposta_corretta - corso.risposta_sbagliata or 1) == 0:
                spiegazione = (f"Voto più alto di {diff} rispetto al ricalcolo: forse una risposta allora contata come giusta "
                               "ora risulta sbagliata (soluzioni corrette dopo la correzione, o domanda annullata).")
            elif diff < 0 and -diff % (corso.risposta_corretta - corso.risposta_sbagliata or 1) == 0:
                spiegazione = (f"Voto più basso di {-diff} rispetto al ricalcolo: forse una risposta giusta è stata contata "
                               "come sbagliata, o lo svolgimento di un esercizio obbligatorio non è stato accettato.")
            else:
                spiegazione = (f"Differenza di {diff:+d} che non corrisponde a una singola risposta: forse il voto è stato "
                               "inserito o modificato a mano.")
            anomalie.append({**studente, "tipo": "voto", "ricalcolato": base, "differenza": diff, "spiegazione": spiegazione})
        return {"appello": appello, "controllati": controllati, "anomalie": anomalie,
                "domande_sospette": _domande_sospette(conn, appello.id)}
    finally:
        if propria:
            conn.close()


def _domande_sospette(conn, appello_id: int) -> list[dict]:
    """Domande in cui una stessa risposta sbagliata è stata scelta da più studenti di
    quella indicata come corretta: spesso è la chiave a essere sbagliata. Richiede il
    dettaglio dei compiti (quale esercizio e quale ordine delle risposte in ogni posizione)."""
    conteggi: dict[tuple, Counter] = defaultdict(Counter)
    nomi, testi = {}, {}
    righe = conn.execute(
        "SELECT r.risposte, c.soluzioni, ce.posizione, ce.esercizio_id, ce.risposte_mischiate, e.nome "
        "FROM risultati r JOIN compiti c ON c.id = r.compito_id "
        "JOIN compito_esercizi ce ON ce.compito_id = c.id JOIN esercizi e ON e.id = ce.esercizio_id "
        "WHERE r.appello_id=? AND r.risposte IS NOT NULL AND ce.aperta = 0 AND ce.risposte_mischiate IS NOT NULL",
        (appello_id,),
    ).fetchall()
    for r in righe:
        i, risposte, soluzioni = r["posizione"], r["risposte"], r["soluzioni"]
        if i >= len(risposte) or i >= len(soluzioni):
            continue
        opzioni = json.loads(r["risposte_mischiate"])
        indice_corretta = ord(soluzioni[i].upper()) - ord("A")
        if not 0 <= indice_corretta < len(opzioni):
            continue
        # la variante si riconosce dalla risposta corretta (stesso esercizio, varianti diverse)
        chiave = (r["esercizio_id"], opzioni[indice_corretta])
        nomi[chiave] = r["nome"]
        testi[chiave] = sorted(opzioni)
        lettera = risposte[i].upper()
        if lettera == "X":
            continue
        indice = ord(lettera) - ord("A")
        if 0 <= indice < len(opzioni):
            conteggi[chiave][opzioni[indice]] += 1
    sospette = []
    for (esercizio_id, corretta), c in conteggi.items():
        n_corretta = c.get(corretta, 0)
        sbagliata, n_sbagliata = next(((t, n) for t, n in c.most_common() if t != corretta), (None, 0))
        if sbagliata is not None and n_sbagliata >= 3 and n_sbagliata > n_corretta:
            domanda = conn.execute(
                "SELECT testo, risposte_json FROM esercizio_varianti WHERE esercizio_id=?", (esercizio_id,)
            ).fetchall()
            testo = next((v["testo"] for v in domanda if corretta in json.loads(v["risposte_json"])), "")
            sospette.append({"esercizio_id": esercizio_id, "nome": nomi[(esercizio_id, corretta)], "testo": testo,
                             "corretta": corretta, "n_corretta": n_corretta,
                             "sbagliata": sbagliata, "n_sbagliata": n_sbagliata, "risposte": sum(c.values())})
    sospette.sort(key=lambda s: s["n_sbagliata"] - s["n_corretta"], reverse=True)
    return sospette


def controlla_corso(tag: str) -> list[dict]:
    """Il controllo di ogni appello del corso che ha compiti corretti, in ordine."""
    corso = corsi_service.get_corso(tag)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        esiti = []
        for appello in sorted(corsi_service.list_appelli(tag), key=lambda a: a.id):
            esito = controlla_appello(tag, appello, corso, conn)
            if esito["controllati"] or esito["anomalie"]:
                senza_dettaglio = conn.execute(
                    "SELECT count(*) FROM compiti c WHERE c.appello_id=? AND EXISTS "
                    "(SELECT 1 FROM risultati r WHERE r.compito_id = c.id) AND NOT EXISTS "
                    "(SELECT 1 FROM compito_esercizi ce WHERE ce.compito_id = c.id)", (appello.id,)
                ).fetchone()[0]
                esiti.append({**esito, "compiti_senza_dettaglio": senza_dettaglio})
        return esiti
    finally:
        conn.close()


def riepilogo_tutti_i_corsi() -> list[dict]:
    riepilogo = []
    for corso in corsi_service.list_corsi():
        try:
            esiti = controlla_corso(corso.tag)
        except Exception:
            continue
        riepilogo.append({
            "corso": corso, "controllati": sum(e["controllati"] for e in esiti),
            "anomalie": sum(len(e["anomalie"]) for e in esiti),
            "domande_sospette": sum(len(e["domande_sospette"]) for e in esiti),
        })
    return riepilogo
