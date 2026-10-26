import base64
import json
import math
import os
import re
import traceback
import webbrowser
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Form, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from quizesame import config
from quizesame.services import corsi as corsi_service
from quizesame.services import studenti as studenti_service
from quizesame.services import esercizi as esercizi_service
from quizesame.services import compiti as compiti_service
from quizesame.services import correzione as correzione_service
from quizesame.services import risultati as risultati_service
from quizesame.services import verbalizzazione as verbalizzazione_service
from quizesame.services import migrazione as migrazione_service
from quizesame.services import statistiche as statistiche_service
from quizesame.services import app_config as app_config_service
from quizesame.services import esportazione as esportazione_service
from quizesame.services import aggiornamenti as aggiornamenti_service
from quizesame.services import aule as aule_service
from quizesame.services import latex as latex_service
from quizesame.services import presenze as presenze_service
from quizesame.services import ia as ia_service
from quizesame.services import documenti as documenti_service
from quizesame.services import registro as registro_service
from quizesame.services import verifiche_ia as verifiche_ia_service
from quizesame.services import controllo_correzioni as controllo_service
from quizesame.services import corso_prova as corso_prova_service
from quizesame.services import testo_web

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _static_version() -> str:
    """Data di ultima modifica dei file statici (style.css, app.js), usata come parametro
    ?v= nei link: così il browser scarica sempre la versione aggiornata invece di tenersi
    in cache quella vecchia ogni volta che CSS o JS vengono modificati."""
    try:
        return str(max(int((PACKAGE_DIR / "static" / f).stat().st_mtime) for f in ("style.css", "app.js")))
    except OSError:
        return "0"


def _data_it_a_iso(data: str) -> str:
    """Converte una data 'gg/mm/aaaa' (formato in cui l'app salva le date) in 'aaaa-mm-gg'
    (formato richiesto da <input type="date"> per precompilare il selettore): stringa
    vuota se la data manca o non è nel formato atteso."""
    try:
        g, m, a = (data or "").strip().split("/")
        return f"{int(a):04d}-{int(m):02d}-{int(g):02d}"
    except ValueError:
        return ""


def _data_iso_a_it(data: str) -> str:
    """Converte una data 'aaaa-mm-gg' (formato inviato da <input type="date">) nel formato
    'gg/mm/aaaa' con cui l'app salva le date altrove (testo dei compiti, verbali, ecc.):
    stringa vuota se la data manca o non è nel formato atteso. Solleva ValueError se
    l'anno è fuori dall'intervallo 2001-2099 (tipicamente un errore di battitura o di
    scorrimento del selettore)."""
    dato = (data or "").strip()
    if not dato:
        return ""
    try:
        a, m, g = dato.split("-")
        anno, mese, giorno = int(a), int(m), int(g)
    except ValueError:
        return ""
    if not (2001 <= anno <= 2099):
        raise ValueError(f"Data non valida: l'anno deve essere compreso tra il 2001 e il 2099 (hai indicato {anno})")
    return f"{giorno:02d}/{mese:02d}/{anno:04d}"


templates.env.filters["data_iso"] = _data_it_a_iso
# Il codice Python viene caricato una volta sola all'avvio, mentre i template vengono
# riletti dal disco: dopo un aggiornamento senza riavvio le pagine nuove girano sul codice
# vecchio (pulsanti che non fanno nulla, campi ignorati). Se un file .py è più recente
# dell'avvio, ogni pagina mostra un avviso a riavviare.
_AVVIO = __import__("time").time()
_controllo_codice = {"quando": 0.0, "aggiornato": False}


def _codice_aggiornato() -> bool:
    import time
    adesso = time.time()
    if not _controllo_codice["aggiornato"] and adesso - _controllo_codice["quando"] > 15:
        _controllo_codice["quando"] = adesso
        cartella = Path(__file__).parent
        _controllo_codice["aggiornato"] = any(f.stat().st_mtime > _AVVIO for f in cartella.rglob("*.py"))
    return _controllo_codice["aggiornato"]


templates.env.globals["codice_aggiornato"] = _codice_aggiornato
# segnalazione degli esercizi con risposte ripetute in una variante (vedi esercizi_service)
templates.env.globals["problemi_risposte"] = esercizi_service.problemi_risposte
templates.env.globals["stati_verifica"] = esercizi_service.stati_verifica
templates.env.globals["ultima_verifica_ia"] = verifiche_ia_service.ultima
templates.env.filters["accenti_tex"] = testo_web.accenti
# inizio leggibile del testo di un esercizio (formule abbreviate in "…"), per gli elenchi
templates.env.filters["anteprima_testo"] = lambda t: statistiche_service._anteprima(t or "", 90)
# testo di un esercizio (LaTeX) in una pagina con KaTeX: accenti, grassetti, eqnarray
templates.env.filters["testo_web"] = testo_web.testo_html
# contrassegno nei link ai documenti, diverso a ogni apertura della pagina: il browser scarica
# sempre la versione attuale di un PDF rigenerato invece di mostrarne una copia conservata
templates.env.globals["versione_link"] = lambda: str(int(__import__("time").time()))
templates.env.filters["js"] = lambda value: json.dumps(str(value))
templates.env.globals["static_version"] = _static_version
templates.env.globals["get_app_settings"] = app_config_service.get_settings

@__import__("contextlib").asynccontextmanager
async def _ciclo_di_vita(_app):
    _registra_aggiornamento()  # definita più sotto: viene chiamata solo all'avvio
    # compila subito tutti i template: altrimenti la prima apertura di ogni pagina paga
    # la compilazione (fino a qualche decimo di secondo per le pagine più grandi)
    for nome in templates.env.list_templates(extensions=["html"]):
        try:
            templates.env.get_template(nome)
        except Exception:
            pass
    yield


app = FastAPI(title="EsaMiX", lifespan=_ciclo_di_vita)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # chiesta dal browser per le pagine senza <link rel="icon">, come i PDF aperti in una scheda
    return FileResponse(PACKAGE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


_CORSO_APPELLO = re.compile(r"^/corsi/([^/]+)(?:/appelli/(\d+))?(?:/esercizi/(\d+))?")


_DOCUMENTO = re.compile(r"\.(pdf|tex|md|csv|zip|json)$")


@app.middleware("http")
async def registra_azioni(request: Request, call_next):
    """Registro di tutte le azioni dell'utente: ogni modulo inviato (con il messaggio di
    esito mostrato al docente, quando c'è) e ogni documento scaricato o aperto (PDF, TeX,
    Markdown, CSV, backup), con corso, appello ed esercizio ricavati dall'indirizzo. Le
    semplici visite alle pagine e le richieste interne (file statici, ricerche) no."""
    risposta = await call_next(request)
    percorso = request.url.path
    if percorso.startswith(("/static", "/api/")) or percorso.endswith(("/modelli-ollama", "/form", "/anteprima")):
        return risposta
    # il corso di prova si installa e si elimina senza lasciare tracce nel registro
    if percorso.startswith("/impostazioni/corso-prova"):
        return risposta
    documento = request.method == "GET" and _DOCUMENTO.search(percorso)
    if request.method != "POST" and not documento:
        return risposta
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(risposta.headers.get("location", "")).query) if risposta.status_code in (302, 303) else {}
    m = _CORSO_APPELLO.match(percorso)
    if documento:
        azione, esito = f"Documento aperto ({documento.group(1).upper()})", percorso.rsplit("/", 1)[-1]
    else:
        azione = _etichetta_azione(percorso)
        esito = query["msg"][0] if "msg" in query else ("completata" if risposta.status_code < 400 else "non riuscita")
    gravita = (query.get("kind") or ["success" if risposta.status_code < 400 else "error"])[0]
    if documento:
        # i documenti si rigenerano con lo stesso nome: il browser non deve mai riusarne una copia vecchia
        risposta.headers["Cache-Control"] = "no-store"
    registro_service.registra(
        "azione", azione, esito=esito, tag=m.group(1) if m else "",
        appello_id=int(m.group(2)) if m and m.group(2) else None,
        esercizio_id=int(m.group(3)) if m and m.group(3) else None,
        gravita=gravita, percorso=percorso, metodo=request.method,
    )
    return risposta


_ETICHETTE_AZIONI = [
    ("ia/verifica", "Verifica con l'IA"), ("ia/genera", "Generazione esercizi con l'IA"),
    ("ia/correggi/conferma", "Conferma correzione con l'IA"), ("ia/correggi", "Correzione proposta dall'IA"),
    ("esercizi/ordine", "Riordino esercizi"), ("importa-json", "Import esercizi"),
    ("ricompila-pdf", "Rigenerazione PDF"), ("importa-da-altri-corsi", "Import da altri corsi"),
    ("esercizi/nuovo", "Nuovo esercizio"), ("/modifica", "Modifica"), ("/elimina", "Eliminazione"),
    ("/rimuovi", "Rimozione"),
    ("/verificato", "Esercizio segnato come verificato"), ("/obbligatorio", "Esercizio obbligatorio"),
    ("esercizi/assegna", "Assegnazione esercizi"), ("iscritti", "Iscritti"), ("segreteria-csv", "File della segreteria"),
    ("presenze", "Presenze"), ("correggi", "Correzione"), ("risultati", "Risultati"), ("verbalizz", "Verbalizzazione"),
    ("chiudi", "Chiusura"), ("riapri", "Riapertura"), ("impostazioni", "Impostazioni"), ("studenti", "Studenti"),
    ("orale", "Orale"), ("aule", "Aule"), ("blocchi", "Blocchi di compiti"), ("backup", "Backup"),
]


def _etichetta_azione(percorso: str) -> str:
    for chiave, etichetta in _ETICHETTE_AZIONI:
        if chiave in percorso:
            return etichetta
    ultimo = [p for p in percorso.split("/") if p and not p.isdigit()]
    return (ultimo[-1] if ultimo else percorso).replace("-", " ").capitalize()


def _registra_aggiornamento() -> None:
    """All'avvio: se versione o codice sono cambiati dall'avvio precedente, lo annota nel
    registro con l'elenco delle novità (titoli dei commit)."""
    commit = registro_service.commit_corrente()
    precedente = next((v for v in registro_service.leggi(5000) if v.get("tipo") == "aggiornamento"), None)
    novita = registro_service.novita_tra((precedente or {}).get("commit"), commit)
    registro_service.registra_avvio(_versione_app(), commit, novita)


def _anchor_membro(appello, base: str) -> str:
    """Nome della scheda su cui tornare dopo un'azione su questo appello: per una prova
    membro di un raggruppamento le schede per-prova hanno un suffisso "-{id}" (vedi
    appello_detail.html), altrimenti il nome della scheda resta quello nudo."""
    return f"{base}-{appello.id}" if appello.membro_raggruppamento else base


def flash_redirect(
    url: str, message: str, kind: str = "success", anchor: str = "", dettaglio: str = "",
    extra: Optional[dict] = None,
) -> RedirectResponse:
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    # l'anchor va anche nella query string (oltre che nel fragment): un fragment non
    # arriva mai al server, quindi se questo redirect punta a una prova membro di un
    # raggruppamento, il secondo redirect (appello_detail, verso la pagina del
    # raggruppamento) non avrebbe altrimenti modo di sapere su quale scheda tornare.
    params = f"msg={quote(message)}&kind={kind}"
    if anchor:
        params += f"&anchor={quote(anchor)}"
    if dettaglio:
        # usato per errori con un dettaglio lungo (es. log di compilazione LaTeX): il
        # template mostra questo testo in un riquadro copiabile che non sparisce da solo.
        params += f"&dettaglio={quote(dettaglio)}"
    for chiave, valore in (extra or {}).items():
        params += f"&{chiave}={quote(str(valore))}"
    suffisso = f"#{anchor}" if anchor else ""
    return RedirectResponse(f"{url}{sep}{params}{suffisso}", status_code=303)


@app.exception_handler(Exception)
async def gestisci_errore_generico(request: Request, exc: Exception):
    """Rete di sicurezza: qualunque eccezione non gestita esplicitamente da una route
    finisce qui invece che in una pagina di errore grezza. Il messaggio resta comunque
    stampato in console (nel terminale da cui gira `quizesame`) per poterlo diagnosticare."""
    traceback.print_exc()
    destinazione = request.headers.get("referer") or "/"
    return flash_redirect(destinazione, f"Si è verificato un errore imprevisto: {exc}", "error")


def _proteggi_modifica_esercizi(tag: str, appello_id: int) -> None:
    """Solleva ValueError se il testo di questo appello è chiuso (va riaperto dalla scheda
    Testo) o se ha già blocchi generati con risultati registrati: cambiare gli esercizi
    assegnati a quel punto invaliderebbe codici già usati da studenti reali."""
    corsi_service.verifica_testo_aperto(tag, appello_id)
    bloccanti = compiti_service.blocchi_con_risultati(tag, appello_id)
    if bloccanti:
        raise ValueError(
            "Non puoi modificare gli esercizi di questo appello: il blocco "
            + ", ".join(str(n) for n in bloccanti)
            + " ha già risultati registrati. Elimina prima quei risultati, oppure crea un nuovo appello."
        )


def _compila_in_background(percorsi: list) -> None:
    with ThreadPoolExecutor(max_workers=len(percorsi)) as pool:
        risultati = list(pool.map(compiti_service.compila_pdf, percorsi))
    for percorso, comp in zip(percorsi, risultati):
        if not comp.ok:
            # non c'è una richiesta HTTP a cui agganciare un flash (la compilazione gira
            # dopo che la risposta è già stata inviata): l'errore resta comunque visibile
            # nel terminale da cui gira l'app, e il PDF vecchio (se esisteva) non viene
            # toccato, quindi il link non punta a un file rotto.
            print(f"Compilazione PDF fallita per {percorso}: {comp.messaggio}")
            if comp.errore_dettagliato:
                print(comp.errore_dettagliato)


def _rigenera_se_necessario(tag: str, appello_id: int, background_tasks: Optional[BackgroundTasks] = None) -> str:
    """Da chiamare dopo aver modificato gli esercizi assegnati a un appello: se erano già
    stati generati blocchi, li rigenera (file di riferimento compreso, sempre in modo
    sincrono: i vecchi codici vanno invalidati subito) e ritorna un suffisso da aggiungere
    al messaggio di conferma. La ricompilazione dei PDF (pdflatex, la parte lenta) viene
    invece schedulata come background task quando il chiamante passa `background_tasks`
    (così la risposta HTTP non resta bloccata per diversi secondi ad ogni piccola
    modifica): il file .tex è comunque aggiornato subito, il PDF segue a breve."""
    risultato = compiti_service.rigenera_tutto(tag, appello_id)
    if risultato is None:
        return ""
    blocchi = compiti_service.list_blocchi(tag, appello_id)
    da_compilare = [
        compiti_service.path_riferimento(tag, appello_id, "tex"),
        compiti_service.path_riferimento_soluzioni(tag, appello_id, "tex"),
    ]
    for b in blocchi:
        da_compilare.append(compiti_service.path_blocco(tag, appello_id, b["numero"], "tex"))
        da_compilare.append(compiti_service.path_griglia(tag, appello_id, b["numero"], "tex"))
    if background_tasks is not None:
        background_tasks.add_task(_compila_in_background, da_compilare)
        return (
            f" {risultato.n_blocchi} blocco/i già generato/i sono stati rigenerati con i nuovi "
            "esercizi (i vecchi codici sono stati eliminati); i PDF si stanno aggiornando in "
            "background e saranno pronti tra qualche istante."
        )
    _compila_in_background(da_compilare)
    return (
        f" {risultato.n_blocchi} blocco/i già generato/i sono stati rigenerati con i nuovi "
        "esercizi (i vecchi codici sono stati eliminati)."
    )


def _parse_varianti_form(form) -> list[dict]:
    """Ogni variante è identificata da un indice libero (assegnato lato client dal
    pulsante 'Aggiungi variante', non necessariamente consecutivo), elencato nei valori
    ripetuti del campo 'variante_idx'. Le risposte sbagliate di una variante sono i
    valori ripetuti del campo 'sbagliata_<idx>' (un input per risposta, aggiunto/rimosso
    lato client dal pulsante 'Aggiungi risposta sbagliata')."""
    varianti = []
    for idx in form.getlist("variante_idx"):
        testo = (form.get(f"testo_{idx}") or "").strip()
        if not testo:
            continue
        corretta = (form.get(f"corretta_{idx}") or "").strip()
        sbagliate = [s.strip() for s in form.getlist(f"sbagliata_{idx}") if s.strip()]
        varianti.append({"testo": testo, "risposte": [corretta] + sbagliate})
    return varianti


def _parse_esercizio_extra_form(form) -> dict:
    difficolta_raw = (form.get("difficolta") or "").strip()
    return {
        "difficolta": int(difficolta_raw) if difficolta_raw else None,
        "soluzione": (form.get("soluzione") or "").strip(),
        "aperta": bool(form.get("aperta")),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = ""):
    corsi = corsi_service.list_corsi()
    corsi_correnti = {c.tag for c in corsi if corsi_service.corso_e_corrente(c)}
    corsi_chiusi = {
        c.tag for c in corsi
        if (appelli_c := corsi_service.list_appelli(c.tag)) and all(a.chiuso for a in appelli_c)
    }
    gruppi_corsi = corsi_service.raggruppa_per_nome(corsi)
    # per l'elenco dei corsi: quanti appelli ha ciascuno e quanti sono ancora aperti
    # (le prove di un raggruppamento contano col raggruppamento, non a parte)
    conteggio_appelli = {}
    for c in corsi:
        elenco = [a for a in corsi_service.list_appelli(c.tag, includi_raggruppamenti=True) if not a.membro_raggruppamento]
        conteggio_appelli[c.tag] = {"totale": len(elenco), "aperti": sum(1 for a in elenco if not a.chiuso)}
    # "Accesso rapido": gli appelli non ancora chiusi dei corsi in corso, con i link
    # diretti alle schede usate più spesso (per un raggruppamento, una riga per prova)
    in_lavorazione = []
    for c in corsi:
        if c.tag not in corsi_correnti:
            continue
        for a in corsi_service.list_appelli(c.tag, includi_raggruppamenti=True):
            if a.chiuso or a.membro_raggruppamento:
                continue
            ragg = corsi_service.get_raggruppamento_by_appello(c.tag, a.id)
            in_lavorazione.append({
                "corso": c, "appello": a, "url": f"/corsi/{c.tag}/appelli/{a.id}",
                "prove": [{"nome": m.nome, "suffisso": f"-{m.id}", "data": m.data} for m in ragg.membri]
                if ragg else [{"nome": "", "suffisso": "", "data": a.data}],
                "raggruppamento": bool(ragg),
            })
    risultati_ricerca = studenti_service.cerca_in_tutti_i_corsi(q.strip()) if q.strip() else None
    app_settings = app_config_service.get_settings()
    riepilogo_globale = statistiche_service.calcola_globale() if app_settings.mostra_riepilogo_home else None
    # per il form "Nuovo corso": nome → dati dell'edizione più recente (corsi è già
    # ordinato per anno decrescente), per proporli e precompilare i campi
    edizioni_recenti = {}
    for c in corsi:
        edizioni_recenti.setdefault(c.nome.strip(), {
            "facolta": c.facolta, "universita": c.universita, "docente": c.docente, "anno": c.anno,
        })
    return templates.TemplateResponse(request, "corsi_list.html", {
        "edizioni_recenti": edizioni_recenti, "in_lavorazione": in_lavorazione,
        "corsi": corsi, "gruppi_corsi": gruppi_corsi, "corsi_correnti": corsi_correnti,
        "corsi_chiusi": corsi_chiusi, "conteggio_appelli": conteggio_appelli,
        "prossimi_appelli": corsi_service.prossimi_appelli(),
        "q": q, "risultati_ricerca": risultati_ricerca, "riepilogo_globale": riepilogo_globale,
        "aggiornamento_disponibile": aggiornamenti_service.aggiornamento_disponibile(),
    })


@app.get("/statistiche-generali", response_class=HTMLResponse)
def statistiche_generali(request: Request, corsi: list[str] = Query(None)):
    tutti_corsi = corsi_service.list_corsi()
    tags_selezionati = set(corsi) if corsi is not None else {c.tag for c in tutti_corsi}
    statistiche = statistiche_service.calcola_multi_corsi(
        [c.tag for c in tutti_corsi if c.tag in tags_selezionati]
    )
    return templates.TemplateResponse(request, "statistiche_generali.html", {
        "corsi": tutti_corsi, "tags_selezionati": tags_selezionati, "statistiche": statistiche,
    })


@app.get("/backup-tutti.zip")
def scarica_backup_tutti():
    dati = corsi_service.crea_backup_tutti()
    nome_file = f"backup-tutti-i-corsi-{date.today().isoformat()}.zip"
    return Response(
        dati, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )


@app.post("/ripristina-backup", response_class=HTMLResponse)
async def ripristina_backup(request: Request, file: UploadFile = File(...)):
    contenuto = await file.read()
    try:
        candidati = corsi_service.anteprima_ripristino(contenuto)
    except ValueError as e:
        return flash_redirect("/impostazioni", str(e), "error")
    return templates.TemplateResponse(request, "ripristina_conferma.html", {
        "candidati": candidati, "dati_zip": base64.b64encode(contenuto).decode("ascii"),
    })


@app.post("/ripristina-backup/conferma")
async def ripristina_backup_conferma(request: Request):
    form = await request.form()
    tags_scelti = form.getlist("tag_scelti")
    if not tags_scelti:
        return flash_redirect("/impostazioni", "Nessun corso selezionato per il ripristino", "error")
    try:
        contenuto = base64.b64decode(form.get("dati_zip") or "")
        ripristinati = corsi_service.ripristina_backup(contenuto, tags_scelti)
    except Exception as e:
        return flash_redirect("/impostazioni", f"Errore nel ripristino: {e}", "error")
    return flash_redirect("/", f"Corsi ripristinati: {', '.join(ripristinati) if ripristinati else 'nessuno'}")


@app.get("/impostazioni", response_class=HTMLResponse)
def impostazioni_app(request: Request):
    imp = app_config_service.get_settings()
    return templates.TemplateResponse(request, "impostazioni_app.html", {
        # delle chiavi le pagine mostrano solo le ultime cifre, mai la chiave intera
        "ia_profili": ia_service.profili(), "ia_attivo": ia_service.fornitore(),
        "ia_nomi_fornitori": ia_service.NOMI_FORNITORI, "ia_modelli_predefiniti": ia_service.MODELLI_PREDEFINITI,
        "app_settings": imp,
        "numero_corsi": len(corsi_service.list_corsi()),
        "frase_conferma_elimina_tutto": FRASE_CONFERMA_ELIMINA_TUTTO,
        "corso_prova_installato": corso_prova_service.installato(),
        "corso_prova_tag": corso_prova_service.TAG,
        "corso_prova_da_correggere": corso_prova_service.da_correggere(),
    })


@app.post("/impostazioni/corso-prova/installa")
def installa_corso_prova():
    try:
        non_compilati = corso_prova_service.installa(app_config_service.get_settings().docente)
    except Exception as e:
        return flash_redirect("/impostazioni", f"Corso di prova non installato: {e}", "error", anchor="corso-prova")
    msg = "Corso di prova installato: dati inventati, da eliminare quando hai finito da Impostazioni → Corso di prova"
    if non_compilati:
        return flash_redirect(
            f"/corsi/{corso_prova_service.TAG}",
            msg + f". {len(non_compilati)} PDF non compilati (LaTeX non disponibile?): i .tex ci sono, "
            "e i PDF si possono rigenerare dalla scheda Compiti d'esame", "warning", dettaglio="\n".join(non_compilati),
        )
    return flash_redirect(f"/corsi/{corso_prova_service.TAG}", msg + ", con i PDF dei compiti")


