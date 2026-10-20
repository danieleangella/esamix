import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Optional

from quizesame import config, db
from quizesame.services import esercizi as esercizi_service
from quizesame.services import corsi as corsi_service
from quizesame.services.latex import (
    LatexContext, crea_file_riferimento, crea_file_blocco, crea_file_griglia, crea_file_anteprima, BEGIN_DOCUMENT,
)


@dataclass
class BloccoResult:
    numero: int
    n_compiti_generati: int
    n_saltati_duplicati: int
    avviso_obbligatori: str | None = None
    riferimento_generato: bool = False


@dataclass
class RigenerazioneResult:
    n_blocchi: int


def _esercizi_per_generazione(tag: str, appello_id: int):
    esercizi = esercizi_service.list_esercizi_appello(tag, appello_id)
    if not esercizi:
        raise ValueError("Nessun esercizio assegnato a questo appello: aggiungine dalla pagina dell'appello")
    for e in esercizi:
        if not e.varianti:
            raise ValueError(f"L'esercizio '{e.nome or e.id}' non ha nessuna variante")

    return [
        {
            "esercizio_id": e.id, "nome": e.nome, "obbligatorio": e.obbligatorio, "aperta": e.aperta,
            "soluzione": e.soluzione,
            "varianti": [{"variante_id": v.id, "testo": v.testo, "risposte": list(v.risposte)} for v in e.varianti],
        }
        for e in esercizi
    ]


def anteprima_riferimento(tag: str, appello_id: int, tutte_varianti: bool = False) -> list[dict]:
    """Righe del "Compito A" del foglio di riferimento (prima variante di ciascun
    esercizio assegnato, risposte non mischiate: la corretta è sempre la prima), da
    mostrare in pagina senza generare/scaricare il PDF. Con `tutte_varianti` ogni riga ha
    anche "varianti": tutte le varianti dell'esercizio (anteprima di controllo a schermo)."""
    esercizi = esercizi_service.list_esercizi_appello(tag, appello_id)
    righe = []
    for e in esercizi:
        if not e.varianti:
            continue
        variante = e.varianti[0]
        righe.append({
            "esercizio_nome": e.nome or f"Esercizio #{e.id}", "obbligatorio": e.obbligatorio,
            "aperta": e.aperta, "testo": variante.testo, "risposte": list(variante.risposte),
            "soluzione": e.soluzione or "", "n_varianti": len(e.varianti),
            "varianti": [{"testo": v.testo, "risposte": list(v.risposte)} for v in e.varianti] if tutte_varianti else [],
        })
    return righe


