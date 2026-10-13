"""Generazione degli esercizi di un appello con Claude (API di Anthropic), prendendo
come modello gli esercizi delle prove analoghe degli anni precedenti: per una prova di
un raggruppamento (es. la seconda prova parziale) le prove nella stessa posizione dei
raggruppamenti degli altri anni, per un appello normale gli altri appelli normali.

Claude risponde nel formato testuale di import degli esercizi (FORMATO_ESERCIZI, lo
stesso descritto nella guida), che passa poi dalla normale pagina di conferma
dell'import: nulla viene salvato senza che il docente scelga cosa tenere.

Ad Anthropic (o a OpenRouter, che inoltra ad Anthropic) vengono inviati solo testi di
esercizi e impostazioni del corso, mai dati degli studenti, e solo quando il docente lo
chiede esplicitamente. Senza chiave API lo
stesso prompt si può copiare e incollare in claude.ai (vedi costruisci_prompt)."""
import os
import re
from datetime import datetime
from typing import Optional

from quizesame.services import app_config as app_config_service
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service

MODELLO = "claude-opus-5"
# OpenRouter espone la stessa API Messages di Anthropic a questo indirizzo, con la propria
# chiave come token Bearer e i modelli con il prefisso "anthropic/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
MODELLO_OPENROUTER = "anthropic/claude-opus-5"
MAX_PROVE_RIFERIMENTO = 8

FORMATO_ESERCIZI = r"""FORMATO DEL FILE

Ogni esercizio inizia con una riga "### ESERCIZIO", seguita da alcune righe "campo: valore" (tutte facoltative):
  nome: etichetta breve dell'esercizio (es. "Limite notevole", "Rango di una matrice")
  argomento: argomento del corso (es. "Limiti", "Algebra lineare"); usa lo stesso nome per esercizi dello stesso argomento
  difficolta: 1, 2 o 3 (1 = facile, 3 = difficile); omettila se non è chiara
  aperta: sì se è una domanda aperta senza risposte a scelta multipla, altrimenti no
  obbligatorio: sì solo se il compito indica che l'esercizio va obbligatoriamente svolto per esteso, altrimenti no
  note: nota interna per il docente (non compare nel compito)

Poi una o più sezioni "### VARIANTE". Ogni variante contiene:
  - il testo dell'esercizio, anche su più righe;
  - subito dopo, le risposte a scelta multipla, una per riga: la riga della risposta corretta inizia con "+ " e quelle sbagliate con "- " (segno, spazio, risposta). Esattamente UNA risposta corretta e almeno una sbagliata; la corretta può essere scritta in qualunque posizione, perché l'ordine viene comunque rimescolato per ogni studente.
  Una domanda aperta non ha righe "+" né "-".
Se nel compito ci sono più versioni dello stesso esercizio (es. stessi esercizi con numeri diversi in compiti A/B/C), mettile come varianti dello stesso esercizio; altrimenti ogni esercizio ha una sola variante.

Infine, facoltativa, una sezione "### SOLUZIONE" con la soluzione o un suggerimento (anche su più righe), che compare solo nel foglio di riferimento del docente.

REGOLE
- Scrivi formule e simboli in LaTeX, così come andrebbero in un documento LaTeX: $...$ per le formule in linea, \[ ... \] per quelle in display, \frac, \sqrt, \begin{pmatrix} ecc. Le barre rovesciate vanno scritte una sola volta (non raddoppiarle).
- Non usare markdown nel testo (niente **grassetto**, niente elenchi con "- " o "* "): per gli elenchi usa \begin{enumerate} \item ... \end{enumerate}, per il grassetto \textbf{...}.
- Ogni risposta deve stare su una sola riga.
- Dopo le risposte di una variante non scrivere altro testo: la riga successiva non vuota deve essere un nuovo "### VARIANTE", "### SOLUZIONE" o "### ESERCIZIO".
- Non inserire numerazioni come "Esercizio 1." nel testo: la numerazione la aggiunge EsaMiX.

ESEMPIO

### ESERCIZIO
nome: Limite notevole
argomento: Limiti
difficolta: 1

### VARIANTE
Calcolare $\displaystyle\lim_{x \to 0} \frac{\sin(3x)}{x}$.
+ $3$
- $1$
- $0$
- $\frac{1}{3}$

### VARIANTE
Calcolare $\displaystyle\lim_{x \to 0} \frac{\sin(5x)}{x}$.
+ $5$
- $1$
- $0$
- $\frac{1}{5}$

### SOLUZIONE
Si scrive $\frac{\sin(kx)}{x} = k\,\frac{\sin(kx)}{kx}$ e si usa il limite notevole.

### ESERCIZIO
nome: Teorema di Rolle
argomento: Derivate
aperta: sì
obbligatorio: sì

### VARIANTE
Enunciare e dimostrare il teorema di Rolle."""