@app.post("/impostazioni/corso-prova/elimina")
def elimina_corso_prova():
    try:
        corso_prova_service.elimina()
    except Exception as e:
        return flash_redirect("/impostazioni", str(e), "error", anchor="corso-prova")
    return flash_redirect("/impostazioni", "Corso di prova eliminato, con tutti i suoi dati", anchor="corso-prova")


@app.post("/impostazioni")
def modifica_impostazioni_app(
    docente: str = Form(""),
    mostra_riepilogo_home: str = Form(""),
):
    app_config_service.update_settings(
        docente=docente.strip(), mostra_riepilogo_home=bool(mostra_riepilogo_home),
    )
    return flash_redirect("/impostazioni", "Impostazioni salvate")


def _url_ollama(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


@app.post("/impostazioni/ia/profili")
def aggiungi_profilo_ia(
    nome: str = Form(""), fornitore: str = Form("anthropic"), chiave: str = Form(""),
    modello: str = Form(""), url: str = Form(""), token: str = Form(""), predefinito: str = Form(""),
):
    """Salva una nuova configurazione IA (se ne possono avere più d'una e scegliere di
    volta in volta quale usare)."""
    if fornitore not in ia_service.FORNITORI:
        return flash_redirect("/impostazioni", "Fornitore non valido", "error", anchor="ia")
    chiave, modello = chiave.strip(), modello.strip()
    if fornitore == "anthropic" and not chiave.startswith("sk-ant-"):
        return flash_redirect("/impostazioni", "Non sembra una chiave API di Anthropic (inizia con sk-ant-)", "error", anchor="ia")
    if fornitore == "openrouter" and not chiave.startswith("sk-or-"):
        return flash_redirect("/impostazioni", "Non sembra una chiave di OpenRouter (inizia con sk-or-)", "error", anchor="ia")
    if fornitore == "ollama" and (not url.strip() or not modello):
        return flash_redirect("/impostazioni", "Per Ollama servono l'indirizzo del server e il modello", "error", anchor="ia")
    imp = app_config_service.get_settings()
    nuovo = {
        "id": app_config_service.nuovo_id_profilo(),
        "nome": nome.strip() or ia_service.NOMI_FORNITORI[fornitore],
        "fornitore": fornitore, "chiave": chiave if fornitore != "ollama" else "",
        "url": _url_ollama(url) if fornitore == "ollama" else "", "modello": modello,
        "token": token.strip() if fornitore == "ollama" else "",
    }
    profili = imp.ia_profili + [nuovo]
    predefinita = nuovo["id"] if (predefinito or not imp.ia_profilo_predefinito) else imp.ia_profilo_predefinito
    app_config_service.update_settings(ia_profili=profili, ia_profilo_predefinito=predefinita)
    msg = f"Configurazione \"{nuovo['nome']}\" salvata"
    if predefinita == nuovo["id"]:
        msg += ": sarà quella proposta di default"
    return flash_redirect("/impostazioni", msg, anchor="ia")


@app.post("/impostazioni/ia/profili/{profilo_id}/modifica")
def modifica_profilo_ia(profilo_id: str, nome: str = Form(""), chiave: str = Form(""), modello: str = Form(""),
                        url: str = Form(""), token: str = Form("")):
    """Nome, modello e (se se ne scrive una nuova) chiave o token di una configurazione già
    salvata; il fornitore non cambia: per un altro fornitore se ne aggiunge una nuova."""
    imp = app_config_service.get_settings()
    profilo = next((p for p in imp.ia_profili if p["id"] == profilo_id), None)
    if profilo is None:
        return flash_redirect("/impostazioni", "Configurazione non trovata", "error", anchor="ia")
    fornitore_p, chiave, modello = profilo.get("fornitore"), chiave.strip(), modello.strip()
    if fornitore_p == "anthropic" and chiave and not chiave.startswith("sk-ant-"):
        return flash_redirect("/impostazioni", "Non sembra una chiave API di Anthropic (inizia con sk-ant-)", "error", anchor="ia")
    if fornitore_p == "openrouter" and chiave and not chiave.startswith("sk-or-"):
        return flash_redirect("/impostazioni", "Non sembra una chiave di OpenRouter (inizia con sk-or-)", "error", anchor="ia")
    if fornitore_p == "ollama" and (not url.strip() or not modello):
        return flash_redirect("/impostazioni", "Per Ollama servono l'indirizzo del server e il modello", "error", anchor="ia")
    aggiornato = dict(profilo, nome=nome.strip() or profilo.get("nome", ""), modello=modello)
    if fornitore_p == "ollama":
        aggiornato["url"] = _url_ollama(url)
        if token.strip():
            aggiornato["token"] = token.strip()
    elif chiave:
        aggiornato["chiave"] = chiave
    profili = [aggiornato if p["id"] == profilo_id else p for p in imp.ia_profili]
    app_config_service.update_settings(ia_profili=profili)
    return flash_redirect("/impostazioni", f"Configurazione \"{aggiornato['nome']}\" aggiornata", anchor="ia")


@app.post("/impostazioni/ia/profili/{profilo_id}/predefinito")
def profilo_ia_predefinito(profilo_id: str):
    if not any(p["id"] == profilo_id for p in ia_service.profili()):
        return flash_redirect("/impostazioni", "Configurazione non trovata", "error", anchor="ia")
    app_config_service.update_settings(ia_profilo_predefinito=profilo_id)
    return flash_redirect("/impostazioni", "Configurazione predefinita aggiornata", anchor="ia")


@app.post("/impostazioni/ia/profili/{profilo_id}/elimina")
def elimina_profilo_ia(profilo_id: str):
    imp = app_config_service.get_settings()
    profili = [p for p in imp.ia_profili if p["id"] != profilo_id]
    if len(profili) == len(imp.ia_profili):
        return flash_redirect("/impostazioni", "Configurazione non trovata", "error", anchor="ia")
    predefinita = imp.ia_profilo_predefinito if imp.ia_profilo_predefinito != profilo_id else (profili[0]["id"] if profili else "")
    app_config_service.update_settings(ia_profili=profili, ia_profilo_predefinito=predefinita)
    return flash_redirect("/impostazioni", "Configurazione eliminata (con la sua chiave)", anchor="ia")


@app.post("/impostazioni/ia/modelli-ollama")
def modelli_ollama(ollama_url: str = Form(""), ollama_token: str = Form("")):
    """Verifica la connessione al server Ollama ed elenca i modelli installati (usato
    dal pulsante "Verifica connessione" delle impostazioni)."""
    try:
        return JSONResponse({"modelli": ia_service.modelli_ollama(_url_ollama(ollama_url), ollama_token.strip())})
    except ValueError as e:
        return JSONResponse({"errore": str(e)}, status_code=400)


@app.post("/impostazioni/controlla-aggiornamenti")
def controlla_aggiornamenti_app():
    disponibile, errore = aggiornamenti_service.forza_controllo()
    if errore:
        return flash_redirect("/impostazioni", f"Controllo aggiornamenti non riuscito: {errore}", "error")
    if disponibile:
        return flash_redirect("/impostazioni", "È disponibile un aggiornamento: usa ./aggiorna.sh (o aggiorna.bat) per scaricarlo")
    return flash_redirect("/impostazioni", "Nessun aggiornamento disponibile: hai già la versione più recente")


FRASE_CONFERMA_ELIMINA_TUTTO = "ELIMINA TUTTO"


@app.post("/elimina-tutti-i-dati")
def elimina_tutti_i_dati(conferma: str = Form("")):
    if conferma.strip() != FRASE_CONFERMA_ELIMINA_TUTTO:
        return flash_redirect("/impostazioni", "Testo di conferma non corretto: nessun dato è stato eliminato", "error")
    n = corsi_service.elimina_tutti_i_corsi()
    return flash_redirect("/impostazioni", f"Eliminati tutti i dati: {n} corsi rimossi definitivamente")


@app.get("/studenti/{matricola}", response_class=HTMLResponse)
def studente_riepilogo(request: Request, matricola: str):
    riepilogo = studenti_service.riepilogo_globale(matricola)
    if not riepilogo:
        return flash_redirect("/", f"Nessuno studente trovato con matricola '{matricola}'", "error")
    return templates.TemplateResponse(request, "studente_riepilogo.html", {
        "matricola": matricola, "riepilogo": riepilogo,
    })


@app.post("/corsi/nuovo")
def crea_corso(
    tag: str = Form(...), nome: str = Form(...), facolta: str = Form(""),
    universita: str = Form(""), anno: str = Form(""), docente: str = Form(""),
):
    try:
        _, precedente = corsi_service.create_corso(
            tag.strip(), nome.strip(), facolta.strip(), universita.strip(), anno.strip(), docente.strip(),
        )
    except ValueError as e:
        return flash_redirect("/", str(e), "error")
    # subito alle impostazioni: voti, punteggi e domande d'esame vanno controllati prima
    # di creare il primo appello
    if precedente:
        msg = (
            f"Corso creato. Impostazioni e domande d'esame copiate da \"{precedente.nome}\" "
            f"({precedente.anno or precedente.tag}): controllale qui sotto, poi crea il primo appello."
        )
    else:
        msg = "Corso creato. Controlla le impostazioni principali qui sotto, poi crea il primo appello."
    return flash_redirect(f"/corsi/{tag.strip()}/impostazioni", msg)


@app.get("/corsi/{tag}", response_class=HTMLResponse)
def corso_detail(request: Request, tag: str):
    corso = corsi_service.get_corso(tag)
    # Una prova membro di un raggruppamento non compare più come appello a sé: la sua
    # pagina vive dentro quella del raggruppamento (vedi appello_detail).
    appelli = [
        a for a in corsi_service.list_appelli(tag, includi_raggruppamenti=False)
        if not a.membro_raggruppamento
    ]
    raggruppamenti = corsi_service.list_raggruppamenti(tag)
    statistiche = {}
    for a in corsi_service.list_appelli(tag, includi_raggruppamenti=True):
        risultati = [
            r for r in correzione_service.list_risultati(tag, a.id)
            if not (r["richiede_orale"] and not r["orale_svolto"])
        ]
        totale = len(risultati)
        votomin = corsi_service.effective_votomin(corso, a)
        sufficienti = sum(1 for r in risultati if r["voto"] is not None and r["voto"] >= votomin)
        voti = [r["voto"] for r in risultati if r["voto"] is not None]
        # la media è dei voti: assenti e ritirati non hanno un voto e non vanno contati
        media = round(sum(voti) / len(voti), 1) if voti else None
        statistiche[a.id] = {
            "totale": totale, "sufficienti": sufficienti,
            "percentuale": round(100 * sufficienti / totale) if totale else None,
            "media": media,
        }
    raggruppamenti_appelli = {r.appello_id: corsi_service.get_appello(tag, r.appello_id) for r in raggruppamenti}
    # prossimo passo di ogni appello ancora aperto (gli stessi calcoli della sua pagina;
    # per quelli chiusi non c'è più niente da fare e si evita il lavoro)
    prossimi_passi = {}
    for a in appelli + [raggruppamenti_appelli[r.appello_id] for r in raggruppamenti]:
        if a is not None and not a.chiuso:
            ctx = _contesto_appello(tag, corso, a)
            prossimi_passi[a.id] = ctx["prossimo_passo"]
    # un solo elenco in ordine di creazione (di solito quello cronologico): un
    # raggruppamento sta dove inizia la sua prima prova, con le prove elencate sotto
    voci = [{"tipo": "appello", "appello": a, "ordine": a.id} for a in appelli] + [
        {"tipo": "raggruppamento", "raggruppamento": r, "appello": raggruppamenti_appelli[r.appello_id],
         "ordine": min([m.id for m in r.membri] or [r.appello_id])}
        for r in raggruppamenti if raggruppamenti_appelli.get(r.appello_id)
    ]
    voci.sort(key=lambda v: v["ordine"])
    return templates.TemplateResponse(request, "corso_detail.html", {
        "prossimi_passi": prossimi_passi, "voci": voci,
        "corso": corso, "appelli": appelli, "raggruppamenti": raggruppamenti, "statistiche": statistiche,
        "raggruppamenti_appelli": raggruppamenti_appelli, "oggi": date.today().strftime("%d/%m/%Y"),
    })


@app.get("/corsi/{tag}/controllo-correzioni", response_class=HTMLResponse)
def controllo_correzioni(request: Request, tag: str):
    """Ricalcola i voti delle correzioni già fatte e cerca le domande con una chiave sospetta."""
    controllo_service.segnala_sospette_corso(tag)
    return templates.TemplateResponse(request, "controllo_correzioni.html", {
        "corso": corsi_service.get_corso(tag), "esiti": controllo_service.controlla_corso(tag),
        "segnalazioni": controllo_service.stato_sospette(tag),
        "cartella_legacy": migrazione_service.cartella_originale(tag),
    })


@app.get("/controllo-correzioni", response_class=HTMLResponse)
def controllo_correzioni_tutti(request: Request):
    return templates.TemplateResponse(request, "controllo_correzioni_tutti.html", {
        "riepilogo": controllo_service.riepilogo_tutti_i_corsi(),
    })


@app.post("/corsi/{tag}/ripara-legacy")
def ripara_legacy(tag: str, cartella: str = Form("")):
    """Ricostruisce esercizi assegnati e dettaglio dei compiti di un corso importato
    dalla vecchia CLI, dalla sua cartella originale."""
    try:
        esito = migrazione_service.ripara_da_cartella_legacy(tag, cartella.strip())
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/controllo-correzioni", str(e), "error")
    sostituiti = sum(a["esercizi_sostituiti"] for a in esito["appelli"])
    ricostruiti = sum(a["compiti_ricostruiti"] for a in esito["appelli"])
    non_riconosciuti = sum(a["esercizi_non_riconosciuti"] for a in esito["appelli"])
    avvisi = [f"{a['appello']}: {x}" for a in esito["appelli"] for x in a["avvisi"]]
    msg = (f"Dalla cartella {esito['cartella']}: {sostituiti} esercizi riassegnati agli appelli, "
           f"{ricostruiti} compiti ricostruiti")
    if non_riconosciuti:
        msg += f", {non_riconosciuti} domande stampate non riconosciute (esercizio modificato dopo la stampa?)"
    if avvisi:
        msg += ". " + "; ".join(avvisi)
    return flash_redirect(f"/corsi/{tag}/controllo-correzioni", msg, "warning" if avvisi or non_riconosciuti else "success")


@app.get("/corsi/{tag}/statistiche", response_class=HTMLResponse)
def statistiche_corso(request: Request, tag: str):
    corso = corsi_service.get_corso(tag)
    statistiche = statistiche_service.calcola_corso(tag)
    return templates.TemplateResponse(request, "corso_statistiche.html", {
        "corso": corso, "statistiche": statistiche,
    })


@app.get("/corsi/{tag}/impostazioni", response_class=HTMLResponse)
def impostazioni_corso(request: Request, tag: str):
    corso = corsi_service.get_corso(tag)
    return templates.TemplateResponse(request, "impostazioni.html", {
        "corso": corso,
        "default_frase_consegna": corsi_service.DEFAULT_FRASE_CONSEGNA,
        "default_frase_regole": corsi_service.DEFAULT_FRASE_REGOLE,
        "default_frase_obbligatorio": corsi_service.DEFAULT_FRASE_OBBLIGATORIO,
    })


@app.post("/corsi/{tag}/impostazioni")
def modifica_corso(
    tag: str, nome: str = Form(...), facolta: str = Form(""), universita: str = Form(""),
    anno: str = Form(""), docente: str = Form(""), votomin: int = Form(...), consegna: int = Form(...),
    votomin_raggruppamento: int = Form(...),
    risposta_corretta: int = Form(...), risposta_sbagliata: int = Form(...), risposta_vuota: int = Form(...),
    punteggio_max_aperta: int = Form(corsi_service.DEFAULT_PUNTEGGIO_MAX_APERTA),
    domande_aperte_attive: str = Form(""),
    frase_consegna: str = Form(""), frase_regole: str = Form(""), frase_obbligatorio: str = Form(""),
    orale_dopo_richiesta: str = Form(""), orale_soglia_attiva: str = Form(""),
    orale_soglia_n: str = Form(""), orale_soglia_voto: str = Form(""),
    orale_soglia_escludi_parziali: str = Form(""),
    ritirato_conta_insufficiente: str = Form(""), annullato_come: str = Form("insufficiente"),
    soglia_occupazione_aule: int = Form(corsi_service.DEFAULT_SOGLIA_OCCUPAZIONE_AULE),
    domande_esame: list[str] = Form([]),
):
    try:
        corsi_service.update_meta(
            tag, nome=nome, facolta=facolta, universita=universita, anno=anno, docente=docente,
            votomin=str(votomin), consegna=str(consegna), votomin_raggruppamento=str(votomin_raggruppamento),
            risposta_corretta=str(risposta_corretta), risposta_sbagliata=str(risposta_sbagliata),
            risposta_vuota=str(risposta_vuota), punteggio_max_aperta=str(punteggio_max_aperta),
            domande_aperte_attive="1" if domande_aperte_attive else "0",
            frase_consegna=frase_consegna.strip() or corsi_service.DEFAULT_FRASE_CONSEGNA,
            frase_regole=frase_regole.strip() or corsi_service.DEFAULT_FRASE_REGOLE,
            frase_obbligatorio=frase_obbligatorio.strip() or corsi_service.DEFAULT_FRASE_OBBLIGATORIO,
            orale_dopo_richiesta="1" if orale_dopo_richiesta else "0",
            orale_soglia_attiva="1" if orale_soglia_attiva else "0",
            orale_soglia_n=orale_soglia_n.strip(), orale_soglia_voto=orale_soglia_voto.strip(),
            orale_soglia_escludi_parziali="1" if orale_soglia_escludi_parziali else "0",
            ritirato_conta_insufficiente="1" if ritirato_conta_insufficiente else "0",
            annullato_come="ritirato" if annullato_come == "ritirato" else "insufficiente",
            soglia_occupazione_aule=str(max(1, min(100, soglia_occupazione_aule))),
            **corsi_service.domande_esame_meta(domande_esame),
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/impostazioni", str(e), "error")
    msg = "Impostazioni corso salvate"
    if not domande_aperte_attive:
        n_aperte = sum(1 for e in esercizi_service.list_esercizi(tag) if e.aperta)
        if n_aperte:
            msg += (
                f". Attenzione: nella banca ci sono ancora {n_aperte} domande aperte, che restano"
                " utilizzabili e modificabili, ma non se ne possono creare di nuove"
            )
    return flash_redirect(f"/corsi/{tag}/impostazioni", msg)


@app.get("/corsi/{tag}/backup.zip")
def scarica_backup_corso(tag: str):
    try:
        dati = corsi_service.crea_backup_corso(tag)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/impostazioni", str(e), "error")
    nome_file = f"backup-{tag}-{date.today().isoformat()}.zip"
    return Response(
        dati, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )


@app.post("/corsi/{tag}/elimina")
def elimina_corso(tag: str):
    try:
        corsi_service.elimina_corso(tag)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/impostazioni", str(e), "error")
    return flash_redirect("/", f"Corso '{tag}' eliminato definitivamente")


@app.post("/corsi/{tag}/chiudi")
def chiudi_corso(tag: str):
    n = corsi_service.chiudi_corso(tag)
    msg = f"Chiusi {n} appelli" if n else "Nessun appello da chiudere: erano già tutti chiusi"
    return flash_redirect(f"/corsi/{tag}/impostazioni", msg)


@app.post("/corsi/{tag}/appelli/nuovo")
def crea_appello(tag: str, nome: str = Form(...), data: str = Form("")):
    try:
        appello = corsi_service.create_appello(tag, nome=nome.strip(), data=_data_iso_a_it(data) or None)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}", str(e), "error")
    # subito dentro l'appello, sulla scheda del testo: il passo successivo è sempre
    # assegnare gli esercizi
    return flash_redirect(
        f"/corsi/{tag}/appelli/{appello.id}", "Appello creato: ora assegna gli esercizi del compito", anchor="creazione",
    )


@app.post("/corsi/{tag}/raggruppamenti/nuovo")
async def crea_raggruppamento(tag: str, request: Request):
    form = await request.form()
    nome = (form.get("nome") or "").strip()
    membro_ids = [int(v) for v in form.getlist("membri")]
    try:
        corsi_service.create_raggruppamento(tag, nome, membro_ids)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}", str(e), "error")
    return flash_redirect(f"/corsi/{tag}", "Raggruppamento creato")


def _numero_studenti_suggerito(numero_iscritti, n_compiti_esistenti: int) -> int:
    """Default per il campo "numero studenti" di Crea compiti: un numero tale che il
    totale dei compiti generati (quelli già generati più questo nuovo blocco) superi
    leggermente (+10%) gli iscritti. Senza un numero di iscritti noto resta 1, il
    comportamento di sempre."""
    if not numero_iscritti:
        return 1
    obiettivo = math.ceil(numero_iscritti * 1.10)
    return max(1, obiettivo - n_compiti_esistenti)


