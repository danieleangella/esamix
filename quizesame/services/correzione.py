import json
from dataclasses import dataclass, field
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import compiti as compiti_service
from quizesame.services import studenti as studenti_service


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


class LunghezzaRisposteNonValida(Exception):
    """La stringa di risposte non ha la stessa lunghezza della griglia del compito: una
    lettera in meno o in più farebbe scivolare tutte le posizioni successive, quindi non
    si salva finché non viene corretta."""

    def __init__(self, attesa: int, ricevuta: int):
        self.attesa = attesa
        self.ricevuta = ricevuta
        super().__init__(f"La stringa di risposte ha {ricevuta} caratteri, ma il compito ne prevede {attesa}")


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


@dataclass
class CalcoloVoto:
    voto: int  # già limitato a votomin-1 se un obbligatorio non è stato svolto
    manuali: dict  # posizione -> True se domanda aperta, False se obbligatorio da giudicare
    mancanti: list  # posizioni manuali ancora senza punteggio (valutazione rimandata)
    insufficiente: bool  # un obbligatorio non svolto


def _posizioni_o_ripiego(tag: str, compito_id: int, soluzioni: str) -> list[dict]:
    posizioni = compiti_service.list_posizioni_compito(tag, compito_id)
    if posizioni:
        return posizioni
    return [{"posizione": i, "esercizio_id": None, "esercizio_nome": None, "obbligatorio": False, "aperta": c == "-"}
            for i, c in enumerate(soluzioni)]


def punteggio_massimo(corso, aperta: bool) -> int:
    return corso.punteggio_max_aperta if aperta else corso.risposta_corretta


def calcola_voto(corso, votomin: int, soluzioni: str, risposte: str, posizioni: list[dict],
                 punteggi: dict[int, int]) -> CalcoloVoto:
    """Unico calcolo del voto di un compito, usato in tutte le pagine di correzione. Le
    posizioni "manuali" (domande aperte, e obbligatori con la risposta multiple-choice
    corretta) valgono il punteggio assegnato dal docente; se manca (valutazione
    rimandata), provvisoriamente il massimo possibile, così il voto provvisorio non
    sottostima mai quello definitivo."""
    manuali = {}
    insufficiente = False
    for p in posizioni:
        i = p["posizione"]
        lettera = risposte[i].upper() if i < len(risposte) else "X"
        corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
        if p["aperta"]:
            manuali[i] = True
        elif p["obbligatorio"]:
            if lettera == "X":
                insufficiente = True
            elif lettera == corretta:
                manuali[i] = False
    mancanti = [i for i in manuali if i not in punteggi]
    override = {i: punteggi.get(i, punteggio_massimo(corso, aperta)) for i, aperta in manuali.items()}
    voto = valuta_con_override(
        soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota, override=override,
    )
    if insufficiente and voto >= votomin:
        voto = votomin - 1
    return CalcoloVoto(voto=voto, manuali=manuali, mancanti=mancanti, insufficiente=insufficiente)


def _controlla_punteggio(corso, aperta: bool, punteggio: int) -> None:
    massimo = punteggio_massimo(corso, aperta)
    if not corso.risposta_sbagliata <= punteggio <= massimo:
        raise ValueError(f"Punteggio {punteggio} fuori dall'intervallo consentito ({corso.risposta_sbagliata}…{massimo})")


