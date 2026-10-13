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


def costruisci_prompt_verifica(tag: str, esercizi: list, contesto: str = "") -> dict:
    """Prompt di verifica per un elenco qualunque di esercizi (di un appello, della banca
    del corso o uno solo); `contesto` è una riga descrittiva (es. "Prova: Primo parziale")."""
    corso = corsi_service.get_corso(tag)
    if not esercizi:
        raise ValueError("Nessun esercizio da verificare")
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
        + (f"\n{contesto}" if contesto else "")
        + f"\n\nCi sono {len(esercizi)} esercizi da controllare:\n\n"
        + "\n\n".join(_esercizio_da_verificare(i, e) for i, e in enumerate(esercizi, start=1))
    )
    return {"sistema": sistema, "richiesta": richiesta, "esercizi": esercizi}


def _esito(valore, ammessi=ESITI_VERIFICA) -> str:
    v = str(valore or "").strip().lower()
    return v if v in ammessi else "dubbio"


# Escape JSON che, scritti da un modello senza raddoppiare le barre, sono in realtà comandi
# LaTeX: \frac diventa "salto pagina" + "rac", \theta "tab" + "heta", \neq "a capo" + "eq"...
_CONTROLLO_A_LATEX = {"\f": "\\f", "\b": "\\b", "\t": "\\t", "\r": "\\r"}
_COMANDI_CON_N = re.compile(r"\n(?=(?:eq|u|abla|ot|otin|i|ewline|orm|eg|leq|geq|parallel|exists|mid)\b)")
_ESCAPE_NON_VALIDO = re.compile(r'(?<!\\)((?:\\\\)*)\\(?=[^"\\/bfnrtu])')


def _ripara_stringhe(valore):
    if isinstance(valore, str):
        for c, sostituto in _CONTROLLO_A_LATEX.items():
            valore = valore.replace(c, sostituto)
        return _COMANDI_CON_N.sub(r"\\n", valore)
    if isinstance(valore, list):
        return [_ripara_stringhe(v) for v in valore]
    if isinstance(valore, dict):
        return {k: _ripara_stringhe(v) for k, v in valore.items()}
    return valore


def _json_tollerante(testo: str):
    """Legge il JSON della verifica anche quando il LaTeX non ha le barre raddoppiate
    (capita spesso con le risposte copiate da claude.ai): prima raddoppia le barre che
    non formano un escape JSON valido (\\det, \\sqrt, \\lambda...), poi ricostruisce i
    comandi LaTeX che il JSON ha interpretato come caratteri di controllo (\\frac...)."""
    import json
    try:
        dati = json.loads(_ESCAPE_NON_VALIDO.sub(r"\1\\\\", testo))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON non valido ({e})")
    return _ripara_stringhe(dati)


def _leggi_verifica(testo: str, esercizi: list) -> list[dict]:
    """Dal JSON del modello a un elenco allineato agli esercizi: ValueError se il JSON non
    è leggibile o non ha la struttura richiesta (il chiamante chiede allora di correggerlo)."""
    dati = _json_tollerante(_estrai_file(testo))
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


def risultato_serializzabile(r: dict) -> dict:
    """Il risultato della verifica di un esercizio senza l'oggetto Esercizio, da salvare
    nel database insieme allo stato di verifica."""
    return {k: v for k, v in r.items() if k != "esercizio"}


def leggi_risposta_verifica(esercizi: list, testo: str) -> list[dict]:
    """La risposta della verifica ottenuta copiando il prompt in claude.ai (o altrove) e
    incollata nell'app: stesso formato e stesso risultato della verifica diretta."""
    try:
        return _leggi_verifica(testo, esercizi)
    except ValueError as e:
        raise ValueError(
            f"La risposta incollata non è nel formato atteso: {e}. Copia l'intera risposta di Claude "
            "(il blocco con il JSON); se gli esercizi sono cambiati nel frattempo, ripeti con un prompt nuovo."
        ) from None