def _calcola_riepilogo_home(corso, appello, dati: dict) -> dict:
    """Punto della situazione di una singola prova (per un appello normale, o per
    ciascuna prova membro di un raggruppamento) per la scheda Home: dove siamo nel
    flusso testo -> stampa -> valutazione -> orali -> chiusura, e una percentuale
    complessiva di avanzamento (ogni fase pesa uguale; "in corso" vale mezzo punto)."""
    risultati = dati["risultati"]
    numero_iscritti = dati["numero_iscritti"]
    compiti_totali = len(dati["compiti"])
    assenti = sum(1 for r in risultati if r["esito"] == "assente")
    ritirati = sum(1 for r in risultati if r["esito"] == "ritirato")
    annullati = sum(1 for r in risultati if r["esito"] == "annullato")
    valutati = sum(1 for r in risultati if r["esito"] == "voto")
    sufficienti = sum(
        1 for r in risultati
        if r["esito"] == "voto" and r["voto"] is not None and r["voto"] >= dati["votomin_effettivo"]
    )
    presenti = (numero_iscritti - assenti) if numero_iscritti is not None else None
    valutati_totale = valutati + assenti + ritirati + annullati

    # Le fasi di una singola prova, nell'ordine in cui si svolgono: ognuna sa in quale
    # scheda si completa e cosa c'è da fare (usato per il percorso e il "prossimo passo"
    # in cima alla pagina dell'appello). Orali, verbalizzazione e chiusura riguardano
    # l'appello nel suo insieme e si aggiungono in _fasi_globali.
    fasi = []
    n_esercizi = len(dati["esercizi_assegnati"])
    testo_chiuso = bool(getattr(appello, "testo_chiuso", False))
    fasi.append({
        "nome": "Testo", "scheda": "creazione", "fatta": testo_chiuso,
        "in_corso": bool(n_esercizi) and not testo_chiuso,
        "dettaglio": f"{n_esercizi} esercizi, " + ("testo chiuso" if testo_chiuso else "testo ancora aperto"),
        "azione": ("Controlla l'anteprima e chiudi il testo: si generano i fogli da pubblicare" if n_esercizi
                   else "Scegli gli esercizi del compito (dalla banca, da altri corsi o creandone di nuovi)"),
    })
    fase_stampa_fatta = bool(dati["blocchi"]) and (numero_iscritti is None or compiti_totali >= numero_iscritti)
    dettaglio_stampa = f"{compiti_totali} compiti generati"
    if numero_iscritti is not None:
        dettaglio_stampa += f" su {numero_iscritti} iscritti"
    fasi.append({
        "nome": "Compiti d'esame", "scheda": "compiti", "fatta": fase_stampa_fatta,
        "in_corso": bool(dati["blocchi"]) and not fase_stampa_fatta, "dettaglio": dettaglio_stampa,
        "azione": "Chiudi prima il testo, poi genera i compiti" if not testo_chiuso and not dati["blocchi"]
        else "Carica gli iscritti, genera i compiti e stampali" if not dati["blocchi"]
        else "Genera altri compiti: non bastano per tutti gli iscritti",
    })

    atteso = numero_iscritti if numero_iscritti is not None else (compiti_totali or None)
    concluso = bool(getattr(appello, "correzione_scritti_conclusa", False))
    # le presenze sono una fase obbligatoria: si completano chiudendo il registro il giorno
    # della prova oppure, dopo le correzioni, segnando assenti tutti quelli senza un esito
    # (pulsante nella scheda Presenze e in Correzione)
    presenze = dati.get("riepilogo_presenze")
    non_valutati = dati.get("non_valutati")
    if appello.presenze_chiuse:
        fasi.append({"nome": "Presenze", "scheda": "presenze", "fatta": True, "dettaglio": "registro chiuso"})
    elif valutati_totale > 0 and non_valutati is not None and not non_valutati:
        fasi.append({"nome": "Presenze", "scheda": "presenze", "fatta": True, "dettaglio": "tutti con un esito"})
    elif concluso or appello.chiuso:
        # correzione chiusa (o appello chiuso): chi non ha un esito non si è presentato,
        # non resta nulla da fare sulle presenze
        fasi.append({"nome": "Presenze", "scheda": "presenze", "fatta": True, "dettaglio": "correzione chiusa"})
    elif valutati_totale > 0:
        n = len(non_valutati or [])
        fasi.append({
            "nome": "Presenze", "scheda": "presenze", "fatta": False, "in_corso": True,
            # si completa solo a correzioni finite: il prossimo passo resta la Correzione
            "attende_correzione": True,
            "dettaglio": f"{n} attesi ancora senza esito" if non_valutati is not None else "nessun elenco iscritti",
            "azione": "Finite le correzioni, segna come assenti gli iscritti senza esito (o chiudi il registro presenze)",
        })
    else:
        fasi.append({
            "nome": "Presenze", "scheda": "presenze", "fatta": False,
            "in_corso": bool(presenze and presenze["n_presenti"]),
            "dettaglio": f"{presenze['n_presenti']} presenti su {presenze['n_iscritti']}" if presenze else "il giorno della prova",
            "azione": "Il giorno della prova fai l'appello in aula e segna i ritirati; finite le correzioni, segna assenti gli altri",
        })

    fase_valutazione_fatta = concluso or (bool(atteso) and valutati_totale >= atteso)
    dettaglio_valutazione = f"{valutati_totale} valutati" + (f" su {atteso}" if atteso else "")
    fasi.append({
        "nome": "Correzione", "scheda": "valutazione", "fatta": fase_valutazione_fatta,
        "in_corso": valutati_totale > 0 and not fase_valutazione_fatta,
        "dettaglio": dettaglio_valutazione + (" · conclusa" if concluso else ""),
        "azione": ("Correggi i compiti consegnati; poi segna assenti chi non ha consegnato (scheda Presenze) e chiudi la correzione"
                   if valutati_totale and not appello.presenze_chiuse and non_valutati
                   else "Correggi i compiti consegnati, poi chiudi la correzione" if valutati_totale
                   else "Correggi i compiti consegnati"),
    })

    punti = sum(1.0 if f.get("fatta") or f.get("saltata") else (0.5 if f.get("in_corso") else 0.0) for f in fasi)
    percentuale = round(100 * punti / len(fasi)) if fasi else 0

    return {
        "data": appello.data, "compiti_stampati": compiti_totali, "iscritti": numero_iscritti,
        "presenti": presenti, "assenti": assenti, "ritirati": ritirati, "annullati": annullati,
        "sufficienti": sufficienti,
        "valutati": valutati, "fasi": fasi, "percentuale": percentuale,
    }


def _fasi_globali(contesto: dict) -> list[dict]:
    """Fasi che riguardano l'appello nel suo insieme (per un raggruppamento, dopo tutte
    le prove): orali (solo se qualcuno li deve sostenere), verbalizzazione e chiusura."""
    appello = contesto["appello"]
    risultati = contesto.get("risultati") or []
    fasi = []
    da_orale = len(contesto["orali_da_svolgere"])
    if da_orale or contesto["orali_svolti"]:
        fasi.append({
            "nome": "Orali", "scheda": "orali", "fatta": da_orale == 0,
            "dettaglio": "tutti svolti" if da_orale == 0 else f"{da_orale} da svolgere",
            "azione": f"Registra l'esito degli orali ({da_orale} studenti da sentire)",
        })
    n_idonei, n_verbalizzati = len(contesto["idonei"]), len(contesto["verbalizzati"])
    fasi.append({
        "nome": "Verbalizzazione", "scheda": "risultati" if contesto.get("raggruppamento") else "verbalizzati",
        "fatta": bool(risultati) and n_idonei == 0,
        "in_corso": n_verbalizzati > 0 and n_idonei > 0,
        "dettaglio": f"{n_verbalizzati} verbalizzati" + (f", {n_idonei} da verbalizzare" if n_idonei else ""),
        "azione": "Esporta i voti per la segreteria e segna come verbalizzati quelli registrati",
    })
    fasi.append({
        "nome": "Chiusura", "scheda": "impostazioni", "fatta": appello.chiuso,
        "dettaglio": "chiuso" if appello.chiuso else "ancora aperto",
        "azione": "Chiudi l'appello: nessuna modifica sarà più possibile (si può riaprire)",
    })
    return fasi


def _percorso(contesto: dict) -> tuple[list[dict], Optional[dict]]:
    """Il percorso completo dell'appello, nell'ordine (per un raggruppamento: le fasi di
    ciascuna prova, poi quelle globali), con la scheda effettiva di ogni fase, e il
    prossimo passo da fare: la prima fase non completata, saltando quelle facoltative se
    nel frattempo si è già andati avanti."""
    passi = []
    if contesto.get("raggruppamento"):
        for m, md in zip(contesto["raggruppamento"].membri, contesto["membri_dati"]):
            for f in md["riepilogo_home"]["fasi"]:
                passi.append({**f, "prova": m.nome, "scheda": f"{f['scheda']}-{m.id}"})
    else:
        passi.extend(dict(f, prova="") for f in contesto["riepilogo_home"]["fasi"])
    passi.extend(dict(f, prova="") for f in _fasi_globali(contesto))
    for p in passi:
        p["stato"] = (
            "fatta" if p.get("fatta") else "saltata" if p.get("saltata")
            else "in_corso" if p.get("in_corso") else "da_fare"
        )
    prossimo = None
    for i, p in enumerate(passi):
        if contesto["appello"].chiuso:
            break  # appello chiuso: nessun passo da proporre (si può solo riaprire)
        if p["stato"] in ("fatta", "saltata") or p.get("attende_correzione"):
            continue
        if p.get("facoltativa") and any(q["stato"] in ("fatta", "in_corso") for q in passi[i + 1:]):
            continue
        prossimo = p
        break
    if prossimo:
        prossimo["prossimo"] = True
    return passi, prossimo


def _dati_appello(tag: str, corso, appello) -> dict:
    """Il fascio di dati per una singola prova (esercizi assegnati, blocchi, risultati
    scritti): calcolato una volta per un appello normale, o una volta per ciascuna prova
    membro quando la pagina è quella di un raggruppamento."""
    esercizi_assegnati = esercizi_service.list_esercizi_appello(tag, appello.id)
    banca_esercizi = esercizi_service.list_esercizi(tag)
    assegnati_ids = {e.id for e in esercizi_assegnati}
    disponibili = [e for e in banca_esercizi if e.id not in assegnati_ids]
    blocchi = compiti_service.list_blocchi(tag, appello.id)
    for b in blocchi:
        b["tex_esiste"] = compiti_service.path_blocco(tag, appello.id, b["numero"], "tex", appello=appello).exists()
        b["pdf_esiste"] = compiti_service.path_blocco(tag, appello.id, b["numero"], "pdf", appello=appello).exists()
        b["griglia_pdf_esiste"] = compiti_service.path_griglia(tag, appello.id, b["numero"], "pdf", appello=appello).exists()
    compiti = compiti_service.list_compiti(tag, appello.id)
    iscritti = esportazione_service.list_iscritti(tag, appello.id)
    numero_iscritti = len(iscritti) if iscritti is not None else appello.iscritti_manuale
    dati = {
        "appello": appello,
        "compiti": compiti,
        "risultati": correzione_service.list_risultati(tag, appello.id),
        "valutazioni_sospese": correzione_service.list_valutazioni_sospese(tag, appello.id),
        "esercizi_assegnati": esercizi_assegnati,
        "esercizi_disponibili": disponibili,
        "argomenti": esercizi_service.list_argomenti(tag),
        "blocchi": blocchi,
        "riferimento_pdf_esiste": compiti_service.path_riferimento(tag, appello.id, "pdf", appello=appello).exists(),
        "riferimento_tex_esiste": compiti_service.path_riferimento(tag, appello.id, "tex", appello=appello).exists(),
        "riferimento_sol_pdf_esiste": compiti_service.path_riferimento_soluzioni(tag, appello.id, "pdf", appello=appello).exists(),
        "riferimento_sol_tex_esiste": compiti_service.path_riferimento_soluzioni(tag, appello.id, "tex", appello=appello).exists(),
        "votomin_effettivo": corsi_service.effective_votomin(corso, appello),
        "consegna_effettivo": corsi_service.effective_consegna(corso, appello),
        "segreteria_csv": esportazione_service.get_segreteria_csv(tag, appello.id),
        "esclusi_filtro": esportazione_service.list_esclusi_dal_filtro(tag, appello.id),
        "iscritti": iscritti,
        "iscritti_manuali": esportazione_service.list_iscritti_manuali(tag, appello.id),
        "numero_iscritti": numero_iscritti,
        "numero_studenti_suggerito": _numero_studenti_suggerito(numero_iscritti, len(compiti)),
        "avviso_pochi_compiti": numero_iscritti is not None and len(compiti) < numero_iscritti,
        "riepilogo_presenze": presenze_service.riepilogo(tag, appello.id),
        "bozze_correzione": correzione_service.list_bozze(tag, appello.id),
        "non_valutati": presenze_service.non_valutati(tag, appello.id),
        "risultati_tex_esiste": compiti_service.path_risultati(tag, appello.id, "tex", appello=appello).exists(),
        "risultati_pdf_esiste": compiti_service.path_risultati(tag, appello.id, "pdf", appello=appello).exists(),
    }
    # usato dall'avviso della verifica con l'IA, in due schede: calcolato una volta sola
    dati["stati_verifica_esercizi"] = esercizi_service.stati_verifica(tag, esercizi_assegnati) if esercizi_assegnati else {}
    dati["riepilogo_home"] = _calcola_riepilogo_home(corso, appello, dati)
    return dati


def _contesto_appello(tag: str, corso, appello) -> dict:
    """Tutti i dati della pagina di un appello (o di un raggruppamento), compreso il
    percorso delle fasi e il prossimo passo: usato dalla pagina stessa e, per il solo
    prossimo passo, dall'elenco appelli del corso."""
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello.id)
    contesto = {
        "corso": corso, "appello": appello, "raggruppamento": raggruppamento,
        "orali_da_svolgere": correzione_service.list_orali_da_svolgere(tag, appello.id),
        "orali_svolti": correzione_service.list_orali_svolti(tag, appello.id),
        "idonei": verbalizzazione_service.list_idonei(tag, appello.id),
        "verbalizzati": verbalizzazione_service.list_verbalizzati(tag, appello.id),
        "statistiche": statistiche_service.calcola(tag, appello.id),
        "votomin_effettivo": corsi_service.effective_votomin(corso, appello),
        "consegna_effettivo": corsi_service.effective_consegna(corso, appello),
    }
    if raggruppamento:
        membri_dati = []
        for i, m in enumerate(raggruppamento.membri):
            dati_membro = _dati_appello(tag, corso, m)
            ammessi = corsi_service.list_ammessi_prova(tag, raggruppamento, i)
            # per una prova parziale il numero di studenti attesi si stima dagli ammessi
            # (chi ha superato la prova precedente / rispetta la soglia di matricola),
            # non dagli iscritti/CSV segreteria: quello riguarda l'intero raggruppamento,
            # non la singola prova.
            dati_membro["numero_studenti_suggerito"] = _numero_studenti_suggerito(len(ammessi), len(dati_membro["compiti"]))
            # senza elenco iscritti, per una prova del raggruppamento si attendono gli ammessi
            if dati_membro["riepilogo_presenze"] is None:
                dati_membro["riepilogo_presenze"] = presenze_service.riepilogo(tag, m.id, ammessi)
            if dati_membro["non_valutati"] is None:
                dati_membro["non_valutati"] = presenze_service.non_valutati(tag, m.id, ammessi)
                # la fase Presenze dipende da chi è ancora senza esito: va ricalcolata
                dati_membro["riepilogo_home"] = _calcola_riepilogo_home(corso, m, dati_membro)
            membri_dati.append({**dati_membro, "aule": aule_service.list_aule(tag, m.id), "ammessi": ammessi})
        contesto["membri_dati"] = membri_dati
        contesto["statistiche_confronto"] = statistiche_service.confronto_raggruppamento(tag, raggruppamento)
        contesto["risultati"] = correzione_service.list_risultati(tag, appello.id)
        contesto["segreteria_csv"] = esportazione_service.get_segreteria_csv(tag, appello.id)
        contesto["risultati_tex_esiste"] = compiti_service.path_risultati(tag, appello.id, "tex", appello=appello).exists()
        contesto["risultati_pdf_esiste"] = compiti_service.path_risultati(tag, appello.id, "pdf", appello=appello).exists()
        percentuali = [md["riepilogo_home"]["percentuale"] for md in contesto["membri_dati"]]
        contesto["riepilogo_home_raggruppamento"] = {
            "percentuale": round(sum(percentuali) / len(percentuali)) if percentuali else 0,
            "chiuso": appello.chiuso,
        }
    else:
        contesto.update(_dati_appello(tag, corso, appello))
    contesto["percorso"], contesto["prossimo_passo"] = _percorso(contesto)
    fatti = sum(1.0 if p["stato"] in ("fatta", "saltata") else 0.5 if p["stato"] == "in_corso" else 0.0
                for p in contesto["percorso"])
    contesto["percentuale_percorso"] = round(100 * fatti / len(contesto["percorso"])) if contesto["percorso"] else 0
    return contesto


@app.get("/corsi/{tag}/appelli/{appello_id}", response_class=HTMLResponse)
def appello_detail(request: Request, tag: str, appello_id: int):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)

    if appello.membro_raggruppamento:
        # Una prova membro non ha più una pagina propria: tutto (testo, correzione,
        # orale, verbalizzazione) vive nella pagina del raggruppamento, con una scheda
        # dedicata per ciascuna prova. La query string (es. msg/kind/anchor di un
        # flash_redirect arrivato da un'azione su questa prova) va preservata, altrimenti
        # questo secondo redirect la perderebbe silenziosamente. L'anchor esatto della
        # scheda da cui arriva l'azione viaggia come parametro "anchor" nella query (un
        # fragment come "#creazione-5" non arriva mai al server): se manca (es. link
        # diretto senza contesto) si torna comunque sulla Valutazione di questa prova.
        raggruppamento_padre = corsi_service.get_raggruppamento_by_membro(tag, appello_id)
        target = f"/corsi/{tag}/appelli/{raggruppamento_padre.appello_id}"
        if request.url.query:
            target += f"?{request.url.query}"
        anchor = request.query_params.get("anchor") or f"valutazione-{appello_id}"
        target += f"#{anchor}"
        return RedirectResponse(target, status_code=303)

    contesto = _contesto_appello(tag, corso, appello)
    return templates.TemplateResponse(request, "appello_detail.html", contesto)


@app.post("/corsi/{tag}/appelli/{appello_id}/modifica")
def modifica_appello(
    tag: str, appello_id: int, nome: str = Form(...), data: str = Form(""),
    orale_data: str = Form(""), orale_ora: str = Form(""), orale_aula: str = Form(""),
):
    try:
        corsi_service.update_appello(
            tag, appello_id, nome=nome, data=_data_iso_a_it(data) or None,
            orale_data=_data_iso_a_it(orale_data) or None, orale_ora=orale_ora or None, orale_aula=orale_aula or None,
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Impostazioni appello salvate")


@app.post("/corsi/{tag}/appelli/{appello_id}/rinomina")
def rinomina_appello(tag: str, appello_id: int, nome: str = Form(...), data: str = Form(""), torna: int = Form(0)):
    """Nome e data di una prova, dall'elenco delle prove di un raggruppamento (senza
    toccare i dati dell'orale, che si impostano nelle impostazioni dell'appello)."""
    destinazione = f"/corsi/{tag}/appelli/{torna or appello_id}"
    if not nome.strip():
        return flash_redirect(destinazione, "Il nome della prova non può essere vuoto", "error", anchor="impostazioni")
    corsi_service.update_appello(tag, appello_id, nome=nome.strip(), data=_data_iso_a_it(data) or None)
    return flash_redirect(destinazione, f"Prova «{nome.strip()}» aggiornata", anchor="impostazioni")


@app.post("/corsi/{tag}/appelli/{appello_id}/elimina")
def elimina_appello(tag: str, appello_id: int):
    try:
        corsi_service.elimina_appello(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error")
    return flash_redirect(f"/corsi/{tag}", "Appello eliminato")


@app.post("/corsi/{tag}/appelli/{appello_id}/chiudi")
def chiudi_appello(tag: str, appello_id: int):
    corsi_service.chiudi_appello(tag, appello_id)
    _genera_risultati_cache(tag, appello_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Appello chiuso: non sono più possibili modifiche")


def _anchor_correzione(appello) -> str:
    """Come _anchor_membro, ma per azioni disponibili anche sull'appello 'virtuale' di un
    raggruppamento (che non ha una propria scheda Valutazione: la correzione vive nella
    scheda Risultati, comune a tutte le prove membro)."""
    if appello.tipo == "raggruppamento":
        return "risultati"
    return _anchor_membro(appello, "valutazione")


@app.post("/corsi/{tag}/appelli/{appello_id}/concludi-correzione-scritto")
def concludi_correzione_scritto(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_correzione(appello)
    sospese = correzione_service.list_valutazioni_sospese(tag, appello_id)
    if sospese:
        n = len(sospese)
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}",
            f"Non si può chiudere la correzione: {n} compit{'o ha' if n == 1 else 'i hanno'} ancora svolgimenti "
            "da valutare (il voto è provvisorio). Completali dalla pagina Svolgimenti e note.",
            "error", anchor=anchor,
        )
    corsi_service.concludi_correzione_scritti(tag, appello_id)
    _genera_risultati_cache(tag, appello_id)
    msg = "Correzione scritta conclusa: elenco risultati generato, nessuna nuova correzione è possibile finché non riapri"
    try:
        sospette = controllo_service.segnala_sospette(tag, appello_id)
    except Exception:
        sospette = []
    if sospette:
        n = len(sospette)
        return flash_redirect(
            f"/corsi/{tag}/controllo-correzioni", msg + f". Attenzione: {n} domand{'a' if n == 1 else 'e'} con una soluzione "
            "sospetta (una risposta sbagliata scelta più di quella corretta): ricontrolla"
            f"{'la' if n == 1 else 'le'} qui sotto, anche con la verifica con l'IA",
            "warning", anchor=f"appello-{appello_id}",
        )
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/riapri-correzione-scritto")
def riapri_correzione_scritto(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_correzione(appello)
    corsi_service.riapri_correzione_scritti(tag, appello_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Correzione scritta riaperta", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/riapri")
def riapri_appello(tag: str, appello_id: int):
    corsi_service.riapri_appello(tag, appello_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Appello riaperto", anchor="impostazioni")


@app.post("/corsi/{tag}/appelli/{appello_id}/genera-compiti")
def genera_compiti(tag: str, appello_id: int, numero_studenti: int = Form(...)):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    try:
        result = compiti_service.genera_blocco(tag, appello_id, numero_studenti)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)

    msg = f"Blocco {result.numero}: generati {result.n_compiti_generati} compiti"
    if result.n_saltati_duplicati:
        msg += f" ({result.n_saltati_duplicati} scartati per codice duplicato)"
    kind = "success"

    # le compilazioni pdflatex sono indipendenti (file diversi): lanciarle in parallelo
    # invece che una dopo l'altra dimezza (o più) il tempo di attesa dell'utente.
    da_compilare = {
        "Blocco": compiti_service.path_blocco(tag, appello_id, result.numero, "tex"),
        "Griglia": compiti_service.path_griglia(tag, appello_id, result.numero, "tex"),
    }
    if result.riferimento_generato or not compiti_service.path_riferimento(tag, appello_id, "pdf").exists():
        da_compilare["Riferimento"] = compiti_service.path_riferimento(tag, appello_id, "tex")
    if result.riferimento_generato or not compiti_service.path_riferimento_soluzioni(tag, appello_id, "pdf").exists():
        da_compilare["Riferimento con soluzioni"] = compiti_service.path_riferimento_soluzioni(tag, appello_id, "tex")
    with ThreadPoolExecutor(max_workers=len(da_compilare)) as pool:
        risultati_compilazione = dict(zip(da_compilare, pool.map(compiti_service.compila_pdf, da_compilare.values())))
    dettaglio = ""
    for etichetta in ["Riferimento", "Riferimento con soluzioni", "Blocco", "Griglia"]:
        if etichetta not in risultati_compilazione:
            continue
        comp = risultati_compilazione[etichetta]
        msg += f". {etichetta}: " + comp.messaggio
        if etichetta == "Blocco" and comp.ok:
            oltre = compiti_service.compiti_oltre_due_pagine(tag, da_compilare[etichetta], comp.pagine)
            if oltre:
                msg += (f" (attenzione: i compiti occupano {oltre} pagine invece di 2: accorcia o togli qualche"
                        " esercizio, o riduci le formule molto alte, per stare in un foglio fronte-retro)")
                kind = "warning" if kind == "success" else kind
        if comp.ok and comp.sporgenze:
            msg += (f" (attenzione: {len(comp.sporgenze)} punti escono dal margine, controlla il PDF;"
                    " di solito basta accorciare una formula o andare a capo nel testo dell'esercizio)")
            kind = "warning" if kind == "success" else kind
            if not dettaglio:
                dettaglio = f"{etichetta}, testo che esce dal margine:\n" + "\n".join(comp.sporgenze[:10])
        if not comp.ok:
            kind = "error"
            if comp.errore_dettagliato and not dettaglio:
                dettaglio = f"{etichetta}:\n{comp.errore_dettagliato}"

    if result.avviso_obbligatori:
        msg += " " + result.avviso_obbligatori
        kind = "warning" if kind == "success" else kind
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, kind, anchor=anchor, dettaglio=dettaglio)


@app.post("/corsi/{tag}/appelli/{appello_id}/ricompila-pdf")
def ricompila_pdf(tag: str, appello_id: int):
    """Rigenera con la grafica attuale i PDF già generati (compiti, griglie, fogli di
    riferimento), mantenendo codici e ordine: utile dopo un aggiornamento dell'app."""
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    try:
        da_compilare = compiti_service.ricostruisci_documenti(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    # anche gli elenchi (iscritti, ammessi) e i risultati, se esistono già per questo appello
    for tipo in ("iscritti", "ammessi"):
        _, _, elenco, _, tex_path = _elenco_studenti(tag, appello_id, tipo)
        if elenco is not None and tex_path is not None:
            da_compilare.append(tex_path)
    tex_risultati = compiti_service.path_risultati(tag, appello_id, "tex", appello=appello)
    if tex_risultati.exists():
        tex_risultati.write_text(risultati_service.stampa_risultati(tag, appello_id), encoding="utf-8")
        da_compilare.append(tex_risultati)
    with ThreadPoolExecutor(max_workers=min(8, len(da_compilare))) as pool:
        esiti = list(pool.map(compiti_service.compila_pdf, da_compilare))
    falliti = [(p, e) for p, e in zip(da_compilare, esiti) if not e.ok]
    oltre = [p.name for p, e in zip(da_compilare, esiti) if e.ok and p.name.startswith("blocco")
             and compiti_service.compiti_oltre_due_pagine(tag, p, e.pagine)]
    sporgenti = [(p, e) for p, e in zip(da_compilare, esiti) if e.ok and e.sporgenze]
    if falliti:
        p, e = falliti[0]
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", f"{len(falliti)} PDF non compilati ({p.name}): {e.messaggio}",
            "error", anchor=anchor, dettaglio=e.errore_dettagliato or "",
        )
    msg = f"Rigenerati {len(da_compilare)} documenti (PDF e altri formati) con gli esercizi e la grafica attuali"
    msg += (" (stessi codici e stesso ordine di esercizi e risposte)." if compiti_service.list_blocchi(tag, appello_id) else ".")
    if oltre:
        msg += f" Attenzione: in {', '.join(oltre)} i compiti superano le due pagine."
    if sporgenti:
        dettaglio = "\n".join(f"{p.name}: {v}" for p, e in sporgenti for v in e.sporgenze[:5])
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}",
                              msg + f" Attenzione: in {len(sporgenti)} file del testo esce dal margine, controllali.",
                              "warning", anchor=anchor, dettaglio=dettaglio)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