def _json_posizioni(valore: Optional[str]) -> dict[int, object]:
    return {int(k): v for k, v in json.loads(valore).items()} if valore else {}


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
            f"Lo studente {matricola} ha già un voto verbalizzato per questo appello: non può essere "
            "corretto di nuovo. Se serve davvero rifarlo, prima annulla la verbalizzazione dalla scheda "
            "\"Voti verbalizzati\" di questo appello."
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
    studente ha appena raggiunto quella soglia (contando tutti gli appelli del corso, o
    escludendo le prove parziali se richiesto), lo rende obbligato all'orale da qui in
    avanti (a meno che non lo sia già)."""
    if not corso.orale_soglia_attiva or not corso.orale_soglia_n or corso.orale_soglia_voto is None:
        return
    if _orale_obbligatorio(conn, matricola):
        return
    query = "SELECT COUNT(*) c FROM risultati r WHERE r.matricola=? AND (r.voto < ?"
    params = [matricola, corso.orale_soglia_voto]
    if corso.ritirato_conta_insufficiente:
        query += " OR r.esito='ritirato'"
    # un compito annullato conta come insufficiente, o come ritirato se così impostato nel corso
    if corso.annullato_come == "insufficiente" or corso.ritirato_conta_insufficiente:
        query += " OR r.esito='annullato'"
    query += ")"
    if corso.orale_soglia_escludi_parziali:
        # "prova parziale" = appello membro di un raggruppamento (es. il raggruppamento
        # "Prove parziali (media)" creato dalla migrazione legacy, o uno creato a mano):
        # stesso criterio già usato altrove nel programma per riconoscerle.
        query += " AND NOT EXISTS (SELECT 1 FROM raggruppamento_membri rm WHERE rm.appello_id = r.appello_id)"
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
    esercizio_id: Optional[int]
    esercizio_nome: str
    obbligatorio: bool
    aperta: bool
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
    domande_aperte: list[RigaRisposta] = field(default_factory=list)
    orale_obbligatorio: Optional[dict] = None
    lunghezza_attesa: int = 0
    codice_duplicato: Optional[dict] = None
    risposte_simili: list[dict] = field(default_factory=list)
    dettaglio_esercizi_disponibile: bool = True
    # voto con tutte le posizioni da giudicare a mano a 0: la pagina somma i punteggi
    # assegnati a questa parte fissa per mostrare il voto in tempo reale
    voto_fisso: int = 0


def _righe_da_soluzioni(soluzioni: str, risposte: str) -> list[RigaRisposta]:
    """Ripiego per i compiti a cui non è (ancora) collegato quale esercizio occupa
    ciascuna posizione (es. compiti importati da una migrazione dati legacy): la lettera
    corretta di ogni posizione è comunque nota da 'soluzioni', quindi si può sempre
    confrontarla con quella data, solo senza poter mostrare il nome dell'esercizio né
    sapere se era obbligatorio."""
    righe = []
    for i, corretta in enumerate(soluzioni):
        aperta = corretta == "-"
        lettera_data = risposte[i].upper() if i < len(risposte) else "X"
        lettera_corretta = corretta.upper()
        svolta = (not aperta) and lettera_data != "X"
        righe.append(RigaRisposta(
            posizione=i, esercizio_id=None, esercizio_nome=f"Posizione {i + 1}",
            obbligatorio=False, aperta=aperta,
            lettera_data=lettera_data, lettera_corretta=lettera_corretta,
            svolta=svolta, corretta=svolta and lettera_data == lettera_corretta,
        ))
    return righe


def _righe_risposta(tag: str, compito_id: int, soluzioni: str, risposte: str) -> tuple[list[RigaRisposta], bool]:
    posizioni = compiti_service.list_posizioni_compito(tag, compito_id)
    if not posizioni:
        return _righe_da_soluzioni(soluzioni, risposte), False
    righe = []
    for pos in posizioni:
        i = pos["posizione"]
        aperta = bool(pos["aperta"])
        lettera_data = risposte[i].upper() if i < len(risposte) else "X"
        lettera_corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
        svolta = (not aperta) and lettera_data != "X"
        righe.append(RigaRisposta(
            posizione=i, esercizio_id=pos["esercizio_id"],
            esercizio_nome=pos["esercizio_nome"] or f"Esercizio #{pos['esercizio_id']}",
            obbligatorio=bool(pos["obbligatorio"]), aperta=aperta,
            lettera_data=lettera_data, lettera_corretta=lettera_corretta,
            svolta=svolta, corretta=svolta and lettera_data == lettera_corretta,
        ))
    return righe, True


SOGLIA_SIMILARITA = 0.85
MINIMO_POSIZIONI_CONFRONTABILI = 5


def _codice_duplicato(conn, appello_id: int, matricola: str, compito_id: int) -> Optional[dict]:
    """Il codice di un compito dovrebbe essere usato da un solo studente per appello:
    se un altro risultato già registrato punta allo stesso compito, è quasi sempre un
    codice letto male dalla carta (docente che trascrive) o due studenti che hanno
    riportato lo stesso codice per errore, quindi va segnalato subito."""
    row = conn.execute(
        "SELECT r.matricola, s.nome, s.cognome FROM risultati r "
        "JOIN studenti s ON s.matricola = r.matricola "
        "WHERE r.appello_id=? AND r.compito_id=? AND r.matricola!=?",
        (appello_id, compito_id, matricola),
    ).fetchone()
    return dict(row) if row else None


def _similarita(a: str, b: str) -> Optional[tuple[float, int]]:
    """Percentuale di posizioni uguali tra due stringhe di risposte, ignorando qualunque
    posizione dove almeno una delle due è 'X' (non data): una risposta non data non è né
    un indizio di copiatura né di originalità. None se non ci sono abbastanza posizioni
    confrontabili per dare un giudizio significativo."""
    confrontabili = 0
    uguali = 0
    for ca, cb in zip(a.upper(), b.upper()):
        if ca == "X" or cb == "X":
            continue
        confrontabili += 1
        if ca == cb:
            uguali += 1
    if confrontabili < MINIMO_POSIZIONI_CONFRONTABILI:
        return None
    return uguali / confrontabili, confrontabili


def _risposte_simili(conn, appello_id: int, matricola: str, risposte: str) -> list[dict]:
    altri = conn.execute(
        "SELECT r.matricola, r.risposte, s.nome, s.cognome FROM risultati r "
        "JOIN studenti s ON s.matricola = r.matricola "
        "WHERE r.appello_id=? AND r.matricola!=? AND r.risposte IS NOT NULL",
        (appello_id, matricola),
    ).fetchall()
    simili = []
    for altro in altri:
        esito = _similarita(risposte, altro["risposte"])
        if esito is None:
            continue
        percentuale, confrontabili = esito
        if percentuale >= SOGLIA_SIMILARITA:
            simili.append({
                "matricola": altro["matricola"], "nome": altro["nome"], "cognome": altro["cognome"],
                "percentuale": round(percentuale * 100), "identica": percentuale == 1.0,
                "posizioni_confrontabili": confrontabili,
            })
    simili.sort(key=lambda s: s["percentuale"], reverse=True)
    return simili


def valuta_preliminare(tag: str, appello_id: int, matricola: str, codice: str, risposte: str) -> ValutazionePreliminare:
    """Calcola il voto "a lettere" e prepara il riepilogo completo (tutte le risposte,
    corrette o no) mostrato sempre prima di salvare. Segnala separatamente gli esercizi
    obbligatori: non svolti (compito automaticamente insufficiente) o svolti con la
    risposta multiple-choice corretta (il docente deve ancora giudicare lo svolgimento
    scritto e assegnare un punteggio) — un obbligatorio svolto ma con risposta sbagliata
    non richiede invece nessuna valutazione aggiuntiva, conta come risposta sbagliata. Le
    domande aperte (senza risposta a scelta multipla) richiedono sempre un punteggio
    assegnato a mano dal docente, indipendentemente dal fatto che siano obbligatorie."""
    corso = corsi_service.get_corso(tag)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        studente, compito = _carica_dati_correzione(conn, matricola, appello_id, codice)
        soluzioni = compito["soluzioni"]
        voto_base = valuta(soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota)

        righe, dettaglio_disponibile = _righe_risposta(tag, compito["id"], soluzioni, risposte)
        non_svolte = [r for r in righe if r.obbligatorio and not r.aperta and not r.svolta]
        da_valutare = [r for r in righe if r.obbligatorio and not r.aperta and r.svolta and r.corretta]
        domande_aperte = [r for r in righe if r.aperta]
        voto_fisso = valuta_con_override(
            soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota,
            override={r.posizione: 0 for r in da_valutare + domande_aperte},
        )

        return ValutazionePreliminare(
            matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            codice=codice, risposte=risposte, voto_base=voto_base, righe=righe,
            non_svolte_obbligatorie=non_svolte, da_valutare=da_valutare, domande_aperte=domande_aperte,
            orale_obbligatorio=_orale_obbligatorio(conn, studente["matricola"]),
            lunghezza_attesa=len(soluzioni),
            codice_duplicato=_codice_duplicato(conn, appello_id, studente["matricola"], compito["id"]),
            risposte_simili=_risposte_simili(conn, appello_id, studente["matricola"], risposte),
            dettaglio_esercizi_disponibile=dettaglio_disponibile,
            voto_fisso=voto_fisso,
        )
    finally:
        conn.close()


def salva_bozza(tag: str, appello_id: int, matricola: str, codice: str, risposte: str) -> None:
    """Salva (o sovrascrive) una bozza di correzione per riprenderla più tardi: non tocca
    'risultati', quindi non conta come esito né blocca il codice come duplicato."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO bozze_correzione (appello_id, matricola, codice, risposte, salvata_il) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(appello_id, matricola) DO UPDATE SET "
            "codice=excluded.codice, risposte=excluded.risposte, salvata_il=excluded.salvata_il",
            (appello_id, matricola, codice, risposte),
        )
        conn.commit()
    finally:
        conn.close()


