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
    rimandata), provvisoriamente una domanda aperta vale 0 e un obbligatorio il massimo
    (lo stesso che gli si assegna in automatico correggendo). La revisione del testo dopo
    la prova (p["revisione"]) conta così: un esercizio "annullato" vale il punteggio pieno
    per tutti, qualunque risposta; uno "facoltativo" non svolto non rende più insufficiente
    il compito (svolto con la risposta giusta resta giudicato sullo svolgimento)."""
    manuali = {}
    annullati = {}
    insufficiente = False
    for p in posizioni:
        i = p["posizione"]
        lettera = risposte[i].upper() if i < len(risposte) else "X"
        corretta = soluzioni[i].upper() if i < len(soluzioni) else "?"
        if p.get("revisione") == "annullato":
            annullati[i] = punteggio_massimo(corso, bool(p["aperta"]))
        elif p["aperta"]:
            manuali[i] = True
        elif p["obbligatorio"]:
            if lettera == "X":
                insufficiente = insufficiente or p.get("revisione") != "facoltativo"
            elif lettera == corretta:
                manuali[i] = False
    mancanti = [i for i in manuali if i not in punteggi]
    override = {i: punteggi.get(i, 0 if aperta else punteggio_massimo(corso, False)) for i, aperta in manuali.items()}
    override.update(annullati)
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
    revisione: Optional[str] = None  # "annullato" o "facoltativo" (revisione del testo dopo la prova)
    testo: str = ""  # testo della variante stampata su questo compito
    risposte: list = field(default_factory=list)  # risposte nell'ordine stampato (A, B, ...)


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


def insufficiente_comunque(corso, votomin: int, valutazione: ValutazionePreliminare) -> bool:
    """Il compito è insufficiente qualunque punteggio abbiano gli svolgimenti da giudicare
    a mano: c'è un obbligatorio non svolto, oppure anche con tutti gli svolgimenti al
    massimo il voto resterebbe sotto la soglia. Valutarli non cambia l'esito."""
    manuali = valutazione.da_valutare + valutazione.domande_aperte
    if not manuali:
        return False
    if valutazione.non_svolte_obbligatorie:
        return True
    return valutazione.voto_fisso + sum(punteggio_massimo(corso, r.aperta) for r in manuali) < votomin


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
            revisione=pos.get("revisione"), testo=pos.get("testo") or "",
            risposte=json.loads(pos["risposte_mischiate"]) if pos.get("risposte_mischiate") else [],
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


# le file in aula sono di quattro posti al più: due compiti a non più di tre posti di
# distanza nell'ordine di stampa possono essere finiti nella stessa fila
DISTANZA_STESSA_FILA = 3


def _posto_nella_stampa(conn, compito_id: int) -> Optional[dict]:
    """Blocco del compito e suo numero d'ordine nella stampa di quel blocco (i compiti di
    un blocco si stampano, e di solito si distribuiscono, in quest'ordine)."""
    c = conn.execute(
        "SELECT c.blocco_id, b.numero FROM compiti c LEFT JOIN blocchi b ON b.id = c.blocco_id WHERE c.id=?",
        (compito_id,),
    ).fetchone()
    if c is None or c["blocco_id"] is None:
        return None
    ids = [r["id"] for r in conn.execute("SELECT id FROM compiti WHERE blocco_id=? ORDER BY id", (c["blocco_id"],))]
    return {"blocco": c["numero"], "ordine": ids.index(compito_id) + 1, "totale": len(ids)}


def _vicinanza(mio: Optional[dict], suo: Optional[dict]) -> Optional[dict]:
    """Quanto erano vicini nella stampa due compiti: consecutivi o a pochi posti nello
    stesso blocco possono essere stati dati a persone sedute vicine."""
    if not mio or not suo:
        return None
    if mio["blocco"] != suo["blocco"]:
        return {"vicini": False, "testo": f"blocchi diversi (n. {mio['ordine']} del blocco {mio['blocco']}, "
                                          f"n. {suo['ordine']} del blocco {suo['blocco']})"}
    distanza = abs(mio["ordine"] - suo["ordine"])
    posti = f"n. {mio['ordine']} e {suo['ordine']} del blocco {mio['blocco']}"
    if distanza == 1:
        return {"vicini": True, "distanza": 1,
                "testo": f"consecutivi nella stampa ({posti}): potrebbero essere stati dati a persone vicine"}
    if distanza <= DISTANZA_STESSA_FILA:
        return {"vicini": True, "distanza": distanza,
                "testo": f"a {distanza} posti nella stampa ({posti}): possibile la stessa fila (file da 4)"}
    return {"vicini": False, "distanza": distanza, "testo": f"lontani nella stampa ({posti})"}


