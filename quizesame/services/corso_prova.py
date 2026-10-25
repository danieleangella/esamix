"""Corso di prova: un corso completo con dati inventati, per provare tutte le funzioni
dell'app senza toccare i corsi veri (un appello e due prove parziali raggruppate, con
correzioni già fatte e altre da fare), e da eliminare senza lasciare tracce.

Tutto sta nella cartella del corso (database con studenti, esercizi, risultati; file
generati): eliminarla basta, più le righe del registro delle attività che lo riguardano.
Il corso ha un tag fisso e un marcatore nei suoi metadati: si elimina solo un corso che
li ha entrambi, così non si può mai cancellare per sbaglio un corso vero."""
import json
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from quizesame import config
from quizesame.services import compiti as compiti_service
from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service
from quizesame.services import esportazione as esportazione_service
from quizesame.services import presenze as presenze_service
from quizesame.services import registro as registro_service
from quizesame.services import studenti as studenti_service

TAG = "PROVA-EsaMiX"
NOME = "Corso di prova EsaMiX"
MARCATORE = "corso_prova"

# matricole fuori dagli intervalli reali (999…), per non confondersi con studenti veri
STUDENTI = [
    ("9990001", "Anna", "Abate"), ("9990002", "Bruno", "Bianchi"), ("9990003", "Carla", "Conti"),
    ("9990004", "Dario", "De Luca"), ("9990005", "Elena", "Esposito"), ("9990006", "Fabio", "Ferri"),
    ("9990007", "Giulia", "Gallo"), ("9990008", "Luca", "Lombardi"), ("9990009", "Marta", "Moretti"),
    ("9990010", "Nicola", "Neri"), ("9990011", "Olga", "Orlando"), ("9990012", "Paolo", "Pellegrini"),
    ("9990013", "Rita", "Russo"), ("9990014", "Sergio", "Santoro"), ("9990015", "Teresa", "Testa"),
    ("9990016", "Ugo", "Villa"),
]

NOTE_ERRORI = [
    ("svolgimento completo e chiaro", 3),
    ("passaggi giusti ma non giustificati", 2),
    ("errore di segno in un passaggio", 1),
    ("risultato giusto ma procedimento sbagliato", 0),
]


def installato() -> bool:
    return config.corso_exists(TAG) and corsi_service.get_meta(TAG).get(MARCATORE) == "1"


def da_correggere() -> list[dict]:
    """Le correzioni lasciate da fare, con codice e risposte da inserire."""
    if not installato():
        return []
    return json.loads(corsi_service.get_meta(TAG).get("prova_da_correggere") or "[]")


def _risposte_esercizio(a: int, b: int, operazione: str) -> tuple[str, list[str]]:
    risultato = {"+": a + b, "-": a - b, "×": a * b}[operazione]
    giusta = f"{risultato} (giusta)"
    sbagliate = [f"{risultato + d} (sbagliata)" for d in (1, -1, 2, 10)]
    simbolo = {"+": "+", "-": "-", "×": r"\times"}[operazione]
    return f"Quanto fa ${a} {simbolo} {b}$? (la risposta giusta è {risultato})", [giusta] + sbagliate


