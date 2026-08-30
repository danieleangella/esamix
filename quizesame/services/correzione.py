import json
from dataclasses import dataclass, field
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import compiti as compiti_service


class StudenteNonTrovato(Exception):
    pass


class CompitoNonTrovato(Exception):
    pass


class GiaVerbalizzato(Exception):
    pass


class RisultatoGiaValutato(Exception):
    """Lo studente ha già un esito registrato per questo appello (voto, assente o
    ritirato): una correzione "nuova" non può sovrascriverlo per sbaglio — va usato
    esplicitamente "Modifica" se si intende correggerlo di nuovo."""


class OraleNonConsentito(Exception):
    """L'orale si può richiedere solo per un compito scritto sufficiente."""


class OraleObbligatorioNonConfermato(Exception):
    """Lo studente ha una riga in orale_obbligatorio (l'orale gli è già stato imposto in
    un appello precedente, in questo corso o in uno da cui è stato importato) ma il
    correttore non ha spuntato la conferma: il salvataggio viene bloccato per non
    registrare per sbaglio il voto scritto come se fosse quello finale."""

    def __init__(self, motivazione: str, origine: str):
        self.motivazione = motivazione
        self.origine = origine
        super().__init__("Questo studente deve fare l'orale in ogni appello: conferma per procedere")


def valuta_con_override(
    str_corretta: str, str_grade: str, risposta_corretta: int, risposta_sbagliata: int, risposta_vuota: int,
    override: Optional[dict[int, int]] = None,
) -> int:
    """Come valuta(), ma per le posizioni elencate in `override` usa direttamente il
    punteggio indicato invece di calcolarlo dalla lettera scelta: serve per gli esercizi
    obbligatori risposti correttamente, dove il punteggio finale lo decide il docente in
    base allo svolgimento scritto (tra risposta_sbagliata e risposta_corretta)."""
    override = override or {}
    punteggio = 0
    for i in range(len(str_grade)):
        if i in override:
            punteggio += override[i]
            continue
        ch = str_grade[i].upper()
        if ch == "X":
            punteggio += risposta_vuota
        elif ch == str_corretta[i]:
            punteggio += risposta_corretta
        else:
            punteggio += risposta_sbagliata
    return punteggio


def valuta(str_corretta: str, str_grade: str, risposta_corretta: int, risposta_sbagliata: int, risposta_vuota: int) -> int:
    return valuta_con_override(str_corretta, str_grade, risposta_corretta, risposta_sbagliata, risposta_vuota)


def _carica_dati_correzione(conn, matricola: str, appello_id: int, codice: str):
    studente = conn.execute(
        "SELECT matricola, nome, cognome FROM studenti WHERE matricola=?", (matricola,)
    ).fetchone()
    if studente is None:
        raise StudenteNonTrovato(f"Matricola '{matricola}' non registrata in questo corso")

    esistente = conn.execute(
        "SELECT verbalizzato FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
    ).fetchone()
    if esistente and esistente["verbalizzato"]:
        raise GiaVerbalizzato(
            f"Lo studente {matricola} ha già un voto verbalizzato per questo appello: "
            "non può essere corretto di nuovo."
        )

    compito = conn.execute(
        "SELECT id, soluzioni FROM compiti WHERE appello_id=? AND codice=?", (appello_id, codice)
    ).fetchone()
    if compito is None:
        raise CompitoNonTrovato(f"Nessun compito con codice '{codice}' per questo appello")

    return studente, compito


def _orale_obbligatorio(conn, matricola: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT motivazione, origine FROM orale_obbligatorio WHERE matricola=?", (matricola,)
    ).fetchone()
    return dict(row) if row else None


def _applica_soglia_orale_automatica(conn, corso, matricola: str) -> None:
    """Se il corso ha impostato 'orale obbligatorio dopo N compiti con voto sotto M', e lo
    studente ha appena raggiunto quella soglia (contando tutti gli appelli del corso), lo
    rende obbligato all'orale da qui in avanti (a meno che non lo sia già)."""
    if not corso.orale_soglia_attiva or not corso.orale_soglia_n or corso.orale_soglia_voto is None:
        return
    if _orale_obbligatorio(conn, matricola):
        return
    query = "SELECT COUNT(*) c FROM risultati WHERE matricola=? AND (voto < ?"
    params = [matricola, corso.orale_soglia_voto]
    if corso.ritirato_conta_insufficiente:
        query += " OR esito='ritirato'"
    query += ")"
    n_insufficienti = conn.execute(query, params).fetchone()["c"]
    if n_insufficienti >= corso.orale_soglia_n:
        conn.execute(
            "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
            "ON CONFLICT(matricola) DO NOTHING",
            (
                matricola,
                f"regola automatica del corso: {n_insufficienti} compiti con voto inferiore a {corso.orale_soglia_voto}",
                "regola automatica",
            ),
        )