def verifica(tag: str, esercizi: list, contesto: str = "", profilo_id: str = "") -> dict:
    """Fa ricontrollare al modello gli esercizi indicati. Ritorna {"esercizi": [...],
    "conf": configurazione usata}: per ogni esercizio l'esito complessivo (ok / dubbio /
    errore), quello di ogni variante con la risposta calcolata dal modello, e quello della
    soluzione."""
    conf = fornitore(profilo_id)
    if not conf:
        raise ValueError("Nessuna configurazione IA (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    prompt = costruisci_prompt_verifica(tag, esercizi, contesto)
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


# --- Correzione di un esercizio a partire dalla sua verifica: il modello riscrive
# l'esercizio nel formato di import correggendo solo ciò che la verifica ha segnalato; il
# docente vede prima e dopo e decide se accettare. ------------------------------------------

def _esercizio_completo_come_testo(e) -> str:
    """L'esercizio con TUTTE le varianti, nel formato di import (la corretta con "+")."""
    righe = ["### ESERCIZIO"]
    for campo, valore in (("nome", e.nome), ("argomento", e.argomento), ("difficolta", e.difficolta), ("note", e.note)):
        if valore:
            righe.append(f"{campo}: {valore}")
    if e.aperta:
        righe.append("aperta: sì")
    for v in e.varianti:
        righe += ["", "### VARIANTE", v.testo.strip()]
        if not e.aperta and v.risposte:
            righe.append(f"+ {v.risposte[0]}")
            righe += [f"- {r}" for r in v.risposte[1:]]
    if e.soluzione:
        righe += ["", "### SOLUZIONE", e.soluzione.strip()]
    return "\n".join(righe)


_PEZZI = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\[.*?\\\]|\s+|[^\s$]+", re.DOTALL)


def _evidenzia(prima: str, dopo: str) -> tuple[str, str]:
    """Le due versioni di un testo in HTML, con le parti tolte (in `prima`) e aggiunte (in
    `dopo`) evidenziate. Il confronto è per parole, ma ogni formula ($...$, $$...$$,
    \\[...\\]) è un pezzo unico, così l'evidenziazione non la spezza e resta visualizzabile."""
    from difflib import SequenceMatcher
    from html import escape
    a, b = _PEZZI.findall(prima or ""), _PEZZI.findall(dopo or "")
    out_a, out_b = [], []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        pa, pb = escape("".join(a[i1:i2])), escape("".join(b[j1:j2]))
        if op == "equal":
            out_a.append(pa); out_b.append(pb)
        else:
            if pa.strip():
                out_a.append(f'<del class="diff-tolto">{pa}</del>')
            else:
                out_a.append(pa)
            if pb.strip():
                out_b.append(f'<ins class="diff-nuovo">{pb}</ins>')
            else:
                out_b.append(pb)
    return "".join(out_a), "".join(out_b)


def _risposte_segnate(queste: list, altre: list) -> list[tuple[str, bool]]:
    """(risposta in HTML, cambiata?) — la prima, la corretta, è cambiata se è diversa
    dalla corretta dell'altra versione; una sbagliata, se non è tra le sbagliate dell'altra."""
    from html import escape
    return [(escape(r), (r != (altre[0] if altre else None)) if j == 0 else (r not in altre[1:]))
            for j, r in enumerate(queste)]


def confronto_correzione(esercizio, proposta: dict) -> dict:
    """Prima e dopo pronti da mostrare affiancati, con le differenze evidenziate: per ogni
    variante il testo e le risposte (segnate se cambiate), e la soluzione."""
    from html import escape
    varianti = []
    vecchie, nuove = list(esercizio.varianti), list(proposta.get("varianti") or [])
    for k in range(max(len(vecchie), len(nuove))):
        v = vecchie[k] if k < len(vecchie) else None
        n = nuove[k] if k < len(nuove) else None
        testo_a, testo_b = _evidenzia(v.testo if v else "", (n or {}).get("testo", ""))
        r_a, r_b = list(v.risposte) if v else [], list((n or {}).get("risposte") or [])
        varianti.append({
            "prima_testo": testo_a, "dopo_testo": testo_b,
            "prima_risposte": _risposte_segnate(r_a, r_b),
            "dopo_risposte": _risposte_segnate(r_b, r_a),
            "assente_prima": v is None, "assente_dopo": n is None,
        })
    sol_a, sol_b = _evidenzia(esercizio.soluzione or "", proposta.get("soluzione") or "")
    return {"varianti": varianti, "prima_soluzione": sol_a, "dopo_soluzione": sol_b,
            "aperta_prima": bool(esercizio.aperta), "aperta_dopo": bool(proposta.get("aperta"))}


def modifiche_proposte(esercizio, proposta: dict) -> list[str]:
    """Elenco leggibile delle differenze tra l'esercizio attuale e la correzione proposta,
    da mostrare prima della conferma (testo, risposta corretta, risposte sbagliate e
    soluzione di ogni variante)."""
    modifiche = []
    vecchie, nuove = esercizio.varianti, proposta.get("varianti") or []
    if len(vecchie) != len(nuove):
        modifiche.append(f"Numero di varianti: da {len(vecchie)} a {len(nuove)}")
    for k, (v, n) in enumerate(zip(vecchie, nuove), start=1):
        nome = f"Variante {k}" if max(len(vecchie), len(nuove)) > 1 else "Esercizio"
        if v.testo.strip() != (n.get("testo") or "").strip():
            modifiche.append(f"{nome}: testo modificato")
        r_vecchie, r_nuove = list(v.risposte), list(n.get("risposte") or [])
        if r_vecchie[:1] != r_nuove[:1]:
            modifiche.append(f"{nome}: risposta corretta da {r_vecchie[0] if r_vecchie else '—'} a {r_nuove[0] if r_nuove else '—'}")
        tolte = [r for r in r_vecchie[1:] if r not in r_nuove[1:]]
        aggiunte = [r for r in r_nuove[1:] if r not in r_vecchie[1:]]
        if tolte or aggiunte:
            modifiche.append(f"{nome}: risposte sbagliate" + (f" — tolte: {', '.join(tolte)}" if tolte else "")
                             + (f" — nuove: {', '.join(aggiunte)}" if aggiunte else ""))
    if (esercizio.soluzione or "").strip() != (proposta.get("soluzione") or "").strip():
        modifiche.append("Soluzione " + ("aggiunta" if not (esercizio.soluzione or "").strip() else "modificata"))
    if bool(esercizio.aperta) != bool(proposta.get("aperta")):
        modifiche.append("Tipo: " + ("diventa domanda aperta" if proposta.get("aperta") else "diventa a scelta multipla"))
    return modifiche


def proposta_come_testo(proposta: dict) -> str:
    """Una proposta di correzione (dict come quelli di parse_testo_esercizi) di nuovo nel
    formato di import, da modificare a mano prima di accettarla."""
    from types import SimpleNamespace
    return _esercizio_completo_come_testo(SimpleNamespace(
        nome=proposta.get("nome"), argomento=proposta.get("argomento"), difficolta=proposta.get("difficolta"),
        note=proposta.get("note"), aperta=bool(proposta.get("aperta")), soluzione=proposta.get("soluzione") or "",
        varianti=[SimpleNamespace(testo=v["testo"], risposte=list(v.get("risposte") or [])) for v in proposta["varianti"]],
    ))


def _problemi_come_testo(risultato: dict) -> str:
    righe = []
    if risultato.get("commento"):
        righe.append(f"- In generale: {risultato['commento']}")
    for v in risultato.get("varianti") or []:
        if v.get("esito") != "ok":
            righe.append(f"- Variante {v['numero']} ({v['esito']}): {v.get('commento') or ''}"
                         + (f" Risultato calcolato: {v['risposta_calcolata']}." if v.get("risposta_calcolata") else ""))
    sol = risultato.get("soluzione") or {}
    if sol.get("esito") in ("errore", "dubbio"):
        righe.append(f"- Soluzione ({sol['esito']}): {sol.get('commento') or ''}")
    return "\n".join(righe) or "- (nessun problema specifico indicato: ricontrolla comunque l'esercizio)"


def costruisci_prompt_correzione(esercizio, risultato: dict) -> dict:
    sistema = (
        "Sei un docente universitario esperto. Correggi un esercizio d'esame a risposta multipla in cui una "
        "verifica ha trovato errori o dubbi. Cambia SOLO ciò che serve a risolvere i problemi segnalati "
        "(dopo averli ricontrollati tu stesso): mantieni lo stesso numero di varianti, lo stesso stile, gli "
        "stessi dati quando sono corretti, e per ogni variante una sola risposta corretta e risposte "
        "sbagliate verosimili e tutte diverse tra loro. Se un problema segnalato in realtà non c'è, lascia "
        "quella parte invariata. Scrivi in italiano.\n\n"
        "Rispondi SOLO con l'esercizio corretto, dentro un unico blocco di codice, nel formato seguente.\n\n"
        + FORMATO_ESERCIZI
    )
    richiesta = (
        "ESERCIZIO ATTUALE:\n\n" + _esercizio_completo_come_testo(esercizio)
        + "\n\nPROBLEMI SEGNALATI DALLA VERIFICA:\n" + _problemi_come_testo(risultato)
    )
    return {"sistema": sistema, "richiesta": richiesta}


def leggi_correzione(testo: str) -> dict:
    """Dal testo nel formato di import all'esercizio corretto (dict come quelli di
    parse_testo_esercizi): ValueError se non c'è esattamente un esercizio valido."""
    esercizi = esercizi_service.parse_testo_esercizi(_estrai_file(testo))["esercizi"]
    if len(esercizi) != 1:
        raise ValueError(f"attesa la correzione di un solo esercizio, trovati {len(esercizi)}")
    esercizi_service._valida_varianti([dict(v) for v in esercizi[0]["varianti"]], esercizi[0]["aperta"])
    return esercizi[0]


def proponi_correzione(esercizio, risultato: dict, profilo_id: str = "") -> dict:
    """Chiede al modello la versione corretta dell'esercizio (non salva nulla)."""
    conf = fornitore(profilo_id)
    if not conf:
        raise ValueError("Nessuna configurazione IA (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    prompt = costruisci_prompt_correzione(esercizio, risultato)
    messaggi = [{"role": "user", "content": prompt["richiesta"]}]
    for tentativo in range(2):
        risposta = _chiama(conf, prompt["sistema"], messaggi, max_tokens=32000)
        try:
            return leggi_correzione(_testo(risposta))
        except ValueError as e:
            if tentativo == 1:
                raise ValueError(f"La correzione proposta non è valida: {e}")
            messaggi += [
                {"role": "assistant", "content": risposta.content},
                {"role": "user", "content": f"La risposta non è valida: {e}. Rispondi di nuovo con il solo esercizio corretto, nel formato richiesto."},
            ]
    raise AssertionError("non raggiungibile")