FORNITORI = app_config_service.FORNITORI_IA
NOMI_FORNITORI = {"anthropic": "Anthropic", "openrouter": "OpenRouter", "ollama": "Ollama"}
MODELLI_PREDEFINITI = {"anthropic": MODELLO, "openrouter": MODELLO_OPENROUTER}


def _completa(p: dict) -> Optional[dict]:
    """Una configurazione salvata pronta per l'uso (con modello e nome del fornitore),
    oppure None se è incompleta (chiave o server mancanti)."""
    f = p.get("fornitore")
    if f in ("anthropic", "openrouter"):
        if not p.get("chiave"):
            return None
        modello = (p.get("modello") or "").strip() or MODELLI_PREDEFINITI[f]
    elif f == "ollama":
        if not p.get("url") or not p.get("modello"):
            return None
        modello = p["modello"]
    else:
        return None
    return {
        "id": p["id"], "nome": p.get("nome") or NOMI_FORNITORI[f], "fornitore": f,
        "nome_fornitore": NOMI_FORNITORI[f], "modello": modello, "chiave": p.get("chiave", ""),
        "url": (p.get("url") or "").rstrip("/"), "token": p.get("token", ""),
        "finale_chiave": (p.get("chiave") or "")[-4:], "da_ambiente": p.get("da_ambiente", False),
    }


def profili() -> list[dict]:
    """Tutte le configurazioni utilizzabili, nell'ordine in cui sono state salvate, più
    quelle date dalle variabili d'ambiente ANTHROPIC_API_KEY / OPENROUTER_API_KEY."""
    imp = app_config_service.get_settings()
    elenco = [c for c in (_completa(p) for p in imp.ia_profili) if c]
    for f, var in (("anthropic", "ANTHROPIC_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")):
        if os.environ.get(var):
            elenco.append(_completa({"id": f"env-{f}", "nome": f"{NOMI_FORNITORI[f]} (variabile {var})",
                                     "fornitore": f, "chiave": os.environ[var], "da_ambiente": True}))
    return elenco


def fornitore(profilo_id: str = "") -> Optional[dict]:
    """La configurazione da usare: quella richiesta (scelta nel pannello), altrimenti la
    predefinita delle Impostazioni, altrimenti la prima utilizzabile. None se non c'è
    nulla: resta comunque disponibile il prompt da copiare in claude.ai."""
    elenco = profili()
    if not elenco:
        return None
    predefinito = app_config_service.get_settings().ia_profilo_predefinito
    for cercato in (profilo_id, predefinito):
        trovato = next((c for c in elenco if cercato and c["id"] == cercato), None)
        if trovato:
            return {**trovato, "id_fornitore": trovato["fornitore"]}
    return {**elenco[0], "id_fornitore": elenco[0]["fornitore"]}


def modelli_ollama(url: str, token: str = "") -> list[str]:
    """I modelli installati su un server Ollama (GET /api/tags), per scegliere quale
    usare e per verificare che il server risponda. ValueError se non è raggiungibile."""
    import json
    import urllib.error
    import urllib.request

    richiesta = urllib.request.Request(url.rstrip("/") + "/api/tags")
    if token:
        richiesta.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(richiesta, timeout=10) as r:
            dati = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"Il server Ollama ha risposto con un errore ({e.code})")
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"Server Ollama non raggiungibile: {getattr(e, 'reason', e)}")
    except json.JSONDecodeError:
        raise ValueError("L'indirizzo non sembra un server Ollama (risposta non valida)")
    return sorted(m.get("name", "") for m in dati.get("models", []) if m.get("name"))