@dataclass
class RigaRisposta:
    posizione: int
    esercizio_id: int
    esercizio_nome: str
    obbligatorio: bool
    lettera_data: str
    lettera_corretta: str
    svolta: bool  # ha dato una risposta (diversa da 'X') in quella posizione
    corretta: bool  # la lettera scelta coincide con quella corretta


@dataclass
class ValutazionePreliminare:
    matricola: str
    nome: str
    cognome: str
    codice: str
    risposte: str
    voto_base: int
    righe: list[RigaRisposta] = field(default_factory=list)
    non_svolte_obbligatorie: list[RigaRisposta] = field(default_factory=list)
    da_valutare: list[RigaRisposta] = field(default_factory=list)
    orale_obbligatorio: Optional[dict] = None


def _righe_risposta(tag: str, compito_id: int, soluzioni: str, risposte: str) -> list[RigaRisposta]:
    righe = []
    for pos in compiti_service.list_posizioni_compito(tag, compito_id):
        i = pos["posizione"]
        lettera_data = risposte[i].upper() if i < len(risposte) else "X"
        lettera_corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
        svolta = lettera_data != "X"
        righe.append(RigaRisposta(
            posizione=i, esercizio_id=pos["esercizio_id"],
            esercizio_nome=pos["esercizio_nome"] or f"Esercizio #{pos['esercizio_id']}",
            obbligatorio=bool(pos["obbligatorio"]), lettera_data=lettera_data, lettera_corretta=lettera_corretta,
            svolta=svolta, corretta=svolta and lettera_data == lettera_corretta,
        ))
    return righe


def valuta_preliminare(tag: str, appello_id: int, matricola: str, codice: str, risposte: str) -> ValutazionePreliminare:
    """Calcola il voto "a lettere" e prepara il riepilogo completo (tutte le risposte,
    corrette o no) mostrato sempre prima di salvare. Segnala separatamente gli esercizi
    obbligatori: non svolti (compito automaticamente insufficiente) o svolti con la
    risposta multiple-choice corretta (il docente deve ancora giudicare lo svolgimento
    scritto e assegnare un punteggio) — un obbligatorio svolto ma con risposta sbagliata
    non richiede invece nessuna valutazione aggiuntiva, conta come risposta sbagliata."""
    corso = corsi_service.get_corso(tag)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        studente, compito = _carica_dati_correzione(conn, matricola, appello_id, codice)
        soluzioni = compito["soluzioni"]
        voto_base = valuta(soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota)

        righe = _righe_risposta(tag, compito["id"], soluzioni, risposte)
        non_svolte = [r for r in righe if r.obbligatorio and not r.svolta]
        da_valutare = [r for r in righe if r.obbligatorio and r.svolta and r.corretta]

        return ValutazionePreliminare(
            matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            codice=codice, risposte=risposte, voto_base=voto_base, righe=righe,
            non_svolte_obbligatorie=non_svolte, da_valutare=da_valutare,
            orale_obbligatorio=_orale_obbligatorio(conn, studente["matricola"]),
        )
    finally:
        conn.close()


@dataclass
class CorrezioneResult:
    voto: int
    matricola: str
    nome: str
    cognome: str
    insufficiente_per_obbligatorio: bool = False
    richiede_orale: bool = False