def _scelte_per_esercizio(conn, compito_id: int, risposte: str) -> dict[int, tuple[str, bool]]:
    """{esercizio_id: (testo della risposta scelta, se è giusta)} per le domande a risposta
    chiusa date: confrontabile tra compiti diversi, dove lo stesso esercizio sta in
    un'altra posizione e le sue risposte in un altro ordine."""
    soluzioni = conn.execute("SELECT soluzioni FROM compiti WHERE id=?", (compito_id,)).fetchone()
    soluzioni = (soluzioni["soluzioni"] if soluzioni else "").upper()
    scelte = {}
    for p in conn.execute(
        "SELECT posizione, esercizio_id, aperta, risposte_mischiate FROM compito_esercizi WHERE compito_id=?",
        (compito_id,),
    ):
        i = p["posizione"]
        lettera = risposte[i].upper() if i < len(risposte) else "X"
        if p["aperta"] or not p["risposte_mischiate"] or not ("A" <= lettera <= "Z") or lettera == "X":
            continue
        opzioni = json.loads(p["risposte_mischiate"])
        k = ord(lettera) - ord("A")
        if k < len(opzioni):
            scelte[p["esercizio_id"]] = (opzioni[k], i < len(soluzioni) and lettera == soluzioni[i])
    return scelte


def _risposte_simili(conn, appello_id: int, matricola: str, risposte: str,
                     compito_id: Optional[int] = None, soluzioni: str = "") -> list[dict]:
    """Altri compiti dell'appello con le risposte molto simili, con quanto serve per
    decidere: le due griglie a confronto (stesse lettere, stessi errori), le stesse
    risposte date agli stessi esercizi (in posizioni e ordini diversi) e quanto erano
    vicini i due compiti nella stampa."""
    altri = conn.execute(
        "SELECT r.matricola, r.risposte, r.voto, r.voto_scritto, r.compito_id, c.codice, c.soluzioni, "
        "s.nome, s.cognome FROM risultati r "
        "JOIN studenti s ON s.matricola = r.matricola LEFT JOIN compiti c ON c.id = r.compito_id "
        "WHERE r.appello_id=? AND r.matricola!=? AND r.risposte IS NOT NULL",
        (appello_id, matricola),
    ).fetchall()
    simili = []
    mio_posto = mie_scelte = None
    for altro in altri:
        esito = _similarita(risposte, altro["risposte"])
        if esito is None:
            continue
        percentuale, confrontabili = esito
        if percentuale < SOGLIA_SIMILARITA:
            continue
        sue, sue_sol = altro["risposte"].upper(), (altro["soluzioni"] or "").upper()
        confronto, errori_comuni, giuste_comuni = [], 0, 0
        for i, mia in enumerate(risposte.upper()):
            sua = sue[i] if i < len(sue) else ""
            uguale = mia == sua and mia not in ("X", "-")
            sbagliata_mia = i < len(soluzioni) and mia != soluzioni[i].upper()
            sbagliata_sua = i < len(sue_sol) and sua != sue_sol[i]
            errore = uguale and sbagliata_mia and sbagliata_sua
            errori_comuni += errore
            giuste_comuni += uguale and not sbagliata_mia and not sbagliata_sua
            confronto.append({"mia": mia, "sua": sua, "uguale": uguale, "errore": errore})
        voce = {
            "matricola": altro["matricola"], "nome": altro["nome"], "cognome": altro["cognome"],
            "percentuale": round(percentuale * 100), "identica": percentuale == 1.0,
            "posizioni_confrontabili": confrontabili, "codice": altro["codice"],
            "voto": altro["voto_scritto"] if altro["voto_scritto"] is not None else altro["voto"],
            "confronto": confronto, "errori_comuni": errori_comuni, "giuste_comuni": giuste_comuni,
            "uguali": sum(c["uguale"] for c in confronto), "vicinanza": None, "stesse_scelte": None,
        }
        if compito_id is not None and altro["compito_id"] is not None:
            if mio_posto is None:
                mio_posto = _posto_nella_stampa(conn, compito_id) or {}
                mie_scelte = _scelte_per_esercizio(conn, compito_id, risposte)
            voce["vicinanza"] = _vicinanza(mio_posto, _posto_nella_stampa(conn, altro["compito_id"]))
            sue_scelte = _scelte_per_esercizio(conn, altro["compito_id"], altro["risposte"])
            comuni = [e for e in mie_scelte if e in sue_scelte]
            if comuni:
                uguali = [e for e in comuni if mie_scelte[e][0] == sue_scelte[e][0]]
                voce["stesse_scelte"] = {
                    "esercizi": len(comuni), "uguali": len(uguali),
                    "sbagliate": sum(not mie_scelte[e][1] for e in uguali),
                }
        simili.append(voce)
    simili.sort(key=lambda s: s["percentuale"], reverse=True)
    return simili


def _avviso_simile(s: dict) -> dict:
    """La parte da conservare di un avviso di compiti simili (vedi _risposte_simili)."""
    return {
        "matricola": s["matricola"], "nome": s["nome"], "cognome": s["cognome"], "codice": s["codice"],
        "percentuale": s["percentuale"], "identica": s["identica"], "errori_comuni": s["errori_comuni"],
        "uguali": s["uguali"], "confrontabili": s["posizioni_confrontabili"],
        "stesse_scelte": s.get("stesse_scelte"),
        "vicini": bool(s["vicinanza"] and s["vicinanza"].get("vicini")),
        "vicinanza": s["vicinanza"]["testo"] if s["vicinanza"] else "",
    }