def _anno_iniziale(corso) -> int:
    m = re.search(r"\d{4}", corso.anno or "")
    return int(m.group()) if m else 0


def _data_ordinabile(appello) -> datetime:
    try:
        return datetime.strptime(appello.data or "", "%d/%m/%Y")
    except ValueError:
        return datetime.min


def riferimenti_precedenti(tag: str, appello, max_prove: int = MAX_PROVE_RIFERIMENTO) -> dict:
    """Le prove da usare come modello, dalla più recente, al massimo MAX_PROVE_RIFERIMENTO
    e solo quelle con almeno un esercizio assegnato. Ritorna {"tipo": descrizione del
    criterio usato, "prove": [{"corso", "appello", "esercizi"}]}."""
    corso = corsi_service.get_corso(tag)
    corsi = [corso] + corsi_service.corsi_simili(corso, corsi_service.list_corsi())

    indice_prova = None
    if appello.membro_raggruppamento:
        ragg = corsi_service.get_raggruppamento_by_membro(tag, appello.id)
        indice_prova = next(i for i, m in enumerate(ragg.membri) if m.id == appello.id)

    candidati = []
    for c in corsi:
        if indice_prova is not None:
            for r in corsi_service.list_raggruppamenti(c.tag):
                if len(r.membri) > indice_prova:
                    candidati.append((c, r.membri[indice_prova]))
        else:
            for a in corsi_service.list_appelli(c.tag, includi_raggruppamenti=False):
                if not a.membro_raggruppamento:
                    candidati.append((c, a))
    candidati = [(c, a) for c, a in candidati if not (c.tag == tag and a.id == appello.id)]
    candidati.sort(key=lambda ca: (_anno_iniziale(ca[0]), _data_ordinabile(ca[1])), reverse=True)

    prove = []
    for c, a in candidati:
        esercizi = esercizi_service.list_esercizi_appello(c.tag, a.id)
        if esercizi:
            prove.append({"corso": c, "appello": a, "esercizi": esercizi})
        if len(prove) >= max_prove:
            break
    if indice_prova is not None:
        tipo = f"la prova n. {indice_prova + 1} dei raggruppamenti di prove (es. prove parziali) degli anni precedenti"
    else:
        tipo = "gli appelli d'esame (non le prove parziali) degli anni precedenti"
    return {"tipo": tipo, "prove": prove}


def _esercizio_come_testo(e) -> str:
    """Un esercizio di riferimento nello stesso formato in cui deve rispondere Claude
    (prima variante soltanto: le altre sono di norma la stessa domanda con numeri diversi)."""
    righe = ["### ESERCIZIO"]
    if e.nome:
        righe.append(f"nome: {e.nome}")
    if e.argomento:
        righe.append(f"argomento: {e.argomento}")
    if e.difficolta:
        righe.append(f"difficolta: {e.difficolta}")
    if e.aperta:
        righe.append("aperta: sì")
    if getattr(e, "obbligatorio", False):
        righe.append("obbligatorio: sì")
    if e.varianti:
        v = e.varianti[0]
        righe += ["", "### VARIANTE", v.testo.strip()]
        if not e.aperta and v.risposte:
            righe.append(f"+ {v.risposte[0]}")
            righe += [f"- {r}" for r in v.risposte[1:]]
    if e.soluzione:
        righe += ["", "### SOLUZIONE", e.soluzione.strip()]
    return "\n".join(righe)