def conferma_risultato(
    tag: str, appello_id: int, matricola: str, codice: str, risposte: str,
    voto_finale: Optional[int] = None,
    punteggi_obbligatori: Optional[dict[int, int]] = None,
    richiedi_orale: bool = False,
    orale_motivazione: str = "",
    conferma_orale_obbligatorio: bool = False,
    modifica: bool = False,
) -> CorrezioneResult:
    """Salva il risultato. `punteggi_obbligatori` sono i punteggi (tra risposta_sbagliata
    e risposta_corretta) assegnati dal docente agli esercizi obbligatori risposti
    correttamente, dopo aver giudicato lo svolgimento scritto. Se `richiedi_orale`, il
    voto scritto viene comunque salvato come riferimento ma l'esito resta "in attesa di
    orale" finché non si chiama completa_orale(). Se lo studente ha già una riga in
    orale_obbligatorio (imposta in un appello precedente, anche di un altro corso da cui
    è stato importato) e non è arrivata `conferma_orale_obbligatorio`, non salva nulla e
    solleva OraleObbligatorioNonConfermato perché il chiamante mostri l'avviso. Se lo
    studente ha già un esito registrato per questo appello (voto, assente o ritirato) e
    `modifica` non è True, non salva nulla e solleva RisultatoGiaValutato: una
    correzione "nuova" (dal form principale) non deve poter sovrascrivere per sbaglio un
    esito già inserito — solo un "Modifica" esplicito può."""
    punteggi_obbligatori = punteggi_obbligatori or {}
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        studente, compito = _carica_dati_correzione(conn, matricola, appello_id, codice)
        soluzioni = compito["soluzioni"]

        if not modifica:
            esistente = conn.execute(
                "SELECT 1 FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
            ).fetchone()
            if esistente:
                raise RisultatoGiaValutato(
                    f"Lo studente {matricola} ha già un esito registrato per questo appello: "
                    "usa \"Modifica\" nella tabella dei risultati per correggerlo."
                )

        orale_obb = _orale_obbligatorio(conn, studente["matricola"])
        if orale_obb and not richiedi_orale and not conferma_orale_obbligatorio:
            raise OraleObbligatorioNonConfermato(orale_obb["motivazione"] or "", orale_obb["origine"] or "")

        voto_calcolato = valuta_con_override(
            soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota,
            override=punteggi_obbligatori,
        )

        non_svolte_obbligatorie = [
            p for p in compiti_service.list_posizioni_compito(tag, compito["id"])
            if p["obbligatorio"] and (p["posizione"] >= len(risposte) or risposte[p["posizione"]].upper() == "X")
        ]
        insufficiente = bool(non_svolte_obbligatorie)
        votomin = corsi_service.effective_votomin(corso, appello)

        voto = voto_finale if voto_finale is not None else voto_calcolato
        if richiedi_orale and (insufficiente or voto < votomin):
            raise OraleNonConsentito("L'orale si può richiedere solo se il compito scritto è sufficiente.")
        if insufficiente and voto >= votomin:
            voto = votomin - 1

        # Se l'orale è già stato completato per questo esito, ricorreggere lo scritto (es.
        # dalla pagina "Modifica") non deve cancellarne l'esito: richiede_orale/orale_svolto/
        # esito_orale restano quelli già registrati finché non si passa da completa_orale().
        conn.execute(
            "INSERT INTO risultati (matricola, appello_id, compito_id, risposte, voto, voto_scritto, esito, "
            "insufficiente_per_obbligatorio, punteggi_obbligatori, richiede_orale, orale_svolto, "
            "orale_motivazione, esito_orale) VALUES (?,?,?,?,?,?,'voto',?,?,?,?,?,NULL) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET "
            "compito_id=excluded.compito_id, risposte=excluded.risposte, "
            "voto=CASE WHEN risultati.orale_svolto=1 THEN risultati.voto ELSE excluded.voto END, "
            "voto_scritto=excluded.voto_scritto, esito='voto', "
            "insufficiente_per_obbligatorio=excluded.insufficiente_per_obbligatorio, "
            "punteggi_obbligatori=excluded.punteggi_obbligatori, "
            "richiede_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.richiede_orale ELSE excluded.richiede_orale END, "
            "orale_svolto=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_svolto ELSE excluded.orale_svolto END, "
            "orale_motivazione=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_motivazione ELSE excluded.orale_motivazione END, "
            "esito_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.esito_orale ELSE excluded.esito_orale END",
            (
                matricola, appello_id, compito["id"], risposte, voto, voto, insufficiente,
                json.dumps(punteggi_obbligatori) if punteggi_obbligatori else None,
                richiedi_orale, False, orale_motivazione or None,
            ),
        )
        if richiedi_orale and corso.orale_dopo_richiesta:
            conn.execute(
                "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
                "ON CONFLICT(matricola) DO NOTHING",
                (matricola, orale_motivazione or "", appello.nome),
            )
        _applica_soglia_orale_automatica(conn, corso, matricola)
        conn.commit()
        return CorrezioneResult(
            voto=voto, matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            insufficiente_per_obbligatorio=insufficiente, richiede_orale=richiedi_orale,
        )
    finally:
        conn.close()


ESITI_ORALE = ("assente", "insufficiente", "voto")