def _salva_avvisi_simili(conn, appello_id: int, studente, codice: str, simili: list[dict]) -> None:
    """Salva sul risultato appena registrato gli avvisi di compiti simili, e su ciascuno
    degli altri compiti quello reciproco (l'altro era stato corretto prima, senza saperlo)."""
    avvisi = [_avviso_simile(s) for s in simili]
    conn.execute("UPDATE risultati SET avvisi_simili=? WHERE appello_id=? AND matricola=?",
                 (json.dumps(avvisi) if avvisi else None, appello_id, studente["matricola"]))
    for a in avvisi:
        r = conn.execute("SELECT avvisi_simili FROM risultati WHERE appello_id=? AND matricola=?",
                         (appello_id, a["matricola"])).fetchone()
        if r is None:
            continue
        suoi = [x for x in json.loads(r["avvisi_simili"] or "[]") if x["matricola"] != studente["matricola"]]
        suoi.append({**a, "matricola": studente["matricola"], "nome": studente["nome"],
                     "cognome": studente["cognome"], "codice": codice})
        conn.execute("UPDATE risultati SET avvisi_simili=? WHERE appello_id=? AND matricola=?",
                     (json.dumps(suoi), appello_id, a["matricola"]))


def completa_aperte(tag: str, appello_id: int, codice: str, risposte: str) -> str:
    """Se sono arrivate solo le lettere delle domande chiuse (senza il "-" delle aperte,
    es. JavaScript disattivato o codice non ancora riconosciuto mentre si scriveva), le
    rimette nelle posizioni giuste; in ogni altro caso le risposte restano come sono."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        compito = conn.execute(
            "SELECT soluzioni FROM compiti WHERE appello_id=? AND codice=?", (appello_id, codice)
        ).fetchone()
    finally:
        conn.close()
    soluzioni = compito["soluzioni"] if compito else ""
    if "-" not in soluzioni or "-" in risposte or len(risposte) != len(soluzioni.replace("-", "")):
        return risposte
    lettere = iter(risposte)
    return "".join("-" if c == "-" else next(lettere) for c in soluzioni)


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
        righe, dettaglio_disponibile = _righe_risposta(tag, compito["id"], soluzioni, risposte)
        # esercizi annullati nella revisione del testo: punteggio pieno, niente da giudicare
        annullati = {r.posizione: punteggio_massimo(corso, r.aperta) for r in righe if r.revisione == "annullato"}
        voto_base = valuta_con_override(
            soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota,
            override=annullati,
        )
        righe_valide = [r for r in righe if r.revisione != "annullato"]
        non_svolte = [r for r in righe_valide if r.obbligatorio and not r.aperta and not r.svolta
                      and r.revisione != "facoltativo"]
        da_valutare = [r for r in righe_valide if r.obbligatorio and not r.aperta and r.svolta and r.corretta]
        domande_aperte = [r for r in righe_valide if r.aperta]
        voto_fisso = valuta_con_override(
            soluzioni, risposte, corso.risposta_corretta, corso.risposta_sbagliata, corso.risposta_vuota,
            override={**annullati, **{r.posizione: 0 for r in da_valutare + domande_aperte}},
        )

        return ValutazionePreliminare(
            matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            codice=codice, risposte=risposte, voto_base=voto_base, righe=righe,
            non_svolte_obbligatorie=non_svolte, da_valutare=da_valutare, domande_aperte=domande_aperte,
            orale_obbligatorio=_orale_obbligatorio(conn, studente["matricola"]),
            lunghezza_attesa=len(soluzioni),
            codice_duplicato=_codice_duplicato(conn, appello_id, studente["matricola"], compito["id"]),
            risposte_simili=_risposte_simili(conn, appello_id, studente["matricola"], risposte,
                                             compito["id"], soluzioni.upper()),
            dettaglio_esercizi_disponibile=dettaglio_disponibile,
            voto_fisso=voto_fisso,
        )
    finally:
        conn.close()


def salva_bozza(
    tag: str, appello_id: int, matricola: str, codice: str, risposte: str, stato: Optional[dict] = None,
) -> None:
    """Salva (o sovrascrive) una bozza di correzione per riprenderla più tardi: non tocca
    'risultati', quindi non conta come esito né blocca il codice come duplicato. `stato`
    è quanto già fatto nella pagina di correzione (vedi stato_bozza)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO bozze_correzione (appello_id, matricola, codice, risposte, salvata_il, stato) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP,?) "
            "ON CONFLICT(appello_id, matricola) DO UPDATE SET "
            "codice=excluded.codice, risposte=excluded.risposte, salvata_il=excluded.salvata_il, "
            "stato=excluded.stato",
            (appello_id, matricola, codice, risposte, json.dumps(stato) if stato else None),
        )
        conn.commit()
    finally:
        conn.close()