def _crea_esercizi() -> dict:
    """Esercizi in cui la risposta giusta è sempre dichiarata: sul foglio stampato e nella
    correzione si vede subito quale lettera è giusta e quali sono sbagliate. Ritorna gli
    id: "mc" (scelta multipla), "obbl" (da svolgere, per gli obbligatori), "aperta"."""
    operazioni = [("+", "Somma"), ("-", "Differenza"), ("×", "Prodotto")]
    ids = {"mc": [], "obbl": []}
    for n in range(14):
        op, nome = operazioni[n % 3]
        coppie = [(n + 2, n % 5 + 1), (n + 5, n % 4 + 2)]
        da_svolgere = n >= 11
        varianti = []
        for a, b in coppie:
            testo, risposte = _risposte_esercizio(a, b, op)
            if da_svolgere:
                testo += " Scrivi lo svolgimento: viene valutato a mano."
            varianti.append({"testo": testo, "risposte": risposte})
        e = esercizi_service.create_esercizio(
            TAG, f"{nome} {n + 1}" + (" (da svolgere)" if da_svolgere else ""), "Esercizio di prova", varianti,
            argomento="Prova", soluzione="Basta fare il conto: la risposta giusta è quella indicata nel testo.",
        )
        ids["obbl" if da_svolgere else "mc"].append(e.id)
    ids["aperta"] = esercizi_service.create_esercizio(
        TAG, "Domanda aperta di prova", "Esercizio di prova",
        [{"testo": "Spiega con parole tue perché $2 + 2 = 4$ (domanda aperta: punteggio a mano).", "risposte": []}],
        argomento="Prova", soluzione="Qualunque spiegazione sensata del conteggio.", aperta=True,
    ).id
    return ids


def _risposte_studente(compito: dict, bravura: float, rng: random.Random, obbligatorio_vuoto: bool = False) -> str:
    """Risposte plausibili: giuste con probabilità `bravura`, altrimenti sbagliate o non
    date; un obbligatorio non resta mai senza risposta, a meno di chiederlo (un solo caso,
    per mostrare il compito insufficiente per obbligatorio non svolto)."""
    posizioni = compiti_service.list_posizioni_compito(TAG, compito["id"])
    obbligatorie = [p["posizione"] for p in posizioni if p["obbligatorio"] and not p["aperta"]]
    vuota = obbligatorie[0] if obbligatorio_vuoto and obbligatorie else None
    lettere = []
    for i, giusta in enumerate(compito["soluzioni"]):
        x = rng.random()
        if giusta == "-":
            lettere.append("-")
        elif i == vuota:
            lettere.append("X")
        elif x < bravura or (i in obbligatorie and x < 0.8):
            lettere.append(giusta)
        elif i in obbligatorie or x < bravura + (1 - bravura) * 0.6:
            lettere.append(rng.choice([c for c in "ABCDE" if c != giusta]))
        else:
            lettere.append("X")
    return "".join(lettere)


def _correggi(appello_id: int, matricola: str, compito: dict, rng: random.Random, bravura: float,
              rimanda: bool = False, voto_a_mano: int | None = None, nota: str = "",
              richiedi_orale: bool = False, obbligatorio_vuoto: bool = False) -> None:
    posizioni = compiti_service.list_posizioni_compito(TAG, compito["id"])
    risposte = _risposte_studente(compito, bravura, rng, obbligatorio_vuoto)
    punteggi, note = {}, {}
    for p in posizioni:
        i = p["posizione"]
        da_giudicare = p["aperta"] or (p["obbligatorio"] and risposte[i] == compito["soluzioni"][i])
        if not da_giudicare or rimanda:
            continue
        nota_errore, punteggio = NOTE_ERRORI[rng.randrange(len(NOTE_ERRORI))]
        punteggi[i], note[i] = punteggio, nota_errore
    def salva(orale: bool):
        correzione_service.conferma_risultato(
            TAG, appello_id, matricola, compito["codice"], risposte, voto_finale=voto_a_mano,
            punteggi_obbligatori=punteggi, note_svolgimenti=note, nota_correzione=nota,
            richiedi_orale=orale, conferma_orale_obbligatorio=True,
        )
    try:
        salva(richiedi_orale)
    except correzione_service.OraleNonConsentito:
        salva(False)  # scritto insufficiente: niente orale


