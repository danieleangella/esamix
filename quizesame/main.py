import base64
import json
import math
import traceback
import webbrowser
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
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

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _static_version() -> str:
    """Data di ultima modifica di style.css, usata come parametro ?v= nel link allo
    stylesheet: così il browser scarica sempre la versione aggiornata invece di tenersi
    in cache quella vecchia ogni volta che il CSS viene modificato durante lo sviluppo."""
    try:
        return str(int((PACKAGE_DIR / "static" / "style.css").stat().st_mtime))
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
    stringa vuota se la data manca o non è nel formato atteso."""
    try:
        a, m, g = (data or "").strip().split("-")
        return f"{int(g):02d}/{int(m):02d}/{int(a):04d}"
    except ValueError:
        return ""


templates.env.filters["data_iso"] = _data_it_a_iso
templates.env.globals["static_version"] = _static_version
templates.env.globals["get_app_settings"] = app_config_service.get_settings

app = FastAPI(title="EsaMiX")
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


def _anchor_membro(appello, base: str) -> str:
    """Nome della scheda su cui tornare dopo un'azione su questo appello: per una prova
    membro di un raggruppamento le schede per-prova hanno un suffisso "-{id}" (vedi
    appello_detail.html), altrimenti il nome della scheda resta quello nudo."""
    return f"{base}-{appello.id}" if appello.membro_raggruppamento else base


def flash_redirect(url: str, message: str, kind: str = "success", anchor: str = "") -> RedirectResponse:
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    # l'anchor va anche nella query string (oltre che nel fragment): un fragment non
    # arriva mai al server, quindi se questo redirect punta a una prova membro di un
    # raggruppamento, il secondo redirect (appello_detail, verso la pagina del
    # raggruppamento) non avrebbe altrimenti modo di sapere su quale scheda tornare.
    params = f"msg={quote(message)}&kind={kind}"
    if anchor:
        params += f"&anchor={quote(anchor)}"
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
    """Solleva ValueError se questo appello ha già blocchi generati con risultati
    registrati: cambiare gli esercizi assegnati a quel punto invaliderebbe codici già
    usati da studenti reali."""
    bloccanti = compiti_service.blocchi_con_risultati(tag, appello_id)
    if bloccanti:
        raise ValueError(
            "Non puoi modificare gli esercizi di questo appello: il blocco "
            + ", ".join(str(n) for n in bloccanti)
            + " ha già risultati registrati. Elimina prima quei risultati, oppure crea un nuovo appello."
        )


def _rigenera_se_necessario(tag: str, appello_id: int) -> str:
    """Da chiamare dopo aver modificato gli esercizi assegnati a un appello: se erano già
    stati generati blocchi, li rigenera (file di riferimento compreso) con i nuovi
    esercizi, ricompila tutti i PDF in parallelo, e ritorna un suffisso da aggiungere al
    messaggio di conferma."""
    risultato = compiti_service.rigenera_tutto(tag, appello_id)
    if risultato is None:
        return ""
    blocchi = compiti_service.list_blocchi(tag, appello_id)
    da_compilare = [compiti_service.path_riferimento(tag, appello_id, "tex")]
    for b in blocchi:
        da_compilare.append(compiti_service.path_blocco(tag, appello_id, b["numero"], "tex"))
        da_compilare.append(compiti_service.path_griglia(tag, appello_id, b["numero"], "tex"))
    with ThreadPoolExecutor(max_workers=len(da_compilare)) as pool:
        list(pool.map(compiti_service.compila_pdf, da_compilare))
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
    risultati_ricerca = studenti_service.cerca_in_tutti_i_corsi(q.strip()) if q.strip() else None
    app_settings = app_config_service.get_settings()
    riepilogo_globale = statistiche_service.calcola_globale() if app_settings.mostra_riepilogo_home else None
    return templates.TemplateResponse(request, "corsi_list.html", {
        "corsi": corsi, "q": q, "risultati_ricerca": risultati_ricerca, "riepilogo_globale": riepilogo_globale,
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
    return templates.TemplateResponse(request, "impostazioni_app.html", {
        "app_settings": app_config_service.get_settings(),
    })


@app.post("/impostazioni")
def modifica_impostazioni_app(
    docente: str = Form(""),
    mostra_riepilogo_home: str = Form(""),
):
    app_config_service.update_settings(
        docente=docente.strip(), mostra_riepilogo_home=bool(mostra_riepilogo_home),
    )
    return flash_redirect("/impostazioni", "Impostazioni salvate")


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
        corsi_service.create_corso(tag, nome, facolta, universita, anno, docente)
    except ValueError as e:
        return flash_redirect("/", str(e), "error")
    return flash_redirect(f"/corsi/{tag}", "Corso creato")


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
        media = round(sum(r["voto"] for r in risultati if r["voto"] is not None) / totale, 1) if totale else None
        statistiche[a.id] = {
            "totale": totale, "sufficienti": sufficienti,
            "percentuale": round(100 * sufficienti / totale) if totale else None,
            "media": media,
        }
    raggruppamenti_appelli = {r.appello_id: corsi_service.get_appello(tag, r.appello_id) for r in raggruppamenti}
    return templates.TemplateResponse(request, "corso_detail.html", {
        "corso": corso, "appelli": appelli, "raggruppamenti": raggruppamenti, "statistiche": statistiche,
        "raggruppamenti_appelli": raggruppamenti_appelli,
    })


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
    })


@app.post("/corsi/{tag}/impostazioni")
def modifica_corso(
    tag: str, nome: str = Form(...), facolta: str = Form(""), universita: str = Form(""),
    anno: str = Form(""), docente: str = Form(""), votomin: int = Form(...), consegna: int = Form(...),
    votomin_raggruppamento: int = Form(...),
    risposta_corretta: int = Form(...), risposta_sbagliata: int = Form(...), risposta_vuota: int = Form(...),
    punteggio_max_aperta: int = Form(corsi_service.DEFAULT_PUNTEGGIO_MAX_APERTA),
    frase_consegna: str = Form(""), frase_regole: str = Form(""),
    orale_dopo_richiesta: str = Form(""), orale_soglia_attiva: str = Form(""),
    orale_soglia_n: str = Form(""), orale_soglia_voto: str = Form(""),
    ritirato_conta_insufficiente: str = Form(""), domande_esame: str = Form(""),
):
    try:
        corsi_service.update_meta(
            tag, nome=nome, facolta=facolta, universita=universita, anno=anno, docente=docente,
            votomin=str(votomin), consegna=str(consegna), votomin_raggruppamento=str(votomin_raggruppamento),
            risposta_corretta=str(risposta_corretta), risposta_sbagliata=str(risposta_sbagliata),
            risposta_vuota=str(risposta_vuota), punteggio_max_aperta=str(punteggio_max_aperta),
            frase_consegna=frase_consegna.strip() or corsi_service.DEFAULT_FRASE_CONSEGNA,
            frase_regole=frase_regole.strip() or corsi_service.DEFAULT_FRASE_REGOLE,
            orale_dopo_richiesta="1" if orale_dopo_richiesta else "0",
            orale_soglia_attiva="1" if orale_soglia_attiva else "0",
            orale_soglia_n=orale_soglia_n.strip(), orale_soglia_voto=orale_soglia_voto.strip(),
            ritirato_conta_insufficiente="1" if ritirato_conta_insufficiente else "0",
            domande_esame=domande_esame.strip(),
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/impostazioni", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/impostazioni", "Impostazioni corso salvate")


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


@app.post("/corsi/{tag}/appelli/nuovo")
def crea_appello(tag: str, nome: str = Form(...), data: str = Form("")):
    try:
        corsi_service.create_appello(tag, nome=nome.strip(), data=_data_iso_a_it(data) or None)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}", str(e), "error")
    return flash_redirect(f"/corsi/{tag}", "Appello creato")


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
    valutati = sum(1 for r in risultati if r["esito"] == "voto")
    sufficienti = sum(
        1 for r in risultati
        if r["esito"] == "voto" and r["voto"] is not None and r["voto"] >= dati["votomin_effettivo"]
    )
    presenti = (numero_iscritti - assenti) if numero_iscritti is not None else None
    valutati_totale = valutati + assenti + ritirati

    fasi = []
    fasi.append({
        "nome": "Preparazione testo", "fatta": bool(dati["esercizi_assegnati"]),
        "dettaglio": f"{len(dati['esercizi_assegnati'])} esercizi assegnati",
    })
    fase_stampa_fatta = bool(dati["blocchi"]) and (numero_iscritti is None or compiti_totali >= numero_iscritti)
    dettaglio_stampa = f"{compiti_totali} compiti generati"
    if numero_iscritti is not None:
        dettaglio_stampa += f" su {numero_iscritti} iscritti"
    fasi.append({"nome": "Stampa dei compiti", "fatta": fase_stampa_fatta, "dettaglio": dettaglio_stampa})

    atteso = numero_iscritti if numero_iscritti is not None else (compiti_totali or None)
    fase_valutazione_fatta = bool(atteso) and valutati_totale >= atteso
    fase_valutazione_in_corso = bool(atteso) and 0 < valutati_totale < atteso
    dettaglio_valutazione = f"{valutati_totale} valutati" + (f" su {atteso}" if atteso else "")
    fasi.append({
        "nome": "Valutazione scritti", "fatta": fase_valutazione_fatta,
        "in_corso": fase_valutazione_in_corso, "dettaglio": dettaglio_valutazione,
    })

    orali_richiesti = any(r["richiede_orale"] for r in risultati)
    if orali_richiesti:
        orali_rimasti = sum(1 for r in risultati if r["richiede_orale"] and not r["orale_svolto"])
        fasi.append({
            "nome": "Orali", "fatta": orali_rimasti == 0,
            "dettaglio": "tutti svolti" if orali_rimasti == 0 else f"{orali_rimasti} da svolgere",
        })

    fasi.append({
        "nome": "Chiusura appello", "fatta": appello.chiuso,
        "dettaglio": "chiuso" if appello.chiuso else "ancora aperto",
    })

    punti = sum(1.0 if f.get("fatta") else (0.5 if f.get("in_corso") else 0.0) for f in fasi)
    percentuale = round(100 * punti / len(fasi)) if fasi else 0

    return {
        "data": appello.data, "compiti_stampati": compiti_totali, "iscritti": numero_iscritti,
        "presenti": presenti, "assenti": assenti, "ritirati": ritirati, "sufficienti": sufficienti,
        "valutati": valutati, "fasi": fasi, "percentuale": percentuale,
    }


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
        "votomin_effettivo": corsi_service.effective_votomin(corso, appello),
        "consegna_effettivo": corsi_service.effective_consegna(corso, appello),
        "segreteria_csv": esportazione_service.get_segreteria_csv(tag, appello.id),
        "iscritti": iscritti,
        "numero_iscritti": numero_iscritti,
        "numero_studenti_suggerito": _numero_studenti_suggerito(numero_iscritti, len(compiti)),
        "avviso_pochi_compiti": numero_iscritti is not None and len(compiti) < numero_iscritti,
    }
    dati["riepilogo_home"] = _calcola_riepilogo_home(corso, appello, dati)
    return dati


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

    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    contesto = {
        "corso": corso, "appello": appello, "raggruppamento": raggruppamento,
        "orali_da_svolgere": correzione_service.list_orali_da_svolgere(tag, appello_id),
        "idonei": verbalizzazione_service.list_idonei(tag, appello_id),
        "verbalizzati": verbalizzazione_service.list_verbalizzati(tag, appello_id),
        "statistiche": statistiche_service.calcola(tag, appello_id),
        "votomin_effettivo": corsi_service.effective_votomin(corso, appello),
        "consegna_effettivo": corsi_service.effective_consegna(corso, appello),
    }
    if raggruppamento:
        contesto["membri_dati"] = [
            {**_dati_appello(tag, corso, m), "aule": aule_service.list_aule(tag, m.id),
             "ammessi": corsi_service.list_ammessi_prova(tag, raggruppamento, i)}
            for i, m in enumerate(raggruppamento.membri)
        ]
        contesto["statistiche_confronto"] = statistiche_service.confronto_raggruppamento(tag, raggruppamento)
        contesto["risultati"] = correzione_service.list_risultati(tag, appello_id)
        contesto["segreteria_csv"] = esportazione_service.get_segreteria_csv(tag, appello_id)
        percentuali = [md["riepilogo_home"]["percentuale"] for md in contesto["membri_dati"]]
        contesto["riepilogo_home_raggruppamento"] = {
            "percentuale": round(sum(percentuali) / len(percentuali)) if percentuali else 0,
            "chiuso": appello.chiuso,
        }
    else:
        contesto.update(_dati_appello(tag, corso, appello))
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
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Appello chiuso: non sono più possibili modifiche")


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
    with ThreadPoolExecutor(max_workers=len(da_compilare)) as pool:
        risultati_compilazione = dict(zip(da_compilare, pool.map(compiti_service.compila_pdf, da_compilare.values())))
    for etichetta in ["Riferimento", "Blocco", "Griglia"]:
        if etichetta not in risultati_compilazione:
            continue
        comp = risultati_compilazione[etichetta]
        msg += f". {etichetta}: " + comp.messaggio
        if not comp.ok:
            kind = "error"

    if result.avviso_obbligatori:
        msg += " " + result.avviso_obbligatori
        kind = "warning" if kind == "success" else kind
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, kind, anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.tex", response_class=PlainTextResponse)
def scarica_riferimento_tex(tag: str, appello_id: int):
    path = compiti_service.path_riferimento(tag, appello_id, "tex")
    if not path.exists():
        return PlainTextResponse("Testo non ancora generato", status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/riferimento.pdf")
def scarica_riferimento_pdf(tag: str, appello_id: int):
    path = compiti_service.path_riferimento(tag, appello_id, "pdf")
    if not path.exists():
        return PlainTextResponse("PDF non ancora compilato", status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


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
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error", anchor=anchor)
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


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/assegna")
async def assegna_esercizio(tag: str, appello_id: int, request: Request):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    form = await request.form()
    esercizio_ids = [int(v) for v in form.getlist("esercizio_ids")]
    obbligatorio = bool(form.get("obbligatorio"))
    if not esercizio_ids:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessun esercizio selezionato", "error", anchor=anchor)
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        for esercizio_id in esercizio_ids:
            esercizi_service.assegna_a_appello(tag, appello_id, esercizio_id, obbligatorio=obbligatorio)
        msg = f"{len(esercizio_ids)} esercizi assegnati" + _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/rimuovi")
def rimuovi_esercizio(tag: str, appello_id: int, esercizio_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        esercizi_service.rimuovi_da_appello(tag, appello_id, esercizio_id)
        msg = "Esercizio rimosso dall'appello" + _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/obbligatorio")
def imposta_obbligatorio(tag: str, appello_id: int, esercizio_id: int, obbligatorio: str = Form("")):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        esercizi_service.imposta_obbligatorio(tag, appello_id, esercizio_id, bool(obbligatorio))
        msg = "Esercizio aggiornato" + _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/sposta-su")
def sposta_su_esercizio(tag: str, appello_id: int, esercizio_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        esercizi_service.sposta_esercizio(tag, appello_id, esercizio_id, -1)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Ordine aggiornato", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/{esercizio_id}/sposta-giu")
def sposta_giu_esercizio(tag: str, appello_id: int, esercizio_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        esercizi_service.sposta_esercizio(tag, appello_id, esercizio_id, 1)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Ordine aggiornato", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/nuovo")
async def nuovo_esercizio_appello(tag: str, appello_id: int, request: Request):
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
        msg = "Esercizio creato e assegnato" + _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


@app.get("/corsi/{tag}/appelli/{appello_id}/esercizi/esporta")
def esporta_esercizi(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    dati = esercizi_service.esporta_esercizi_appello(tag, appello_id)
    contenuto = json.dumps(dati, ensure_ascii=False, indent=2)
    return Response(
        contenuto, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="esercizi-{appello.slug}.json"'},
    )


@app.post("/corsi/{tag}/esercizi/importa-json", response_class=HTMLResponse)
async def importa_esercizi_json_banca(request: Request, tag: str, file: UploadFile = File(...)):
    try:
        contenuto_bytes = await file.read()
        contenuto = json.loads(contenuto_bytes.decode("utf-8"))
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
async def importa_esercizi_json(request: Request, tag: str, appello_id: int, file: UploadFile = File(...)):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "creazione")
    try:
        _proteggi_modifica_esercizi(tag, appello_id)
        contenuto_bytes = await file.read()
        contenuto = json.loads(contenuto_bytes.decode("utf-8"))
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
    })


@app.post("/corsi/{tag}/appelli/{appello_id}/esercizi/importa-json/conferma")
async def importa_esercizi_json_conferma(request: Request, tag: str, appello_id: int):
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
        n = esercizi_service.importa_json(tag, appello_id, scelti)
        msg = f"Importati {n} esercizi dal file" + _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Errore import: {e}", "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


def _render_correggi_revisione(
    request: Request, tag: str, appello_id: int, valutazione, voto_proposto=None, punteggi_proposti=None,
    errore: str = "", richiedi_orale_checked=None, modifica: bool = False,
):
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    if voto_proposto is None:
        voto_proposto = valutazione.voto_base
    if punteggi_proposti is None:
        punteggi_proposti = {r.posizione: corso.risposta_corretta for r in valutazione.da_valutare}
    if richiedi_orale_checked is None:
        richiedi_orale_checked = bool(valutazione.orale_obbligatorio)
    corretta_grezza = "".join(r.lettera_corretta for r in valutazione.righe)
    gruppi = [corretta_grezza[i:i + 5] for i in range(0, len(corretta_grezza), 5)]
    risposte_corrette_raggruppate = " ".join(gruppi)

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
        "punteggi_proposti": punteggi_proposti, "errore": errore,
        "richiedi_orale_checked": richiedi_orale_checked, "modifica": modifica,
        "risposte_corrette_raggruppate": risposte_corrette_raggruppate,
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
def correggi(
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
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return _render_correggi_revisione(request, tag, appello_id, valutazione, modifica=bool(modifica))


@app.post("/corsi/{tag}/appelli/{appello_id}/correggi/conferma")
async def correggi_conferma(request: Request, tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    form = await request.form()
    matricola = form.get("matricola") or ""
    codice = form.get("codice") or ""
    risposte = (form.get("risposte") or "").strip().replace(" ", "").upper()
    azione = form.get("azione") or "salva"
    sospendi_valutazione = bool(form.get("sospendi_valutazione"))
    orale_motivazione = (form.get("orale_motivazione") or "").strip()
    conferma_orale_obbligatorio = bool(form.get("conferma_orale_obbligatorio"))
    modifica = bool(form.get("modifica"))

    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)

    try:
        valutazione = correzione_service.valuta_preliminare(tag, appello_id, matricola, codice, risposte)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)

    punteggi_obbligatori = {}
    for r in valutazione.da_valutare + valutazione.domande_aperte:
        valore = form.get(f"punteggio_{r.posizione}")
        if valore is not None and valore != "":
            punteggi_obbligatori[r.posizione] = int(valore)
    # con "Completa successivamente" i campi non compilati sono valutazioni rimandate,
    # non errori: il voto proposto dal client andrebbe ricalcolato assumendo un punteggio
    # ottimistico anche per quelle posizioni, quindi si lascia decidere al server (None).
    voto_str = form.get("voto_finale")
    voto_finale = None if sospendi_valutazione else (int(voto_str) if voto_str not in (None, "") else None)

    richiedi_orale = azione == "richiedi_orale" and not sospendi_valutazione

    try:
        result = correzione_service.conferma_risultato(
            tag, appello_id, matricola, codice, risposte, voto_finale=voto_finale,
            punteggi_obbligatori=punteggi_obbligatori, richiedi_orale=richiedi_orale,
            orale_motivazione=orale_motivazione, conferma_orale_obbligatorio=conferma_orale_obbligatorio,
            modifica=modifica, sospendi_valutazione=sospendi_valutazione,
        )
    except correzione_service.OraleObbligatorioNonConfermato as e:
        return _render_correggi_revisione(
            request, tag, appello_id, valutazione, voto_proposto=voto_finale, punteggi_proposti=punteggi_obbligatori,
            errore=(
                f"Questo studente deve fare l'orale in ogni appello (imposto in \"{e.origine}\": {e.motivazione}). "
                "Conferma la casella qui sotto per salvare comunque un voto scritto, oppure spunta \"Richiedi l'orale\"."
            ),
            richiedi_orale_checked=richiedi_orale, modifica=modifica,
        )
    except correzione_service.OraleNonConsentito as e:
        return _render_correggi_revisione(
            request, tag, appello_id, valutazione, voto_proposto=voto_finale, punteggi_proposti=punteggi_obbligatori,
            errore=str(e), richiedi_orale_checked=richiedi_orale, modifica=modifica,
        )
    except correzione_service.RisultatoGiaValutato as e:
        return _render_correggi_revisione(
            request, tag, appello_id, valutazione, voto_proposto=voto_finale, punteggi_proposti=punteggi_obbligatori,
            errore=str(e), richiedi_orale_checked=richiedi_orale, modifica=modifica,
        )
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)

    if result.valutazione_sospesa:
        msg = (
            f"Valutazione di {result.nome} {result.cognome} messa in sospeso (voto provvisorio: {result.voto}): "
            "completala dalla sezione \"Valutazioni in sospeso\""
        )
    elif result.richiede_orale:
        msg = f"Orale richiesto per {result.nome} {result.cognome} (voto scritto di riferimento: {result.voto})"
    else:
        msg = f"Voto calcolato per {result.nome} {result.cognome}: {result.voto}"
        if result.insufficiente_per_obbligatorio:
            msg += " (insufficiente: esercizio obbligatorio non svolto)"
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", msg, anchor=anchor)


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
def segna_assenza(tag: str, appello_id: int, matricola: str = Form(...), esito: str = Form(...)):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "valutazione")
    errore_iscritto = _errore_non_iscritto(tag, appello_id, matricola)
    if errore_iscritto:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", errore_iscritto, "error", anchor=anchor)
    try:
        correzione_service.segna_esito_speciale(tag, appello_id, matricola, esito)
    except Exception as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", f"Studente segnato come {esito}", anchor=anchor)


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
        "storico": dettaglio["storico"],
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
    return templates.TemplateResponse(request, "_risultato_dettaglio.html", {
        "corso_tag": tag, "risultato": dettaglio["risultato"], "righe": dettaglio["righe"],
        "storico": dettaglio["storico"],
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
    punteggi_esistenti = json.loads(r["punteggi_obbligatori"]) if r["punteggi_obbligatori"] else {}
    punteggi_proposti = {int(k): v for k, v in punteggi_esistenti.items()}
    return _render_correggi_revisione(
        request, tag, appello_id, valutazione, voto_proposto=r["voto"], punteggi_proposti=punteggi_proposti,
        modifica=True,
    )


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.tex", response_class=PlainTextResponse)
def scarica_risultati(tag: str, appello_id: int):
    tex = risultati_service.stampa_risultati(tag, appello_id)
    return PlainTextResponse(tex, media_type="application/x-tex")


@app.get("/corsi/{tag}/appelli/{appello_id}/risultati.pdf")
def scarica_risultati_pdf(tag: str, appello_id: int):
    tex = risultati_service.stampa_risultati(tag, appello_id)
    tex_path = compiti_service.path_risultati(tag, appello_id, "tex")
    tex_path.write_text(tex, encoding="utf-8")
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        appello = corsi_service.get_appello(tag, appello_id)
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error", anchor=_anchor_membro(appello, "valutazione"))
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


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


@app.post("/corsi/{tag}/appelli/{appello_id}/segreteria-csv")
async def carica_segreteria_csv(tag: str, appello_id: int, file: UploadFile = File(...)):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_esporta(tag, appello)
    contenuto = await file.read()
    try:
        n_iscritti = esportazione_service.carica_csv_segreteria(tag, appello_id, file.filename, contenuto)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=anchor)
    return flash_redirect(
        f"/corsi/{tag}/appelli/{appello_id}", f"File caricato: {n_iscritti} iscritti trovati", anchor=anchor,
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/segreteria-csv/elimina")
def elimina_segreteria_csv_route(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_esporta(tag, appello)
    esportazione_service.elimina_segreteria_csv(tag, appello_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "File della segreteria rimosso", anchor=anchor)


@app.post("/corsi/{tag}/appelli/{appello_id}/iscritti-manuale")
def imposta_iscritti_manuale(tag: str, appello_id: int, iscritti_manuale: str = Form("")):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    valore = int(iscritti_manuale) if iscritti_manuale.strip() else None
    corsi_service.update_appello(tag, appello_id, iscritti_manuale=valore)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Numero di iscritti salvato", anchor=anchor)


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


@app.get("/corsi/{tag}/appelli/{appello_id}/iscritti.pdf")
def scarica_iscritti_pdf(tag: str, appello_id: int):
    appello = corsi_service.get_appello(tag, appello_id)
    anchor = _anchor_membro(appello, "compiti")
    iscritti = esportazione_service.list_iscritti(tag, appello_id)
    if iscritti is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Nessun elenco iscritti caricato", "error", anchor=anchor)
    corso = corsi_service.get_corso(tag)
    ctx = risultati_service.ctx_for(tag, corso, appello)
    tex = latex_service.crea_tex_ammessi(ctx, f"Studenti iscritti: {appello.nome}", iscritti, [])
    tex_path = compiti_service.path_iscritti(tag, appello_id, "tex", appello=appello)
    tex_path.write_text(tex, encoding="utf-8")
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error", anchor=anchor)
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


@app.get("/corsi/{tag}/appelli/{appello_id}/esporta-voti")
def esporta_voti(request: Request, tag: str, appello_id: int):
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    appello_singolo = corsi_service.get_appello(tag, appello_id)
    anchor_errore = _anchor_esporta(tag, appello_singolo)
    try:
        testo, codifica, compilati, extra = esportazione_service.compila_export_voti(tag, appello_id)
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
def modifica_raggruppamento(tag: str, appello_id: int, matricola_minima_prima_prova: str = Form("")):
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questo appello non è un raggruppamento", "error")
    corsi_service.update_raggruppamento_soglia(tag, raggruppamento.id, matricola_minima_prima_prova.strip() or None)
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


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/nuova")
def crea_aula(tag: str, appello_id: int, nome: str = Form(...), capienza: int = Form(...)):
    aule_service.crea_aula(tag, appello_id, nome, capienza)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Aula aggiunta", anchor=f"compiti-{appello_id}")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{aula_id}/elimina")
def elimina_aula(tag: str, appello_id: int, aula_id: int):
    aule_service.elimina_aula(tag, appello_id, aula_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Aula eliminata", anchor=f"compiti-{appello_id}")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/assegna")
def assegna_aule(tag: str, appello_id: int):
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questa prova non appartiene a un raggruppamento", "error")
    ammessi = corsi_service.list_ammessi_prova(tag, raggruppamento, indice)
    aule_service.assegna_automatica(tag, appello_id, [s["matricola"] for s in ammessi])
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Assegnazione aule aggiornata", anchor=f"compiti-{appello_id}")


@app.post("/corsi/{tag}/appelli/{appello_id}/aule/{matricola}/assegna-manuale")
def assegna_aula_manuale(tag: str, appello_id: int, matricola: str, aula_id: int = Form(...)):
    aule_service.assegna_manuale(tag, appello_id, matricola, aula_id)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Aula assegnata", anchor=f"compiti-{appello_id}")


@app.post("/corsi/{tag}/appelli/{appello_id}/ammessi/aggiungi")
def aggiungi_ammesso_manuale(tag: str, appello_id: int, matricola: str = Form(...)):
    try:
        corsi_service.ammetti_manualmente(tag, appello_id, matricola.strip())
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", str(e), "error", anchor=f"compiti-{appello_id}")
    return flash_redirect(
        f"/corsi/{tag}/appelli/{appello_id}",
        "Studente aggiunto agli ammessi: verifica che soddisfi davvero i requisiti, se non è già segnalato in deroga",
        anchor=f"compiti-{appello_id}",
    )


@app.post("/corsi/{tag}/appelli/{appello_id}/ammessi/{matricola}/rimuovi")
def rimuovi_ammesso_manuale(tag: str, appello_id: int, matricola: str):
    corsi_service.rimuovi_ammissione_manuale(tag, appello_id, matricola)
    return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Rimosso dall'elenco ammessi", anchor=f"compiti-{appello_id}")


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
    raggruppamento, indice = _membro_e_indice(tag, appello_id)
    if raggruppamento is None:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", "Questa prova non appartiene a un raggruppamento", "error")
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    ammessi = corsi_service.list_ammessi_prova(tag, raggruppamento, indice)
    aule = aule_service.list_aule(tag, appello_id)
    ctx = risultati_service.ctx_for(tag, corso, appello)
    tex = latex_service.crea_tex_ammessi(ctx, f"Studenti ammessi: {appello.nome}", ammessi, aule)
    tex_path = compiti_service.path_ammessi(tag, appello_id, "tex", appello=appello)
    tex_path.write_text(tex, encoding="utf-8")
    compilazione = compiti_service.compila_pdf(tex_path)
    if not compilazione.ok:
        return flash_redirect(f"/corsi/{tag}/appelli/{appello_id}", compilazione.messaggio, "error", anchor=f"compiti-{appello_id}")
    return FileResponse(compilazione.pdf_path, media_type="application/pdf", filename=compilazione.pdf_path.name)


@app.get("/corsi/{tag}/studenti", response_class=HTMLResponse)
def studenti_list(request: Request, tag: str, q: str = "", da: str = "", filtro: str = "", dsa: str = ""):
    corso = corsi_service.get_corso(tag)
    lista = studenti_service.list_studenti(tag, q or None)
    stato_esame = verbalizzazione_service.stato_esame_studenti(tag)
    if filtro == "fatto":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "verbalizzato"]
    elif filtro == "da_verbalizzare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "da_verbalizzare"]
    elif filtro == "da_fare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") not in ("verbalizzato", "da_verbalizzare")]
    if dsa == "1":
        lista = [s for s in lista if s.dsa]
    tutti_corsi = corsi_service.list_corsi()
    corsi_suggeriti = corsi_service.corsi_simili(corso, tutti_corsi)
    altri_corsi = [c for c in tutti_corsi if c.tag != tag]
    corso_sorgente = corsi_service.get_corso(da) if da and config.corso_exists(da) else None
    studenti_sorgente = studenti_service.list_non_superati(da) if corso_sorgente else []
    return templates.TemplateResponse(request, "studenti.html", {
        "corso": corso, "studenti": lista, "q": q, "filtro": filtro, "dsa": dsa, "stato_esame": stato_esame,
        "corsi_suggeriti": corsi_suggeriti, "altri_corsi": altri_corsi,
        "corso_sorgente": corso_sorgente, "studenti_sorgente": studenti_sorgente,
    })


@app.get("/corsi/{tag}/studenti/esporta.csv")
def esporta_studenti(tag: str, q: str = "", filtro: str = ""):
    lista = studenti_service.list_studenti(tag, q or None)
    stato_esame = verbalizzazione_service.stato_esame_studenti(tag)
    if filtro == "fatto":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "verbalizzato"]
    elif filtro == "da_verbalizzare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") == "da_verbalizzare"]
    elif filtro == "da_fare":
        lista = [s for s in lista if stato_esame.get(s.matricola, {}).get("stato") not in ("verbalizzato", "da_verbalizzare")]
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
    })


@app.post("/corsi/{tag}/studenti/nuovo")
def nuovo_studente(
    tag: str, matricola: str = Form(...), nome: str = Form(...), cognome: str = Form(...), dsa: bool = Form(False),
):
    try:
        studenti_service.crea_studente(tag, matricola, nome, cognome, dsa=dsa)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/studenti", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/studenti", "Studente salvato")


@app.post("/corsi/{tag}/studenti/{matricola}/modifica")
def modifica_studente(
    tag: str, matricola: str, nome: str = Form(...), cognome: str = Form(...), nuova_matricola: str = Form(""),
    dsa: bool = Form(False),
):
    try:
        studenti_service.aggiorna_studente(tag, matricola, nome, cognome, nuova_matricola or None, dsa=dsa)
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
def esercizi_list(request: Request, tag: str, da: str = ""):
    corso = corsi_service.get_corso(tag)
    esercizi = esercizi_service.list_esercizi(tag)
    tutti_corsi = corsi_service.list_corsi()
    corsi_suggeriti = corsi_service.corsi_simili(corso, tutti_corsi)
    altri_corsi = [c for c in tutti_corsi if c.tag != tag]

    corso_sorgente = None
    esercizi_sorgente = []
    if da and config.corso_exists(da) and da != tag:
        corso_sorgente = corsi_service.get_corso(da)
        esercizi_sorgente = esercizi_service.list_esercizi(da)

    blocchi_generati = {}
    for e in esercizi:
        appelli_coinvolti = esercizi_service.appelli_che_usano(tag, e.id)
        blocchi_generati[e.id] = any(compiti_service.list_blocchi(tag, aid) for aid in appelli_coinvolti)

    return templates.TemplateResponse(request, "esercizi.html", {
        "corso": corso, "esercizi": esercizi, "corsi_suggeriti": corsi_suggeriti, "altri_corsi": altri_corsi,
        "corso_sorgente": corso_sorgente, "esercizi_sorgente": esercizi_sorgente,
        "argomenti": esercizi_service.list_argomenti(tag), "blocchi_generati": blocchi_generati,
        "duplicati": esercizi_service.trova_duplicati(tag),
    })


@app.post("/corsi/{tag}/esercizi/collega-importa")
async def collega_importa_esercizi(tag: str, request: Request):
    form = await request.form()
    tag_sorgente = form.get("tag_sorgente") or ""
    esercizio_ids = [int(v) for v in form.getlist("esercizio_ids")]
    if not esercizio_ids:
        return flash_redirect(f"/corsi/{tag}/esercizi?da={tag_sorgente}", "Nessun esercizio selezionato", "error")
    n = esercizi_service.importa_da_altro_corso(tag, tag_sorgente, esercizio_ids)
    return flash_redirect(f"/corsi/{tag}/esercizi", f"Importati {n} esercizi dal corso '{tag_sorgente}'")


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
async def modifica_esercizio(tag: str, esercizio_id: int, request: Request):
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
            msg += _rigenera_se_necessario(tag, appello_id)
    except ValueError as e:
        return flash_redirect(destinazione, str(e), "error", anchor=ancora)
    return flash_redirect(destinazione, msg, anchor=ancora)


@app.post("/corsi/{tag}/esercizi/{esercizio_id}/elimina")
def elimina_esercizio(tag: str, esercizio_id: int):
    try:
        esercizi_service.elimina_esercizio(tag, esercizio_id)
    except ValueError as e:
        return flash_redirect(f"/corsi/{tag}/esercizi", str(e), "error")
    return flash_redirect(f"/corsi/{tag}/esercizi", "Esercizio eliminato")


@app.get("/api/studenti/cerca")
def api_cerca_studenti_globale(q: str = ""):
    return studenti_service.cerca_globale(q)


@app.get("/corsi/{tag}/api/studenti/cerca")
def api_cerca_studenti(tag: str, q: str = ""):
    if not q.strip():
        return []
    return [
        {"matricola": s.matricola, "nome": s.nome, "cognome": s.cognome}
        for s in studenti_service.list_studenti(tag, q.strip())[:15]
    ]


@app.get("/corsi/{tag}/appelli/{appello_id}/api/compiti/cerca")
def api_cerca_compiti(tag: str, appello_id: int, prefix: str = ""):
    if not prefix.strip():
        return []
    return compiti_service.cerca_codici(tag, appello_id, prefix.strip())


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
    return templates.TemplateResponse(request, "help.html", {})


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    try:
        versione = version("quizesame")
    except PackageNotFoundError:
        versione = None
    return templates.TemplateResponse(request, "about.html", {"versione": versione})


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


def run():
    import uvicorn
    host, port = "127.0.0.1", 8000
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