def stato_bozza(tag: str, appello_id: int, matricola: str, codice: str) -> Optional[dict]:
    """Lo stato salvato con la bozza di questo studente, se è dello stesso compito."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        r = conn.execute(
            "SELECT codice, stato FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola)
        ).fetchone()
    finally:
        conn.close()
    if not r or not r["stato"] or r["codice"] != codice:
        return None
    return json.loads(r["stato"])


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


def _sincronizza_convocazione(conn, raggruppamento, appello_id: int, matricola: str) -> None:
    """Se `appello_id` è l'ultima prova del raggruppamento, la convocazione all'orale
    confermata (o segnata) lì diventa l'orale da sostenere nei risultati complessivi;
    tolta, o senza un compito valutato, lo studente non è convocato. Un orale già
    svolto non si tocca."""
    if raggruppamento is None or raggruppamento.membri[-1].id != appello_id:
        return
    r = conn.execute(
        "SELECT convocazione_orale, orale_motivazione FROM risultati "
        "WHERE appello_id=? AND matricola=? AND esito='voto' AND valutazione_sospesa=0",
        (appello_id, matricola),
    ).fetchone()
    if r and r["convocazione_orale"]:
        conn.execute(
            "INSERT INTO risultati (matricola, appello_id, richiede_orale, orale_motivazione, esito) "
            "VALUES (?,?,1,?,'voto') "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET "
            "richiede_orale=CASE WHEN risultati.orale_svolto=1 THEN risultati.richiede_orale ELSE 1 END, "
            "orale_motivazione=CASE WHEN risultati.orale_svolto=1 THEN risultati.orale_motivazione ELSE excluded.orale_motivazione END",
            (matricola, raggruppamento.appello_id, r["orale_motivazione"]),
        )
        return
    # la riga complessiva creata solo per l'orale (media non ancora calcolata) sparisce
    conn.execute(
        "DELETE FROM risultati WHERE appello_id=? AND matricola=? AND voto IS NULL AND voto_scritto IS NULL "
        "AND orale_svolto=0 AND verbalizzato=0",
        (raggruppamento.appello_id, matricola),
    )
    conn.execute(
        "UPDATE risultati SET richiede_orale=0, orale_motivazione=NULL "
        "WHERE appello_id=? AND matricola=? AND orale_svolto=0",
        (raggruppamento.appello_id, matricola),
    )


def convocazione_precedente(tag: str, appello_id: int, matricola: str) -> Optional[dict]:
    """Per una prova di un raggruppamento dopo la prima: {prova, motivazione} se nella
    prova precedente svolta dallo studente il docente l'ha convocato all'orale (va
    confermata o tolta in questa), None altrimenti. Una convocazione tolta in una prova
    non si ripropone nelle successive. Per le correzioni registrate prima delle
    convocazioni per prova, vale l'orale già richiesto sui risultati complessivi."""
    raggruppamento = corsi_service.get_raggruppamento_by_membro(tag, appello_id)
    if raggruppamento is None:
        return None
    ids = [m.id for m in raggruppamento.membri]
    if appello_id not in ids or ids.index(appello_id) == 0:
        return None
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for membro in reversed(raggruppamento.membri[:ids.index(appello_id)]):
            r = conn.execute(
                "SELECT convocazione_orale, orale_motivazione FROM risultati WHERE appello_id=? AND matricola=?",
                (membro.id, matricola),
            ).fetchone()
            if r is None:
                continue
            if r["convocazione_orale"]:
                return {"prova": membro.nome, "motivazione": r["orale_motivazione"] or ""}
            break
        vecchia = conn.execute(
            "SELECT orale_motivazione FROM risultati WHERE appello_id=? AND matricola=? "
            "AND richiede_orale=1 AND orale_svolto=0",
            (raggruppamento.appello_id, matricola),
        ).fetchone()
        nuove = conn.execute(
            f"SELECT 1 FROM risultati WHERE matricola=? AND convocazione_orale=1 "
            f"AND appello_id IN ({','.join('?' * len(ids))})",
            (matricola, *ids),
        ).fetchone()
        if vecchia and not nuove:
            return {"prova": "una prova precedente", "motivazione": vecchia["orale_motivazione"] or ""}
        return None
    finally:
        conn.close()


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
    rivalutazioni: Optional[dict[int, int]] = None,
) -> CorrezioneResult:
    """Salva il risultato. `punteggi_obbligatori` sono i punteggi (tra risposta_sbagliata
    e risposta_corretta, o punteggio_max_aperta per le domande aperte) assegnati dal
    docente agli esercizi che richiedono un giudizio manuale (obbligatori risposti
    correttamente, o domande aperte), dopo aver giudicato lo svolgimento scritto: devono
    esserci tutti (una correzione incompleta si salva come bozza, vedi salva_bozza),
    altrimenti solleva ValueError. `note_svolgimenti` sono
    le note del docente sugli errori di ciascuno svolgimento, `nota_correzione` una nota
    libera sul compito (es. perché il voto finale è stato cambiato), `rivalutazioni` i
    punteggi dati a mano agli esercizi a risposta chiusa (già compresi in voto_finale:
    si conservano solo per riproporli modificando la correzione). Se `richiedi_orale`,
    il voto scritto viene comunque salvato come riferimento ma l'esito resta "in attesa di
    orale" finché non si chiama completa_orale(). Se lo studente ha già una riga in
    orale_obbligatorio (imposta in un appello precedente, anche di un altro corso da cui è
    stato importato) e non è arrivata `conferma_orale_obbligatorio`, non salva nulla e
    solleva OraleObbligatorioNonConfermato perché il chiamante mostri l'avviso. Se lo studente ha già un
    esito registrato per questo appello (voto, assente o ritirato) e `modifica` non è
    True, non salva nulla e solleva RisultatoGiaValutato: una correzione "nuova" (dal
    form principale) non deve poter sovrascrivere per sbaglio un esito già inserito —
    solo un "Modifica" esplicito può."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    # Se questa prova è membro di un raggruppamento, l'orale è unico per l'intero
    # raggruppamento (non per la singola prova parziale): sulla prova la richiesta è solo
    # una convocazione segnalata, che l'ultima prova porta sulla riga del raggruppamento.
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
        # una nota si può scrivere su qualunque esercizio, non solo sugli svolgimenti
        note = {i: n.strip() for i, n in (note_svolgimenti or {}).items()
                if 0 <= i < len(soluzioni) and n and n.strip()}
        minimo_chiusa = min(corso.risposta_sbagliata, corso.risposta_vuota)
        for i, v in (rivalutazioni or {}).items():
            if not minimo_chiusa <= v <= corso.risposta_corretta:
                raise ValueError(f"Punteggio {v} dell'esercizio {i + 1} fuori dall'intervallo consentito "
                                 f"({minimo_chiusa}…{corso.risposta_corretta})")
        calcolo = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        if calcolo.mancanti:
            numeri = ", ".join(str(i + 1) for i in sorted(calcolo.mancanti))
            raise ValueError(f"Manca il punteggio degli esercizi {numeri}: confermalo, o salva la correzione come bozza")
        insufficiente = calcolo.insufficiente
        richiedi_orale_su_prova = richiedi_orale and raggruppamento_membro is None
        ultima_prova = raggruppamento_membro is not None and raggruppamento_membro.membri[-1].id == appello_id

        voto = voto_finale if voto_finale is not None else calcolo.voto
        if richiedi_orale and (insufficiente or voto < votomin):
            raise OraleNonConsentito("L'orale si può richiedere solo se il compito scritto è sufficiente.")
        if insufficiente and voto >= votomin:
            voto = votomin - 1
        voto_modificato = voto != calcolo.voto

        # con uno scritto insufficiente l'orale non si può richiedere: niente da confermare
        orale_obb = _orale_obbligatorio(conn, studente["matricola"])
        if (orale_obb and not richiedi_orale and not conferma_orale_obbligatorio
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
            "orale_motivazione, esito_orale, valutazione_sospesa, note_svolgimenti, nota_correzione, voto_modificato, "
            "convocazione_orale, rivalutazioni) "
            "VALUES (?,?,?,?,?,?,'voto',?,?,?,?,?,NULL,?,?,?,?,?,?) "
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
            "voto_modificato=excluded.voto_modificato, convocazione_orale=excluded.convocazione_orale, "
            "rivalutazioni=excluded.rivalutazioni",
            (
                matricola, appello_id, compito["id"], risposte, voto, voto, insufficiente,
                json.dumps(punteggi) if punteggi else None,
                richiedi_orale_su_prova, False,
                orale_motivazione or None if richiedi_orale else None,
                False, json.dumps(note) if note else None, (nota_correzione or "").strip() or None,
                voto_modificato, richiedi_orale and raggruppamento_membro is not None,
                json.dumps(rivalutazioni) if rivalutazioni else None,
            ),
        )
        _salva_avvisi_simili(conn, appello_id, studente, codice, _risposte_simili(
            conn, appello_id, studente["matricola"], risposte, compito["id"], soluzioni.upper()))
        # In una prova di un raggruppamento l'orale è una convocazione segnalata sulla
        # prova: decide l'ultima prova, che la porta (o la toglie) nei risultati complessivi
        _sincronizza_convocazione(conn, raggruppamento_membro, appello_id, matricola)
        if richiedi_orale and corso.orale_dopo_richiesta and (raggruppamento_membro is None or ultima_prova):
            conn.execute(
                "INSERT INTO orale_obbligatorio (matricola, motivazione, origine) VALUES (?,?,?) "
                "ON CONFLICT(matricola) DO NOTHING",
                (matricola, orale_motivazione or "", appello.nome),
            )
        _applica_soglia_orale_automatica(conn, corso, matricola)
        # una correzione confermata rende superflua un'eventuale bozza ancora aperta per lo
        # stesso studente su questo appello.
        conn.execute("DELETE FROM bozze_correzione WHERE appello_id=? AND matricola=?", (appello_id, matricola))
        conn.commit()
        return CorrezioneResult(
            voto=voto, matricola=studente["matricola"], nome=studente["nome"], cognome=studente["cognome"],
            insufficiente_per_obbligatorio=insufficiente, richiede_orale=richiedi_orale,
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
    """Cambia il punteggio di un singolo svolgimento di un compito già registrato, con la
    sua nota, dalla pagina che confronta gli svolgimenti esercizio per esercizio. Il voto si
    ricalcola, tranne quando era stato cambiato a mano (resta quello, e si avvisa) o l'orale
    è già stato svolto (cambia solo il voto scritto di riferimento)."""
    if punteggio is None:
        raise ValueError("Scrivi il punteggio dello svolgimento")
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
        # risultati registrati prima che i punteggi venissero salvati tutti: le posizioni
        # senza punteggio valevano quello calcolato dalla lettera
        for i in prima.mancanti:
            lettera = risposte[i].upper() if i < len(risposte) else "X"
            punteggi[i] = corso.risposta_corretta if lettera == soluzioni[i].upper() else (
                corso.risposta_vuota if lettera == "X" else corso.risposta_sbagliata)
        prima = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        _controlla_punteggio(corso, prima.manuali[posizione], punteggio)
        punteggi[posizione] = punteggio
        note = _json_posizioni(r["note_svolgimenti"])
        if nota and nota.strip():
            note[posizione] = nota.strip()
        else:
            note.pop(posizione, None)

        dopo = calcola_voto(corso, votomin, soluzioni, risposte, posizioni, punteggi)
        voto_a_mano = bool(r["voto_modificato"]) and r["voto_scritto"] is not None
        voto_scritto = r["voto_scritto"] if voto_a_mano else dopo.voto
        voto = r["voto"] if r["orale_svolto"] else voto_scritto
        avviso = None
        if voto_a_mano and dopo.voto != prima.voto:
            avviso = (f"Il voto finale di {r['cognome']} {r['nome']} era stato cambiato a mano ({voto_scritto}) e "
                      f"non è stato ricalcolato: con i punteggi attuali sarebbe {dopo.voto}.")
        conn.execute(
            "UPDATE risultati SET punteggi_obbligatori=?, note_svolgimenti=?, voto=?, voto_scritto=?, "
            "valutazione_sospesa=0, insufficiente_per_obbligatorio=?, voto_modificato=? WHERE appello_id=? AND matricola=?",
            (json.dumps(punteggi) if punteggi else None, json.dumps(note) if note else None,
             voto, voto_scritto, dopo.insufficiente, voto_a_mano and voto_scritto != dopo.voto,
             appello_id, matricola),
        )
        conn.commit()
        return {"voto": voto, "voto_calcolato": dopo.voto, "avviso": avviso}
    finally:
        conn.close()


def svolgimenti_appello(tag: str, appello_id: int, escludi_matricola: Optional[str] = None) -> dict:
    """Tutti gli svolgimenti giudicati a mano in un appello (obbligatori con la risposta
    corretta e domande aperte), raggruppati per esercizio: per confrontare punteggi e note
    di studenti diversi sullo stesso esercizio. Per ogni esercizio anche le note usate, con i punteggi dati a ciascuna (una
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
            "e.nome AS esercizio_nome, rv.revisione FROM compito_esercizi ce JOIN esercizi e ON e.id = ce.esercizio_id "
            "JOIN compiti c ON c.id = ce.compito_id "
            "LEFT JOIN appello_revisione_esercizi rv ON rv.appello_id = c.appello_id AND rv.esercizio_id = ce.esercizio_id "
            "WHERE c.appello_id=? ORDER BY ce.posizione",
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
                "voci": [], "prima_posizione": i,
            })
            es["prima_posizione"] = min(es["prima_posizione"], i)
            es["voci"].append({
                "matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"], "posizione": i,
                "punteggio": punteggi.get(i), "nota": note.get(i, ""),
                "voto": r["voto"], "modificabile": modificabile,
                "verbalizzato": bool(r["verbalizzato"]),
            })
        voto_a_mano = bool(r["voto_modificato"]) and r["voto_scritto"] is not None
        if voto_a_mano or r["nota_correzione"]:
            compiti_annotati.append({
                "matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"],
                "voto": r["voto_scritto"], "voto_calcolato": calcolo.voto if voto_a_mano else None,
                "nota": r["nota_correzione"] or "", "verbalizzato": bool(r["verbalizzato"]),
            })

    elenco = sorted(esercizi.values(), key=lambda e: e["prima_posizione"])
    for es in elenco:
        # dal punteggio più alto: studenti con lo stesso punteggio vicini, per confrontarne
        # le note a colpo d'occhio
        es["voci"].sort(key=lambda v: (-(v["punteggio"] if v["punteggio"] is not None else 1e9), v["cognome"].lower()))
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
    return {"esercizi": elenco, "compiti_annotati": compiti_annotati}