def costruisci_prompt(
    tag: str, appello, n_esercizi: int, n_varianti: int, istruzioni: str = "",
    max_prove: int = MAX_PROVE_RIFERIMENTO,
) -> dict:
    """Il prompt completo (sistema + richiesta) e i dati usati per costruirlo."""
    corso = corsi_service.get_corso(tag)
    rif = riferimenti_precedenti(tag, appello, max_prove)
    gia_assegnati = esercizi_service.list_esercizi_appello(tag, appello.id)

    sistema = (
        "Sei un docente universitario esperto che prepara il testo di un compito d'esame a risposta "
        "multipla per il proprio corso. Scrivi in italiano. Gli esercizi devono essere corretti dal punto "
        "di vista matematico: per ogni domanda a scelta multipla calcola con cura la risposta corretta e "
        "verifica che sia l'unica corretta tra le opzioni proposte; le risposte sbagliate devono essere "
        "verosimili, tipicamente gli errori più comuni degli studenti.\n\n"
        "Rispondi SOLO con il contenuto del file da importare, dentro un unico blocco di codice, senza "
        "commenti prima o dopo, rispettando esattamente questo formato.\n\n" + FORMATO_ESERCIZI
    )

    parti = [
        f"Corso: {corso.nome}" + (f" ({corso.facolta})" if corso.facolta else "")
        + (f", anno accademico {corso.anno}" if corso.anno else ""),
        f"Prova da preparare: {appello.nome}" + (f" del {appello.data}" if appello.data else ""),
        "",
        f"Prepara {n_esercizi} esercizi nuovi per questa prova, ciascuno con {n_varianti} "
        + ("varianti (stessa domanda con dati diversi, di difficoltà equivalente)." if n_varianti > 1 else "variante."),
        "Segui gli stessi argomenti, lo stesso livello di difficoltà e lo stesso stile degli esercizi "
        f"delle prove precedenti riportate qui sotto ({rif['tipo']}): in proporzione, gli argomenti "
        "che compaiono più spesso devono avere più esercizi. Non copiare gli esercizi precedenti: "
        "cambia dati, funzioni, matrici, contesto, in modo che siano esercizi davvero nuovi.",
        f"Punteggi del corso: risposta corretta {corso.risposta_corretta}, sbagliata "
        f"{corso.risposta_sbagliata}, non data {corso.risposta_vuota}.",
    ]
    if not corso.domande_aperte_attive:
        parti.append("Questo corso NON usa domande aperte: tutti gli esercizi devono essere a scelta multipla.")
    if corso.consegna:
        parti.append(
            f"Nel compito {corso.consegna} esercizi sono obbligatori (vanno svolti per esteso): "
            "segnane altrettanti con \"obbligatorio: sì\", scegliendo quelli più adatti a uno svolgimento scritto."
        )
    parti.append("Includi per ogni esercizio una breve sezione ### SOLUZIONE con i passaggi principali.")
    if istruzioni.strip():
        parti += ["", "Indicazioni aggiuntive del docente:", istruzioni.strip()]
    if gia_assegnati:
        parti += [
            "",
            "Esercizi GIÀ presenti in questa prova (non ripeterli, gli esercizi nuovi si aggiungono a questi):",
            "\n\n".join(_esercizio_come_testo(e) for e in gia_assegnati),
        ]
    if rif["prove"]:
        parti += ["", "PROVE PRECEDENTI DA PRENDERE A MODELLO"]
        for p in rif["prove"]:
            c, a = p["corso"], p["appello"]
            parti += [
                "",
                f"=== {a.nome}" + (f" del {a.data}" if a.data else "") + f" — {c.nome} {c.anno or ''}".rstrip() + " ===",
                "\n\n".join(_esercizio_come_testo(e) for e in p["esercizi"]),
            ]
    else:
        parti += [
            "",
            "Non ci sono prove precedenti con esercizi da prendere a modello: basati sul nome del corso "
            "e sulle indicazioni del docente, con argomenti e difficoltà tipici di un esame di questo corso.",
        ]
    return {"sistema": sistema, "richiesta": "\n".join(parti), "riferimenti": rif}