def list_bozze(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT b.matricola, b.codice, b.risposte, b.salvata_il, s.nome, s.cognome "
            "FROM bozze_correzione b JOIN studenti s ON s.matricola = b.matricola "
            "WHERE b.appello_id=? ORDER BY b.salvata_il",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def elimina_bozza(tag: str, appello_id: int, matricola: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("DELETE FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
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
    valutazione_sospesa: bool = False
    svolgimenti_rimandati: int = 0


def conferma_risultato(
    tag: str, appello_id: int, matricola: str, codice: str, risposte: str,
    voto_finale: Optional[int] = None,
    punteggi_obbligatori: Optional[dict[int, int]] = None,
    richiedi_orale: bool = False,
    orale_motivazione: str = "",
    conferma_orale_obbligatorio: bool = False,
    modifica: bool = False,
    note_svolgimenti: Optional[dict[int, str]] = None,
    nota_correzione: str = "",
) -> CorrezioneResult:
    """Salva il risultato. `punteggi_obbligatori` sono i punteggi (tra risposta_sbagliata
    e risposta_corretta, o punteggio_max_aperta per le domande aperte) assegnati dal
    docente agli esercizi che richiedono un giudizio manuale (obbligatori risposti
    correttamente, o domande aperte), dopo aver giudicato lo svolgimento scritto. Le
    posizioni manuali senza punteggio sono valutazioni rimandate: il risultato resta "in
    sospeso" con un voto provvisorio (ottimistico, vedi calcola_voto), si completa più
    tardi da "Modifica" o dalla pagina degli svolgimenti, e intanto voto_finale (voto
    cambiato a mano) e la richiesta dell'orale vengono ignorati. `note_svolgimenti` sono
    le note del docente sugli errori di ciascuno svolgimento, `nota_correzione` una nota
    libera sul compito (es. perché il voto finale è stato cambiato). Se `richiedi_orale`,
    il voto scritto viene comunque salvato come riferimento ma l'esito resta "in attesa di
    orale" finché non si chiama completa_orale(). Se lo studente ha già una riga in
    orale_obbligatorio (imposta in un appello precedente, anche di un altro corso da cui è
    stato importato) e non è arrivata `conferma_orale_obbligatorio`, non salva nulla e
    solleva OraleObbligatorioNonConfermato perché il chiamante mostri l'avviso (non per
    una valutazione rimandata: lo si chiederà al completamento). Se lo studente ha già un
    esito registrato per questo appello (voto, assente o ritirato) e `modifica` non è
    True, non salva nulla e solleva RisultatoGiaValutato: una correzione "nuova" (dal
    form principale) non deve poter sovrascrivere per sbaglio un esito già inserito —
    solo un "Modifica" esplicito (necessario anche per completare una valutazione
    sospesa) può."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    # Se questa prova è membro di un raggruppamento, l'orale è unico per l'intero
    # raggruppamento (non per la singola prova parziale): la richiesta va quindi
    # registrata sulla riga del raggruppamento invece che su quella della prova.
    raggruppamento_membro = (
        corsi_service.get_raggruppamento_by_membro(tag, appello_id) if appello.membro_raggruppamento else None
    )
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        studente, compito = _carica_dati_correzione(conn, matricola, appello_id, codice)
        soluzioni = compito["soluzioni"]
        if len(risposte) != len(soluzioni):
            raise LunghezzaRisposteNonValida(len(soluzioni), len(risposte))

        if not modifica:
            esistente = conn.execute(
                "SELECT 1 FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
            ).fetchone()
            if esistente:
                raise RisultatoGiaValutato(
                    f"Lo studente {matricola} ha già un esito registrato per questo appello: "
                    "usa \"Modifica\" nella tabella dei risultati per correggerlo."
                )

        votomin = corsi_service.effective_votomin(corso, appello)
        posizioni = _posizioni_o_ripiego(tag, compito["id"], soluzioni)
        manuali = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, {}).manuali
        punteggi = {i: v for i, v in (punteggi_obbligatori or {}).items() if i in manuali}
        for i, v in punteggi.items():
            _controlla_punteggio(corso, manuali[i], v)
        note = {i: n.strip() for i, n in (note_svolgimenti or {}).items() if i in manuali and n and n.strip()}
        calcolo = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        sospesa = bool(calcolo.mancanti)
        insufficiente = calcolo.insufficiente
        if sospesa:
            richiedi_orale = False
            voto_finale = None
        richiedi_orale_su_prova = richiedi_orale and raggruppamento_membro is None

        voto = voto_finale if voto_finale is not None else calcolo.voto
        if richiedi_orale and (insufficiente or voto < votomin):
            raise OraleNonConsentito("L'orale si può richiedere solo se il compito scritto è sufficiente.")
        if insufficiente and voto >= votomin:
            voto = votomin - 1
        voto_modificato = voto != calcolo.voto

        # con uno scritto insufficiente l'orale non si può richiedere: niente da confermare
        orale_obb = _orale_obbligatorio(conn, studente["matricola"])
        if (orale_obb and not sospesa and not richiedi_orale and not conferma_orale_obbligatorio
                and voto >= votomin):
            raise OraleObbligatorioNonConfermato(orale_obb["motivazione"] or "", orale_obb["origine"] or "")

        # Se l'orale è già stato completato per questo esito, ricorreggere lo scritto (es.
        # dalla pagina "Modifica") non deve cancellarne l'esito: richiede_orale/orale_svolto/
        # esito_orale restano quelli già registrati finché non si passa da completa_orale().
        # Analogamente, modificare i dettagli di un risultato "rifiutato" dallo studente
        # (per correggere un errore di valutazione) non deve farlo tornare automaticamente
        # verbalizzabile: l'esito resta "rifiutato" finché non lo si corregge esplicitamente
        # con un'altra correzione dal form principale (che richiede un nuovo esito "voto").
        conn.execute(
            "INSERT INTO risultati (matricola, appello_id, compito_id, risposte, voto, voto_scritto, esito, "
            "insufficiente_per_obbligatorio, punteggi_obbligatori, richiede_orale, orale_svolto, "
            "orale_motivazione, esito_orale, valutazione_sospesa, note_svolgimenti, nota_correzione, voto_modificato) "
            "VALUES (?,?,?,?,?,?,'voto',?,?,?,?,?,NULL,?,?,?,?) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET "
            "compito_id=excluded.compito_id, risposte=excluded.risposte, "
            "voto=CASE WHEN risultati.orale_svolto=1 THEN risultati.voto ELSE excluded.voto END, "
            "voto_scritto=excluded.voto_scritto, "
            "esito=CASE WHEN risultati.esito='rifiutato' THEN 'rifiutato' ELSE 'voto' END, "
            "insufficiente_per_obbligatorio=excluded.insufficiente_per_obbligatorio, "
            "punteggi_obbligatori=excluded.punteggi_obbligatori, "
            "richiede_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.richiede_orale ELSE excluded.richiede_orale END, "
            "orale_svolto=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_svolto ELSE excluded.orale_svolto END, "
            "orale_motivazione=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_motivazione ELSE excluded.orale_motivazione END, "
            "esito_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.esito_orale ELSE excluded.esito_orale END, "
            "valutazione_sospesa=excluded.valutazione_sospesa, "
            "note_svolgimenti=excluded.note_svolgimenti, nota_correzione=excluded.nota_correzione, "
            "voto_modificato=excluded.voto_modificato",
            (
                matricola, appello_id, compito["id"], risposte, voto, voto, insufficiente,
                json.dumps(punteggi) if punteggi else None,
                richiedi_orale_su_prova, False, orale_motivazione or None if richiedi_orale_su_prova else None,
                sospesa, json.dumps(note) if note else None, (nota_correzione or "").strip() or None,
                voto_modificato,
            ),
        )
        if richiedi_orale and raggruppamento_membro is not None:
            # L'orale richiesto su una prova membro va sulla riga "combinata" del
            # raggruppamento (può non esistere ancora, se il voto combinato non è mai
            # stato calcolato): CASE protegge un orale già svolto lì da una richiesta
            # tardiva ripetuta su un'altra prova membro dello stesso raggruppamento.
            conn.execute(
                "INSERT INTO risultati (matricola, appello_id, richiede_orale, orale_motivazione, esito) "
                "VALUES (?,?,1,?,'voto') "
                "ON CONFLICT(matricola, appello_id) DO UPDATE SET "
                "richiede_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.richiede_orale ELSE 1 END, "
                "orale_motivazione=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_motivazione ELSE excluded.orale_motivazione END",
                (matricola, raggruppamento_membro.appello_id, orale_motivazione or None),
            )
        if richiedi_orale and corso.orale_dopo_richiesta:
            conn.execute(
                "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
                "ON CONFLICT(matricola) DO NOTHING",
                (matricola, orale_motivazione or "", appello.nome),
            )
        if not sospesa:
            _applica_soglia_orale_automatica(conn, corso, matricola)
        # una correzione confermata rende superflua un'eventuale bozza in sospeso per lo
        # stesso studente su questo appello.
        conn.execute("DELETE FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
        return CorrezioneResult(
            voto=voto, matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            insufficiente_per_obbligatorio=insufficiente, richiede_orale=richiedi_orale,
            valutazione_sospesa=sospesa, svolgimenti_rimandati=len(calcolo.mancanti),
        )
    finally:
        conn.close()


def _carica_risultato_con_compito(conn, appello_id: int, matricola: str):
    return conn.execute(
        "SELECT r.*, c.soluzioni, s.nome, s.cognome FROM risultati r "
        "JOIN compiti c ON c.id = r.compito_id JOIN studenti s ON s.matricola = r.matricola "
        "WHERE r.appello_id=? AND r.matricola=?",
        (appello_id, matricola),
    ).fetchone()


def valuta_svolgimento(
    tag: str, appello_id: int, matricola: str, posizione: int, punteggio: Optional[int], nota: str = "",
) -> dict:
    """Assegna (o toglie, con None: valutazione rimandata) il punteggio di un singolo
    svolgimento di un compito già registrato, con la sua nota, dalla pagina che confronta
    gli svolgimenti esercizio per esercizio. Il voto si ricalcola, tranne quando era stato
    cambiato a mano (resta quello, e si avvisa) o l'orale è già stato svolto (cambia solo
    il voto scritto di riferimento). Quando non resta più niente da giudicare la
    valutazione esce dalla sospensione, a meno che lo studente non abbia l'orale
    obbligatorio: allora va confermata dalla pagina del compito, che lo chiede."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    if appello.correzione_scritti_conclusa:
        raise ValueError("La correzione di questa prova è conclusa: riaprila dalla scheda Correzione per cambiare i punteggi")
    votomin = corsi_service.effective_votomin(corso, appello)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        r = _carica_risultato_con_compito(conn, appello_id, matricola)
        if r is None:
            raise ValueError("Nessun compito registrato per questo studente in questo appello")
        if r["verbalizzato"]:
            raise GiaVerbalizzato("Il voto di questo studente è già verbalizzato: non si può più modificare")
        if r["esito"] not in ("voto", "rifiutato"):
            raise ValueError(f"Il compito di questo studente risulta {r['esito']}: non c'è niente da valutare")
        soluzioni, risposte = r["soluzioni"], r["risposte"] or ""
        posizioni = _posizioni_o_ripiego(tag, r["compito_id"], soluzioni)
        punteggi = _json_posizioni(r["punteggi_obbligatori"])
        prima = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        if posizione not in prima.manuali:
            raise ValueError("Questa posizione non richiede una valutazione a mano")
        if not r["valutazione_sospesa"]:
            # risultati registrati prima che i punteggi venissero salvati tutti: le
            # posizioni senza punteggio valevano quello calcolato dalla lettera, non sono
            # rimandate (altrimenti cambiarne una rimetterebbe il compito in sospeso)
            for i in prima.mancanti:
                lettera = risposte[i].upper() if i < len(risposte) else "X"
                punteggi[i] = corso.risposta_corretta if lettera == soluzioni[i].upper() else (
                    corso.risposta_vuota if lettera == "X" else corso.risposta_sbagliata)
            prima = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        if punteggio is None:
            punteggi.pop(posizione, None)
        else:
            _controlla_punteggio(corso, prima.manuali[posizione], punteggio)
            punteggi[posizione] = punteggio
        note = _json_posizioni(r["note_svolgimenti"])
        if nota and nota.strip():
            note[posizione] = nota.strip()
        else:
            note.pop(posizione, None)

        dopo = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        voto_a_mano = bool(r["voto_modificato"]) and not r["valutazione_sospesa"] and r["voto_scritto"] is not None
        voto_scritto = r["voto_scritto"] if voto_a_mano else dopo.voto
        voto = r["voto"] if r["orale_svolto"] else voto_scritto
        sospesa = bool(dopo.mancanti)
        avviso = None
        if voto_a_mano and dopo.voto != prima.voto:
            avviso = (f"Il voto finale di {r['cognome']} {r['nome']} era stato cambiato a mano ({voto_scritto}) e "
                      f"non è stato ricalcolato: con i punteggi attuali sarebbe {dopo.voto}.")
        elif (r["valutazione_sospesa"] and not sospesa and not r["richiede_orale"]
              and voto_scritto >= votomin and _orale_obbligatorio(conn, matricola)):
            sospesa = True
            avviso = (f"{r['cognome']} {r['nome']} deve fare l'orale in ogni appello: completa la "
                      "valutazione dalla pagina del compito per scegliere se richiederlo.")
        conn.execute(
            "UPDATE risultati SET punteggi_obbligatori=?, note_svolgimenti=?, voto=?, voto_scritto=?, "
            "valutazione_sospesa=?, insufficiente_per_obbligatorio=?, voto_modificato=? WHERE appello_id=? AND matricola=?",
            (json.dumps(punteggi) if punteggi else None, json.dumps(note) if note else None,
             voto, voto_scritto, sospesa, dopo.insufficiente, voto_a_mano and voto_scritto != dopo.voto,
             appello_id, matricola),
        )
        if r["valutazione_sospesa"] and not sospesa:
            _applica_soglia_orale_automatica(conn, corso, matricola)
        conn.commit()
        return {
            "voto": voto, "voto_calcolato": dopo.voto, "sospesa": sospesa,
            "da_valutare": len(dopo.mancanti), "avviso": avviso,
        }
    finally:
        conn.close()


def svolgimenti_appello(tag: str, appello_id: int, escludi_matricola: Optional[str] = None) -> dict:
    """Tutti gli svolgimenti giudicati a mano in un appello (obbligatori con la risposta
    corretta e domande aperte), raggruppati per esercizio: per valutare quelli rimandati
    uno dopo l'altro e confrontare punteggi e note di studenti diversi sullo stesso
    esercizio. Per ogni esercizio anche le note usate, con i punteggi dati a ciascuna (una
    stessa nota con punteggi diversi è segnalata), e l'elenco dei compiti con una nota o
    un voto cambiato a mano."""
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
        posizioni_per_compito: dict[int, list[dict]] = {}
        for p in conn.execute(
            "SELECT ce.compito_id, ce.posizione, ce.obbligatorio, ce.aperta, e.id AS esercizio_id, "
            "e.nome AS esercizio_nome FROM compito_esercizi ce JOIN esercizi e ON e.id = ce.esercizio_id "
            "JOIN compiti c ON c.id = ce.compito_id WHERE c.appello_id=? ORDER BY ce.posizione",
            (appello_id,),
        ):
            posizioni_per_compito.setdefault(p["compito_id"], []).append(dict(p))
    finally:
        conn.close()

    esercizi: dict = {}
    compiti_annotati = []
    for r in risultati:
        if escludi_matricola is not None and r["matricola"] == escludi_matricola:
            continue
        soluzioni, risposte = r["soluzioni"], r["risposte"] or ""
        posizioni = posizioni_per_compito.get(r["compito_id"]) or [
            {"posizione": i, "esercizio_id": None, "esercizio_nome": f"Posizione {i + 1}", "obbligatorio": False,
             "aperta": c == "-"} for i, c in enumerate(soluzioni)
        ]
        punteggi = _json_posizioni(r["punteggi_obbligatori"])
        note = _json_posizioni(r["note_svolgimenti"])
        calcolo = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        modificabile = not appello.chiuso and not appello.correzione_scritti_conclusa and not r["verbalizzato"]
        for p in posizioni:
            i = p["posizione"]
            if i not in calcolo.manuali:
                continue
            chiave = p["esercizio_id"] if p["esercizio_id"] is not None else f"pos{i}"
            es = esercizi.setdefault(chiave, {
                "chiave": str(chiave), "esercizio_id": p["esercizio_id"], "nome": p["esercizio_nome"],
                "aperta": bool(p["aperta"]), "massimo": punteggio_massimo(corso, bool(p["aperta"])),
                "voci": [], "da_valutare": 0, "prima_posizione": i,
            })
            es["prima_posizione"] = min(es["prima_posizione"], i)
            da_valutare = i not in punteggi and bool(r["valutazione_sospesa"])
            es["da_valutare"] += da_valutare
            es["voci"].append({
                "matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"], "posizione": i,
                "punteggio": punteggi.get(i), "nota": note.get(i, ""), "da_valutare": da_valutare,
                "voto": r["voto"], "sospesa": bool(r["valutazione_sospesa"]), "modificabile": modificabile,
                "verbalizzato": bool(r["verbalizzato"]),
            })
        voto_a_mano = bool(r["voto_modificato"]) and not r["valutazione_sospesa"] and r["voto_scritto"] is not None
        if voto_a_mano or r["nota_correzione"]:
            compiti_annotati.append({
                "matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"],
                "voto": r["voto_scritto"], "voto_calcolato": calcolo.voto if voto_a_mano else None,
                "nota": r["nota_correzione"] or "", "verbalizzato": bool(r["verbalizzato"]),
            })

    elenco = sorted(esercizi.values(), key=lambda e: e["prima_posizione"])
    for es in elenco:
        # da valutare in cima, poi dal punteggio più alto: studenti con lo stesso
        # punteggio vicini, per confrontarne le note a colpo d'occhio
        es["voci"].sort(key=lambda v: (not v["da_valutare"], -(v["punteggio"] if v["punteggio"] is not None else 1e9),
                                       v["cognome"].lower()))
        gruppi: dict = {}
        for v in es["voci"]:
            if v["nota"] and v["punteggio"] is not None:
                g = gruppi.setdefault(v["nota"].strip().lower(), {"nota": v["nota"].strip(), "punteggi": {}})
                g["punteggi"][v["punteggio"]] = g["punteggi"].get(v["punteggio"], 0) + 1
        es["note"] = sorted(
            ({**g, "n": sum(g["punteggi"].values()), "incoerente": len(g["punteggi"]) > 1,
              "punteggi": sorted(g["punteggi"].items(), reverse=True)} for g in gruppi.values()),
            key=lambda g: (-g["n"], g["nota"].lower()),
        )
        valutati = [v["punteggio"] for v in es["voci"] if v["punteggio"] is not None]
        es["media"] = round(sum(valutati) / len(valutati), 2) if valutati else None
        es["n_valutati"] = len(valutati)
    return {
        "esercizi": elenco, "da_valutare": sum(e["da_valutare"] for e in elenco),
        "compiti_annotati": compiti_annotati,
    }