def note_esercizi(tag: str, appello_id: int, escludi_matricola: Optional[str] = None,
                  esercizio_id: Optional[int] = None) -> dict[int, list[dict]]:
    """Le note scritte sugli esercizi dei compiti registrati nell'appello (su qualunque
    esercizio, non solo sugli svolgimenti), per esercizio: {esercizio_id: [voce]}, con
    studente, compito, posizione, risposta data e giusta, punteggio calcolato e assegnato
    e voto del compito. Servono come suggerimento mentre si scrive una nota simile (per
    dare lo stesso giudizio agli stessi errori) e per l'elenco delle note di un esercizio."""
    corso = corsi_service.get_corso(tag)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        risultati = conn.execute(
            "SELECT r.matricola, r.risposte, r.voto, r.voto_scritto, r.punteggi_obbligatori, r.rivalutazioni, "
            "r.note_svolgimenti, r.compito_id, c.codice, c.soluzioni, s.nome, s.cognome "
            "FROM risultati r JOIN compiti c ON c.id = r.compito_id JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.note_svolgimenti IS NOT NULL "
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
        posizioni: dict[int, dict[int, dict]] = {}
        for p in conn.execute(
            "SELECT ce.compito_id, ce.posizione, ce.esercizio_id, ce.aperta, ce.obbligatorio, ce.risposte_mischiate, "
            "rv.revisione, e.nome AS esercizio_nome, ev.testo FROM compito_esercizi ce JOIN compiti c ON c.id = ce.compito_id "
            "LEFT JOIN esercizi e ON e.id = ce.esercizio_id LEFT JOIN esercizio_varianti ev ON ev.id = ce.variante_id "
            "LEFT JOIN appello_revisione_esercizi rv ON rv.appello_id = c.appello_id AND rv.esercizio_id = ce.esercizio_id "
            "WHERE c.appello_id=?",
            (appello_id,),
        ):
            posizioni.setdefault(p["compito_id"], {})[p["posizione"]] = dict(p)
    finally:
        conn.close()
    esito: dict[int, list[dict]] = {}
    for r in risultati:
        if escludi_matricola is not None and r["matricola"] == escludi_matricola:
            continue
        note = _json_posizioni(r["note_svolgimenti"])
        punteggi = _json_posizioni(r["punteggi_obbligatori"])
        rivalutazioni = _json_posizioni(r["rivalutazioni"])
        soluzioni, risposte = (r["soluzioni"] or "").upper(), (r["risposte"] or "").upper()
        for i, nota in note.items():
            p = posizioni.get(r["compito_id"], {}).get(i)
            if not nota or p is None or (esercizio_id is not None and p["esercizio_id"] != esercizio_id):
                continue
            lettera = risposte[i] if i < len(risposte) else "X"
            giusta = soluzioni[i] if i < len(soluzioni) else "?"
            if p["aperta"]:
                calcolato = None
            else:
                calcolato = corso.risposta_vuota if lettera == "X" else (
                    corso.risposta_corretta if lettera == giusta else corso.risposta_sbagliata)
            if p["revisione"] == "annullato":
                assegnato = punteggio_massimo(corso, bool(p["aperta"]))
            else:
                assegnato = punteggi.get(i, rivalutazioni.get(i, calcolato))
            esito.setdefault(p["esercizio_id"], []).append({
                "nota": nota, "punteggio": assegnato, "calcolato": calcolato,
                "studente": f"{r['cognome']} {r['nome']}", "matricola": r["matricola"],
                "codice": r["codice"], "posizione": i, "aperta": bool(p["aperta"]),
                "obbligatorio": bool(p["obbligatorio"]), "lettera_data": "" if p["aperta"] else lettera,
                "lettera_corretta": "" if p["aperta"] else giusta,
                "voto": r["voto_scritto"] if r["voto_scritto"] is not None else r["voto"],
                # per mostrare l'esercizio com'era stampato su quel compito
                "esercizio_nome": p["esercizio_nome"] or f"Esercizio #{p['esercizio_id']}",
                "testo": p["testo"] or "", "corretta": not p["aperta"] and lettera == giusta,
                "risposte": json.loads(p["risposte_mischiate"]) if p["risposte_mischiate"] else [],
            })
    return esito


def note_appello(tag: str, appello_id: int) -> dict:
    """Tutte le note della correzione di un appello, per la pagina che le riassume: le
    note sul voto dei compiti (con il voto calcolato, se cambiato a mano), le motivazioni
    dell'orale e le note esercizio per esercizio (vedi note_esercizi), per esercizio
    nell'ordine del nome."""
    annotati = svolgimenti_appello(tag, appello_id)["compiti_annotati"]
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        codici = {r["matricola"]: r["codice"] for r in conn.execute(
            "SELECT r.matricola, c.codice FROM risultati r JOIN compiti c ON c.id = r.compito_id WHERE r.appello_id=?",
            (appello_id,))}
        orali = [dict(r) for r in conn.execute(
            "SELECT r.matricola, r.orale_motivazione, r.orale_svolto, r.esito_orale, r.convocazione_orale, r.voto, "
            "r.voto_scritto, s.nome, s.cognome FROM risultati r JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND (r.richiede_orale=1 OR r.convocazione_orale=1) "
            "ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,))]
    finally:
        conn.close()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        simili = []
        visti = set()
        for r in conn.execute(
            "SELECT r.matricola, r.avvisi_simili, s.nome, s.cognome FROM risultati r JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.avvisi_simili IS NOT NULL ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ):
            for a in json.loads(r["avvisi_simili"]):
                coppia = frozenset((r["matricola"], a["matricola"]))
                if coppia in visti:
                    continue
                visti.add(coppia)
                simili.append({**a, "di_matricola": r["matricola"], "di_nome": r["nome"], "di_cognome": r["cognome"],
                               "di_codice": codici.get(r["matricola"])})
    finally:
        conn.close()
    for c in annotati:
        c["codice"] = codici.get(c["matricola"])
    for o in orali:
        o["codice"] = codici.get(o["matricola"])
    esercizi = []
    for esercizio_id, voci in note_esercizi(tag, appello_id).items():
        voci.sort(key=lambda v: (v["nota"].strip().lower(), v["studente"].lower()))
        esercizi.append({"esercizio_id": esercizio_id, "nome": voci[0]["esercizio_nome"], "voci": voci})
    esercizi.sort(key=lambda e: e["nome"].lower())
    return {"compiti": annotati, "orali": orali, "esercizi": esercizi, "simili": simili}


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
            "esito_orale=NULL, valutazione_sospesa=0, voto_modificato=0, rivalutazioni=NULL",
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
        rivalutazioni = json.loads(r["rivalutazioni"]) if r["rivalutazioni"] else {}

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
            calcolato = None if pos["aperta"] else (
                corso.risposta_corretta if corretta
                else (corso.risposta_vuota if lettera_data == "X" else corso.risposta_sbagliata)
            )
            if pos.get("revisione") == "annullato":
                punteggio_assegnato = punteggio_massimo(corso, bool(pos["aperta"]))
            elif str(i) in punteggi:
                punteggio_assegnato = punteggi[str(i)]
            elif str(i) in rivalutazioni:
                punteggio_assegnato = rivalutazioni[str(i)]
            else:
                punteggio_assegnato = calcolato
            righe.append({
                "posizione": i, "esercizio_nome": pos["esercizio_nome"] or f"Posizione {i + 1}",
                "obbligatorio": bool(pos["obbligatorio"]), "aperta": bool(pos["aperta"]),
                "lettera_data": lettera_data, "lettera_corretta": lettera_corretta, "corretta": corretta,
                "punteggio_assegnato": punteggio_assegnato, "calcolato": calcolato, "nota": note.get(str(i), ""),
                "revisione": pos.get("revisione"), "testo": pos.get("testo") or "",
                "risposte": json.loads(pos["risposte_mischiate"]) if pos.get("risposte_mischiate") else [],
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


def note_prove_precedenti(tag: str, appello_id: int, matricola: str) -> list[dict]:
    """Per una prova di un raggruppamento dopo la prima: le note scritte correggendo lo
    stesso studente nelle prove precedenti (sul voto, per l'orale ed esercizio per
    esercizio), prova per prova, con voto e codice del compito. Solo le prove con qualche
    nota; vuoto per un appello qualsiasi o per la prima prova."""
    raggruppamento = corsi_service.get_raggruppamento_by_membro(tag, appello_id)
    if raggruppamento is None:
        return []
    ids = [m.id for m in raggruppamento.membri]
    if appello_id not in ids:
        return []
    esito = []
    for membro in raggruppamento.membri[:ids.index(appello_id)]:
        try:
            d = dettaglio_risultato(tag, membro.id, matricola)
        except ValueError:
            continue
        r = d["risultato"]
        esercizi = [e for e in d["righe"] if e["nota"]]
        orale = r["orale_motivazione"] if (r["richiede_orale"] or r["convocazione_orale"]) else ""
        if not (r["nota_correzione"] or orale or esercizi or r["annullamento_nota"]):
            continue
        esito.append({
            "prova": membro.nome, "appello_id": membro.id, "codice": r["codice"], "esito": r["esito"],
            "voto": r["voto_scritto"] if r["voto_scritto"] is not None else r["voto"],
            "nota": r["nota_correzione"] or r["annullamento_nota"] or "", "orale": orale or "",
            "convocato": bool(r["convocazione_orale"]), "esercizi": esercizi,
        })
    return esito


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
    finally:
        conn.close()
    # per un raggruppamento l'orale è solo di chi ha superato tutte le prove: una
    # convocazione resta registrata, ma non si mostra finché lo studente non le supera
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    if raggruppamento is not None:
        from quizesame.services import risultati as risultati_service
        voti = risultati_service.voti_prove_raggruppamento(tag, raggruppamento)
        rows = [r for r in rows if voti.get(r["matricola"], {}).get("superate")]
    return [dict(r) for r in rows]


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


def elimina_risultato(tag: str, appello_id: int, matricola: str) -> None:
    """Cancella l'esito registrato, ma lo studente non sparisce: torna da valutare. Se
    aveva un compito corretto, codice e risposte restano come bozza (in "Correzioni da
    completare", da riprendere); se aveva svolto la prova (non assente) resta segnato
    presente, così compare anche tra i presenti da correggere."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT r.verbalizzato, r.esito, r.risposte, c.codice FROM risultati r "
            "LEFT JOIN compiti c ON c.id = r.compito_id WHERE r.matricola=? AND r.appello_id=?",
            (matricola, appello_id),
        ).fetchone()
        if row and row["verbalizzato"]:
            raise GiaVerbalizzato("Non è possibile eliminare un risultato già verbalizzato")
        if row and row["codice"] and row["risposte"]:
            conn.execute(
                "INSERT INTO bozze_correzione (appello_id, matricola, codice, risposte, salvata_il) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(appello_id, matricola) DO UPDATE SET "
                "codice=excluded.codice, risposte=excluded.risposte, salvata_il=excluded.salvata_il",
                (appello_id, matricola, row["codice"], row["risposte"]),
            )
        if row and (row["codice"] or row["esito"] == "ritirato"):
            conn.execute(
                "INSERT OR IGNORE INTO appello_presenze (matricola, appello_id) VALUES (?,?)", (matricola, appello_id)
            )
        conn.execute("DELETE FROM risultati WHERE matricola=? AND appello_id=?", (matricola, appello_id))
        # gli avvisi di compiti simili che gli altri avevano con questo
        for r in conn.execute("SELECT matricola, avvisi_simili FROM risultati WHERE appello_id=? AND avvisi_simili IS NOT NULL",
                              (appello_id,)).fetchall():
            restanti = [a for a in json.loads(r["avvisi_simili"]) if a["matricola"] != matricola]
            conn.execute("UPDATE risultati SET avvisi_simili=? WHERE appello_id=? AND matricola=?",
                         (json.dumps(restanti) if restanti else None, appello_id, r["matricola"]))
        conn.commit()
    finally:
        conn.close()
