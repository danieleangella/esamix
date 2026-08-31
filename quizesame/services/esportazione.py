"""Gestione del file "Lista Studenti Esame" caricato dalla segreteria per un appello: si
carica una volta sola (scheda "Compiti d'esame"), da cui si ricava sia l'elenco degli
iscritti (numero, elenco stampabile per fare l'appello, controllo in correzione) sia,
su richiesta, il file compilato da riproporre alla segreteria con le colonne "Esito" e
"Domande d'esame" compilate. Si compilano solo quelle due colonne per gli studenti già
corretti, lasciando invariato il resto del file (comprese le altre colonne e gli
studenti senza un risultato)."""
import csv
import io
from typing import Optional

from quizesame import config, db
from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service


def _decodifica(contenuto: bytes) -> tuple[str, str]:
    for codifica in ("utf-8-sig", "cp1252"):
        try:
            return contenuto.decode(codifica), codifica
        except UnicodeDecodeError:
            continue
    raise ValueError("Non riconosco la codifica del file: attesi UTF-8 o Windows-1252")


def _trova_intestazione(righe: list[list[str]]) -> int:
    indice = next((i for i, r in enumerate(righe) if "Matricola" in r and "Esito" in r), None)
    if indice is None:
        raise ValueError("Non trovo nel file la riga di intestazione con le colonne 'Matricola' ed 'Esito'")
    return indice


def _esito_per_export(risultato: Optional[dict], votomin: int) -> Optional[str]:
    """None significa "non scrivere nulla" (nessuna valutazione ancora completata o
    confermata): lo studente resta come nel file caricato."""
    if risultato is None:
        return None
    if risultato["esito"] == "assente":
        return "ASS"
    if risultato["esito"] == "ritirato":
        return "RIT"
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
    stesso appello). Ritorna il numero di studenti iscritti trovati nel file."""
    testo, codifica = _decodifica(contenuto)
    righe = list(csv.reader(io.StringIO(testo)))
    indice_intestazione = _trova_intestazione(righe)
    idx_matricola = righe[indice_intestazione].index("Matricola")
    n_iscritti = sum(
        1 for r in righe[indice_intestazione + 1:] if len(r) > idx_matricola and r[idx_matricola].strip()
    )

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO appello_segreteria_csv (appello_id, nome_file, contenuto, codifica) VALUES (?,?,?,?) "
            "ON CONFLICT(appello_id) DO UPDATE SET nome_file=excluded.nome_file, contenuto=excluded.contenuto, "
            "codifica=excluded.codifica, caricato_il=CURRENT_TIMESTAMP",
            (appello_id, nome_file, testo, codifica),
        )
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
        conn.commit()
    finally:
        conn.close()


def list_iscritti_manuali(tag: str, appello_id: int) -> list[dict]:
    """Iscritti aggiunti a mano uno per uno (quando non è stato caricato un file della
    segreteria): stessa forma dell'elenco derivato dal CSV."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT s.matricola, s.nome, s.cognome, s.dsa FROM appello_iscritti_manuali im "
            "JOIN studenti s ON s.matricola = im.matricola "
            "WHERE im.appello_id=? ORDER BY s.cognome, s.nome",
            (appello_id,),
        ).fetchall()
        return [
            {"matricola": r["matricola"], "cognome": r["cognome"], "nome": r["nome"], "dsa": bool(r["dsa"])}
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
        conn.commit()
    finally:
        conn.close()


def rimuovi_iscritto_manuale(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "DELETE FROM appello_iscritti_manuali WHERE matricola=? AND appello_id=?", (matricola, appello_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_iscritti(tag: str, appello_id: int) -> Optional[list[dict]]:
    """None se non è stato caricato nessun file per questo appello e non è stato aggiunto
    a mano nessuno studente. Il file della segreteria, quando presente, ha sempre la
    precedenza sull'elenco aggiunto a mano. L'elenco (matricola/cognome/nome/dsa) è
    ordinato per cognome poi nome: i dati anagrafici vengono dall'anagrafica del corso
    quando lo studente è già censito, altrimenti (solo per il CSV) dalle colonne
    Cognome/Nome del file stesso, se presenti."""
    riga_csv = get_segreteria_csv(tag, appello_id)
    if riga_csv is None:
        manuali = list_iscritti_manuali(tag, appello_id)
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
            r["matricola"]: r for r in conn.execute("SELECT matricola, nome, cognome, dsa FROM studenti")
        }
    finally:
        conn.close()

    iscritti = []
    for r in righe[indice_intestazione + 1:]:
        if len(r) <= idx_matricola or not r[idx_matricola].strip():
            continue
        matricola = r[idx_matricola].strip()
        studente = anagrafica.get(matricola)
        if studente:
            iscritti.append({
                "matricola": matricola, "cognome": studente["cognome"], "nome": studente["nome"],
                "dsa": bool(studente["dsa"]),
            })
        else:
            iscritti.append({
                "matricola": matricola,
                "cognome": r[idx_cognome].strip() if idx_cognome is not None and len(r) > idx_cognome else "",
                "nome": r[idx_nome].strip() if idx_nome is not None and len(r) > idx_nome else "",
                "dsa": False,
            })
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


def compila_export_voti(tag: str, appello_id: int) -> tuple[str, str, list[dict], list[dict]]:
    """Compila il file caricato in precedenza per questo appello (vedi
    carica_csv_segreteria) con "Esito" (voto, ASS, RIT o 0 per insufficiente) e "Domande
    d'esame" (testo salvato nelle impostazioni del corso), per gli studenti già corretti.

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
        esito = _esito_per_export(risultato, votomin)
        if esito is None:
            continue
        riga[idx_esito] = esito
        if idx_domande is not None:
            riga[idx_domande] = corso.domande_esame
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
        esito = _esito_per_export(risultato, votomin)
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