def _prepara_appello(appello_id: int, esercizi: list[tuple[int, bool]], n_compiti: int,
                     da_compilare: list) -> list[dict]:
    """Testo chiuso e un blocco di compiti, come dalla scheda Compiti d'esame; i .tex da
    compilare in PDF (compiti, griglia, fogli da pubblicare) si aggiungono a `da_compilare`."""
    for esercizio_id, obbligatorio in esercizi:
        esercizi_service.assegna_a_appello(TAG, appello_id, esercizio_id, obbligatorio=obbligatorio)
    corsi_service.chiudi_testo(TAG, appello_id)
    blocco = compiti_service.genera_blocco(TAG, appello_id, n_compiti)
    da_compilare.extend([
        compiti_service.path_blocco(TAG, appello_id, blocco.numero, "tex"),
        compiti_service.path_griglia(TAG, appello_id, blocco.numero, "tex"),
        compiti_service.path_riferimento(TAG, appello_id, "tex"),
        compiti_service.path_riferimento_soluzioni(TAG, appello_id, "tex"),
    ])
    return compiti_service.list_compiti(TAG, appello_id)


def _compila(percorsi: list) -> list[str]:
    """Compila i PDF in parallelo; ritorna i messaggi di quelli non riusciti (es. LaTeX non
    installato): il corso di prova resta comunque utilizzabile, i PDF si rigenerano dopo."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        esiti = list(pool.map(compiti_service.compila_pdf, percorsi))
    return [f"{p.name}: {e.messaggio}" for p, e in zip(percorsi, esiti) if not e.ok]


def _da_fare(appello, matricola: str, compito: dict, rng: random.Random) -> dict:
    nome = next(f"{c} {n}" for m, n, c in STUDENTI if m == matricola)
    return {
        "appello": appello.nome, "appello_id": appello.id, "studente": nome, "matricola": matricola,
        "codice": compito["codice"], "risposte": _risposte_studente(compito, 0.75, rng),
    }


def installa(docente: str = "") -> list[str]:
    """Ritorna gli eventuali PDF non compilati (lista vuota se è andato tutto bene)."""
    if config.corso_exists(TAG):
        raise ValueError("Il corso di prova è già installato: eliminalo prima di reinstallarlo")
    oggi = date.today()
    anno = f"{oggi.year}/{oggi.year + 1}" if oggi.month >= 8 else f"{oggi.year - 1}/{oggi.year}"
    corsi_service.create_corso(TAG, NOME, "Dati di esempio", "", anno, docente or "Docente di prova",
                               consegna=2, eredita=False)
    try:
        return _popola(oggi)
    except Exception:
        # mai lasciare un corso di prova a metà
        corsi_service.elimina_corso(TAG)
        raise


def _popola(oggi: date) -> list[str]:
    corsi_service.update_meta(TAG, **{MARCATORE: "1"})
    da_compilare = []
    rng = random.Random(2026)
    for i, (matricola, nome, cognome) in enumerate(STUDENTI):
        studenti_service.crea_studente(TAG, matricola, nome, cognome, dsa_tipo="DSA" if i == 4 else "")
    ids = _crea_esercizi()
    da_fare = []

    # --- appello singolo: 2 obbligatori e una domanda aperta; correzioni di ogni tipo
    appello = corsi_service.create_appello(TAG, "Appello di prova", data=(oggi - timedelta(days=2)).strftime("%d/%m/%Y"))
    compiti = _prepara_appello(
        appello.id, [(i, False) for i in ids["mc"][:9]] + [(i, True) for i in ids["obbl"][:2]] + [(ids["aperta"], False)], 16, da_compilare,
    )
    iscritti = [m for m, _, _ in STUDENTI[:14]]
    for m in iscritti:
        esportazione_service.aggiungi_iscritto_manuale(TAG, appello.id, m)
    presenti = iscritti[:12]
    for m in presenti:
        presenze_service.segna_presente(TAG, appello.id, m)
    c = iter(compiti)
    _correggi(appello.id, iscritti[0], next(c), rng, 0.95)
    _correggi(appello.id, iscritti[1], next(c), rng, 0.85)
    _correggi(appello.id, iscritti[2], next(c), rng, 0.8)
    _correggi(appello.id, iscritti[3], next(c), rng, 0.6)
    _correggi(appello.id, iscritti[4], next(c), rng, 0.9, voto_a_mano=27,
              nota="Voto alzato: svolgimenti molto ordinati, errori solo di distrazione")
    _correggi(appello.id, iscritti[5], next(c), rng, 0.9, richiedi_orale=True)
    _correggi(appello.id, iscritti[6], next(c), rng, 0.9, obbligatorio_vuoto=True)
    _correggi(appello.id, iscritti[7], next(c), rng, 0.8, rimanda=True)
    bozza = next(c)
    correzione_service.salva_bozza(TAG, appello.id, iscritti[8], bozza["codice"],
                                   _risposte_studente(bozza, 0.75, rng))
    for m in iscritti[9:12]:
        da_fare.append(_da_fare(appello, m, next(c), rng))
    correzione_service.segna_esito_speciale(TAG, appello.id, iscritti[12], "assente")
    # iscritti[13]: iscritto, non presente, nessun esito (da segnare assente a fine correzione)

    # --- due prove parziali raggruppate: la prima corretta e conclusa, la seconda in corso
    # con gli svolgimenti rimandati da valutare tutti insieme
    prima = corsi_service.create_appello(TAG, "Prima prova parziale di prova", data=(oggi - timedelta(days=40)).strftime("%d/%m/%Y"))
    seconda = corsi_service.create_appello(TAG, "Seconda prova parziale di prova", data=(oggi - timedelta(days=1)).strftime("%d/%m/%Y"))
    corsi_service.create_raggruppamento(TAG, "Prove parziali di prova", [prima.id, seconda.id])
    compiti_prima = _prepara_appello(
        prima.id, [(i, False) for i in ids["mc"][:8]] + [(ids["obbl"][0], True), (ids["obbl"][2], True)], 12, da_compilare,
    )
    iscritti_prima = [m for m, _, _ in STUDENTI[:10]]
    for m in iscritti_prima:
        esportazione_service.aggiungi_iscritto_manuale(TAG, prima.id, m)
        presenze_service.segna_presente(TAG, prima.id, m)
    for m, compito, bravura in zip(iscritti_prima[:9], compiti_prima, [0.95, 0.9, 0.85, 0.8, 0.9, 0.85, 0.75, 0.55, 0.5]):
        _correggi(prima.id, m, compito, rng, bravura)
    correzione_service.segna_esito_speciale(TAG, prima.id, iscritti_prima[9], "ritirato")
    corsi_service.concludi_correzione_scritti(TAG, prima.id)

    compiti_seconda = _prepara_appello(
        seconda.id, [(i, False) for i in ids["mc"][3:11]] + [(ids["obbl"][1], True), (ids["aperta"], False)], 12, da_compilare,
    )
    raggruppamento = corsi_service.get_raggruppamento_by_membro(TAG, seconda.id)
    ammessi = [s["matricola"] for s in corsi_service.list_ammessi_prova(TAG, raggruppamento, 1)]
    for m in ammessi:
        presenze_service.segna_presente(TAG, seconda.id, m)
    c = iter(compiti_seconda)
    for m in ammessi[:2]:
        _correggi(seconda.id, m, next(c), rng, 0.85)
    for m in ammessi[2:4]:
        _correggi(seconda.id, m, next(c), rng, 0.8, rimanda=True)
    corsi_service.update_appello(TAG, seconda.id, rimanda_svolgimenti=True)
    for m in ammessi[4:]:
        da_fare.append(_da_fare(seconda, m, next(c), rng))

    corsi_service.update_meta(TAG, prova_da_correggere=json.dumps(da_fare, ensure_ascii=False))
    return _compila(da_compilare)


def elimina() -> None:
    """Elimina il corso di prova e le righe del registro delle attività che lo riguardano.
    Rifiuta un corso con lo stesso tag ma senza il marcatore: potrebbe essere un corso vero."""
    if not config.corso_exists(TAG):
        raise ValueError("Il corso di prova non è installato")
    if not installato():
        raise ValueError(f"Il corso «{TAG}» non è il corso di prova creato dall'app: non lo elimino")
    corsi_service.elimina_corso(TAG)
    registro_service.dimentica_corso(TAG)
