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


FORNITORI = ("anthropic", "openrouter", "ollama")
NOMI_FORNITORI = {"anthropic": "Anthropic", "openrouter": "OpenRouter", "ollama": "Ollama"}


def _configurazione(chiave_fornitore: str, imp) -> Optional[dict]:
    """La configurazione di un fornitore, se completa; None altrimenti."""
    if chiave_fornitore == "anthropic":
        chiave = imp.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        return {"chiave": chiave, "modello": MODELLO} if chiave else None
    if chiave_fornitore == "openrouter":
        chiave = imp.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        return {"chiave": chiave, "modello": MODELLO_OPENROUTER} if chiave else None
    if chiave_fornitore == "ollama":
        if imp.ollama_url and imp.ollama_modello:
            return {"url": imp.ollama_url.rstrip("/"), "modello": imp.ollama_modello, "token": imp.ollama_token}
    return None


def fornitore() -> Optional[dict]:
    """Con chi generare: Anthropic direttamente, tramite OpenRouter, oppure un modello
    Ollama su un proprio server. Vale quello scelto nelle Impostazioni dell'app, se
    configurato; altrimenti il primo configurato nell'ordine (chiavi anche da variabili
    d'ambiente ANTHROPIC_API_KEY / OPENROUTER_API_KEY). None se non c'è nulla: resta
    comunque disponibile il prompt da copiare in claude.ai."""
    imp = app_config_service.get_settings()
    ordine = ([imp.ia_fornitore] if imp.ia_fornitore in FORNITORI else []) + list(FORNITORI)
    for f in ordine:
        conf = _configurazione(f, imp)
        if conf:
            return {"id": f, "nome": NOMI_FORNITORI[f], **conf}
    return None


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


def _estrai_file(testo: str) -> str:
    m = _BLOCCO_CODICE.search(testo)
    return (m.group(1) if m else testo).strip()


def genera(tag: str, appello, n_esercizi: int, n_varianti: int, istruzioni: str = "") -> dict:
    """Chiama Claude e ritorna {"esercizi": [...]} (la stessa struttura del file JSON di
    esportazione, pronta per anteprima_importa_json). Se la risposta non rispetta il
    formato, chiede una sola volta a Claude di correggerla indicando l'errore."""
    import anthropic  # importato qui: l'app funziona anche senza il pacchetto installato

    conf = fornitore()
    if not conf:
        raise ValueError("Nessun fornitore di IA configurato (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    nome = conf["nome"]
    # un modello locale ha di solito un contesto più piccolo: meno prove di riferimento
    max_prove = 3 if conf["id"] == "ollama" else MAX_PROVE_RIFERIMENTO
    prompt = costruisci_prompt(tag, appello, n_esercizi, n_varianti, istruzioni, max_prove=max_prove)
    messaggi = [{"role": "user", "content": prompt["richiesta"]}]

    def chiama():
        comuni = {"max_tokens": 64000, "system": prompt["sistema"], "messages": messaggi}
        if conf["id"] == "ollama":
            # Ollama accetta la stessa API Messages; la chiave è obbligatoria per l'SDK ma
            # ignorata da Ollama, a meno che il server stia dietro un proxy con token
            if conf["token"]:
                client = anthropic.Anthropic(base_url=conf["url"], auth_token=conf["token"], timeout=1800)
            else:
                client = anthropic.Anthropic(base_url=conf["url"], api_key="ollama", timeout=1800)
            return client.messages.stream(model=conf["modello"], **{**comuni, "max_tokens": 32000})
        if conf["id"] == "openrouter":
            # richiesta essenziale: su Claude Opus 5 il ragionamento adattivo e lo sforzo
            # "high" sono già i valori predefiniti, e le funzioni beta di Anthropic (come i
            # fallback lato server) non passano da OpenRouter
            client = anthropic.Anthropic(base_url=OPENROUTER_BASE_URL, auth_token=conf["chiave"])
            return client.messages.stream(model=conf["modello"], **comuni)
        client = anthropic.Anthropic(api_key=conf["chiave"])
        return client.beta.messages.stream(
            model=conf["modello"], thinking={"type": "adaptive"}, output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **comuni,
        )

    for tentativo in range(2):
        try:
            with chiama() as stream:
                risposta = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise ValueError(f"Chiave API di {nome} non valida: controllala in Impostazioni")
        except anthropic.PermissionDeniedError:
            raise ValueError(f"La chiave API di {nome} non ha accesso al modello \"{conf['modello']}\" (o il credito è esaurito)")
        except anthropic.RateLimitError:
            raise ValueError(f"Troppe richieste a {nome} in poco tempo: riprova tra qualche minuto")
        except anthropic.NotFoundError:
            raise ValueError(f"Modello \"{conf['modello']}\" non trovato su {nome}: controllalo in Impostazioni")
        except anthropic.APIStatusError as e:
            raise ValueError(f"Errore dell'API di {nome} ({e.status_code}): {e.message}")
        except anthropic.APITimeoutError:
            raise ValueError(f"{nome} non ha risposto in tempo: riprova, magari chiedendo meno esercizi")
        except anthropic.APIConnectionError:
            raise ValueError(f"Impossibile contattare {nome}: controlla la connessione (e l'indirizzo del server)")

        if risposta.stop_reason == "refusal":
            raise ValueError("Claude ha rifiutato la richiesta: prova a riformulare le indicazioni aggiuntive")
        if risposta.stop_reason == "max_tokens":
            raise ValueError("Risposta troppo lunga e interrotta: chiedi meno esercizi o meno varianti alla volta")
        testo = "".join(b.text for b in risposta.content if b.type == "text")
        try:
            return esercizi_service.parse_testo_esercizi(_estrai_file(testo))
        except ValueError as e:
            if tentativo == 1:
                raise ValueError(f"La risposta di Claude non rispetta il formato di import: {e}")
            messaggi += [
                {"role": "assistant", "content": risposta.content},
                {"role": "user", "content": (
                    f"Il file non rispetta il formato richiesto: {e}. "
                    "Correggilo e rispondi di nuovo con il file completo, nello stesso formato."
                )},
            ]
    raise AssertionError("non raggiungibile")