_BLOCCO_CODICE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def _chiama(conf: dict, sistema: str, messaggi: list, max_tokens: int = 64000):
    """Una richiesta al modello della configurazione `conf`, in streaming (le risposte
    lunghe richiedono minuti), con gli errori dell'API tradotti in ValueError leggibili.
    Ritorna il messaggio finale."""
    import anthropic  # importato qui: l'app funziona anche senza il pacchetto installato

    nome = conf["nome"]
    comuni = {"max_tokens": max_tokens, "system": sistema, "messages": messaggi}
    try:
        if conf["fornitore"] == "ollama":
            # Ollama accetta la stessa API Messages; la chiave è obbligatoria per l'SDK ma
            # ignorata da Ollama, a meno che il server stia dietro un proxy con token
            if conf["token"]:
                client = anthropic.Anthropic(base_url=conf["url"], auth_token=conf["token"], timeout=1800)
            else:
                client = anthropic.Anthropic(base_url=conf["url"], api_key="ollama", timeout=1800)
            stream = client.messages.stream(model=conf["modello"], **{**comuni, "max_tokens": min(max_tokens, 32000)})
        elif conf["fornitore"] == "openrouter":
            # richiesta essenziale: su Claude Opus 5 il ragionamento adattivo e lo sforzo
            # "high" sono già i valori predefiniti, e le funzioni beta di Anthropic (come i
            # fallback lato server) non passano da OpenRouter
            client = anthropic.Anthropic(base_url=OPENROUTER_BASE_URL, auth_token=conf["chiave"])
            stream = client.messages.stream(model=conf["modello"], **comuni)
        else:
            client = anthropic.Anthropic(api_key=conf["chiave"])
            stream = client.beta.messages.stream(
                model=conf["modello"], thinking={"type": "adaptive"}, output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **comuni,
            )
        with stream as s:
            risposta = s.get_final_message()
    except anthropic.AuthenticationError:
        raise ValueError(f"Chiave API di \"{nome}\" non valida: controllala in Impostazioni")
    except anthropic.PermissionDeniedError:
        raise ValueError(f"La chiave di \"{nome}\" non ha accesso al modello \"{conf['modello']}\" (o il credito è esaurito)")
    except anthropic.RateLimitError:
        raise ValueError(f"Troppe richieste a {conf['nome_fornitore']} in poco tempo: riprova tra qualche minuto")
    except anthropic.NotFoundError:
        raise ValueError(f"Modello \"{conf['modello']}\" non trovato su {conf['nome_fornitore']}: controllalo in Impostazioni")
    except anthropic.APIStatusError as e:
        raise ValueError(f"Errore dell'API di {conf['nome_fornitore']} ({e.status_code}): {e.message}")
    except anthropic.APITimeoutError:
        raise ValueError(f"{conf['nome_fornitore']} non ha risposto in tempo: riprova con meno esercizi")
    except anthropic.APIConnectionError:
        raise ValueError(f"Impossibile contattare {conf['nome_fornitore']}: controlla la connessione (e l'indirizzo del server)")
    if risposta.stop_reason == "refusal":
        raise ValueError("Il modello ha rifiutato la richiesta: prova a riformularla")
    if risposta.stop_reason == "max_tokens":
        raise ValueError("Risposta troppo lunga e interrotta: prova con meno esercizi alla volta")
    return risposta


def _testo(risposta) -> str:
    return "".join(b.text for b in risposta.content if b.type == "text")


def _estrai_file(testo: str) -> str:
    m = _BLOCCO_CODICE.search(testo)
    return (m.group(1) if m else testo).strip()