def anteprima_blocco(tag: str, appello_id: int, numero: int) -> Optional[dict]:
    """Il primo compito del blocco indicato, testo e risposte esattamente come stampate
    su quel foglio (ordine mischiato, nessuna indicata come corretta: è quello che vede
    lo studente), da mostrare in pagina senza scaricare il PDF."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        blocco = conn.execute(
            "SELECT id FROM blocchi WHERE appello_id=? AND numero=?", (appello_id, numero)
        ).fetchone()
        if blocco is None:
            return None
        compito = conn.execute(
            "SELECT id, codice FROM compiti WHERE blocco_id=? ORDER BY id LIMIT 1", (blocco["id"],)
        ).fetchone()
        if compito is None:
            return None
        posizioni = conn.execute(
            "SELECT ce.posizione, ce.obbligatorio, ce.aperta, ce.risposte_mischiate, "
            "e.nome AS esercizio_nome, ev.testo FROM compito_esercizi ce "
            "JOIN esercizi e ON e.id = ce.esercizio_id JOIN esercizio_varianti ev ON ev.id = ce.variante_id "
            "WHERE ce.compito_id=? ORDER BY ce.posizione",
            (compito["id"],),
        ).fetchall()
        return {
            "codice": compito["codice"],
            "righe": [
                {
                    "esercizio_nome": p["esercizio_nome"], "obbligatorio": bool(p["obbligatorio"]),
                    "aperta": bool(p["aperta"]), "testo": p["testo"],
                    "risposte": json.loads(p["risposte_mischiate"]) if p["risposte_mischiate"] else [],
                }
                for p in posizioni
            ],
        }
    finally:
        conn.close()


def _out_dir(tag: str) -> Path:
    out_dir = config.corso_dir(tag) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def path_riferimento(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    # `appello`, se già disponibile al chiamante (es. la pagina dell'appello, che lo
    # carica una volta sola), evita di riaprire una connessione al database solo per
    # rileggere lo slug: rilevante quando questa funzione è chiamata più volte per pagina
    # (es. una volta per blocco) su un progetto sincronizzato con Dropbox, dove aprire un
    # file sqlite è più lento che su disco locale.
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"riferimento-{appello.slug}.{ext}"


def path_riferimento_soluzioni(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    """Come il foglio di riferimento, ma con soluzioni/suggerimenti sotto ogni esercizio:
    per il docente (o da pubblicare dopo la prova), mentre quello senza si può pubblicare."""
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"riferimento-soluzioni-{appello.slug}.{ext}"


def path_anteprima(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"anteprima-{appello.slug}.{ext}"


def _salva_riferimenti(tag: str, appello_id: int, ctx: LatexContext, esercizi_struct: list[dict]) -> None:
    """Scrive le due versioni del foglio di riferimento: senza e con soluzioni."""
    _salva_testo(path_riferimento(tag, appello_id, "tex"), crea_file_riferimento(ctx, esercizi_struct))
    _salva_testo(
        path_riferimento_soluzioni(tag, appello_id, "tex"),
        crea_file_riferimento(ctx, esercizi_struct, con_soluzioni=True),
    )


def riferimento_aggiornato(tag: str, appello_id: int, con_soluzioni: bool = False) -> tuple[Path, bool]:
    """Riscrive il .tex del foglio di riferimento (da pubblicare, con o senza soluzioni)
    con gli esercizi e le impostazioni attuali, solo se è cambiato. Ritorna il percorso e
    se è cambiato: in quel caso, o se il PDF manca, il chiamante lo ricompila. Così il
    file da mettere online riflette sempre gli esercizi attuali, anche dopo modifiche."""
    appello = corsi_service.get_appello(tag, appello_id)
    ctx = _crea_contesto(corsi_service.get_corso(tag), appello)
    tex = crea_file_riferimento(ctx, _esercizi_per_generazione(tag, appello_id), con_soluzioni=con_soluzioni)
    path = (path_riferimento_soluzioni if con_soluzioni else path_riferimento)(tag, appello_id, "tex", appello=appello)
    cambiato = not path.exists() or path.read_text(encoding="utf-8") != tex
    if cambiato:
        _salva_testo(path, tex)
    return path, cambiato


def compiti_oltre_due_pagine(tag: str, tex_path: Path, pagine: int | None) -> int | None:
    """Se il PDF di un blocco ha più di due pagine per compito, quante ne ha in media
    (arrotondate per eccesso a un numero pari); None se va tutto bene."""
    if not pagine:
        return None
    testo = tex_path.read_text(encoding="utf-8") if tex_path.exists() else ""
    n_compiti = testo.count("Codice del compito")
    if n_compiti and pagine > 2 * n_compiti:
        return pagine // n_compiti
    return None


def ricostruisci_documenti(tag: str, appello_id: int) -> list[Path]:
    """Riscrive con la grafica attuale i .tex dei documenti dell'appello — fogli di
    riferimento, anteprima e, se ci sono, testo e griglia di ogni blocco — mantenendo gli stessi codici,
    lo stesso ordine di esercizi e risposte e quindi le stesse griglie: i compiti già
    stampati o distribuiti restano validi. Usa il testo attuale degli esercizi (se un
    esercizio è stato modificato dopo la generazione, compare la versione modificata).
    Ritorna i .tex da compilare."""
    from quizesame.services.latex import BEGIN_DOCUMENT as INIZIO, pagina_compito
    appello = corsi_service.get_appello(tag, appello_id)
    blocchi = list_blocchi(tag, appello_id)  # anche nessuno: si rigenerano riferimenti e anteprima
    ctx = _crea_contesto(corsi_service.get_corso(tag), appello)
    _salva_riferimenti(tag, appello_id, ctx, _esercizi_per_generazione(tag, appello_id))
    da_compilare = [path_riferimento(tag, appello_id, "tex"), path_riferimento_soluzioni(tag, appello_id, "tex")]
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for b in blocchi:
            compiti = conn.execute(
                "SELECT c.id, c.codice, c.soluzioni FROM compiti c JOIN blocchi bl ON bl.id = c.blocco_id "
                "WHERE bl.appello_id=? AND bl.numero=? ORDER BY c.id",
                (appello_id, b["numero"]),
            ).fetchall()
            tex = INIZIO
            for c in compiti:
                posizioni = conn.execute(
                    "SELECT ce.obbligatorio, ce.risposte_mischiate, e.aperta, ev.testo FROM compito_esercizi ce "
                    "JOIN esercizi e ON e.id = ce.esercizio_id JOIN esercizio_varianti ev ON ev.id = ce.variante_id "
                    "WHERE ce.compito_id=? ORDER BY ce.posizione",
                    (c["id"],),
                ).fetchall()
                tex += pagina_compito(ctx, c["codice"], [
                    {"testo": p["testo"], "obbligatorio": bool(p["obbligatorio"]), "aperta": bool(p["aperta"]),
                     "risposte": json.loads(p["risposte_mischiate"]) if p["risposte_mischiate"] else []}
                    for p in posizioni
                ])
            tex += "\n\n\\end{document}"
            _salva_testo(path_blocco(tag, appello_id, b["numero"], "tex", appello=appello), tex)
            _salva_testo(
                path_griglia(tag, appello_id, b["numero"], "tex", appello=appello),
                crea_file_griglia(ctx, [(c["codice"], c["soluzioni"]) for c in compiti]),
            )
            da_compilare += [path_blocco(tag, appello_id, b["numero"], "tex", appello=appello),
                             path_griglia(tag, appello_id, b["numero"], "tex", appello=appello)]
    finally:
        conn.close()
    da_compilare.append(genera_anteprima(tag, appello_id)[0])
    return da_compilare


def genera_anteprima(tag: str, appello_id: int) -> tuple[Path, bool]:
    """Scrive il .tex dell'anteprima di controllo del compito (tutti gli esercizi
    assegnati con tutte le varianti, risposte corrette e soluzioni): non tocca blocchi
    né codici, quindi si può rigenerare quando si vuole. Ritorna il percorso del .tex e
    se il testo è cambiato rispetto all'ultima anteprima (esercizi o impostazioni del
    corso modificati): se non è cambiato e il PDF esiste già, non serve ricompilarlo."""
    appello = corsi_service.get_appello(tag, appello_id)
    if appello is None:
        raise ValueError("Appello non trovato")
    ctx = _crea_contesto(corsi_service.get_corso(tag), appello)
    path = path_anteprima(tag, appello_id, "tex", appello=appello)
    tex = crea_file_anteprima(ctx, _esercizi_per_generazione(tag, appello_id))
    cambiato = not path.exists() or path.read_text(encoding="utf-8") != tex
    if cambiato:
        _salva_testo(path, tex)
    return path, cambiato


def path_risultati(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"risultati-{appello.slug}.{ext}"


def path_ammessi(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"ammessi-{appello.slug}.{ext}"


def path_iscritti(tag: str, appello_id: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"iscritti-{appello.slug}.{ext}"


def path_blocco(tag: str, appello_id: int, numero: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"blocco-{appello.slug}-{numero}.{ext}"


def path_griglia(tag: str, appello_id: int, numero: int, ext: str, appello=None) -> Path:
    appello = appello or corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"griglia-{appello.slug}-{numero}.{ext}"


def _salva_testo(path: Path, tex: str) -> None:
    path.write_text(tex, encoding="utf-8")


def _crea_contesto(corso, appello) -> LatexContext:
    return LatexContext(
        corso=corso.nome, facolta=corso.facolta, universita=corso.universita, anno=corso.anno, tag=corso.tag,
        appello_nome=appello.nome, appello_data=appello.data or "",
        consegna=corsi_service.effective_consegna(corso, appello), votomin=corsi_service.effective_votomin(corso, appello),
        risposta_corretta=corso.risposta_corretta, risposta_sbagliata=corso.risposta_sbagliata,
        risposta_vuota=corso.risposta_vuota, frase_consegna=corso.frase_consegna, frase_regole=corso.frase_regole,
        frase_obbligatorio=corso.frase_obbligatorio,
    )


def _recupera_compiti_orfani(tag: str, appello_id: int) -> None:
    """Corsi creati prima che i compiti fossero raggruppati in 'blocchi' (una sola
    generazione per appello, in un unico file testo-<slug>.tex) hanno righe in `compiti`
    con blocco_id NULL: le raccoglie in un blocco recuperato, così tornano visibili e
    scaricabili invece di contare solo nel totale senza comparire in nessuna riga."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        orfani = conn.execute(
            "SELECT id, codice, soluzioni FROM compiti WHERE appello_id=? AND blocco_id IS NULL",
            (appello_id,),
        ).fetchall()
        if not orfani:
            return
        numero_minimo = conn.execute(
            "SELECT MIN(numero) m FROM blocchi WHERE appello_id=?", (appello_id,)
        ).fetchone()["m"]
        numero = (numero_minimo - 1) if numero_minimo is not None else 1
        cur = conn.execute(
            "INSERT INTO blocchi (appello_id, numero, numero_studenti, creato_il) VALUES (?,?,?,NULL)",
            (appello_id, numero, len(orfani)),
        )
        blocco_id = cur.lastrowid
        conn.execute(
            "UPDATE compiti SET blocco_id=? WHERE appello_id=? AND blocco_id IS NULL",
            (blocco_id, appello_id),
        )
        conn.commit()
    finally:
        conn.close()

    appello = corsi_service.get_appello(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    vecchio_tex = _out_dir(tag) / f"testo-{appello.slug}.tex"
    vecchio_pdf = _out_dir(tag) / f"testo-{appello.slug}.pdf"
    if vecchio_tex.exists():
        path_blocco(tag, appello_id, numero, "tex").write_text(
            vecchio_tex.read_text(encoding="utf-8"), encoding="utf-8"
        )
        if vecchio_pdf.exists():
            path_blocco(tag, appello_id, numero, "pdf").write_bytes(vecchio_pdf.read_bytes())
    ctx = _crea_contesto(corso, appello)
    tex_griglia = crea_file_griglia(ctx, [(r["codice"], r["soluzioni"]) for r in orfani])
    _salva_testo(path_griglia(tag, appello_id, numero, "tex"), tex_griglia)
    compila_pdf(path_griglia(tag, appello_id, numero, "tex"))


def list_blocchi(tag: str, appello_id: int) -> list[dict]:
    _recupera_compiti_orfani(tag, appello_id)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT * FROM blocchi WHERE appello_id=? ORDER BY numero", (appello_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def blocchi_con_risultati(tag: str, appello_id: int) -> list[int]:
    """Numeri dei blocchi che hanno già almeno un risultato registrato: non si possono
    più rigenerare (perderebbero i codici a cui quei risultati sono legati)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT DISTINCT b.numero FROM blocchi b "
            "JOIN compiti c ON c.blocco_id = b.id "
            "JOIN risultati r ON r.compito_id = c.id "
            "WHERE b.appello_id=? ORDER BY b.numero",
            (appello_id,),
        ).fetchall()
        return [r["numero"] for r in rows]
    finally:
        conn.close()


def verifica_testo_completo(tag: str, appello_id: int) -> None:
    """Il testo può essere chiuso (e i compiti generati) solo se ha almeno un esercizio e
    nessuna variante con due risposte uguali, che renderebbero ambiguo il foglio."""
    assegnati = esercizi_service.list_esercizi_appello(tag, appello_id)
    if not assegnati:
        raise ValueError("Il testo non ha ancora esercizi assegnati")
    problemi = [
        f"'{e.nome or e.id}' ({'; '.join(esercizi_service.problemi_risposte(e))})"
        for e in assegnati if esercizi_service.problemi_risposte(e)
    ]
    if problemi:
        raise ValueError(
            "Alcuni esercizi hanno due risposte uguali nella stessa variante: correggili nella scheda "
            "Testo. " + " · ".join(problemi)
        )


def genera_blocco(tag: str, appello_id: int, numero_studenti: int) -> BloccoResult:
    corsi_service.verifica_appello_aperto(tag, appello_id)
    appello = corsi_service.get_appello(tag, appello_id)
    if appello is None:
        raise ValueError("Appello non trovato")
    if not appello.testo_chiuso:
        raise ValueError("Chiudi prima il testo nella scheda Testo: i compiti si generano dal testo definitivo")
    corso = corsi_service.get_corso(tag)

    esercizi_struct = _esercizi_per_generazione(tag, appello_id)
    verifica_testo_completo(tag, appello_id)

    n_obbligatori = sum(1 for e in esercizi_struct if e["obbligatorio"])
    avviso_obbligatori = None
    if n_obbligatori != corso.consegna:
        avviso_obbligatori = (
            f"Attenzione: {n_obbligatori} esercizi obbligatori assegnati a questo appello, "
            f"ma le impostazioni del corso ne prevedono {corso.consegna}."
        )

    ctx = _crea_contesto(corso, appello)

    _recupera_compiti_orfani(tag, appello_id)

    conn = db.get_connection(config.corso_db_path(tag))
    riferimento_generato = False
    try:
        numero = conn.execute(
            "SELECT COALESCE(MAX(numero), 0) + 1 AS n FROM blocchi WHERE appello_id=?", (appello_id,)
        ).fetchone()["n"]
        if not (path_riferimento(tag, appello_id, "tex").exists()
                and path_riferimento_soluzioni(tag, appello_id, "tex").exists()):
            _salva_riferimenti(tag, appello_id, ctx, esercizi_struct)
            riferimento_generato = True

        codici_esistenti = set()
        for r in conn.execute("SELECT codice FROM compiti WHERE appello_id=?", (appello_id,)):
            try:
                codici_esistenti.add(int(r["codice"]))
            except (TypeError, ValueError):
                pass
        studenti, tex_blocco = crea_file_blocco(ctx, esercizi_struct, numero_studenti, codici_esistenti)

        cur = conn.execute(
            "INSERT INTO blocchi (appello_id, numero, numero_studenti) VALUES (?,?,?)",
            (appello_id, numero, numero_studenti),
        )
        blocco_id = cur.lastrowid
        inseriti = 0
        saltati = 0
        for codice, griglia, posizioni in studenti:
            try:
                curc = conn.execute(
                    "INSERT INTO compiti (appello_id, blocco_id, codice, soluzioni) VALUES (?,?,?,?)",
                    (appello_id, blocco_id, str(codice), griglia),
                )
                compito_id = curc.lastrowid
                for indice, p in enumerate(posizioni):
                    conn.execute(
                        "INSERT INTO compito_esercizi (compito_id, posizione, esercizio_id, variante_id, "
                        "obbligatorio, aperta, risposte_mischiate) VALUES (?,?,?,?,?,?,?)",
                        (
                            compito_id, indice, p["esercizio_id"], p["variante_id"], p["obbligatorio"],
                            p["aperta"], json.dumps(p["risposte"]),
                        ),
                    )
                inseriti += 1
            except Exception:
                saltati += 1
        conn.commit()
    finally:
        conn.close()

    _salva_testo(path_blocco(tag, appello_id, numero, "tex"), tex_blocco)
    tex_griglia = crea_file_griglia(ctx, [(codice, griglia) for codice, griglia, _ in studenti])
    _salva_testo(path_griglia(tag, appello_id, numero, "tex"), tex_griglia)

    return BloccoResult(
        numero=numero, n_compiti_generati=inseriti, n_saltati_duplicati=saltati,
        avviso_obbligatori=avviso_obbligatori, riferimento_generato=riferimento_generato,
    )


def _svuota_compiti_di_blocchi(conn, blocchi: list[dict]) -> None:
    """Cancella compiti e compito_esercizi di questi blocchi (non le righe blocchi
    stesse): serve sia per rigenerarli con nuovi esercizi, sia per liberare i riferimenti
    a varianti che stanno per essere sostituite (modifica di un esercizio della banca)
    prima che quel DELETE su esercizio_varianti violi la foreign key."""
    for b in blocchi:
        vecchi = conn.execute("SELECT id FROM compiti WHERE blocco_id=?", (b["id"],)).fetchall()
        for c in vecchi:
            conn.execute("DELETE FROM compito_esercizi WHERE compito_id=?", (c["id"],))
        conn.execute("DELETE FROM compiti WHERE blocco_id=?", (b["id"],))


def svuota_blocchi_appello(tag: str, appello_id: int) -> None:
    """Da chiamare prima di modificare un esercizio della banca condiviso da più appelli:
    svuota (senza rigenerare) i blocchi già generati su questo appello, così il DELETE
    delle vecchie varianti non trova più righe compito_esercizi che le referenziano.
    Il richiamante deve aver già verificato con blocchi_con_risultati che è sicuro farlo,
    e chiamare rigenera_tutto subito dopo per ricostruire i blocchi coi nuovi esercizi."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    blocchi = list_blocchi(tag, appello_id)
    if not blocchi:
        return
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        _svuota_compiti_di_blocchi(conn, blocchi)
        conn.commit()
    finally:
        conn.close()


def rigenera_tutto(tag: str, appello_id: int) -> RigenerazioneResult | None:
    """Da chiamare dopo una modifica agli esercizi assegnati a un appello che ha già
    almeno un blocco generato: rigenera il file di riferimento e tutti i blocchi
    esistenti (con lo stesso numero di studenti di ciascuno), eliminando e ricreando da
    capo i relativi codici. Solleva ValueError se qualche blocco ha già risultati
    registrati (non rigenerabile senza perdere dati reali)."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    blocchi = list_blocchi(tag, appello_id)
    if not blocchi:
        return None

    bloccanti = blocchi_con_risultati(tag, appello_id)
    if bloccanti:
        raise ValueError(
            "Non è possibile modificare gli esercizi: il blocco "
            + ", ".join(str(n) for n in bloccanti)
            + " ha già risultati registrati. Elimina prima quei risultati, oppure lascia invariati gli esercizi."
        )

    appello = corsi_service.get_appello(tag, appello_id)
    corso = corsi_service.get_corso(tag)
    esercizi_struct = _esercizi_per_generazione(tag, appello_id)
    ctx = _crea_contesto(corso, appello)

    _salva_riferimenti(tag, appello_id, ctx, esercizi_struct)

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        _svuota_compiti_di_blocchi(conn, blocchi)
        codici_usati = set()
        for b in blocchi:
            studenti, tex_blocco = crea_file_blocco(ctx, esercizi_struct, b["numero_studenti"], codici_usati)
            codici_usati.update(codice for codice, _, _ in studenti)
            for codice, griglia, posizioni in studenti:
                try:
                    curc = conn.execute(
                        "INSERT INTO compiti (appello_id, blocco_id, codice, soluzioni) VALUES (?,?,?,?)",
                        (appello_id, b["id"], str(codice), griglia),
                    )
                    compito_id = curc.lastrowid
                    for indice, p in enumerate(posizioni):
                        conn.execute(
                            "INSERT INTO compito_esercizi (compito_id, posizione, esercizio_id, variante_id, "
                            "obbligatorio, aperta, risposte_mischiate) VALUES (?,?,?,?,?,?,?)",
                            (
                                compito_id, indice, p["esercizio_id"], p["variante_id"], p["obbligatorio"],
                                p["aperta"], json.dumps(p["risposte"]),
                            ),
                        )
                except Exception:
                    pass

            _salva_testo(path_blocco(tag, appello_id, b["numero"], "tex"), tex_blocco)
            tex_griglia = crea_file_griglia(ctx, [(codice, griglia) for codice, griglia, _ in studenti])
            _salva_testo(path_griglia(tag, appello_id, b["numero"], "tex"), tex_griglia)
        conn.commit()
    finally:
        conn.close()

    return RigenerazioneResult(n_blocchi=len(blocchi))


@dataclass
class CompilazioneResult:
    ok: bool
    messaggio: str
    pdf_path: Path | None = None
    errore_dettagliato: str | None = None
    # righe o blocchi che escono dai margini (overfull) in modo visibile: il PDF è stato
    # prodotto, ma qualcosa sporge; ogni voce è un estratto leggibile del log
    sporgenze: list[str] = field(default_factory=list)
    pagine: int | None = None


# solo gli sforamenti orizzontali (testo che esce dal margine laterale): quelli verticali,
# di pochi punti a fine pagina, non si vedono
_OVERFULL = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\)[^\n]*\n(.*)")


def _sporgenze_latex(log: str, soglia_pt: float = 2.0) -> list[str]:
    """Gli overfull oltre `soglia_pt` (sotto i 2pt non si vedono a occhio), con il testo
    della riga interessata ripulito dai comandi di font di TeX."""
    voci = []
    for misura, riga in _OVERFULL.findall(log):
        if float(misura) < soglia_pt:
            continue
        testo = re.sub(r"\\[A-Za-z0-9]+/[^ ]+ ", "", riga).replace("[]", " ").strip()
        voci.append(f"sporge di {float(misura) * 0.3515:.1f} mm: {testo[:80] or '(riquadro o tabella)'}")
    return voci


def _estrai_errore_latex(output: str) -> str:
    """Isola dall'output di pdflatex (rumoroso: centinaia di righe di log) le righe utili
    a capire cosa è andato storto: a partire dalla prima riga di errore (quelle che
    iniziano con "!", come vuole la convenzione di LaTeX), o le ultime righe del log se
    per qualche motivo non ne trova una."""
    righe = output.splitlines()
    for i, r in enumerate(righe):
        if r.startswith("!"):
            return "\n".join(righe[i:i + 20]).strip()
    return "\n".join(righe[-25:]).strip()


def compila_pdf(tex_path: Path) -> CompilazioneResult:
    if not tex_path.exists():
        return CompilazioneResult(ok=False, messaggio="Genera prima il testo (.tex)")

    if which("pdflatex") is None:
        return CompilazioneResult(
            ok=False,
            messaggio="pdflatex non è installato su questo computer: il testo (.tex) è comunque disponibile "
            "per essere compilato altrove",
        )

    out_dir = tex_path.parent
    try:
        # una sola passata: questi documenti non usano \ref/\cite/\tableofcontents, quindi
        # non c'è nulla che una seconda passata risolverebbe meglio della prima.
        risultato = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=out_dir, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return CompilazioneResult(ok=False, messaggio="Compilazione PDF troppo lenta, annullata")

    pdf_path = tex_path.with_suffix(".pdf")
    if pdf_path.exists():
        log_path = tex_path.with_suffix(".log")
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        pagine = re.search(r"Output written on .*?\((\d+) pages?", log)
        return CompilazioneResult(ok=True, messaggio="PDF compilato", pdf_path=pdf_path, sporgenze=_sporgenze_latex(log),
                                  pagine=int(pagine.group(1)) if pagine else None)
    return CompilazioneResult(
        ok=False, messaggio="Compilazione PDF fallita: controlla il testo LaTeX degli esercizi",
        errore_dettagliato=_estrai_errore_latex(risultato.stdout or risultato.stderr or ""),
    )


def get_soluzione(tag: str, appello_id: int, codice: str) -> str | None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT id, soluzioni FROM compiti WHERE appello_id=? AND codice=?", (appello_id, codice)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_posizioni_compito(tag: str, compito_id: int) -> list[dict]:
    """Per ogni posizione della griglia di risposte di un compito, l'esercizio (e se era
    obbligatorio) che vi corrisponde in quello specifico compito."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT ce.posizione, ce.obbligatorio, ce.aperta, ce.risposte_mischiate, e.id AS esercizio_id, "
            "e.nome AS esercizio_nome FROM compito_esercizi ce JOIN esercizi e ON e.id = ce.esercizio_id "
            "WHERE ce.compito_id=? ORDER BY ce.posizione",
            (compito_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_compiti(tag: str, appello_id: int) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT * FROM compiti WHERE appello_id=? ORDER BY codice", (appello_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_compiti_blocco(tag: str, blocco_id: int) -> list[dict]:
    """Per ogni compito del blocco, anche l'eventuale matricola dello studente già
    valutato con quel codice (se esiste una riga in risultati che lo referenzia): serve
    per linkare, dalla griglia delle soluzioni, alla pagina di dettaglio del compito già
    corretto."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT c.codice, c.soluzioni, r.matricola FROM compiti c "
            "LEFT JOIN risultati r ON r.compito_id = c.id "
            "WHERE c.blocco_id=? ORDER BY c.codice",
            (blocco_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_blocco(tag: str, appello_id: int, numero: int) -> Optional[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute(
            "SELECT * FROM blocchi WHERE appello_id=? AND numero=?", (appello_id, numero)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def elimina_blocco(tag: str, appello_id: int, numero: int) -> None:
    """Elimina un blocco e i suoi compiti, rifiutando l'operazione se ha già risultati
    registrati (perderebbero il compito a cui sono legati)."""
    corsi_service.verifica_appello_aperto(tag, appello_id)
    if numero in blocchi_con_risultati(tag, appello_id):
        raise ValueError(
            f"Il blocco {numero} ha già risultati registrati: non può essere eliminato "
            "senza perdere quei dati."
        )
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        blocco = conn.execute(
            "SELECT id FROM blocchi WHERE appello_id=? AND numero=?", (appello_id, numero)
        ).fetchone()
        if blocco is None:
            raise ValueError(f"Blocco {numero} non trovato")
        compiti_ids = [r["id"] for r in conn.execute("SELECT id FROM compiti WHERE blocco_id=?", (blocco["id"],))]
        for cid in compiti_ids:
            conn.execute("DELETE FROM compito_esercizi WHERE compito_id=?", (cid,))
        conn.execute("DELETE FROM compiti WHERE blocco_id=?", (blocco["id"],))
        conn.execute("DELETE FROM blocchi WHERE id=?", (blocco["id"],))
        conn.commit()
    finally:
        conn.close()
    for ext in ("tex", "pdf", "aux", "log", "synctex.gz"):
        path_blocco(tag, appello_id, numero, ext).unlink(missing_ok=True)
        path_griglia(tag, appello_id, numero, ext).unlink(missing_ok=True)


def path_blocchi_uniti(tag: str, appello_id: int, ext: str) -> Path:
    appello = corsi_service.get_appello(tag, appello_id)
    return _out_dir(tag) / f"blocchi-{appello.slug}.{ext}"


def genera_blocchi_uniti(tag: str, appello_id: int) -> Path:
    """Concatena il .tex di tutti i blocchi già generati per questo appello in un unico
    file, per poterli scaricare/stampare tutti insieme in un solo PDF invece che uno alla
    volta quando un appello ne ha più di uno."""
    blocchi = list_blocchi(tag, appello_id)
    corpi = []
    for b in blocchi:
        tex_path = path_blocco(tag, appello_id, b["numero"], "tex")
        if not tex_path.exists():
            continue
        testo = tex_path.read_text(encoding="utf-8")
        corpi.append(testo.removeprefix(BEGIN_DOCUMENT).removesuffix("\n\n\\end{document}"))
    if not corpi:
        raise ValueError("Nessun blocco generato per questo appello")
    tex = BEGIN_DOCUMENT + "\\clearpage\n".join(corpi) + "\n\n\\end{document}"
    path = path_blocchi_uniti(tag, appello_id, "tex")
    _salva_testo(path, tex)
    return path


def verifica_codice(tag: str, appello_id: int, codice: str) -> dict:
    """Controllo immediato, mentre si digita in correzione, di un codice compito: se
    esiste in questo appello, quante risposte prevede (lunghezza della griglia) e se è
    già stato usato da un risultato registrato (di chi), per accorgersi subito di un
    codice letto o trascritto male invece che a riepilogo aperto."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        compito = conn.execute(
            "SELECT id, soluzioni FROM compiti WHERE appello_id=? AND codice=?", (appello_id, codice.strip()),
        ).fetchone()
        if compito is None:
            return {"esiste": False}
        usato = conn.execute(
            "SELECT r.matricola, s.nome, s.cognome FROM risultati r JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? AND r.compito_id=?",
            (appello_id, compito["id"]),
        ).fetchone()
        return {
            "esiste": True, "lunghezza": len(compito["soluzioni"] or ""),
            "usato_da": dict(usato) if usato else None,
        }
    finally:
        conn.close()


def cerca_codici(tag: str, appello_id: int, prefisso: str, limite: int = 10) -> list[str]:
    """Codici compito di questo appello che iniziano per `prefisso`, per l'autocompletamento
    nel form di correzione."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT codice FROM compiti WHERE appello_id=? AND codice LIKE ? ORDER BY codice LIMIT ?",
            (appello_id, f"{prefisso}%", limite),
        ).fetchall()
        return [r["codice"] for r in rows]
    finally:
        conn.close()