def completa_orale(tag: str, appello_id: int, matricola: str, esito_orale: str, voto: Optional[int] = None) -> None:
    """esito_orale: 'assente' | 'insufficiente' | 'voto' (con voto obbligatorio in
    quest'ultimo caso). Negli altri due casi il voto finale resta NULL: il compito non è
    superato, ma voto_scritto conserva comunque il riferimento allo scritto."""
    if esito_orale not in ESITI_ORALE:
        raise ValueError(f"Esito orale non valido: {esito_orale}")
    if esito_orale == "voto" and voto is None:
        raise ValueError("Serve un voto se l'esito dell'orale è 'voto assegnato'")
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT richiede_orale, orale_svolto FROM risultati WHERE matricola=? AND appello_id=?",
            (matricola, appello_id),
        ).fetchone()
        if row is None:
            raise ValueError("Nessun risultato registrato per questo studente in questo appello")
        if not row["richiede_orale"]:
            raise ValueError("Questo risultato non è in attesa di un orale")
        if row["orale_svolto"]:
            raise ValueError("L'orale per questo risultato è già stato registrato")
        conn.execute(
            "UPDATE risultati SET voto=?, esito_orale=?, orale_svolto=1 WHERE matricola=? AND appello_id=?",
            (voto if esito_orale == "voto" else None, esito_orale, matricola, appello_id),
        )
        conn.commit()
    finally:
        conn.close()


def segna_esito_speciale(tag: str, appello_id: int, matricola: str, esito: str) -> None:
    """esito: 'assente' | 'ritirato'. Per uno studente che non ha svolto (o non ha
    completato) il compito scritto: nessun codice/risposte associati, voto NULL."""
    if esito not in ("assente", "ritirato"):
        raise ValueError(f"Esito non valido: {esito}")
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        esistente = conn.execute(
            "SELECT verbalizzato FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
        ).fetchone()
        if esistente:
            if esistente["verbalizzato"]:
                raise GiaVerbalizzato(f"Lo studente {matricola} ha già un voto verbalizzato per questo appello")
            raise RisultatoGiaValutato(
                f"Lo studente {matricola} ha già un esito registrato per questo appello: "
                f"elimina prima quel risultato per poterlo segnare come {esito}."
            )
        conn.execute(
            "INSERT INTO risultati (matricola, appello_id, esito, voto, voto_scritto, compito_id, risposte) "
            "VALUES (?,?,?,NULL,NULL,NULL,NULL)",
            (matricola, appello_id, esito),
        )
        if esito == "ritirato":
            corso = corsi_service.get_corso(tag)
            _applica_soglia_orale_automatica(conn, corso, matricola)
        conn.commit()
    finally:
        conn.close()


def dettaglio_risultato(tag: str, appello_id: int, matricola: str) -> dict:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        r = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.matricola=?",
            (appello_id, matricola),
        ).fetchone()
        if r is None:
            raise ValueError("Nessun risultato registrato per questo studente in questo appello")
        compito_row = conn.execute("SELECT codice, soluzioni FROM compiti WHERE id=?", (r["compito_id"],)).fetchone()
        soluzioni = compito_row["soluzioni"] if compito_row else ""
        risultato = dict(r)
        risultato["codice"] = compito_row["codice"] if compito_row else None
        risposte = r["risposte"] or ""
        punteggi = json.loads(r["punteggi_obbligatori"]) if r["punteggi_obbligatori"] else {}

        righe = []
        for pos in compiti_service.list_posizioni_compito(tag, r["compito_id"]):
            i = pos["posizione"]
            lettera_data = risposte[i].upper() if i < len(risposte) else "X"
            lettera_corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
            righe.append({
                "posizione": i, "esercizio_nome": pos["esercizio_nome"] or f"Esercizio #{pos['esercizio_id']}",
                "obbligatorio": bool(pos["obbligatorio"]), "lettera_data": lettera_data,
                "lettera_corretta": lettera_corretta, "corretta": lettera_data == lettera_corretta,
                "punteggio_assegnato": punteggi.get(str(i)),
            })
        return {"risultato": risultato, "righe": righe}
    finally:
        conn.close()


def list_risultati(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? ORDER BY s.cognome, s.nome",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_orali_da_svolgere(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.richiede_orale=1 AND r.orale_svolto=0 "
            "ORDER BY s.cognome, s.nome",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def elimina_risultato(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT verbalizzato FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
        ).fetchone()
        if row and row["verbalizzato"]:
            raise GiaVerbalizzato("Non è possibile eliminare un risultato già verbalizzato")
        conn.execute("DELETE FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id))
        conn.commit()
    finally:
        conn.close()