def _pdf_da_ricompilare(tex_path: Path) -> bool:
    """Il PDF manca o è più vecchio del .tex (es. il .tex è stato aggiornato aprendo la
    versione .tex prima del PDF): va ricompilato."""
    pdf_path = tex_path.with_suffix(".pdf")
    return not pdf_path.exists() or pdf_path.stat().st_mtime < tex_path.stat().st_mtime


def _riferimento(tag: str, appello_id: int, con_soluzioni: bool, formato: str, rigenera: bool = False):
    """Foglio di riferimento (con o senza soluzioni) sempre aggiornato agli esercizi
    attuali: il .tex viene riscritto se è cambiato e il PDF ricompilato solo in quel caso
    (o se manca); mai servito dalla cache del browser."""
    appello = corsi_service.get_appello(tag, appello_id)
    try:
        tex_path, cambiato = compiti_service.riferimento_aggiornato(tag, appello_id, con_soluzioni)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)
    if formato == "tex":
        return PlainTextResponse(tex_path.read_text(encoding="utf-8"), media_type="application/x-tex",
                                 headers={"Cache-Control": "no-store"})
    pdf_path = tex_path.with_suffix(".pdf")
    if cambiato or rigenera or _pdf_da_ricompilare(tex_path):
        compilazione = compiti_service.compila_pdf(tex_path)
        if not compilazione.ok:
            return flash_redirect(
                f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error",
                anchor=_anchor_membro(appello, "compiti"), dettaglio=compilazione.errore_dettagliato or "",
            )
        pdf_path = compilazione.pdf_path
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name,
                        content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.tex", response_class=PlainTextResponse)
def scarica_riferimento_tex(tag: str, appello_id: int):
    return _riferimento(tag, appello_id, False, "tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.pdf")
def scarica_riferimento_pdf(tag: str, appello_id: int, rigenera: str = ""):
    return _riferimento(tag, appello_id, False, "pdf", bool(rigenera))


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento-soluzioni.tex", response_class=PlainTextResponse)
def scarica_riferimento_soluzioni_tex(tag: str, appello_id: int):
    return _riferimento(tag, appello_id, True, "tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento-soluzioni.pdf")
def scarica_riferimento_soluzioni_pdf(tag: str, appello_id: int, rigenera: str = ""):
    return _riferimento(tag, appello_id, True, "pdf", bool(rigenera))


@app.get("/corsi/{tag}/appelli/{appello_id}/anteprima.pdf")
def scarica_anteprima_pdf(tag: str, appello_id: int, rigenera: str = ""):
    """Anteprima di controllo del testo (tutte le varianti, risposte corrette, soluzioni):
    riflette sempre gli esercizi e le impostazioni attuali, ma viene ricompilata solo se
    il testo è cambiato dall'ultima volta (pdflatex è lento) oppure se richiesto con
    `rigenera` (pulsante "Rigenera" della scheda Testo). Mai dalla cache del browser."""
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        tex_path, cambiato = compiti_service.genera_anteprima(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    pdf_path = tex_path.with_suffix(".pdf")
    if cambiato or rigenera or _pdf_da_ricompilare(tex_path):
        compilazione = compiti_service.compila_pdf(tex_path)
        if not compilazione.ok:
            return flash_redirect(
                f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error",
                anchor=anchor, dettaglio=compilazione.errore_dettagliato or "",
            )
        pdf_path = compilazione.pdf_path
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=pdf_path.name,
        content_disposition_type="inline", headers={"Cache-Control": "no-store"},
    )


@app.get("/corsi/{tag}/appelli/{appello_id}/anteprima.tex", response_class=PlainTextResponse)
def scarica_anteprima_tex(tag: str, appello_id: int):
    try:
        tex_path, _ = compiti_service.genera_anteprima(tag, appello_id)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)
    return PlainTextResponse(tex_path.read_text(encoding="utf-8"), media_type="application/x-tex")


def _pagina_riferimento(request: Request, tag: str, appello_id: int, soluzioni: bool = False, tutte: bool = False):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    righe = compiti_service.anteprima_riferimento(tag, appello_id, tutte_varianti=tutte)
    return templates.TemplateResponse(request, "riferimento_anteprima.html", {
        "corso": corso, "appello": appello, "righe": righe, "soluzioni": soluzioni, "tutte": tutte,
    })


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.html", response_class=HTMLResponse)
def riferimento_anteprima(request: Request, tag: str, appello_id: int):
    return _pagina_riferimento(request, tag, appello_id)


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento-soluzioni.html", response_class=HTMLResponse)
def riferimento_soluzioni_html(request: Request, tag: str, appello_id: int):
    return _pagina_riferimento(request, tag, appello_id, soluzioni=True)


@app.get("/corsi/{tag}/appelli/{appello_id}/anteprima.html", response_class=HTMLResponse)
def anteprima_html(request: Request, tag: str, appello_id: int):
    """Anteprima di controllo a schermo: tutte le varianti di ogni esercizio, con
    risposta corretta e soluzioni (come il PDF di controllo, senza il riquadro di revisione)."""
    return _pagina_riferimento(request, tag, appello_id, soluzioni=True, tutte=True)