def note_precedenti(tag: str, appello_id: int, matricola: str) -> dict:
    """Per la pagina di correzione di un compito: per ogni esercizio, le note già scritte
    sugli altri compiti dell'appello, con studente e punteggio dato, da mostrare come
    suggerimento mentre si scrive una nota simile (per restare coerenti)."""
    return {
        e["esercizio_id"]: [
            {"nota": v["nota"], "punteggio": v["punteggio"], "studente": f"{v['cognome']} {v['nome']}"}
            for v in e["voci"] if v["nota"]
        ]
        for e in svolgimenti_appello(tag, appello_id, escludi_matricola=matricola)["esercizi"]
        if e["esercizio_id"] is not None and any(v["nota"] for v in e["voci"])
    }


ESITI_ORALE = ("assente", "insufficiente", "voto")


def completa_orale(tag: str, appello_id: int, matricola: str, esito_orale: str, voto: Optional[int] = None) -> None:
    """esito_orale: 'assente' | 'insufficiente' | 'voto' (con voto obbligatorio in
    quest'ultimo caso). Negli altri due casi il voto finale resta NULL: il compito non è
    superato, ma voto_scritto conserva comunque il riferimento allo scritto."""
    if esito_orale not in ESITI_ORALE:
        raise ValueError(f"Esito orale non valido: {esito_orale}")
    if esito_orale == "voto" and voto is None:
        raise ValueError("Serve un voto se l'esito dell'orale è 'voto assegnato'")
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
        conn.execute("DELETE FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
    finally:
        conn.close()


def annulla_compito(
    tag: str, appello_id: int, matricola: str, nota: str = "", rendi_orale_obbligatorio: bool = False,
) -> None:
    """Annulla il compito di uno studente (es. sorpreso col cellulare durante la prova):
    l'esito diventa 'annullato', con voto NULL e la nota di spiegazione del docente. Nei
    risultati pubblicati e nel file della segreteria conta come insufficiente o come
    ritirato secondo l'impostazione del corso (annullato_come), mentre nella carriera
    dello studente resta indicato come annullato. A differenza di segna_esito_speciale,
    sostituisce un eventuale esito già registrato e non verbalizzato (il compito può
    essere già stato corretto quando ci si accorge del problema): codice e risposte
    restano come riferimento. Se `rendi_orale_obbligatorio`, lo studente dovrà fare
    l'orale in tutti gli appelli successivi."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    nota = (nota or "").strip()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if conn.execute("SELECT 1 FROM studenti WHERE matricola=?", (matricola,)).fetchone() is None:
            raise StudenteNonTrovato(f"Matricola '{matricola}' non registrata in questo corso")
        esistente = conn.execute(
            "SELECT verbalizzato FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id)
        ).fetchone()
        if esistente and esistente["verbalizzato"]:
            raise GiaVerbalizzato(
                f"Lo studente {matricola} ha già un voto verbalizzato per questo appello: annulla prima la "
                "verbalizzazione dalla scheda \"Voti verbalizzati\"."
            )
        conn.execute(
            "INSERT INTO risultati (matricola, appello_id, esito, voto, voto_scritto, annullamento_nota) "
            "VALUES (?,?,'annullato',NULL,NULL,?) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET esito='annullato', voto=NULL, voto_scritto=NULL, "
            "annullamento_nota=excluded.annullamento_nota, insufficiente_per_obbligatorio=0, "
            "punteggi_obbligatori=NULL, richiede_orale=0, orale_svolto=0, orale_motivazione=NULL, "
            "esito_orale=NULL, valutazione_sospesa=0, voto_modificato=0",
            (matricola, appello_id, nota or None),
        )
        if rendi_orale_obbligatorio:
            conn.execute(
                "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
                "ON CONFLICT(matricola) DO NOTHING",
                (matricola, "compito annullato" + (f": {nota}" if nota else ""), appello.nome),
            )
        _applica_soglia_orale_automatica(conn, corso, matricola)
        conn.execute("DELETE FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
    finally:
        conn.close()


def dettaglio_risultato(tag: str, appello_id: int, matricola: str) -> dict:
    corso = corsi_service.get_corso(tag)
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
        note = json.loads(r["note_svolgimenti"]) if r["note_svolgimenti"] else {}

        posizioni = compiti_service.list_posizioni_compito(tag, r["compito_id"])
        dettaglio_esercizi_disponibile = bool(posizioni)
        if not posizioni:
            # compito senza collegamento esercizio-posizione (es. importato da una
            # migrazione dati legacy): la lettera corretta di ogni posizione resta nota
            # da 'soluzioni', quindi si confronta comunque, solo senza nome/obbligatorio.
            posizioni = [
                {"posizione": i, "esercizio_id": None, "esercizio_nome": None, "obbligatorio": False, "aperta": c == "-"}
                for i, c in enumerate(soluzioni)
            ]
        righe = []
        for pos in posizioni:
            i = pos["posizione"]
            lettera_data = risposte[i].upper() if i < len(risposte) else "X"
            lettera_corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
            corretta = lettera_data == lettera_corretta
            if str(i) in punteggi:
                punteggio_assegnato = punteggi[str(i)]
            elif pos["aperta"]:
                punteggio_assegnato = None
            else:
                punteggio_assegnato = (
                    corso.risposta_corretta if corretta
                    else (corso.risposta_vuota if lettera_data == "X" else corso.risposta_sbagliata)
                )
            righe.append({
                "posizione": i, "esercizio_nome": pos["esercizio_nome"] or f"Posizione {i + 1}",
                "obbligatorio": bool(pos["obbligatorio"]), "lettera_data": lettera_data,
                "lettera_corretta": lettera_corretta, "corretta": corretta,
                "punteggio_assegnato": punteggio_assegnato, "nota": note.get(str(i), ""),
            })

        storico = studenti_service.risultati_studente(tag, matricola)
        for s in storico:
            s["corrente"] = s["appello_id"] == appello_id
        # riusa il calcolo già fatto in risultati_studente (soglia effettiva compresa)
        # invece di rifarlo qui: distingue un verbalizzato "superato" da uno "insufficiente".
        risultato["superato"] = next((s["superato"] for s in storico if s["corrente"]), None)
        return {
            "risultato": risultato, "righe": righe, "storico": storico,
            "dettaglio_esercizi_disponibile": dettaglio_esercizi_disponibile,
        }
    finally:
        conn.close()


def list_risultati(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
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
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_orali_svolti(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.richiede_orale=1 AND r.orale_svolto=1 "
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_valutazioni_sospese(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.valutazione_sospesa=1 "
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def elimina_risultato(tag: str, appello_id: int, matricola: str) -> None:
    corsi_service.verifica_appello_aperto(tag, appello_id)
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