def genera(tag: str, appello, n_esercizi: int, n_varianti: int, istruzioni: str = "", profilo_id: str = "") -> dict:
    """Chiama il modello e ritorna {"esercizi": [...]} (la stessa struttura del file JSON
    di esportazione, pronta per anteprima_importa_json). Se la risposta non rispetta il
    formato, chiede una sola volta al modello di correggerla indicando l'errore."""
    conf = fornitore(profilo_id)
    if not conf:
        raise ValueError("Nessuna configurazione IA (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    # un modello locale ha di solito un contesto più piccolo: meno prove di riferimento
    max_prove = 3 if conf["fornitore"] == "ollama" else MAX_PROVE_RIFERIMENTO
    prompt = costruisci_prompt(tag, appello, n_esercizi, n_varianti, istruzioni, max_prove=max_prove)
    messaggi = [{"role": "user", "content": prompt["richiesta"]}]
    for tentativo in range(2):
        risposta = _chiama(conf, prompt["sistema"], messaggi)
        try:
            return esercizi_service.parse_testo_esercizi(_estrai_file(_testo(risposta)))
        except ValueError as e:
            if tentativo == 1:
                raise ValueError(f"La risposta del modello non rispetta il formato di import: {e}")
            messaggi += [
                {"role": "assistant", "content": risposta.content},
                {"role": "user", "content": (
                    f"Il file non rispetta il formato richiesto: {e}. "
                    "Correggilo e rispondi di nuovo con il file completo, nello stesso formato."
                )},
            ]
    raise AssertionError("non raggiungibile")


# --- Verifica delle soluzioni: il modello ricontrolla ogni esercizio assegnato (tutte le
# varianti, la risposta segnata come corretta e la soluzione) e segnala gli errori. Come
# per la generazione, vengono inviati solo i testi degli esercizi. ------------------------

ESITI_VERIFICA = ("ok", "dubbio", "errore")


def _esercizio_da_verificare(numero: int, e) -> str:
    righe = [f"### ESERCIZIO {numero}" + (f" — {e.nome}" if e.nome else "")]
    if e.aperta:
        righe.append("(domanda aperta: nessuna risposta a scelta multipla)")
    for k, v in enumerate(e.varianti, start=1):
        righe += ["", f"--- VARIANTE {k}", v.testo.strip()]
        if not e.aperta and v.risposte:
            righe.append(f"Risposta indicata come corretta: {v.risposte[0]}")
            righe += [f"Altra opzione: {r}" for r in v.risposte[1:]]
    if e.soluzione:
        righe += ["", "--- SOLUZIONE / SUGGERIMENTO", e.soluzione.strip()]
    return "\n".join(righe)


def costruisci_prompt_verifica(tag: str, appello) -> dict:
    corso = corsi_service.get_corso(tag)
    esercizi = esercizi_service.list_esercizi_appello(tag, appello.id)
    if not esercizi:
        raise ValueError("Nessun esercizio assegnato a questo appello da verificare")
    sistema = (
        "Sei un docente universitario esperto che ricontrolla, prima della stampa, il testo di un compito "
        "d'esame a risposta multipla scritto da un collega. Per ogni esercizio e per ogni variante: risolvi "
        "l'esercizio per conto tuo, con cura, e confronta il risultato con la risposta indicata come "
        "corretta; controlla che sia l'unica corretta tra le opzioni proposte; segnala testi ambigui, dati "
        "mancanti o contraddittori e formule LaTeX scritte male. Controlla anche la soluzione o il "
        "suggerimento, se presente. Non segnalare questioni di stile o preferenze personali: solo errori o "
        "dubbi concreti. Scrivi in italiano.\n\n"
        "Rispondi SOLO con un oggetto JSON dentro un unico blocco di codice, senza testo prima o dopo, con "
        "questa struttura:\n"
        "{\"esercizi\": [{\"numero\": 1, \"esito\": \"ok\" | \"dubbio\" | \"errore\", \"commento\": \"...\", "
        "\"varianti\": [{\"numero\": 1, \"esito\": \"ok\" | \"dubbio\" | \"errore\", "
        "\"risposta_calcolata\": \"...\", \"commento\": \"...\"}], "
        "\"soluzione\": {\"esito\": \"ok\" | \"dubbio\" | \"errore\" | \"assente\", \"commento\": \"...\"}}]}\n"
        "Un elemento per ogni esercizio e, al suo interno, uno per ogni variante, nello stesso ordine. "
        "\"risposta_calcolata\" è il risultato che ottieni tu (in LaTeX, con $...$); \"commento\" spiega in "
        "breve l'errore o il dubbio, ed è vuoto se l'esito è ok. Le barre rovesciate del LaTeX nel JSON vanno "
        "raddoppiate, come richiede il formato JSON."
    )
    richiesta = (
        f"Corso: {corso.nome}" + (f" ({corso.facolta})" if corso.facolta else "")
        + f"\nProva: {appello.nome}" + (f" del {appello.data}" if appello.data else "")
        + f"\n\nCi sono {len(esercizi)} esercizi da controllare:\n\n"
        + "\n\n".join(_esercizio_da_verificare(i, e) for i, e in enumerate(esercizi, start=1))
    )
    return {"sistema": sistema, "richiesta": richiesta, "esercizi": esercizi}


def _esito(valore, ammessi=ESITI_VERIFICA) -> str:
    v = str(valore or "").strip().lower()
    return v if v in ammessi else "dubbio"


def _leggi_verifica(testo: str, esercizi: list) -> list[dict]:
    """Dal JSON del modello a un elenco allineato agli esercizi: ValueError se il JSON non
    è leggibile o non ha la struttura richiesta (il chiamante chiede allora di correggerlo)."""
    import json
    try:
        dati = json.loads(_estrai_file(testo))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON non valido ({e})")
    voci = dati.get("esercizi") if isinstance(dati, dict) else None
    if not isinstance(voci, list):
        raise ValueError("manca l'elenco \"esercizi\"")
    per_numero = {v.get("numero"): v for v in voci if isinstance(v, dict)}
    risultato = []
    for i, e in enumerate(esercizi, start=1):
        voce = per_numero.get(i) or (voci[i - 1] if i - 1 < len(voci) and isinstance(voci[i - 1], dict) else {})
        varianti_v = {v.get("numero"): v for v in voce.get("varianti") or [] if isinstance(v, dict)}
        varianti = []
        for k, var in enumerate(e.varianti, start=1):
            vv = varianti_v.get(k, {})
            varianti.append({
                "numero": k, "testo": var.testo, "indicata": var.risposte[0] if var.risposte else "",
                "esito": _esito(vv.get("esito")) if vv else "dubbio",
                "risposta_calcolata": str(vv.get("risposta_calcolata") or ""),
                "commento": str(vv.get("commento") or ("" if vv else "variante non controllata dal modello")),
            })
        sol = voce.get("soluzione") if isinstance(voce.get("soluzione"), dict) else {}
        esito_sol = _esito(sol.get("esito"), ESITI_VERIFICA + ("assente",)) if e.soluzione else "assente"
        esiti = [v["esito"] for v in varianti] + [_esito(voce.get("esito")) if voce else "dubbio"]
        if esito_sol in ESITI_VERIFICA:
            esiti.append(esito_sol)
        complessivo = "errore" if "errore" in esiti else ("dubbio" if "dubbio" in esiti else "ok")
        risultato.append({
            "numero": i, "esercizio": e, "esito": complessivo, "commento": str(voce.get("commento") or ""),
            "varianti": varianti, "soluzione": {"esito": esito_sol, "commento": str(sol.get("commento") or "")},
        })
    return risultato


def verifica(tag: str, appello, profilo_id: str = "") -> dict:
    """Fa ricontrollare al modello tutti gli esercizi assegnati all'appello. Ritorna
    {"esercizi": [...], "conf": configurazione usata}: per ogni esercizio l'esito
    complessivo (ok / dubbio / errore), quello di ogni variante con la risposta calcolata
    dal modello, e quello della soluzione."""
    conf = fornitore(profilo_id)
    if not conf:
        raise ValueError("Nessuna configurazione IA (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    prompt = costruisci_prompt_verifica(tag, appello)
    messaggi = [{"role": "user", "content": prompt["richiesta"]}]
    for tentativo in range(2):
        risposta = _chiama(conf, prompt["sistema"], messaggi)
        try:
            return {"esercizi": _leggi_verifica(_testo(risposta), prompt["esercizi"]), "conf": conf}
        except ValueError as e:
            if tentativo == 1:
                raise ValueError(f"La risposta del modello non è nel formato richiesto: {e}")
            messaggi += [
                {"role": "assistant", "content": risposta.content},
                {"role": "user", "content": f"La risposta non è nel formato richiesto: {e}. Rispondi di nuovo con il solo JSON completo."},
            ]
    raise AssertionError("non raggiungibile")