def _markdown(testo: str, nome_file: str) -> PlainTextResponse:
    return PlainTextResponse(testo, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'inline; filename="{nome_file}"'})


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.md", response_class=PlainTextResponse)
def riferimento_md(tag: str, appello_id: int):
    try:
        return _markdown(documenti_service.riferimento_md(tag, appello_id), "riferimento.md")
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento-soluzioni.md", response_class=PlainTextResponse)
def riferimento_soluzioni_md(tag: str, appello_id: int):
    try:
        return _markdown(documenti_service.riferimento_md(tag, appello_id, con_soluzioni=True), "riferimento-soluzioni.md")
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.md", response_class=PlainTextResponse)
def risultati_md(tag: str, appello_id: int):
    return _markdown(documenti_service.risultati_md(tag, appello_id), "risultati.md")


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/testo.tex", response_class=PlainTextResponse)
def scarica_blocco_tex(tag: str, appello_id: int, numero: int):
    path = compiti_service.path_blocco(tag, appello_id, numero, "tex")
    if not path.exists():
        return PlainTextResponse("Testo non ancora generato", status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/testo.pdf")
def scarica_blocco_pdf(tag: str, appello_id: int, numero: int):
    path = compiti_service.path_blocco(tag, appello_id, numero, "pdf")
    if not path.exists():
        return PlainTextResponse("PDF non ancora compilato", status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/testo.html", response_class=HTMLResponse)
def blocco_anteprima(request: Request, tag: str, appello_id: int, numero: int):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    dati = compiti_service.anteprima_blocco(tag, appello_id, numero)
    if dati is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Blocco {numero} non trovato", "error")
    return templates.TemplateResponse(request, "blocco_anteprima.html", {
        "corso": corso, "appello": appello, "numero": numero, "codice": dati["codice"], "righe": dati["righe"],
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/elimina")
def elimina_blocco(tag: str, appello_id: int, numero: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    try:
        compiti_service.elimina_blocco(tag, appello_id, numero)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Blocco {numero} eliminato", anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/tutti.pdf")
def scarica_blocchi_pdf(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    try:
        tex_path = compiti_service.genera_blocchi_uniti(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error",
            anchor=anchor, dettaglio=compilazione.errore_dettagliato or "",
        )
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/griglia.tex", response_class=PlainTextResponse)
def scarica_griglia_tex(tag: str, appello_id: int, numero: int):
    path = compiti_service.path_griglia(tag, appello_id, numero, "tex")
    if not path.exists():
        return PlainTextResponse("Testo non ancora generato", status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/griglia.pdf")
def scarica_griglia_pdf(tag: str, appello_id: int, numero: int):
    path = compiti_service.path_griglia(tag, appello_id, numero, "pdf")
    if not path.exists():
        return PlainTextResponse("PDF non ancora compilato", status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/corsi/{tag}/appelli/{appello_id}/blocchi/{numero}/griglia.html", response_class=HTMLResponse)
def scarica_griglia_html(request: Request, tag: str, appello_id: int, numero: int):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    blocco = compiti_service.get_blocco(tag, appello_id, numero)
    if blocco is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Blocco {numero} non trovato", "error")
    compiti = compiti_service.list_compiti_blocco(tag, blocco["id"])
    return templates.TemplateResponse(request, "griglia_html.html", {
        "corso": corso, "appello": appello, "numero": numero, "compiti": compiti,
    })


def _esito_testo(request: Request, tag: str, appello_id: int, messaggio: str, tipo: str = "success"):
    """Esito di un'azione sugli esercizi assegnati (scheda Testo): se la richiesta arriva
    dal riquadro degli esercizi (fetch con X-Frammento, vedi attivaTesto in app.js) si
    restituisce solo il riquadro ridisegnato, altrimenti si torna alla pagina."""
    appello = corsi_service.get_appello(tag, appello_id)
    if not request.headers.get("x-frammento"):
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", messaggio, tipo, anchor=_anchor_membro(appello, "creazione"))
    corso = corsi_service.get_corso(tag)
    assegnati = esercizi_service.list_esercizi_appello(tag, appello_id)
    assegnati_ids = {e.id for e in assegnati}
    md = {
        "appello": appello, "esercizi_assegnati": assegnati,
        "esercizi_disponibili": [e for e in esercizi_service.list_esercizi(tag) if e.id not in assegnati_ids],
        "argomenti": esercizi_service.list_argomenti(tag), "blocchi": compiti_service.list_blocchi(tag, appello_id),
        "consegna_effettivo": corsi_service.effective_consegna(corso, appello),
    }
    return templates.TemplateResponse(request, "_testo_esercizi.html", {
        "corso": corso, "md": md, "messaggio": messaggio, "tipo": tipo,
        "raggruppamento": corsi_service.get_raggruppamento_by_membro(tag, appello_id),
        "pane_suffix": f"-{appello_id}" if appello.membro_raggruppamento else "",
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/assegna")
async def assegna_esercizio(tag: str, appello_id: int, request: Request, background_tasks: BackgroundTasks):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    form = await request.form()
    esercizio_ids = [int(v) for v in form.getlist("esercizio_ids")]
    obbligatorio = bool(form.get("obbligatorio"))
    if not esercizio_ids:
        return _esito_testo(request, tag, appello_id, "Nessun esercizio selezionato", "error")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        for esercizio_id in esercizio_ids:
            esercizi_service.assegna_a_appello(tag, appello_id, esercizio_id, obbligatorio=obbligatorio)
        msg = f"{len(esercizio_ids)} esercizi assegnati" + _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return _esito_testo(request, tag, appello_id, str(e), "error")
    return _esito_testo(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/rimuovi")
def rimuovi_esercizio(
    request: Request, tag: str, appello_id: int, esercizio_id: int, background_tasks: BackgroundTasks,
    elimina_dalla_banca: str = Form(""),
):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    kind = "success"
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        esercizi_service.rimuovi_da_appello(tag, appello_id, esercizio_id)
        rigenerazione = _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return _esito_testo(request, tag, appello_id, str(e), "error")
    msg = "Esercizio tolto dal compito"
    if elimina_dalla_banca:
        # dopo la rigenerazione: i blocchi di questo appello non lo referenziano più
        try:
            esercizi_service.elimina_esercizio(tag, esercizio_id)
            msg += " ed eliminato dalla banca dati del corso."
        except ValueError:
            msg += (
                ", ma non eliminato dalla banca dati: è ancora assegnato ad altri appelli "
                "o compare in loro compiti già generati."
            )
            kind = "warning"
    else:
        msg += " (resta nella banca dati del corso)."
    msg += rigenerazione
    return _esito_testo(request, tag, appello_id, msg, kind)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/obbligatorio")
def imposta_obbligatorio(
    request: Request, tag: str, appello_id: int, esercizio_id: int, background_tasks: BackgroundTasks,
    obbligatorio: str = Form(""),
):
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        esercizi_service.imposta_obbligatorio(tag, appello_id, esercizio_id, bool(obbligatorio))
        msg = ("Esercizio reso obbligatorio" if obbligatorio else "Esercizio reso facoltativo") + _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return _esito_testo(request, tag, appello_id, str(e), "error")
    return _esito_testo(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/ordine")
async def ordina_esercizi(tag: str, appello_id: int, request: Request):
    """Salva il nuovo ordine degli esercizi assegnati, inviato dalla tabella della scheda
    Testo (trascinamento, frecce, posizione o "Ordina per") senza ricaricare la pagina."""
    dati = await request.json()
    try:
        ordine = [int(i) for i in dati.get("ordine") or []]
        esercizi_service.imposta_ordine(tag, appello_id, ordine)
    except (ValueError, TypeError) as e:
        return JSONResponse({"errore": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/corsi/{tag}/appelli/{appello_id}/testo/chiudi")
def chiudi_testo(tag: str, appello_id: int):
    """Chiude il testo: da qui gli esercizi non si modificano più (neanche dalla banca)
    finché non lo si riapre, e si generano i fogli di riferimento da pubblicare, senza e
    con le soluzioni. Se la compilazione fallisce il testo resta aperto."""
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    url = f"/corsi/{tag}/appelli/{appello_id}"
    try:
        corsi_service.verifica_testo_aperto(tag, appello_id)
        compiti_service.verifica_testo_completo(tag, appello_id)
        percorsi = [compiti_service.riferimento_aggiornato(tag, appello_id, sol)[0] for sol in (False, True)]
    except ValueError as e:
        return flash_redirect(url, str(e), "error", anchor=anchor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        esiti = list(pool.map(compiti_service.compila_pdf, percorsi))
    for p, e in zip(percorsi, esiti):
        if not e.ok:
            return flash_redirect(url, f"Testo non chiuso: {p.name} non compilato ({e.messaggio})", "error",
                                  anchor=anchor, dettaglio=e.errore_dettagliato or "")
    corsi_service.chiudi_testo(tag, appello_id)
    msg = "Testo chiuso e fogli da pubblicare generati (senza e con soluzioni): ora puoi creare i compiti"
    sporgenti = [(p, e) for p, e in zip(percorsi, esiti) if e.sporgenze]
    if sporgenti:
        dettaglio = "\n".join(f"{p.name}: {v}" for p, e in sporgenti for v in e.sporgenze[:5])
        return flash_redirect(url, msg + ". Attenzione: in qualche punto il testo esce dal margine, controlla i PDF.",
                              "warning", anchor=anchor, dettaglio=dettaglio)
    return flash_redirect(url, msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/testo/riapri")
def riapri_testo(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    url = f"/corsi/{tag}/appelli/{appello_id}"
    bloccanti = compiti_service.blocchi_con_risultati(tag, appello_id)
    if bloccanti:
        return flash_redirect(
            url, "Non puoi riaprire il testo: il blocco " + ", ".join(str(n) for n in bloccanti)
            + " ha già risultati registrati, e cambiare gli esercizi invaliderebbe quei compiti.", "error", anchor=anchor)
    try:
        corsi_service.riapri_testo(tag, appello_id)
    except ValueError as e:
        return flash_redirect(url, str(e), "error", anchor=anchor)
    msg = "Testo riaperto: puoi modificare gli esercizi, poi richiudilo"
    if compiti_service.list_blocchi(tag, appello_id):
        msg += " (alla prima modifica i compiti già generati verranno rigenerati con nuovi codici)"
    return flash_redirect(url, msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/nuovo")
async def nuovo_esercizio_appello(tag: str, appello_id: int, request: Request, background_tasks: BackgroundTasks):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    form = await request.form()
    varianti = _parse_varianti_form(form)
    extra = _parse_esercizio_extra_form(form)
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        esercizi_service.crea_e_assegna(
            tag, appello_id, nome=(form.get("nome") or "").strip(),
            note=(form.get("note") or "").strip(), varianti=varianti,
            obbligatorio=bool(form.get("obbligatorio")), argomento=(form.get("argomento") or "").strip(),
            **extra,
        )
        msg = "Esercizio creato e assegnato" + _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return _esito_testo(request, tag, appello_id, str(e), "error")
    return _esito_testo(request, tag, appello_id, msg)


@app.get("/corsi/{tag}/appelli/{appello_id}/esercizi/esporta")
def esporta_esercizi(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    dati = esercizi_service.esporta_esercizi_appello(tag, appello_id)
    contenuto = json.dumps(dati, ensure_ascii=False, indent=2)
    return Response(
        contenuto, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="esercizi-{appello.slug}.json"'},
    )


async def _leggi_esercizi_caricati(file: Optional[UploadFile], testo: str) -> dict:
    """Gli esercizi da importare arrivano da un file (.json di esportazione o .txt nel
    formato testuale descritto nella guida) oppure incollati direttamente nella casella
    di testo: il testo incollato, se presente, ha la precedenza."""
    if testo.strip():
        return esercizi_service.leggi_file_esercizi(testo)
    if file is None or not file.filename:
        raise ValueError("Scegli un file oppure incolla il testo degli esercizi")
    contenuto_bytes = await file.read()
    try:
        contenuto = contenuto_bytes.decode("utf-8")
    except UnicodeDecodeError:
        contenuto = contenuto_bytes.decode("cp1252")
    return esercizi_service.leggi_file_esercizi(contenuto, file.filename)


@app.post("/corsi/{tag}/esercizi/importa-json", response_class=HTMLResponse)
async def importa_esercizi_json_banca(
    request: Request, tag: str, file: Optional[UploadFile] = File(None), testo: str = Form(""),
):
    try:
        contenuto = await _leggi_esercizi_caricati(file, testo)
        candidati = esercizi_service.anteprima_importa_json(tag, contenuto)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", str(e), "error")
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", f"File non valido: {e}", "error")
    if not candidati:
        return flash_redirect(f"/corsi/{tag}/esercizi", "Il file non contiene esercizi", "error")
    corso = corsi_service.get_corso(tag)
    return templates.TemplateResponse(request, "esercizi_importa_conferma.html", {
        "corso": corso, "appello": None, "candidati": candidati,
        "dati_json": json.dumps(contenuto, ensure_ascii=False),
    })


@app.post("/corsi/{tag}/esercizi/importa-json/conferma")
async def importa_esercizi_json_banca_conferma(tag: str, request: Request):
    form = await request.form()
    try:
        contenuto = json.loads(form.get("dati_json") or "{}")
        candidati = esercizi_service.anteprima_importa_json(tag, contenuto)
        indici_scelti = {int(i) for i in form.getlist("importa_idx")}
        scelti = [c for i, c in enumerate(candidati) if i in indici_scelti]
        if not scelti:
            return flash_redirect(f"/corsi/{tag}/esercizi", "Nessun esercizio selezionato per l'import", "error")
        n = esercizi_service.importa_json_banca(tag, scelti)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", str(e), "error")
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", f"Errore import: {e}", "error")
    return flash_redirect(f"/corsi/{tag}/esercizi", f"Importati {n} esercizi dal file")


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/importa-json", response_class=HTMLResponse)
async def importa_esercizi_json(
    request: Request, tag: str, appello_id: int,
    file: Optional[UploadFile] = File(None), testo: str = Form(""),
):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        contenuto = await _leggi_esercizi_caricati(file, testo)
        candidati = esercizi_service.anteprima_importa_json(tag, contenuto)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"File non valido: {e}", "error", anchor=anchor)
    if not candidati:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Il file non contiene esercizi", "error", anchor=anchor)
    corso = corsi_service.get_corso(tag)
    return templates.TemplateResponse(request, "esercizi_importa_conferma.html", {
        "corso": corso, "appello": appello, "candidati": candidati,
        "dati_json": json.dumps(contenuto, ensure_ascii=False),
        "esercizi_compito": esercizi_service.list_esercizi_appello(tag, appello_id),
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/importa-json/conferma")
async def importa_esercizi_json_conferma(request: Request, tag: str, appello_id: int, background_tasks: BackgroundTasks):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    form = await request.form()
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        contenuto = json.loads(form.get("dati_json") or "{}")
        candidati = esercizi_service.anteprima_importa_json(tag, contenuto)
        indici_scelti = {int(i) for i in form.getlist("importa_idx")}
        scelti = [c for i, c in enumerate(candidati) if i in indici_scelti]
        if not scelti:
            return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessun esercizio selezionato per l'import", "error", anchor=anchor)
        # "aggiungi" (default), "sostituisci" (i vecchi restano nella banca) oppure
        # "sostituisci_elimina" (i vecchi vengono eliminati anche dalla banca dati)
        modalita = form.get("modalita") or "aggiungi"
        if modalita in ("sostituisci", "sostituisci_elimina"):
            n, rimossi, tenuti = esercizi_service.sostituisci_con_json(tag, appello_id, scelti)
            msg = f"Importati {n} esercizi dal file, al posto dei {len(rimossi)} esercizi precedenti del compito"
            if tenuti:
                msg += f" ({tenuti} già presenti e identici sono stati mantenuti)"
        else:
            n, rimossi = esercizi_service.importa_json(tag, appello_id, scelti), []
            msg = f"Importati {n} esercizi dal file"
        msg += "." + _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Errore import: {e}", "error", anchor=anchor)
    kind = "success"
    if modalita == "sostituisci":
        msg += " I vecchi esercizi restano nella banca dati del corso."
    elif modalita == "sostituisci_elimina":
        # dopo la rigenerazione: i blocchi di questo appello non li referenziano più
        non_eliminati = 0
        for esercizio_id in rimossi:
            try:
                esercizi_service.elimina_esercizio(tag, esercizio_id)
            except ValueError:
                non_eliminati += 1
        msg += f" Eliminati dalla banca dati {len(rimossi) - non_eliminati} vecchi esercizi"
        if non_eliminati:
            msg += (
                f"; {non_eliminati} restano nella banca perché assegnati ad altri appelli "
                "o presenti in loro compiti già generati"
            )
            kind = "warning"
        msg += "."
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, kind, anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/ia", response_class=HTMLResponse)
def pannello_ia(request: Request, tag: str, appello_id: int):
    """Frammento del pannello "Genera con l'IA" della scheda Testo, caricato solo alla
    sua apertura: cercare le prove di riferimento legge le banche di più corsi."""
    appello = corsi_service.get_appello(tag, appello_id)
    rif = ia_service.riferimenti_precedenti(tag, appello)
    # proposte iniziali: quanti esercizi e quante varianti aveva la prova più recente
    ultima = rif["prove"][0]["esercizi"] if rif["prove"] else []
    n_suggerito = len(ultima) or 10
    varianti_suggerite = round(sum(len(e.varianti) for e in ultima) / len(ultima)) if ultima else 2
    return templates.TemplateResponse(request, "_genera_ia.html", {
        "corso": corsi_service.get_corso(tag), "appello": appello, "riferimenti": rif,
        "n_suggerito": n_suggerito, "varianti_suggerite": min(max(1, varianti_suggerite), 5),
        "fornitore_ia": ia_service.fornitore(), "profili_ia": ia_service.profili(),
    })


@app.get("/corsi/{tag}/appelli/{appello_id}/ia/prompt", response_class=PlainTextResponse)
def prompt_ia(tag: str, appello_id: int, n: int = 10, varianti: int = 2, istruzioni: str = ""):
    """Lo stesso prompt della generazione, come testo da copiare in claude.ai (per chi
    non ha una chiave API): la risposta va poi in "Importa un compito da un unico file"."""
    appello = corsi_service.get_appello(tag, appello_id)
    prompt = ia_service.costruisci_prompt(tag, appello, max(1, n), max(1, varianti), istruzioni)
    return prompt["sistema"] + "\n\n" + "=" * 40 + "\n\n" + prompt["richiesta"]


def _ambito_verifica(tag: str, appello_id: Optional[int] = None, esercizio_id: Optional[int] = None) -> dict:
    """Cosa verificare con l'IA: gli esercizi di un appello, uno solo, o tutta la banca
    del corso; con l'indirizzo della pagina e quello a cui tornare per correggere."""
    corso = corsi_service.get_corso(tag)
    if esercizio_id is not None:
        e = esercizi_service.get_esercizio(tag, esercizio_id)
        if e is None:
            raise ValueError("Esercizio non trovato")
        return {"esercizi": [e], "titolo": f"Esercizio «{e.nome or '#' + str(e.id)}»",
                "contesto": "Banca esercizi del corso", "url": f"/corsi/{tag}/esercizi/{esercizio_id}/ia/verifica",
                "torna": f"/corsi/{tag}/esercizi", "torna_etichetta": "Banca esercizi", "corso": corso, "appello": None,
                "filtro_salvate": {"esercizio_id": esercizio_id}}
    if appello_id is not None:
        appello = corsi_service.get_appello(tag, appello_id)
        return {"esercizi": esercizi_service.list_esercizi_appello(tag, appello_id), "titolo": f"Compito: {appello.nome}",
                "contesto": f"Prova: {appello.nome}" + (f" del {appello.data}" if appello.data else ""),
                "url": f"/corsi/{tag}/appelli/{appello_id}/ia/verifica",
                "torna": f"/corsi/{tag}/appelli/{appello_id}#{_anchor_membro(appello, 'creazione')}",
                "torna_etichetta": appello.nome, "corso": corso, "appello": appello, "filtro_salvate": {"appello_id": appello_id}}
    return {"esercizi": esercizi_service.list_esercizi(tag), "titolo": "Banca esercizi del corso",
            "contesto": "Banca esercizi del corso", "url": f"/corsi/{tag}/esercizi/ia/verifica",
            "torna": f"/corsi/{tag}/esercizi", "torna_etichetta": "Banca esercizi", "corso": corso, "appello": None,
            "filtro_salvate": {}}


def _da_verificare(tag: str, esercizi: list, stati: dict) -> list:
    """Gli esercizi mai verificati o modificati dopo l'ultima verifica."""
    return [e for e in esercizi if stati[e.id]["stato"] in ("mai", "modificato")]


def _pagina_verifica_ia(request: Request, tag: str, ambito: dict, **extra):
    stati = esercizi_service.stati_verifica(tag, ambito["esercizi"])
    return templates.TemplateResponse(request, "verifica_ia.html", {
        **ambito, "stati": stati, "da_verificare": _da_verificare(tag, ambito["esercizi"], stati),
        "fornitore_ia": ia_service.fornitore(extra.pop("profilo", "")), "profili_ia": ia_service.profili(),
        "verifiche_salvate": verifiche_ia_service.elenco(tag, **ambito["filtro_salvate"]), "tag": tag, **extra,
    })


def _esegui_verifica(request: Request, tag: str, ambito: dict, profilo: str, risposta: str, solo_da_verificare: str):
    """Avvia la verifica (o legge la risposta incollata da claude.ai), salva l'esito di
    ogni esercizio verificato — così i corretti non vanno ricontrollati — e mostra i risultati."""
    stati = esercizi_service.stati_verifica(tag, ambito["esercizi"])
    esercizi = _da_verificare(tag, ambito["esercizi"], stati) if solo_da_verificare else ambito["esercizi"]
    if not esercizi:
        return _pagina_verifica_ia(request, tag, ambito, errore="Non c'è nessun esercizio da verificare: sono già "
                                   "tutti verificati e non modificati da allora. Togli la spunta per ricontrollarli comunque.")
    try:
        if risposta.strip():
            # la risposta incollata si riferisce agli esercizi del prompt copiato: stessi criteri
            risultati = ia_service.leggi_risposta_verifica(esercizi, risposta)
            conf = {"nome": "risposta incollata", "modello": "claude.ai"}
        else:
            esito = ia_service.verifica(tag, esercizi, ambito["contesto"], profilo_id=profilo)
            risultati, conf = esito["esercizi"], esito["conf"]
    except ValueError as e:
        return _pagina_verifica_ia(request, tag, ambito, errore=str(e), profilo=profilo, risposta_incollata=risposta)
    for r in risultati:
        esercizi_service.registra_verifica(tag, r["esercizio"], r["esito"], ia_service.risultato_serializzabile(r))
    conteggi = {k: sum(1 for r in risultati if r["esito"] == k) for k in ia_service.ESITI_VERIFICA}
    try:
        salvata = verifiche_ia_service.salva(tag, ambito, risultati, conf, conteggi)
    except OSError:
        salvata = None
    registro_service.registra(
        "ia", f"Verifica con l'IA: {ambito['titolo']}", tag=tag, appello_id=ambito["appello"].id if ambito["appello"] else None,
        esito=f"{len(risultati)} esercizi: {conteggi['ok']} corretti, {conteggi['dubbio']} dubbi, {conteggi['errore']} errori ({conf['nome']})",
    )
    return _pagina_verifica_ia(request, tag, ambito, risultati=risultati, conf_usata=conf, conteggi=conteggi, profilo=profilo,
                               salvata_id=salvata)


@app.get("/corsi/{tag}/ia/verifiche/{verifica_id}", response_class=HTMLResponse)
def verifica_ia_salvata(request: Request, tag: str, verifica_id: str):
    """Una verifica salvata, com'era al momento, con accanto lo stato attuale di ogni
    esercizio (corretto nel frattempo, modificato, eliminato)."""
    dati = verifiche_ia_service.carica(tag, verifica_id)
    if dati is None:
        return flash_redirect(f"/corsi/{tag}/esercizi", "Verifica salvata non trovata", "error")
    esistenti = esercizi_service.esercizi_per_id(tag, [r["esercizio"]["id"] for r in dati["risultati"]])
    stati = esercizi_service.stati_verifica(tag, list(esistenti.values()))
    corso = corsi_service.get_corso(tag)
    return templates.TemplateResponse(request, "verifica_ia.html", {
        "corso": corso, "appello": None, "tag": tag, "titolo": dati["titolo"], "contesto": dati["contesto"],
        "url": dati["url"], "torna": dati["torna"], "torna_etichetta": dati["torna_etichetta"],
        "risultati": dati["risultati"], "conf_usata": dati["conf"], "conteggi": dati["conteggi"],
        "salvata": dati, "salvata_id": verifica_id, "stati_attuali": stati,
        "fornitore_ia": ia_service.fornitore(), "profili_ia": ia_service.profili(),
        "verifiche_salvate": [], "stati": {}, "esercizi": [], "da_verificare": [],
    })


@app.get("/corsi/{tag}/ia/verifiche/{verifica_id}.json")
def scarica_verifica_ia(tag: str, verifica_id: str):
    contenuto = verifiche_ia_service.contenuto_file(tag, verifica_id)
    if contenuto is None:
        return PlainTextResponse("Verifica salvata non trovata", status_code=404)
    return Response(contenuto, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="verifica-ia-{tag}-{verifica_id}.json"'})


@app.post("/corsi/{tag}/ia/verifiche/{verifica_id}/elimina")
def elimina_verifica_ia(tag: str, verifica_id: str, torna: str = Form("")):
    dati = verifiche_ia_service.carica(tag, verifica_id)
    torna = torna or (dati["url"] if dati else f"/corsi/{tag}/esercizi")
    if not verifiche_ia_service.elimina(tag, verifica_id):
        return flash_redirect(torna, "Verifica salvata non trovata", "error")
    return flash_redirect(torna, "Verifica salvata eliminata (gli esiti degli esercizi restano)")


@app.get("/corsi/{tag}/appelli/{appello_id}/ia/verifica", response_class=HTMLResponse)
def pagina_verifica_ia(request: Request, tag: str, appello_id: int):
    return _pagina_verifica_ia(request, tag, _ambito_verifica(tag, appello_id=appello_id))


@app.post("/corsi/{tag}/appelli/{appello_id}/ia/verifica", response_class=HTMLResponse)
def esegui_verifica_ia(request: Request, tag: str, appello_id: int, profilo: str = Form(""), risposta: str = Form(""),
                       solo_da_verificare: str = Form("")):
    return _esegui_verifica(request, tag, _ambito_verifica(tag, appello_id=appello_id), profilo, risposta, solo_da_verificare)


@app.get("/corsi/{tag}/esercizi/ia/verifica", response_class=HTMLResponse)
def pagina_verifica_banca(request: Request, tag: str):
    return _pagina_verifica_ia(request, tag, _ambito_verifica(tag))


@app.post("/corsi/{tag}/esercizi/ia/verifica", response_class=HTMLResponse)
def esegui_verifica_banca(request: Request, tag: str, profilo: str = Form(""), risposta: str = Form(""),
                          solo_da_verificare: str = Form("")):
    return _esegui_verifica(request, tag, _ambito_verifica(tag), profilo, risposta, solo_da_verificare)


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/ia/verifica", response_class=HTMLResponse)
def pagina_verifica_esercizio(request: Request, tag: str, esercizio_id: int):
    return _pagina_verifica_ia(request, tag, _ambito_verifica(tag, esercizio_id=esercizio_id))


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/ia/verifica", response_class=HTMLResponse)
def esegui_verifica_esercizio(request: Request, tag: str, esercizio_id: int, profilo: str = Form(""),
                              risposta: str = Form(""), solo_da_verificare: str = Form("")):
    return _esegui_verifica(request, tag, _ambito_verifica(tag, esercizio_id=esercizio_id), profilo, risposta, solo_da_verificare)


def _prompt_verifica_testo(tag: str, ambito: dict, solo_da_verificare: str) -> PlainTextResponse:
    stati = esercizi_service.stati_verifica(tag, ambito["esercizi"])
    esercizi = _da_verificare(tag, ambito["esercizi"], stati) if solo_da_verificare else ambito["esercizi"]
    try:
        prompt = ia_service.costruisci_prompt_verifica(tag, esercizi, ambito["contesto"])
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)
    return PlainTextResponse(prompt["sistema"] + "\n\n" + "=" * 40 + "\n\n" + prompt["richiesta"])


@app.get("/corsi/{tag}/appelli/{appello_id}/ia/verifica/prompt", response_class=PlainTextResponse)
def prompt_verifica_ia(tag: str, appello_id: int, solo_da_verificare: str = ""):
    """Lo stesso prompt della verifica, da copiare in claude.ai senza chiave API."""
    return _prompt_verifica_testo(tag, _ambito_verifica(tag, appello_id=appello_id), solo_da_verificare)


@app.get("/corsi/{tag}/esercizi/ia/verifica/prompt", response_class=PlainTextResponse)
def prompt_verifica_banca(tag: str, solo_da_verificare: str = ""):
    return _prompt_verifica_testo(tag, _ambito_verifica(tag), solo_da_verificare)


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/ia/verifica/prompt", response_class=PlainTextResponse)
def prompt_verifica_esercizio(tag: str, esercizio_id: int, solo_da_verificare: str = ""):
    return _prompt_verifica_testo(tag, _ambito_verifica(tag, esercizio_id=esercizio_id), solo_da_verificare)


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/verificato")
def segna_verificato(tag: str, esercizio_id: int, torna: str = Form("")):
    """Il docente ha ricontrollato l'esercizio (es. il dubbio dell'IA era infondato): lo
    segna come verificato, finché non viene modificato."""
    e = esercizi_service.get_esercizio(tag, esercizio_id)
    if e is None:
        return flash_redirect(torna or f"/corsi/{tag}/esercizi", "Esercizio non trovato", "error")
    esercizi_service.registra_verifica(tag, e, "ok", origine="manuale")
    return flash_redirect(torna or f"/corsi/{tag}/esercizi", f"Esercizio «{e.nome or e.id}» segnato come verificato")


# --- Correzione con l'IA: proposta, confronto, conferma ------------------------------------

def _pagina_correzione(request: Request, tag: str, esercizio_id: int, torna: str, **extra):
    e = esercizi_service.get_esercizio(tag, esercizio_id)
    stato = esercizi_service.stati_verifica(tag, [e])[e.id]
    return templates.TemplateResponse(request, "correzione_ia.html", {
        "corso": corsi_service.get_corso(tag), "esercizio": e, "stato": stato, "torna": torna or f"/corsi/{tag}/esercizi",
        "fornitore_ia": ia_service.fornitore(extra.pop("profilo", "")), "profili_ia": ia_service.profili(),
        "appello": None, **extra,
    })


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/ia/correggi", response_class=HTMLResponse)
def pagina_correzione_ia(request: Request, tag: str, esercizio_id: int, torna: str = ""):
    return _pagina_correzione(request, tag, esercizio_id, torna)


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/ia/correggi/prompt", response_class=PlainTextResponse)
def prompt_correzione_ia(tag: str, esercizio_id: int):
    e = esercizi_service.get_esercizio(tag, esercizio_id)
    stato = esercizi_service.stati_verifica(tag, [e])[e.id]
    prompt = ia_service.costruisci_prompt_correzione(e, stato.get("dettaglio") or {})
    return PlainTextResponse(prompt["sistema"] + "\n\n" + "=" * 40 + "\n\n" + prompt["richiesta"])


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/ia/correggi", response_class=HTMLResponse)
def proponi_correzione_ia(request: Request, tag: str, esercizio_id: int, torna: str = Form(""),
                          profilo: str = Form(""), risposta: str = Form(""), modifica: str = Form("")):
    """Chiede all'IA (o legge dalla risposta incollata di claude.ai) la versione corretta
    dell'esercizio e mostra il confronto: non salva nulla finché il docente non conferma."""
    e = esercizi_service.get_esercizio(tag, esercizio_id)
    stato = esercizi_service.stati_verifica(tag, [e])[e.id]
    try:
        if risposta.strip():
            proposta = ia_service.leggi_correzione(risposta)
        else:
            proposta = ia_service.proponi_correzione(e, stato.get("dettaglio") or {}, profilo_id=profilo)
    except ValueError as err:
        return _pagina_correzione(request, tag, esercizio_id, torna, errore=str(err), profilo=profilo, risposta_incollata=risposta)
    return _pagina_correzione(request, tag, esercizio_id, torna, proposta=proposta, profilo=profilo,
                              proposta_json=json.dumps(proposta, ensure_ascii=False),
                              proposta_testo=ia_service.proposta_come_testo(proposta),
                              modifiche=ia_service.modifiche_proposte(e, proposta),
                              confronto=ia_service.confronto_correzione(e, proposta),
                              modificata=bool(risposta.strip()) and modifica == "1",
                              firma_attuale=esercizi_service.firma_contenuto(e))


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/ia/correggi/conferma")
def conferma_correzione_ia(tag: str, esercizio_id: int, background_tasks: BackgroundTasks, proposta_json: str = Form(...),
                           firma_attuale: str = Form(""), torna: str = Form("")):
    """Salva la correzione accettata (come una modifica fatta a mano: gli appelli che
    usano l'esercizio vengono aggiornati) e segna l'esercizio come verificato."""
    destinazione = torna or f"/corsi/{tag}/esercizi"
    e = esercizi_service.get_esercizio(tag, esercizio_id)
    if e is None:
        return flash_redirect(destinazione, "Esercizio non trovato", "error")
    if firma_attuale and firma_attuale != esercizi_service.firma_contenuto(e):
        return flash_redirect(destinazione, "L'esercizio è stato modificato dopo la proposta: richiedi una nuova correzione", "error")
    proposta = json.loads(proposta_json)
    appelli_coinvolti = esercizi_service.appelli_che_usano(tag, esercizio_id)
    try:
        for appello_id in appelli_coinvolti:
            _proteggi_modifica_esercizi(tag, appello_id)
        for appello_id in appelli_coinvolti:
            compiti_service.svuota_blocchi_appello(tag, appello_id)
        aggiornato = esercizi_service.aggiorna_esercizio(
            tag, esercizio_id, nome=e.nome or proposta.get("nome") or "", note=e.note or "",
            argomento=e.argomento or proposta.get("argomento") or "", varianti=proposta["varianti"],
            difficolta=e.difficolta, soluzione=proposta.get("soluzione") or "", aperta=bool(proposta.get("aperta")),
        )
        esercizi_service.registra_verifica(tag, aggiornato, "ok", origine="correzione")
        msg = f"Correzione dell'esercizio «{e.nome or e.id}» salvata e segnata come verificata"
        for appello_id in appelli_coinvolti:
            msg += _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as err:
        return flash_redirect(destinazione, str(err), "error")
    return flash_redirect(destinazione, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/ia/genera", response_class=HTMLResponse)
def genera_ia(
    request: Request, tag: str, appello_id: int,
    n: int = Form(10), varianti: int = Form(2), istruzioni: str = Form(""), profilo: str = Form(""),
):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        n, varianti = min(max(1, n), 30), min(max(1, varianti), 5)
        contenuto = ia_service.genera(tag, appello, n, varianti, istruzioni, profilo_id=profilo)
        candidati = esercizi_service.anteprima_importa_json(tag, contenuto)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    if not candidati:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Claude non ha prodotto esercizi", "error", anchor=anchor)
    # stessa conferma dell'import da file: il docente sceglie quali esercizi tenere
    return templates.TemplateResponse(request, "esercizi_importa_conferma.html", {
        "corso": corsi_service.get_corso(tag), "appello": appello, "candidati": candidati,
        "dati_json": json.dumps(contenuto, ensure_ascii=False), "generati_ia": True,
        "esercizi_compito": esercizi_service.list_esercizi_appello(tag, appello_id),
    })


def _render_correggi_revisione(
    request: Request, tag: str, appello_id: int, valutazione, voto_proposto=None, punteggi_proposti=None,
    errore: str = "", richiedi_orale_checked=None, modifica: bool = False, note_proposte=None,
    nota_correzione: str = "", voto_a_mano: bool = False, rimandati=None, orale_motivazione: str = "",
):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    manuali = sorted(valutazione.da_valutare + valutazione.domande_aperte, key=lambda r: r.posizione)
    if punteggi_proposti is None:
        # correzione nuova: un obbligatorio con la risposta giusta parte dal punteggio pieno
        # (basta confermare se lo svolgimento è a posto), una domanda aperta da vuoto
        punteggi_proposti = {} if modifica else {r.posizione: corso.risposta_corretta for r in valutazione.da_valutare}
    if rimandati is None:
        rimandati = (
            {r.posizione for r in manuali} if appello.rimanda_svolgimenti and not modifica else set()
        )
    if richiedi_orale_checked is None:
        richiedi_orale_checked = bool(valutazione.orale_obbligatorio)

    avviso_idoneita = None
    if appello.membro_raggruppamento:
        raggruppamento, indice = _membro_e_indice(tag, appello_id)
        if raggruppamento is not None and indice > 0:
            ammessi = {s["matricola"] for s in corsi_service.list_ammessi_prova(tag, raggruppamento, indice)}
            if valutazione.matricola not in ammessi:
                avviso_idoneita = (
                    f"Attenzione: questo studente non risulta tra gli ammessi a questa prova (non "
                    f"risulta aver superato {raggruppamento.membri[indice - 1].nome} con il voto minimo "
                    "delle prove parziali). Puoi comunque salvare la correzione."
                )

    return templates.TemplateResponse(request, "correggi_revisione.html", {
        "corso": corso, "appello": appello, "v": valutazione, "voto_proposto": voto_proposto,
        "voto_a_mano": voto_a_mano and voto_proposto is not None,
        "punteggi_proposti": punteggi_proposti, "note_proposte": note_proposte or {},
        "nota_correzione": nota_correzione or "", "rimandati": rimandati, "errore": errore,
        "richiedi_orale_checked": richiedi_orale_checked, "orale_motivazione": orale_motivazione,
        "modifica": modifica, "manuali": manuali,
        "note_precedenti": correzione_service.note_precedenti(tag, appello_id, valutazione.matricola) if manuali else {},
        "votomin_effettivo": corsi_service.effective_votomin(corso, appello),
        "avviso_idoneita": avviso_idoneita,
    })


def _errore_non_iscritto(tag: str, appello_id: int, matricola: str) -> Optional[str]:
    """Se per questo appello è stato caricato un elenco iscritti (file della segreteria,
    scheda "Compiti d'esame"), uno studente che non vi compare non può essere corretto o
    segnato assente/ritirato: a differenza dell'avviso di idoneità fra prove di un
    raggruppamento (solo informativo), qui il blocco è netto."""
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is None:
        return None
    if matricola in {s["matricola"] for s in iscritti}:
        return None
    return (
        f"Lo studente con matricola {matricola} non risulta nell'elenco iscritti caricato per "
        "questo appello (scheda 'Compiti d'esame')."
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/correggi")
async def correggi(
    request: Request, tag: str, appello_id: int, matricola: str = Form(...), codice: str = Form(...),
    risposte: str = Form(...), modifica: str = Form(""),
):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)
    risposte = risposte.strip().replace(" ", "").upper()
    try:
        valutazione = correzione_service.valuta_preliminare(tag, appello_id, matricola, codice, risposte)
    except correzione_service.StudenteNonTrovato as e:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor,
            extra={"matricola_da_registrare": matricola},
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    # "Ricalcola" dal riepilogo: punteggi e note già scritti restano (le posizioni sono
    # quelle del compito, non cambiano correggendo una lettera)
    form = await request.form()
    if "nota_correzione" not in form:
        return _render_correggi_revisione(request, tag, appello_id, valutazione, modifica=bool(modifica))
    manuali = valutazione.da_valutare + valutazione.domande_aperte
    punteggi, note, rimandati = {}, {}, set()
    for r in manuali:
        note[r.posizione] = form.get(f"nota_{r.posizione}") or ""
        valore = (form.get(f"punteggio_{r.posizione}") or "").strip()
        if form.get(f"rimanda_{r.posizione}"):
            rimandati.add(r.posizione)
        if valore.lstrip("-").isdigit():
            punteggi[r.posizione] = int(valore)
    return _render_correggi_revisione(
        request, tag, appello_id, valutazione, modifica=bool(modifica), punteggi_proposti=punteggi,
        note_proposte=note, rimandati=rimandati, nota_correzione=form.get("nota_correzione") or "",
        orale_motivazione=form.get("orale_motivazione") or "",
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/correggi/conferma")
async def correggi_conferma(request: Request, tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    form = await request.form()
    matricola = form.get("matricola") or ""
    codice = form.get("codice") or ""
    risposte = (form.get("risposte") or "").strip().replace(" ", "").upper()
    azione = form.get("azione") or "salva"
    orale_motivazione = (form.get("orale_motivazione") or "").strip()
    conferma_orale_obbligatorio = bool(form.get("conferma_orale_obbligatorio"))
    modifica = bool(form.get("modifica"))
    nota_correzione = (form.get("nota_correzione") or "").strip()

    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)

    try:
        valutazione = correzione_service.valuta_preliminare(tag, appello_id, matricola, codice, risposte)
    except correzione_service.StudenteNonTrovato as e:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor,
            extra={"matricola_da_registrare": matricola},
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)

    # "rimanda anche tutti i prossimi" (o il suo contrario) scelto in questa pagina
    if form.get("imposta_rimanda") in ("0", "1"):
        corsi_service.update_appello(tag, appello_id, rimanda_svolgimenti=form.get("imposta_rimanda") == "1")

    # svolgimenti: ogni posizione da giudicare ha un punteggio, o è rimandata (spunta
    # "dopo", o campo vuoto: il servizio la tratta comunque come rimandata)
    punteggi, note, rimandati = {}, {}, set()
    errore = ""
    for r in valutazione.da_valutare + valutazione.domande_aperte:
        note[r.posizione] = form.get(f"nota_{r.posizione}") or ""
        valore = (form.get(f"punteggio_{r.posizione}") or "").strip()
        if form.get(f"rimanda_{r.posizione}") or valore == "":
            rimandati.add(r.posizione)
            continue
        try:
            punteggi[r.posizione] = int(valore)
        except ValueError:
            errore = f"Punteggio non valido per {r.esercizio_nome}: \"{valore}\""
    voto_str = (form.get("voto_finale") or "").strip()
    voto_a_mano = bool(form.get("voto_modificato")) and voto_str != ""
    try:
        voto_finale = int(voto_str) if voto_a_mano else None
    except ValueError:
        errore, voto_finale, voto_a_mano = f"Voto finale non valido: \"{voto_str}\"", None, False

    richiedi_orale = azione == "richiedi_orale"

    def riproponi(msg: str):
        return _render_correggi_revisione(
            request, tag, appello_id, valutazione, voto_proposto=voto_finale, punteggi_proposti=punteggi,
            errore=msg, richiedi_orale_checked=richiedi_orale, modifica=modifica, note_proposte=note,
            nota_correzione=nota_correzione, voto_a_mano=voto_a_mano, rimandati=rimandati,
            orale_motivazione=orale_motivazione,
        )

    if errore:
        return riproponi(errore)
    try:
        result = correzione_service.conferma_risultato(
            tag, appello_id, matricola, codice, risposte, voto_finale=voto_finale,
            punteggi_obbligatori=punteggi, richiedi_orale=richiedi_orale,
            orale_motivazione=orale_motivazione, conferma_orale_obbligatorio=conferma_orale_obbligatorio,
            modifica=modifica, note_svolgimenti=note, nota_correzione=nota_correzione,
        )
    except correzione_service.OraleObbligatorioNonConfermato as e:
        richiedi_orale = True  # si ripropone spuntato: togliere la spunta chiede la conferma
        return riproponi(
            f"Questo studente deve fare l'orale in ogni appello (imposto in \"{e.origine}\": {e.motivazione}). "
            "Conferma con \"Richiedi l'orale\" spuntato, oppure togli la spunta per salvare comunque un voto scritto."
        )
    except (correzione_service.OraleNonConsentito, correzione_service.RisultatoGiaValutato, ValueError) as e:
        return riproponi(str(e))
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)

    if result.valutazione_sospesa:
        n = result.svolgimenti_rimandati
        msg = (
            f"{result.cognome} {result.nome} registrato con {n} svolgiment{'o' if n == 1 else 'i'} da valutare "
            f"(voto provvisorio: {result.voto}): li trovi in \"Svolgimenti\""
        )
    elif result.richiede_orale:
        msg = f"Orale richiesto per {result.cognome} {result.nome} (voto scritto di riferimento: {result.voto})"
    else:
        msg = f"{result.cognome} {result.nome}: voto {result.voto}"
        if result.insufficiente_per_obbligatorio:
            msg += " (insufficiente: esercizio obbligatorio non svolto)"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/rimanda-svolgimenti")
def imposta_rimanda_svolgimenti(tag: str, appello_id: int, attivo: str = Form("")):
    appello = corsi_service.get_appello(tag, appello_id)
    corsi_service.update_appello(tag, appello_id, rimanda_svolgimenti=bool(attivo))
    msg = ("Svolgimenti rimandati: li valuterai dopo, esercizio per esercizio, dalla pagina \"Svolgimenti\""
           if attivo else "Svolgimenti da valutare compito per compito, durante la correzione")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=_anchor_membro(appello, "valutazione"))


@app.get("/corsi/{tag}/appelli/{appello_id}/svolgimenti", response_class=HTMLResponse)
def pagina_svolgimenti(request: Request, tag: str, appello_id: int, esercizio: str = "", solo: str = ""):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    dati = correzione_service.svolgimenti_appello(tag, appello_id)
    return templates.TemplateResponse(request, "svolgimenti.html", {
        "corso": corso, "appello": appello, "dati": dati, "esercizio_scelto": esercizio,
        "solo_da_valutare": bool(solo), "anchor": _anchor_membro(appello, "valutazione"),
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/svolgimenti/valuta")
def valuta_svolgimento(
    tag: str, appello_id: int, matricola: str = Form(...), posizione: int = Form(...),
    punteggio: str = Form(""), nota: str = Form(""),
):
    try:
        esito = correzione_service.valuta_svolgimento(
            tag, appello_id, matricola, posizione,
            int(punteggio) if punteggio.strip() else None, nota,
        )
    except Exception as e:
        return JSONResponse({"errore": str(e)}, status_code=400)
    return JSONResponse(esito)


@app.post("/corsi/{tag}/appelli/{appello_id}/correggi/bozza")
def correggi_salva_bozza(
    tag: str, appello_id: int, matricola: str = Form(...), codice: str = Form(...), risposte: str = Form(...),
):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    correzione_service.salva_bozza(tag, appello_id, matricola, codice, risposte.strip().replace(" ", "").upper())
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Bozza salvata: puoi riprenderla più tardi", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/correggi/bozza/{matricola}/elimina")
def correggi_elimina_bozza(tag: str, appello_id: int, matricola: str):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    correzione_service.elimina_bozza(tag, appello_id, matricola)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Bozza eliminata", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/orale/{matricola}/completa")
def completa_orale(tag: str, appello_id: int, matricola: str, esito_orale: str = Form(...), voto: str = Form("")):
    try:
        correzione_service.completa_orale(
            tag, appello_id, matricola, esito_orale, voto=int(voto) if voto.strip() else None,
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor="orali")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Esito dell'orale registrato", anchor="orali")


@app.post("/corsi/{tag}/appelli/{appello_id}/assenze/segna")
def segna_assenza(request: Request, tag: str, appello_id: int, matricola: str = Form(...), esito: str = Form(...)):
    # risponde in JSON alla chiamata fetch dalla scheda "Presenze e ritirati" (bottone
    # "Assente" per riga, aggiornato sul posto), con il solito redirect per il form della
    # scheda "Segna ritirato" e come fallback se il fetch lato client fallisse.
    vuole_json = "application/json" in request.headers.get("accept", "")
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        if vuole_json:
            return JSONResponse({"errore": errore_iscritto}, status_code=400)
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)
    try:
        correzione_service.segna_esito_speciale(tag, appello_id, matricola, esito)
    except Exception as e:
        if vuole_json:
            return JSONResponse({"errore": str(e)}, status_code=400)
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    if vuole_json:
        return JSONResponse({"esito": esito})
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Studente segnato come {esito}", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/annulla-compito")
def annulla_compito(
    tag: str, appello_id: int, matricola: str = Form(...), nota: str = Form(""),
    orale_obbligatorio: str = Form(""), torna: str = Form(""),
):
    appello = corsi_service.get_appello(tag, appello_id)
    # anche per cambiare il motivo di un annullamento dalla tabella dei risultati
    anchor = _anchor_membro(appello, "valutazione" if torna == "valutazione" else "presenze")
    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)
    try:
        correzione_service.annulla_compito(
            tag, appello_id, matricola, nota=nota, rendi_orale_obbligatorio=bool(orale_obbligatorio),
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    msg = f"Motivo dell'annullamento di {matricola} aggiornato" if torna == "valutazione" else f"Compito di {matricola} annullato"
    if orale_obbligatorio:
        msg += ": l'orale sarà obbligatorio negli appelli successivi"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati/{matricola}", response_class=HTMLResponse)
def dettaglio_risultato(request: Request, tag: str, appello_id: int, matricola: str):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    try:
        dettaglio = correzione_service.dettaglio_risultato(tag, appello_id, matricola)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=_anchor_membro(appello, "valutazione"))
    return templates.TemplateResponse(request, "risultato_dettaglio.html", {
        "corso": corso, "appello": appello, "risultato": dettaglio["risultato"], "righe": dettaglio["righe"],
        "storico": dettaglio["storico"], "chiuso": appello.chiuso,
        "dettaglio_esercizi_disponibile": dettaglio["dettaglio_esercizi_disponibile"],
    })


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati/{matricola}/dettaglio-inline", response_class=HTMLResponse)
def dettaglio_risultato_inline(request: Request, tag: str, appello_id: int, matricola: str):
    """Frammento HTML riusato dalla tendina a scomparsa nella tabella dei risultati:
    caricato via fetch solo alla prima apertura, invece di navigare a una pagina
    separata."""
    try:
        dettaglio = correzione_service.dettaglio_risultato(tag, appello_id, matricola)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=404)
    appello = corsi_service.get_appello(tag, appello_id)
    return templates.TemplateResponse(request, "_risultato_dettaglio.html", {
        "corso_tag": tag, "risultato": dettaglio["risultato"], "righe": dettaglio["righe"],
        "storico": dettaglio["storico"], "chiuso": appello.chiuso,
        "dettaglio_esercizi_disponibile": dettaglio["dettaglio_esercizi_disponibile"],
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/risultati/{matricola}/elimina")
def elimina_risultato(tag: str, appello_id: int, matricola: str):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    try:
        correzione_service.elimina_risultato(tag, appello_id, matricola)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Risultato eliminato", anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati/{matricola}/modifica", response_class=HTMLResponse)
def modifica_risultato_form(request: Request, tag: str, appello_id: int, matricola: str):
    try:
        dettaglio = correzione_service.dettaglio_risultato(tag, appello_id, matricola)
        r = dettaglio["risultato"]
        if r["verbalizzato"]:
            raise ValueError("Non è possibile modificare un risultato già verbalizzato")
        if r["codice"] is None:
            raise ValueError("Questo risultato non è associato a un compito con codice (es. calcolato da un raggruppamento)")
        valutazione = correzione_service.valuta_preliminare(tag, appello_id, matricola, r["codice"], r["risposte"] or "")
    except Exception as e:
        appello = corsi_service.get_appello(tag, appello_id)
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=_anchor_membro(appello, "valutazione"))
    punteggi_proposti = {int(k): v for k, v in json.loads(r["punteggi_obbligatori"] or "{}").items()}
    note_proposte = {int(k): v for k, v in json.loads(r["note_svolgimenti"] or "{}").items()}
    voto_scritto = r["voto_scritto"] if r["voto_scritto"] is not None else r["voto"]
    voto_a_mano = bool(r["voto_modificato"]) and not r["valutazione_sospesa"] and voto_scritto is not None
    return _render_correggi_revisione(
        request, tag, appello_id, valutazione, voto_proposto=voto_scritto if voto_a_mano else None,
        punteggi_proposti=punteggi_proposti, modifica=True, note_proposte=note_proposte,
        nota_correzione=r["nota_correzione"] or "", voto_a_mano=voto_a_mano,
        richiedi_orale_checked=bool(r["richiede_orale"]) or None, orale_motivazione=r["orale_motivazione"] or "",
    )


def _genera_risultati_cache(tag: str, appello_id: int) -> None:
    """Genera e compila l'elenco risultati (.tex/.pdf), una volta sola: chiamata quando
    l'appello si chiude, invece che a ogni singolo download (la compilazione LaTeX del
    PDF non è istantanea, e mentre le correzioni sono ancora in corso l'elenco cambia
    comunque a ogni compito corretto)."""
    tex = risultati_service.stampa_risultati(tag, appello_id)
    tex_path = compiti_service.path_risultati(tag, appello_id, "tex")
    tex_path.write_text(tex, encoding="utf-8")
    compiti_service.compila_pdf(tex_path)


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.tex", response_class=PlainTextResponse)
def scarica_risultati(tag: str, appello_id: int):
    path = compiti_service.path_risultati(tag, appello_id, "tex")
    if not path.exists():
        return PlainTextResponse(
            "Elenco non ancora generato: si genera automaticamente quando concludi la correzione scritta "
            "(o chiudi l'appello)", status_code=404,
        )
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.pdf")
def scarica_risultati_pdf(tag: str, appello_id: int):
    path = compiti_service.path_risultati(tag, appello_id, "pdf")
    if not path.exists():
        return PlainTextResponse(
            "PDF non ancora generato: si genera automaticamente quando concludi la correzione scritta "
            "(o chiudi l'appello)", status_code=404,
        )
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.html", response_class=HTMLResponse)
def risultati_anteprima(request: Request, tag: str, appello_id: int):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    if not appello.correzione_scritti_conclusa:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}",
            "Elenco non ancora generato: concludi prima la correzione scritta (o chiudi l'appello)", "error",
            anchor=_anchor_correzione(appello),
        )
    righe = risultati_service.lista_risultati_html(tag, appello_id)
    return templates.TemplateResponse(request, "risultati_anteprima.html", {
        "corso": corso, "appello": appello, "righe": righe,
    })


def _anchor_esporta(tag: str, appello) -> str:
    """Come _anchor_membro, ma per le route condivise fra la scheda Compiti d'esame di
    una singola prova e la scheda Risultati globale di un raggruppamento (che non ha una
    propria scheda Compiti d'esame: il file della segreteria per l'export combinato si
    carica direttamente lì)."""
    if appello.membro_raggruppamento:
        return f"compiti-{appello.id}"
    if corsi_service.get_raggruppamento_by_appello(tag, appello.id):
        return "risultati"
    return "compiti"


def _risposta_iscritti(request: Request, tag: str, appello_id: int, messaggio: str, tipo: str = "success", anchor: str = ""):
    """Esito di un'azione sull'elenco iscritti: se la richiesta arriva dal riquadro
    dell'elenco (fetch con X-Frammento, vedi attivaIscritti) si restituisce solo il riquadro
    ridisegnato, altrimenti si torna alla pagina dell'appello."""
    appello = corsi_service.get_appello(tag, appello_id)
    if not request.headers.get("x-frammento"):
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", messaggio, tipo, anchor=anchor or _anchor_membro(appello, "compiti"),
        )
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    numero = len(iscritti) if iscritti is not None else appello.iscritti_manuale
    numero_attesi = numero
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is not None:
        # come in _contesto_appello: per una prova di un raggruppamento si attendono gli ammessi
        numero_attesi = len(corsi_service.list_ammessi_prova(tag, raggruppamento, indice))
    md = {
        "appello": appello, "iscritti": iscritti, "numero_iscritti": numero,
        "numero_studenti_suggerito": _numero_studenti_suggerito(numero_attesi, len(compiti_service.list_compiti(tag, appello_id))),
        "segreteria_csv": esportazione_service.get_segreteria_csv(tag, appello_id),
        "esclusi_filtro": esportazione_service.list_esclusi_dal_filtro(tag, appello_id),
    }
    return templates.TemplateResponse(request, "_iscritti.html", {
        "corso": corsi_service.get_corso(tag), "md": md, "messaggio": messaggio, "tipo": tipo,
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/segreteria-csv")
async def carica_segreteria_csv(request: Request, tag: str, appello_id: int, file: UploadFile = File(...)):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_esporta(tag, appello)
    contenuto = await file.read()
    try:
        n_iscritti = esportazione_service.carica_csv_segreteria(tag, appello_id, file.filename, contenuto)
    except ValueError as e:
        return _risposta_iscritti(request, tag, appello_id, str(e), "error", anchor=anchor)
    return _risposta_iscritti(request, tag, appello_id, f"File caricato: {n_iscritti} iscritti trovati", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/segreteria-csv/elimina")
def elimina_segreteria_csv_route(request: Request, tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_esporta(tag, appello)
    esportazione_service.elimina_segreteria_csv(tag, appello_id)
    return _risposta_iscritti(request, tag, appello_id, "File della segreteria rimosso", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-manuale")
def imposta_iscritti_manuale(request: Request, tag: str, appello_id: int, iscritti_manuale: str = Form("")):
    valore = int(iscritti_manuale) if iscritti_manuale.strip() else None
    corsi_service.update_appello(tag, appello_id, iscritti_manuale=valore)
    return _risposta_iscritti(request, tag, appello_id, "Numero di iscritti salvato")


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-manuale/aggiungi")
def aggiungi_iscritto_manuale(request: Request, tag: str, appello_id: int, matricola: str = Form(...)):
    try:
        esportazione_service.aggiungi_iscritto_manuale(tag, appello_id, matricola.strip())
    except ValueError as e:
        return _risposta_iscritti(request, tag, appello_id, str(e), "error")
    studente = studenti_service.get_studente(tag, matricola.strip())
    nome = f"{studente.cognome} {studente.nome}" if studente else matricola.strip()
    return _risposta_iscritti(request, tag, appello_id, f"{nome} aggiunto agli iscritti")


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-moodle", response_class=HTMLResponse)
async def carica_iscritti_moodle(request: Request, tag: str, appello_id: int, file: UploadFile = File(...)):
    """Prenotazioni esportate da Moodle: prima un'anteprima, in cui scegliere chi
    importare, poi la conferma (iscritti_moodle_conferma)."""
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    try:
        studenti = esportazione_service.anteprima_iscritti_moodle(tag, appello_id, file.filename, await file.read())
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"File non valido: {e}", "error", anchor=anchor)
    if not studenti:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Il file non contiene prenotazioni", "error", anchor=anchor)
    return templates.TemplateResponse(request, "iscritti_moodle_conferma.html", {
        "corso": corsi_service.get_corso(tag), "appello": appello, "studenti": studenti, "anchor": anchor,
        "dati_json": json.dumps(studenti, ensure_ascii=False),
        "n_iscritti_attuali": len(esportazione_service.list_iscritti_manuali(tag, appello_id)),
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-moodle/conferma")
async def conferma_iscritti_moodle(request: Request, tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    form = await request.form()
    studenti = json.loads(form.get("dati_json") or "[]")
    indici = {int(i) for i in form.getlist("importa_idx")}
    scelti = [s for i, s in enumerate(studenti) if i in indici and s.get("matricola")]
    if not scelti:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessuno studente selezionato", "error", anchor=anchor)
    esito = esportazione_service.importa_iscritti_moodle(
        tag, appello_id, scelti, sostituisci=bool(form.get("sostituisci")),
    )
    msg = f"Importate {len(scelti)} prenotazioni da Moodle: {esito['aggiunti']} nuovi iscritti"
    if esito["registrati"]:
        msg += f", di cui {esito['registrati']} registrati ora nel corso"
    if esito["rimossi"]:
        msg += f"; {esito['rimossi']} iscritti non più presenti nel file sono stati tolti dall'elenco"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg + ".", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-manuale/tutti")
def iscrivi_tutti(request: Request, tag: str, appello_id: int, matricola_minima: str = Form("")):
    n = esportazione_service.iscrivi_tutti_gli_studenti(tag, appello_id, matricola_minima)
    msg = f"{n} studenti del corso aggiunti agli iscritti"
    if matricola_minima.strip():
        msg += f" (matricola ≥ {matricola_minima.strip()})"
    return _risposta_iscritti(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-filtro")
def imposta_filtro_iscritti(request: Request, tag: str, appello_id: int, matricola_minima: str = Form("")):
    soglia = matricola_minima.strip() or None
    corsi_service.update_appello(tag, appello_id, matricola_minima_iscritti=soglia)
    n = esportazione_service.numero_iscritti(tag, appello_id)
    msg = (f"Filtro salvato: solo matricole ≥ {soglia}" if soglia else "Filtro sulle matricole rimosso")
    if n is not None:
        msg += f" — {n} iscritti nell'elenco"
    return _risposta_iscritti(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-filtro/{matricola}")
def decidi_eccezione_filtro(request: Request, tag: str, appello_id: int, matricola: str, decisione: str = Form("")):
    """Decisione del docente su un iscritto escluso dal filtro sulla matricola: ammetterlo
    comunque (eccezione), confermarne l'esclusione o rimettere la decisione in sospeso."""
    try:
        esportazione_service.decidi_eccezione_filtro(tag, appello_id, matricola, decisione)
    except ValueError as e:
        return _risposta_iscritti(request, tag, appello_id, str(e), "error")
    msg = {
        "ammesso": f"Matricola {matricola} ammessa in deroga al filtro",
        "escluso": f"Esclusione della matricola {matricola} confermata",
    }.get(decisione, f"Decisione sulla matricola {matricola} annullata: torna da confermare")
    return _risposta_iscritti(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-manuale/{matricola}/rimuovi")
def rimuovi_iscritto(request: Request, tag: str, appello_id: int, matricola: str):
    """Toglie uno studente dall'elenco iscritti, sia aggiunto a mano sia dal file della segreteria."""
    esportazione_service.rimuovi_iscritto(tag, appello_id, matricola)
    return _risposta_iscritti(request, tag, appello_id, f"Matricola {matricola} tolta dagli iscritti")


@app.get("/corsi/{tag}/appelli/{appello_id}/iscritti.csv")
def scarica_iscritti_csv(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessun elenco iscritti caricato", "error", anchor=anchor)
    dati = studenti_service.esporta_ammessi_csv(iscritti)
    return Response(
        dati, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="iscritti-{appello.slug}.csv"'},
    )


def _elenco_studenti(tag: str, appello_id: int, tipo: str):
    """(appello, titolo, elenco, aule, percorso del .tex) per l'elenco degli iscritti o
    degli ammessi a una prova (tipo "iscritti" | "ammessi"); elenco None se non disponibile.
    Il .tex viene sempre riscritto: l'elenco cambia man mano che si aggiungono studenti."""
    appello = corsi_service.get_appello(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    if tipo == "iscritti":
        elenco, aule = esportazione_service.list_iscritti(tag, appello_id), []
        titolo, tex_path = f"Studenti iscritti: {appello.nome}", compiti_service.path_iscritti(tag, appello_id, "tex", appello=appello)
    else:
        raggruppamento, indice = _membro_e_indice(tag, appello_id)
        if raggruppamento is None:
            return appello, "", None, [], None
        elenco = corsi_service.list_ammessi_prova(tag, raggruppamento, indice)
        aule = aule_service.list_aule(tag, appello_id)
        titolo, tex_path = f"Studenti ammessi: {appello.nome}", compiti_service.path_ammessi(tag, appello_id, "tex", appello=appello)
    if elenco is not None:
        ctx = risultati_service.ctx_for(tag, corso, appello)
        tex_path.write_text(latex_service.crea_tex_ammessi(ctx, titolo, elenco, aule), encoding="utf-8")
    return appello, titolo, elenco, aule, tex_path


@app.get("/corsi/{tag}/appelli/{appello_id}/{tipo}.tex", response_class=PlainTextResponse)
def elenco_studenti_tex(tag: str, appello_id: int, tipo: str):
    if tipo not in ("iscritti", "ammessi"):
        return PlainTextResponse("Documento non trovato", status_code=404)
    _, _, elenco, _, tex_path = _elenco_studenti(tag, appello_id, tipo)
    if elenco is None:
        return PlainTextResponse("Elenco non disponibile per questa prova", status_code=404)
    return PlainTextResponse(tex_path.read_text(encoding="utf-8"), media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/{tipo}.html", response_class=HTMLResponse)
def elenco_studenti_html(request: Request, tag: str, appello_id: int, tipo: str):
    if tipo not in ("iscritti", "ammessi"):
        return PlainTextResponse("Documento non trovato", status_code=404)
    appello, titolo, elenco, aule, _ = _elenco_studenti(tag, appello_id, tipo)
    if elenco is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Elenco non disponibile per questa prova", "error",
                              anchor=_anchor_membro(appello, "compiti"))
    return templates.TemplateResponse(request, "elenco_studenti.html", {
        "corso": corsi_service.get_corso(tag), "appello": appello, "titolo": titolo, "elenco": elenco,
        "aule": aule, "tipo": tipo, "anchor": _anchor_membro(appello, "compiti"),
    })


@app.get("/corsi/{tag}/appelli/{appello_id}/iscritti.pdf")
def scarica_iscritti_pdf(tag: str, appello_id: int):
    appello, _, iscritti, _, tex_path = _elenco_studenti(tag, appello_id, "iscritti")
    anchor = _anchor_membro(appello, "compiti")
    if iscritti is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessun elenco iscritti caricato", "error", anchor=anchor)
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error",
            anchor=anchor, dettaglio=compilazione.errore_dettagliato or "",
        )
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


@app.post("/corsi/{tag}/appelli/{appello_id}/presenze/{matricola}/imposta")
def imposta_presenza(request: Request, tag: str, appello_id: int, matricola: str, presente: str = Form("")):
    # risponde in JSON alla chiamata fetch dalla scheda "Presenze e ritirati" (aggiorna la
    # riga sul posto, senza ricaricare tutta la pagina), altrimenti con il solito redirect
    # — usato anche come fallback se il fetch lato client fallisse per qualunque motivo.
    vuole_json = "application/json" in request.headers.get("accept", "")
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "presenze")
    try:
        if appello and appello.presenze_chiuse:
            raise ValueError("Il registro presenze è chiuso: riaprilo prima di modificare le presenze")
        if presente:
            presenze_service.segna_presente(tag, appello_id, matricola)
        else:
            presenze_service.rimuovi_presente(tag, appello_id, matricola)
    except Exception as e:
        if vuole_json:
            return JSONResponse({"errore": str(e)}, status_code=400)
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    if vuole_json:
        return JSONResponse({"presente": bool(presente)})
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Presenza aggiornata", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/presenze/chiudi")
def chiudi_presenze(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "presenze")
    try:
        esito = presenze_service.chiudi(tag, appello_id, _ammessi_se_membro(tag, appello))
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    msg = f"Registro chiuso: {esito['segnati_assenti']} studenti non presenti segnati come assenti"
    kind = "success"
    if esito["non_registrati"]:
        kind = "warning"
        msg += (
            f" — {len(esito['non_registrati'])} matricole dell'elenco iscritti non sono registrate come "
            "studenti di questo corso e non è stato possibile segnarle: registrale (o correggile) a mano"
        )
    if esito["errori"]:
        kind = "warning"
        msg += f" — {len(esito['errori'])} studenti non segnati per un errore imprevisto: riprova a mano per loro"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, kind, anchor=anchor)


def _ammessi_se_membro(tag: str, appello) -> Optional[list[dict]]:
    """Per una prova di un raggruppamento, gli studenti ammessi (usati come elenco degli
    attesi quando manca l'elenco iscritti); None per un appello normale."""
    if not appello.membro_raggruppamento:
        return None
    ragg = corsi_service.get_raggruppamento_by_membro(tag, appello.id)
    indice = next(i for i, m in enumerate(ragg.membri) if m.id == appello.id)
    return corsi_service.list_ammessi_prova(tag, ragg, indice)


@app.post("/corsi/{tag}/appelli/{appello_id}/segna-assenti-non-valutati")
def segna_assenti_non_valutati(tag: str, appello_id: int, torna: str = Form("valutazione")):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "presenze" if torna == "presenze" else "valutazione")
    try:
        esito = presenze_service.segna_assenti_non_valutati(tag, appello_id, _ammessi_se_membro(tag, appello))
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    msg = f"{esito['segnati_assenti']} studenti non valutati segnati come assenti"
    kind = "success"
    if esito["non_registrati"]:
        kind = "warning"
        msg += (
            f" — {len(esito['non_registrati'])} matricole dell'elenco iscritti non sono registrate come "
            "studenti di questo corso e non è stato possibile segnarle: registrale (o correggile) a mano"
        )
    if esito["errori"]:
        kind = "warning"
        msg += f" — {len(esito['errori'])} studenti non segnati per un errore imprevisto: riprova a mano per loro"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, kind, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/presenze/riapri")
def riapri_presenze(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    presenze_service.riapri(tag, appello_id)
    return flash_redirect(
        f"/corsi/{tag}/appelli/{appello_id}",
        "Registro riaperto: puoi tornare a spuntare le presenze (gli studenti già segnati assenti non cambiano)",
        anchor=_anchor_membro(appello, "presenze"),
    )


@app.get("/corsi/{tag}/appelli/{appello_id}/esporta-voti")
def esporta_voti(request: Request, tag: str, appello_id: int, domanda: Optional[int] = None):
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    appello_singolo = corsi_service.get_appello(tag, appello_id)
    anchor_errore = _anchor_esporta(tag, appello_singolo)
    lista_domande = corsi_service.get_corso(tag).domande_esame_lista
    # `domanda` è l'indice in lista_domande scelto nel menu accanto a "Compila e scarica"
    # (assente se il corso ne ha al massimo una: si usa quella)
    domande_esame = lista_domande[domanda] if domanda is not None and 0 <= domanda < len(lista_domande) else None
    try:
        testo, codifica, compilati, extra = esportazione_service.compila_export_voti(tag, appello_id, domande_esame)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor_errore)
    filename = esportazione_service.get_segreteria_csv(tag, appello_id)["nome_file"]
    if (raggruppamento and compilati) or extra:
        corso = corsi_service.get_corso(tag)
        return templates.TemplateResponse(request, "esporta_voti_conferma.html", {
            "corso": corso, "appello": appello_singolo, "compilati": compilati if raggruppamento else [],
            "extra": extra, "csv_testo": testo, "codifica": codifica, "filename": filename,
            "anchor_ritorno": anchor_errore,
        })
    return Response(
        testo.encode(codifica), media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Studenti-Compilati": str(len(compilati)),
        },
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/esporta-voti/conferma")
async def esporta_voti_conferma(tag: str, appello_id: int, request: Request):
    form = await request.form()
    matricole = form.getlist("matricole")
    if matricole:
        verbalizzazione_service.verbalizza_multipli(tag, appello_id, matricole)
    testo = form.get("csv_testo", "")
    codifica = form.get("codifica", "utf-8")
    return Response(
        testo.encode(codifica), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{form.get("filename", "export.csv")}"'},
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/verbalizza/{matricola}")
def verbalizza(
    tag: str, appello_id: int, matricola: str, voto: str = Form(""), data: str = Form(""),
):
    anchor = "risultati" if corsi_service.get_raggruppamento_by_appello(tag, appello_id) else "verbalizzati"
    try:
        verbalizzazione_service.verbalizza(
            tag, appello_id, matricola, voto=int(voto) if voto else None, data=data or None,
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Verbalizzato", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/verbalizza-multipli")
async def verbalizza_multipli(tag: str, appello_id: int, request: Request):
    anchor = "risultati" if corsi_service.get_raggruppamento_by_appello(tag, appello_id) else "verbalizzati"
    form = await request.form()
    matricole = form.getlist("matricole")
    if not matricole:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessuno studente selezionato", "error", anchor=anchor)
    try:
        n = verbalizzazione_service.verbalizza_multipli(tag, appello_id, matricole)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Verbalizzati {n} studenti", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/verbalizza/{matricola}/annulla")
def annulla_verbalizzazione(tag: str, appello_id: int, matricola: str):
    try:
        verbalizzazione_service.annulla_verbalizzazione(tag, appello_id, matricola)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor="verbalizzati")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Verbalizzazione annullata", anchor="verbalizzati")


@app.post("/corsi/{tag}/appelli/{appello_id}/rifiuta/{matricola}")
def rifiuta_voto(tag: str, appello_id: int, matricola: str):
    anchor = "risultati" if corsi_service.get_raggruppamento_by_appello(tag, appello_id) else "verbalizzati"
    try:
        verbalizzazione_service.rifiuta(tag, appello_id, matricola)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Voto rifiutato: lo studente dovrà ripresentarsi", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/calcola-raggruppamento")
def calcola_raggruppamento(tag: str, appello_id: int, raggruppamento_id: int = Form(...)):
    try:
        result = risultati_service.calcola_raggruppamento(tag, raggruppamento_id)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Calcolati {result.n_calcolati} voti combinati")


@app.post("/corsi/{tag}/appelli/{appello_id}/raggruppamento/modifica")
def modifica_raggruppamento(
    tag: str, appello_id: int, matricola_minima_prima_prova: str = Form(""), fonte_ammessi: str = Form("iscritti"),
):
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questo appello non è un raggruppamento", "error")
    try:
        corsi_service.update_raggruppamento_soglia(
            tag, raggruppamento.id, matricola_minima_prima_prova.strip() or None, fonte_ammessi,
        )
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor="impostazioni")
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Impostazioni raggruppamento salvate", anchor="impostazioni")


def _membro_e_indice(tag: str, appello_id: int):
    """Il raggruppamento e l'indice (0-based) di `appello_id` fra i suoi membri, o
    (None, None) se questo appello non è una prova membro: usata da tutte le route di
    ammissione/aule, che sono sempre scoped su una singola prova."""
    raggruppamento = corsi_service.get_raggruppamento_by_membro(tag, appello_id)
    if raggruppamento is None:
        return None, None
    for i, m in enumerate(raggruppamento.membri):
        if m.id == appello_id:
            return raggruppamento, i
    return None, None


def _risposta_ammessi_aule(request: Request, tag: str, appello_id: int, messaggio: str, tipo: str = "success"):
    """Esito di un'azione del riquadro "Studenti ammessi e aule" di una prova: se la
    richiesta arriva dal riquadro stesso (fetch con X-Frammento, vedi attivaAmmessiAule)
    si restituisce solo il riquadro aggiornato, altrimenti si torna alla pagina come prima."""
    if not request.headers.get("x-frammento"):
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", messaggio, tipo, anchor=f"compiti-{appello_id}")
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return PlainTextResponse("Questa prova non appartiene a un raggruppamento", status_code=404)
    md = {
        "appello": raggruppamento.membri[indice],
        "ammessi": corsi_service.list_ammessi_prova(tag, raggruppamento, indice),
        "aule": aule_service.list_aule(tag, appello_id),
    }
    return templates.TemplateResponse(request, "_ammessi_aule.html", {
        "corso": corsi_service.get_corso(tag), "md": md, "messaggio": messaggio, "tipo": tipo,
    })


@app.get("/corsi/{tag}/appelli/{appello_id}/ammessi-aule", response_class=HTMLResponse)
def riquadro_ammessi_aule(request: Request, tag: str, appello_id: int):
    """Il riquadro "Studenti ammessi e aule" da solo: lo richiede l'elenco iscritti dopo
    una modifica, perché alla prima prova di un raggruppamento gli ammessi ne dipendono."""
    return _risposta_ammessi_aule(request, tag, appello_id, "")


def _risposta_aule(request: Request, tag: str, appello_id: int, messaggio: str, tipo: str = "success"):
    """Esito di una modifica alle aule di una prova (scheda Impostazioni): il riquadro delle
    aule ridisegnato se la richiesta arriva da lì (X-Frammento, vedi attivaAule), altrimenti
    si torna alla scheda Impostazioni."""
    if not request.headers.get("x-frammento"):
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", messaggio, tipo, anchor="impostazioni")
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return PlainTextResponse("Questa prova non appartiene a un raggruppamento", status_code=404)
    md = {
        "appello": raggruppamento.membri[indice],
        "ammessi": corsi_service.list_ammessi_prova(tag, raggruppamento, indice),
        "aule": aule_service.list_aule(tag, appello_id),
    }
    return templates.TemplateResponse(request, "_aule.html", {
        "corso": corsi_service.get_corso(tag), "md": md, "messaggio": messaggio, "tipo": tipo,
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/nuova")
def crea_aula(request: Request, tag: str, appello_id: int, nome: str = Form(...), capienza: int = Form(...)):
    aule_service.crea_aula(tag, appello_id, nome, capienza)
    return _risposta_aule(request, tag, appello_id, f"Aula {nome.strip()} aggiunta")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{aula_id}/dsa")
def imposta_aula_dsa(request: Request, tag: str, appello_id: int, aula_id: int, dsa: str = Form("")):
    aule_service.imposta_aula_dsa(tag, appello_id, aula_id, bool(dsa))
    return _risposta_aule(
        request, tag, appello_id,
        ("Aula riservata ai DSA/L104" if dsa else "L'aula non è più riservata ai DSA/L104")
        + ": per aggiornare le assegnazioni usa \"Assegna automaticamente\" nella scheda Compiti d'esame",
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{aula_id}/modifica")
def modifica_aula(request: Request, tag: str, appello_id: int, aula_id: int, nome: str = Form(...), capienza: int = Form(...)):
    try:
        aule_service.modifica_aula(tag, appello_id, aula_id, nome, capienza)
    except ValueError as e:
        return _risposta_aule(request, tag, appello_id, str(e), "error")
    return _risposta_aule(request, tag, appello_id, f"Aula {nome.strip()} aggiornata")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{aula_id}/elimina")
def elimina_aula(request: Request, tag: str, appello_id: int, aula_id: int):
    aule_service.elimina_aula(tag, appello_id, aula_id)
    return _risposta_aule(request, tag, appello_id, "Aula eliminata")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/assegna")
def assegna_aule(request: Request, tag: str, appello_id: int):
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questa prova non appartiene a un raggruppamento", "error")
    ammessi = corsi_service.list_ammessi_prova(tag, raggruppamento, indice)
    aule_service.assegna_automatica(
        tag, appello_id, [s["matricola"] for s in ammessi], matricole_dsa={s["matricola"] for s in ammessi if s["dsa"]},
    )
    return _risposta_ammessi_aule(request, tag, appello_id, "Assegnazione aule aggiornata")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{matricola}/assegna-manuale")
def assegna_aula_manuale(request: Request, tag: str, appello_id: int, matricola: str, aula_id: str = Form("")):
    # "non assegnata" (valore vuoto) toglie l'aula: lo studente torna all'assegnazione automatica
    aule_service.assegna_manuale(tag, appello_id, matricola, int(aula_id) if aula_id.strip() else None)
    return _risposta_ammessi_aule(request, tag, appello_id, "Aula assegnata" if aula_id.strip() else "Aula tolta")


@app.post("/corsi/{tag}/appelli/{appello_id}/ammessi/aggiungi")
def aggiungi_ammesso_manuale(request: Request, tag: str, appello_id: int, matricola: str = Form(...)):
    try:
        corsi_service.ammetti_manualmente(tag, appello_id, matricola.strip())
    except ValueError as e:
        return _risposta_ammessi_aule(request, tag, appello_id, str(e), "error")
    return _risposta_ammessi_aule(
        request, tag, appello_id,
        "Studente aggiunto agli ammessi: verifica che soddisfi davvero i requisiti, se non è già segnalato in deroga",
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/ammessi/{matricola}/rimuovi")
def rimuovi_ammesso_manuale(request: Request, tag: str, appello_id: int, matricola: str):
    corsi_service.rimuovi_ammissione_manuale(tag, appello_id, matricola)
    return _risposta_ammessi_aule(request, tag, appello_id, "Rimosso dall'elenco ammessi")


@app.get("/corsi/{tag}/appelli/{appello_id}/ammessi.csv")
def scarica_ammessi_csv(tag: str, appello_id: int):
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questa prova non appartiene a un raggruppamento", "error")
    appello = corsi_service.get_appello(tag, appello_id)
    ammessi = corsi_service.list_ammessi_prova(tag, raggruppamento, indice)
    dati = studenti_service.esporta_ammessi_csv(ammessi)
    return Response(
        dati, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ammessi-{appello.slug}.csv"'},
    )


@app.get("/corsi/{tag}/appelli/{appello_id}/ammessi.pdf")
def scarica_ammessi_pdf(tag: str, appello_id: int):
    _, _, ammessi, _, tex_path = _elenco_studenti(tag, appello_id, "ammessi")
    if ammessi is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questa prova non appartiene a un raggruppamento", "error")
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        return flash_redirect(
            f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error",
            anchor=f"compiti-{appello_id}", dettaglio=compilazione.errore_dettagliato or "",
        )
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


def _filtra_studenti(lista: list, stato_esame: dict, filtro: str, dsa: str) -> list:
    """Filtri dell'elenco studenti, combinabili: `filtro` sullo stato dell'esame e `dsa`
    ("1" = DSA o L104, "DSA", "L104", "no" = nessuno dei due)."""
    if filtro == "fatto":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "verbalizzato"]
    elif filtro == "da_verbalizzare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "da_verbalizzare"]
    elif filtro == "da_fare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") not in ("verbalizzato", "da_verbalizzare")]
    if dsa == "1":
        lista = [s for s in lista if s.dsa]
    elif dsa in ("DSA", "L104"):
        lista = [s for s in lista if s.dsa and (s.dsa_tipo or "DSA") == dsa]
    elif dsa == "no":
        lista = [s for s in lista if not s.dsa]
    return lista


@app.get("/corsi/{tag}/studenti", response_class=HTMLResponse)
def studenti_list(request: Request, tag: str, q: str = "", da: str = "", filtro: str = "", dsa: str = "", mai_sostenuto: str = ""):
    corso = corsi_service.get_corso(tag)
    lista = studenti_service.list_studenti(tag, q or None)
    stato_esame = verbalizzazione_service.stato_esame_studenti(tag)
    lista = _filtra_studenti(lista, stato_esame, filtro, dsa)
    tutti_corsi = corsi_service.list_corsi()
    corsi_suggeriti = corsi_service.corsi_simili(corso, tutti_corsi)
    altri_corsi = [c for c in tutti_corsi if c.tag != tag]
    corso_sorgente = corsi_service.get_corso(da) if da and config.corso_exists(da) else None
    if corso_sorgente and mai_sostenuto == "1":
        studenti_sorgente = studenti_service.list_mai_sostenuto(da)
    elif corso_sorgente:
        studenti_sorgente = studenti_service.list_non_superati(da)
    else:
        studenti_sorgente = []
    return templates.TemplateResponse(request, "studenti.html", {
        "corso": corso, "studenti": lista, "q": q, "filtro": filtro, "dsa": dsa, "stato_esame": stato_esame,
        "corsi_suggeriti": corsi_suggeriti, "altri_corsi": altri_corsi, "mai_sostenuto": mai_sostenuto,
        "corso_sorgente": corso_sorgente, "studenti_sorgente": studenti_sorgente,
    })


@app.get("/corsi/{tag}/studenti/esporta.csv")
def esporta_studenti(tag: str, q: str = "", filtro: str = "", dsa: str = ""):
    lista = studenti_service.list_studenti(tag, q or None)
    stato_esame = verbalizzazione_service.stato_esame_studenti(tag)
    lista = _filtra_studenti(lista, stato_esame, filtro, dsa)
    dati = studenti_service.esporta_csv(lista, stato_esame)
    return Response(
        dati, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="studenti-{tag}.csv"'},
    )


@app.get("/corsi/{tag}/studenti/{matricola}", response_class=HTMLResponse)
def studente_dettaglio_corso(request: Request, tag: str, matricola: str):
    corso = corsi_service.get_corso(tag)
    dettaglio = studenti_service.dettaglio_studente_corso(tag, matricola)
    if dettaglio is None:
        return flash_redirect(f"/corsi/{tag}/studenti", f"Nessuno studente trovato con matricola '{matricola}'", "error")
    return templates.TemplateResponse(request, "studente_corso_dettaglio.html", {
        "corso": corso, "studente": dettaglio["studente"], "storico": dettaglio["storico"],
        "orale_obbligatorio": dettaglio["orale_obbligatorio"],
        "altri_corsi": studenti_service.storico_altri_corsi(tag, matricola),
    })


@app.get("/corsi/{tag}/studenti/{matricola}/carriera", response_class=HTMLResponse)
def studente_carriera(request: Request, tag: str, matricola: str):
    """Frammento con i risultati dello studente in tutti i corsi: si apre
    sotto la riga di un escluso dal filtro sulla matricola, per decidere se ammetterlo."""
    return templates.TemplateResponse(request, "_carriera_studente.html", {
        "matricola": matricola, "riepilogo": studenti_service.riepilogo_globale(matricola),
    })


@app.post("/corsi/{tag}/studenti/nuovo")
def nuovo_studente(
    tag: str, matricola: str = Form(...), nome: str = Form(...), cognome: str = Form(...), dsa_tipo: str = Form(""),
    dsa_note: str = Form(""), next: str = Form(""),
):
    # "next" (usato dai form di registrazione rapida su Compiti d'esame/Presenze/
    # Valutazione, per uno studente scoperto non registrato) riporta a quella pagina
    # invece che alla scheda Studenti: solo un percorso locale del corso, mai un URL
    # esterno o protocol-relative ("//..."). L'eventuale "#scheda" va passato come anchor
    # a flash_redirect, non lasciato dentro il path: altrimenti finirebbe prima della
    # query string ("...#scheda?msg=...", un URL non valido, con relativo fragment che il
    # browser non manda più al server).
    destinazione, _, ancora = next.partition("#")
    if not (destinazione.startswith("/corsi/") and not destinazione.startswith("//")):
        destinazione, ancora = f"/corsi/{tag}/studenti", ""
    try:
        studenti_service.crea_studente(tag, matricola, nome, cognome, dsa_tipo=dsa_tipo, dsa_note=dsa_note)
    except ValueError as e:
        return flash_redirect(destinazione, str(e), "error", anchor=ancora)
    return flash_redirect(destinazione, "Studente salvato", anchor=ancora)


@app.post("/corsi/{tag}/studenti/{matricola}/modifica")
def modifica_studente(
    tag: str, matricola: str, nome: str = Form(...), cognome: str = Form(...), nuova_matricola: str = Form(""),
    dsa_tipo: str = Form(""), dsa_note: str = Form(""),
):
    try:
        studenti_service.aggiorna_studente(
            tag, matricola, nome, cognome, nuova_matricola or None, dsa_tipo=dsa_tipo, dsa_note=dsa_note,
        )
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/studenti", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/studenti", "Studente aggiornato")


@app.post("/corsi/{tag}/studenti/{matricola}/elimina")
def elimina_studente(tag: str, matricola: str):
    try:
        studenti_service.elimina_studente(tag, matricola)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/studenti", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/studenti", "Studente eliminato")


@app.post("/corsi/{tag}/studenti/importa-csv", response_class=HTMLResponse)
async def importa_csv(request: Request, tag: str, file: UploadFile = File(...)):
    content = await file.read()
    anteprima = studenti_service.anteprima_csv(content)
    if anteprima["n_colonne"] == 0:
        return flash_redirect(f"/corsi/{tag}/studenti", "Il file CSV è vuoto", "error")
    corso = corsi_service.get_corso(tag)
    return templates.TemplateResponse(request, "csv_mappatura.html", {"corso": corso, "anteprima": anteprima})


@app.post("/corsi/{tag}/studenti/importa-csv/verifica", response_class=HTMLResponse)
async def importa_csv_verifica(request: Request, tag: str):
    form = await request.form()
    csv_testo = form.get("csv_testo", "")
    righe_da_ignorare = int(form.get("righe_da_ignorare") or 0)
    matricola_idx, nome_idx, cognome_idx = form.get("matricola_idx"), form.get("nome_idx"), form.get("cognome_idx")
    laurea_idx = form.get("laurea_idx") or None
    if matricola_idx is None or nome_idx is None or cognome_idx is None or "" in (matricola_idx, nome_idx, cognome_idx):
        return flash_redirect(f"/corsi/{tag}/studenti", "Seleziona una colonna per matricola, nome e cognome", "error")
    corso = corsi_service.get_corso(tag)
    verifica = studenti_service.verifica_import_csv(
        tag, csv_testo, righe_da_ignorare, int(matricola_idx), int(nome_idx), int(cognome_idx),
        int(laurea_idx) if laurea_idx is not None else None,
    )
    return templates.TemplateResponse(request, "csv_conferma.html", {
        "corso": corso, "verifica": verifica, "csv_testo": csv_testo, "righe_da_ignorare": righe_da_ignorare,
        "matricola_idx": matricola_idx, "nome_idx": nome_idx, "cognome_idx": cognome_idx, "laurea_idx": laurea_idx,
    })


@app.post("/corsi/{tag}/studenti/importa-csv/conferma")
async def importa_csv_conferma(request: Request, tag: str):
    form = await request.form()
    csv_testo = form.get("csv_testo", "")
    righe_da_ignorare = int(form.get("righe_da_ignorare") or 0)
    laurea_idx = form.get("laurea_idx") or None
    righe_incluse = {int(v) for v in form.getlist("righe_incluse")}
    report = studenti_service.importa_csv_mappato(
        tag, csv_testo, righe_da_ignorare,
        int(form.get("matricola_idx")), int(form.get("nome_idx")), int(form.get("cognome_idx")),
        int(laurea_idx) if laurea_idx is not None else None, righe_incluse=righe_incluse,
    )
    msg = f"Import: {report.inseriti} inseriti, {report.aggiornati} aggiornati, {report.saltati} saltati"
    return flash_redirect(f"/corsi/{tag}/studenti", msg, "error" if report.errori else "success")


@app.post("/corsi/{tag}/studenti/importa-da-corso")
async def importa_da_corso(tag: str, request: Request):
    form = await request.form()
    tag_sorgente = form.get("da") or ""
    matricole = form.getlist("matricole")
    if not tag_sorgente or not matricole:
        return flash_redirect(f"/corsi/{tag}/studenti", "Seleziona un corso di origine e almeno uno studente", "error")
    report = studenti_service.importa_da_altro_corso(tag, tag_sorgente, matricole)
    msg = f"Import da {tag_sorgente}: {report.inseriti} inseriti, {report.aggiornati} aggiornati, {report.saltati} saltati"
    return flash_redirect(f"/corsi/{tag}/studenti", msg, "error" if report.errori else "success")


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/form", response_class=HTMLResponse)
def form_modifica_esercizio(
    request: Request, tag: str, esercizio_id: int, redirect_to: str = "", redirect_anchor: str = "",
):
    """Modulo di modifica di un esercizio, caricato via fetch al clic su "Modifica" (come
    l'anteprima): prima veniva pre-generato, nascosto, per ogni esercizio di ogni elenco,
    e con banche di centinaia di esercizi le pagine arrivavano a pesare megabyte."""
    esercizio = esercizi_service.get_esercizio(tag, esercizio_id)
    if esercizio is None:
        return HTMLResponse("Esercizio non trovato", status_code=404)
    avvisa = any(
        compiti_service.list_blocchi(tag, aid) for aid in esercizi_service.appelli_che_usano(tag, esercizio_id)
    )
    return templates.TemplateResponse(request, "_esercizio_form.html", {
        "corso": corsi_service.get_corso(tag), "esercizio": esercizio,
        "action": f"/corsi/{tag}/esercizi/{esercizio_id}/modifica",
        "avvisa_rigenerazione": avvisa, "argomenti": esercizi_service.list_argomenti(tag),
        "redirect_to": redirect_to, "redirect_anchor": redirect_anchor,
    })


@app.get("/corsi/{tag}/esercizi/{esercizio_id}/anteprima", response_class=HTMLResponse)
def anteprima_esercizio(request: Request, tag: str, esercizio_id: int):
    """Frammento HTML con testo/risposte/soluzione di tutte le varianti di un esercizio,
    caricato via fetch solo quando l'utente apre l'anteprima: con banche di centinaia di
    esercizi, generare questo blocco per ognuno di essi dentro ogni pagina che li elenca
    (anche se nascosto) appesantisce inutilmente ogni caricamento."""
    esercizio = esercizi_service.get_esercizio(tag, esercizio_id)
    if esercizio is None:
        return HTMLResponse("Esercizio non trovato", status_code=404)
    appelli_assegnato = [
        a for a in (
            corsi_service.get_appello(tag, aid) for aid in esercizi_service.appelli_che_usano(tag, esercizio_id)
        ) if a is not None
    ]
    utilizzo_altrove = esercizi_service.utilizzo_in_altri_corsi(tag, esercizio_id)
    return templates.TemplateResponse(request, "_esercizio_anteprima.html", {
        "e": esercizio, "appelli_assegnato": appelli_assegnato, "utilizzo_altrove": utilizzo_altrove,
    })


@app.get("/corsi/{tag}/esercizi", response_class=HTMLResponse)
def esercizi_list(request: Request, tag: str):
    corso = corsi_service.get_corso(tag)
    esercizi = esercizi_service.list_esercizi(tag)
    return templates.TemplateResponse(request, "esercizi.html", {
        "corso": corso, "esercizi": esercizi, "argomenti": esercizi_service.list_argomenti(tag),
        "duplicati": esercizi_service.trova_duplicati(tag),
    })


def _contesto_elenco_esercizi(escludi_tag: str = "", corso_corrente=None) -> dict:
    """Dati per _elenco_esercizi_corsi.html: tutti gli esercizi di tutti i corsi (tranne
    `escludi_tag`) con i valori dei filtri. Se `corso_corrente` è dato, il filtro sul nome
    del corso parte già da quel nome (le edizioni degli anni precedenti)."""
    righe = esercizi_service.list_esercizi_tutti_i_corsi(escludi_tag=escludi_tag)
    corsi_presenti = []
    for c, _ in righe:
        if c not in corsi_presenti:
            corsi_presenti.append(c)
    nomi_corso = sorted({c.nome for c in corsi_presenti})
    nome_preselezionato = ""
    if corso_corrente and corso_corrente.nome in nomi_corso:
        nome_preselezionato = corso_corrente.nome
    # stato della verifica con l'IA di ogni esercizio, per la piccola icona nella lista
    stati_ia = {}
    for c in corsi_presenti:
        esercizi_c = [e for cc, e in righe if cc is c]
        for eid, st in esercizi_service.stati_verifica(c.tag, esercizi_c).items():
            stati_ia[(c.tag, eid)] = st["stato"]
    return {
        "righe_esercizi": righe, "corsi_presenti": corsi_presenti, "nomi_corso": nomi_corso, "stati_ia": stati_ia,
        "argomenti_tutti": sorted({e.argomento for _, e in righe if e.argomento}),
        "nome_corso_preselezionato": nome_preselezionato,
    }


@app.get("/esercizi", response_class=HTMLResponse)
def esercizi_tutti(request: Request):
    """Tutti gli esercizi di tutti i corsi in un unico elenco filtrabile (per nome del
    corso, corso specifico, argomento, testo): solo consultazione, le banche restano
    separate. Per usarne uno in un compito: scheda Testo dell'appello, "Importa da altri
    corsi o anni precedenti"."""
    contesto = _contesto_elenco_esercizi()
    contesto["selezionabile"] = False
    contesto["verifiche_corsi"] = _riepilogo_verifiche_corsi()
    contesto["fornitore_ia"], contesto["profili_ia"] = ia_service.fornitore(), ia_service.profili()
    return templates.TemplateResponse(request, "esercizi_tutti.html", contesto)


def _riepilogo_verifiche_corsi() -> list[dict]:
    """Per ogni corso con esercizi: quanti sono verificati, da verificare, con problemi."""
    righe = []
    for c in corsi_service.list_corsi():
        esercizi = esercizi_service.list_esercizi(c.tag)
        if not esercizi:
            continue
        stati = [s["stato"] for s in esercizi_service.stati_verifica(c.tag, esercizi).values()]
        righe.append({
            "corso": c, "totale": len(esercizi), "verificati": stati.count("verificato"),
            "da_verificare": stati.count("mai") + stati.count("modificato"),
            "problemi": stati.count("errore") + stati.count("dubbio"),
        })
    return righe


@app.post("/esercizi/ia/verifica")
def verifica_tutti_i_corsi(profilo: str = Form("")):
    """Verifica con l'IA, corso per corso, tutti gli esercizi da verificare di tutte le
    banche (mai verificati o modificati dopo l'ultima verifica); gli esiti restano salvati."""
    totale = {"ok": 0, "dubbio": 0, "errore": 0}
    corsi_fatti, errori = 0, []
    for riga in _riepilogo_verifiche_corsi():
        if not riga["da_verificare"]:
            continue
        ambito = _ambito_verifica(riga["corso"].tag)
        stati = esercizi_service.stati_verifica(riga["corso"].tag, ambito["esercizi"])
        try:
            esito = ia_service.verifica(riga["corso"].tag, _da_verificare(riga["corso"].tag, ambito["esercizi"], stati),
                                        ambito["contesto"], profilo_id=profilo)
        except ValueError as e:
            errori.append(f"{riga['corso'].nome} ({riga['corso'].anno}): {e}")
            continue
        for r in esito["esercizi"]:
            esercizi_service.registra_verifica(riga["corso"].tag, r["esercizio"], r["esito"], ia_service.risultato_serializzabile(r))
            totale[r["esito"]] += 1
        corsi_fatti += 1
    if not corsi_fatti and not errori:
        return flash_redirect("/esercizi", "Non c'è nessun esercizio da verificare: sono già tutti verificati", anchor="verifica-ia")
    msg = (f"Verificati gli esercizi di {corsi_fatti} {'corso' if corsi_fatti == 1 else 'corsi'}: {totale['ok']} corretti, {totale['dubbio']} da controllare, "
           f"{totale['errore']} con errori (vedi le banche dei corsi per correggerli)")
    if errori:
        return flash_redirect("/esercizi", msg + f". Non riuscita per {len(errori)} {'corso' if len(errori) == 1 else 'corsi'}", "warning",
                              anchor="verifica-ia", dettaglio="\n".join(errori))
    return flash_redirect("/esercizi", msg, anchor="verifica-ia")


@app.get("/corsi/{tag}/appelli/{appello_id}/esercizi/altri-corsi", response_class=HTMLResponse)
def esercizi_altri_corsi(request: Request, tag: str, appello_id: int):
    """Frammento caricato all'apertura del pannello "Importa da altri corsi o anni
    precedenti" della scheda Testo (leggere tutte le banche è lento: solo quando serve)."""
    contesto = _contesto_elenco_esercizi(escludi_tag=tag, corso_corrente=corsi_service.get_corso(tag))
    contesto.update({
        "selezionabile": True,
        "action_importa_altri": f"/corsi/{tag}/appelli/{appello_id}/esercizi/importa-da-altri-corsi",
    })
    return templates.TemplateResponse(request, "_elenco_esercizi_corsi.html", contesto)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/importa-da-altri-corsi")
async def importa_esercizi_altri_corsi(tag: str, appello_id: int, request: Request, background_tasks: BackgroundTasks):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    form = await request.form()
    selezionati = []
    for valore in form.getlist("selezionati"):
        tag_sorgente, _, esercizio_id = valore.rpartition(":")
        if tag_sorgente and esercizio_id.isdigit() and config.corso_exists(tag_sorgente):
            selezionati.append((tag_sorgente, int(esercizio_id)))
    if not selezionati:
        return _esito_testo(request, tag, appello_id, "Nessun esercizio selezionato", "error")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        assegnati, gia_presenti = esercizi_service.importa_in_appello_da_altri_corsi(
            tag, appello_id, selezionati, obbligatorio=bool(form.get("obbligatorio")),
        )
        msg = f"{assegnati} esercizi aggiunti al compito"
        if gia_presenti:
            msg += f" ({gia_presenti} erano già nella banca di questo corso e sono stati riusati)"
        msg += _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        return _esito_testo(request, tag, appello_id, str(e), "error")
    return _esito_testo(request, tag, appello_id, msg)


@app.post("/corsi/{tag}/esercizi/nuovo")
async def nuovo_esercizio(tag: str, request: Request):
    form = await request.form()
    varianti = _parse_varianti_form(form)
    extra = _parse_esercizio_extra_form(form)
    try:
        esercizi_service.create_esercizio(
            tag, nome=(form.get("nome") or "").strip(), note=(form.get("note") or "").strip(), varianti=varianti,
            argomento=(form.get("argomento") or "").strip(), **extra,
        )
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/esercizi", "Esercizio creato")


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/modifica")
async def modifica_esercizio(tag: str, esercizio_id: int, request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    varianti = _parse_varianti_form(form)
    extra = _parse_esercizio_extra_form(form)
    destinazione = (form.get("redirect_to") or "").strip() or f"/corsi/{tag}/esercizi"
    ancora = (form.get("redirect_anchor") or "").strip()
    appelli_coinvolti = esercizi_service.appelli_che_usano(tag, esercizio_id)
    try:
        for appello_id in appelli_coinvolti:
            _proteggi_modifica_esercizi(tag, appello_id)
        for appello_id in appelli_coinvolti:
            compiti_service.svuota_blocchi_appello(tag, appello_id)
        esercizi_service.aggiorna_esercizio(
            tag, esercizio_id, nome=(form.get("nome") or "").strip(), note=(form.get("note") or "").strip(),
            argomento=(form.get("argomento") or "").strip(), varianti=varianti, **extra,
        )
        msg = "Esercizio aggiornato"
        for appello_id in appelli_coinvolti:
            msg += _rigenera_se_necessario(tag, appello_id, background_tasks)
    except ValueError as e:
        msg, kind = str(e), "error"
    else:
        kind = "success"
    # modificato dalla scheda Testo di un appello: si ridisegna solo il riquadro degli esercizi
    appello_frammento = request.headers.get("x-appello", "")
    if request.headers.get("x-frammento") and appello_frammento.isdigit():
        return _esito_testo(request, tag, int(appello_frammento), msg, kind)
    return flash_redirect(destinazione, msg, kind, anchor=ancora)


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/elimina")
def elimina_esercizio(request: Request, tag: str, esercizio_id: int):
    # dalla banca del corso l'eliminazione avviene in background (X-Frammento): la pagina
    # toglie la riga da sola, senza ricaricarsi
    in_background = bool(request.headers.get("x-frammento"))
    try:
        esercizi_service.elimina_esercizio(tag, esercizio_id)
    except ValueError as e:
        if in_background:
            return JSONResponse({"errore": str(e)}, status_code=400)
        return flash_redirect(f"/corsi/{tag}/esercizi", str(e), "error")
    if in_background:
        return JSONResponse({"ok": True})
    return flash_redirect(f"/corsi/{tag}/esercizi", "Esercizio eliminato")


@app.get("/api/studenti/cerca")
def api_cerca_studenti_globale(q: str = ""):
    return studenti_service.cerca_globale(q)


def _stato_iscritto(s: dict) -> Optional[str]:
    if s["bloccato"]:
        return {"assente": "assente", "ritirato": "ritirato", "annullato": "annullato"}.get(s["esito"], "già valutato")
    if s["presente"]:
        return "presente"
    return None


@app.get("/corsi/{tag}/api/studenti/cerca")
def api_cerca_studenti(tag: str, q: str = "", appello_id: Optional[int] = None):
    if not q.strip():
        return []
    risultati = studenti_service.list_studenti(tag, q.strip())
    stati_per_matricola: dict[str, Optional[str]] = {}
    if appello_id is not None:
        # una matricola può corrispondere a più persone (numeri simili, o cercando per
        # nome/cognome): chi è iscritto a questo appello è quasi sempre chi si sta
        # cercando davvero, quindi va proposto per primo, senza però nascondere gli altri.
        # Lo stato (presente/assente/ritirato/già valutato) aiuta a riconoscerlo subito,
        # e a capire a colpo d'occhio chi resta ancora da correggere.
        appello = corsi_service.get_appello(tag, appello_id)
        riepilogo = presenze_service.riepilogo(tag, appello_id, _ammessi_se_membro(tag, appello) if appello else None)
        iscritti = riepilogo["iscritti"] if riepilogo else []
        stati_per_matricola = {s["matricola"]: _stato_iscritto(s) for s in iscritti}
        matricole_iscritte = set(stati_per_matricola)
        risultati = sorted(risultati, key=lambda s: s.matricola not in matricole_iscritte)
    return [
        {
            "matricola": s.matricola, "nome": s.nome, "cognome": s.cognome,
            "stato": stati_per_matricola.get(s.matricola),
        }
        for s in risultati[:15]
    ]


@app.get("/corsi/{tag}/appelli/{appello_id}/api/compiti/cerca")
def api_cerca_compiti(tag: str, appello_id: int, prefix: str = ""):
    if not prefix.strip():
        return []
    return compiti_service.cerca_codici(tag, appello_id, prefix.strip())


@app.get("/corsi/{tag}/appelli/{appello_id}/api/compiti/verifica")
def api_verifica_codice(tag: str, appello_id: int, codice: str = ""):
    if not codice.strip():
        return {"esiste": False}
    return compiti_service.verifica_codice(tag, appello_id, codice)


@app.get("/api/fs/sfoglia")
def fs_sfoglia(path: str = ""):
    base = Path(path).expanduser() if path.strip() else config.LEGACY_ROOT
    try:
        base = base.resolve()
        if not base.is_dir():
            base = base.parent
        if not base.is_dir():
            base = config.LEGACY_ROOT.resolve()
    except Exception:
        base = config.LEGACY_ROOT.resolve()
    cartelle = []
    try:
        for p in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if p.is_dir() and not p.name.startswith("."):
                cartelle.append(p.name)
    except PermissionError:
        pass
    genitore = str(base.parent) if base.parent != base else None
    return {"path": str(base), "genitore": genitore, "cartelle": cartelle}


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", {
        "formato_esercizi": ia_service.FORMATO_ESERCIZI,
        "corso_prova_installato": corso_prova_service.installato(), "corso_prova_tag": corso_prova_service.TAG,
    })


def _versione_app() -> Optional[str]:
    """Legge la versione da pyproject.toml nella cartella del progetto: così l'about
    mostra sempre quella del codice sorgente presente sul disco (es. subito dopo un
    ./aggiorna.sh), anche quando il pacchetto Python installato in modalità "editable"
    non viene reinstallato a ogni aggiornamento e ha quindi metadati non più aggiornati.
    Ripiega sui metadati del pacchetto installato solo se pyproject.toml non si trova."""
    try:
        testo = (PACKAGE_DIR.parent / "pyproject.toml").read_text(encoding="utf-8")
        corrispondenza = re.search(r'(?m)^version\s*=\s*"([^"]+)"', testo)
        if corrispondenza:
            return corrispondenza.group(1)
    except OSError:
        pass
    try:
        return version("quizesame")
    except PackageNotFoundError:
        return None


@app.get("/registro", response_class=HTMLResponse)
def pagina_registro(request: Request, corso: str = "", tipo: str = "", q: str = ""):
    """Registro di tutte le azioni svolte e degli aggiornamenti installati, filtrabile."""
    corsi = {c.tag: c for c in corsi_service.list_corsi()}
    voci = registro_service.leggi()
    if corso:
        voci = [v for v in voci if v.get("tag") == corso]
    if tipo:
        voci = [v for v in voci if v.get("tipo") == tipo]
    if q.strip():
        cerca = q.strip().lower()
        voci = [v for v in voci if cerca in (v.get("azione", "") + " " + v.get("esito", "")).lower()]
    nomi_appelli = {}
    for v in voci[:500]:
        chiave = (v.get("tag"), v.get("appello_id"))
        if chiave[0] in corsi and chiave[1] and chiave not in nomi_appelli:
            try:
                a = corsi_service.get_appello(*chiave)
                nomi_appelli[chiave] = a.nome if a else None
            except Exception:
                nomi_appelli[chiave] = None
    return templates.TemplateResponse(request, "registro.html", {
        "voci": voci[:500], "totale": len(voci), "corsi": corsi, "nomi_appelli": nomi_appelli,
        "filtro_corso": corso, "filtro_tipo": tipo, "q": q,
    })


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", {"versione": _versione_app()})


@app.get("/migrazione", response_class=HTMLResponse)
def migrazione_form(request: Request):
    return templates.TemplateResponse(request, "migrazione.html", {"legacy_root": str(config.LEGACY_ROOT)})


@app.post("/migrazione/esegui")
def migrazione_esegui(
    cartella: str = Form(...), tag: str = Form(...), nome: str = Form(...),
    facolta: str = Form(""), universita: str = Form(""), anno: str = Form(""), docente: str = Form(""),
):
    try:
        report = migrazione_service.migra_da_cartella(tag, cartella, nome, facolta, universita, anno, docente)
    except Exception as e:
        return flash_redirect("/migrazione", f"Errore migrazione: {e}", "error")
    msg = f"Migrazione completata: {report.dopo}"
    if report.warnings:
        msg += f" — {len(report.warnings)} avvisi (vedi corso)"
    return flash_redirect(f"/corsi/{tag}", msg)


def _porta_libera(host: str, porta: int, tentativi: int = 50) -> int:
    """Restituisce la prima porta libera a partire da `porta`."""
    import socket
    for p in range(porta, porta + tentativi):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"Nessuna porta libera tra {porta} e {porta + tentativi - 1}")


def run():
    import os
    import uvicorn
    host = "127.0.0.1"
    richiesta = int(os.environ.get("QUIZESAME_PORT", "8000"))
    port = _porta_libera(host, richiesta)
    if port != richiesta:
        print(f"Porta {richiesta} occupata: uso la {port}")
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
