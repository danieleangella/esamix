"""Analisi delle statistiche con un LLM: per un appello (o un raggruppamento di prove), per
un corso o per un insieme di corsi (le Statistiche generali), raccoglie in forma anonima
i risultati delle prove e ne fa un prompt per un modello linguistico, che individua le
criticità utili a ripensare il corso: argomenti capiti meno (quest'anno e negli anni
precedenti), esercizi problematici, compiti probabilmente svolti con l'aiuto dell'LLM o
copiati, calibrazione della prova.

Anonimato: gli studenti compaiono solo come pseudonimi (S001, S002, ...) assegnati in un
ordine casuale diverso a ogni prompt; non escono mai matricole, nomi, corso di laurea,
DSA, né le note e le decisioni del docente sui singoli studenti (motivazioni dell'orale,
compiti annullati, note di correzione), così che la stima dei compiti svolti con un LLM si
basi solo sui risultati. Le note sugli errori negli svolgimenti, se incluse, sono
raggruppate per esercizio e mai collegate allo studente.

Il prompt si può copiare in un chatbot oppure inviare con una configurazione LLM delle
Impostazioni (vedi ia._chiama); le analisi fatte restano salvate in file JSON."""
import hashlib
import json
import math
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from markupsafe import Markup, escape

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service
from quizesame.services import ia as ia_service
from quizesame.services import statistiche as statistiche_service

# esiti con un compito davvero svolto e giudicato sulle risposte; annullati, ritirati e
# assenti restano fuori (l'annullamento è una decisione del docente, non un risultato)
_ESITI_SVOLTI = ("voto", "rifiutato")
# note sugli svolgimenti che parlano di copiature o dispositivi: sono indicazioni del
# docente, che la stima "oggettiva" dei compiti svolti con un LLM non deve vedere
_NOTA_ESCLUSA = re.compile(r"\b(ia|ai|gpt|chatgpt|chatbot|copia\w*|copiat\w*|cellular\w*|telefon\w*|smartphone)\b", re.I)
MAX_NOTE_PER_ESERCIZIO = 15
MAX_COPPIE = 20


# --- utilità ---------------------------------------------------------------------------

def _breve(testo: Optional[str], n: int) -> str:
    # senza le frasi in grassetto fisse (es. "Di questo esercizio è obbligatorio..."), già
    # indicate come attributi dell'esercizio
    t = re.sub(r"\{\\bfseries[^{}]*\}", " ", testo or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def _pct(a: int, b: int) -> str:
    return f"{round(100 * a / b)}%" if b else "-"


def _media(valori: list) -> Optional[float]:
    return round(sum(valori) / len(valori), 1) if valori else None


def _correlazione(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 8:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return round(sxy / (sx * sy), 2) if sx and sy else None


def _istogramma(voti: list[int]) -> str:
    if not voti:
        return "-"
    c = Counter(voti)
    return ", ".join(f"{v}:{c[v]}" for v in sorted(c))


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").lower()


def _variante_da_opzioni(e, opzioni: Optional[list], lettera_corretta: str) -> Optional[int]:
    """Il numero della variante (da 1) le cui risposte sono quelle stampate nel compito, con
    la stessa corretta (varianti diverse possono avere le stesse opzioni): per i compiti
    senza variante registrata (es. importati)."""
    k = ord(lettera_corretta.upper()) - ord("A") if lettera_corretta else -1
    if not opzioni or not 0 <= k < len(opzioni):
        return None
    stampate = sorted(_norm(t) for t in opzioni)
    for n, v in enumerate(e.varianti, start=1):
        if v.risposte and _norm(v.risposte[0]) == _norm(opzioni[k]) and sorted(_norm(t) for t in v.risposte) == stampate:
            return n
    return None


def _voto_scritto(r: dict) -> Optional[int]:
    if r["voto_scritto"] is not None:
        return r["voto_scritto"]
    return r["voto"] if not r["richiede_orale"] else None


def _prove(tag: str, appello) -> list:
    """Le prove con compiti propri: le prove membro per un raggruppamento, se stesso altrimenti."""
    ragg = corsi_service.get_raggruppamento_by_appello(tag, appello.id)
    return list(ragg.membri) if ragg else [appello]


def _edizioni(corso) -> list:
    """Il corso e le altre edizioni dello stesso insegnamento, dalla più vecchia."""
    tutti = corsi_service.list_corsi()
    return sorted([corso] + corsi_service.corsi_simili(corso, tutti), key=lambda c: statistiche_service._anno_key(c.anno))


# --- dettaglio di una prova -------------------------------------------------------------

def _analizza_prova(tag: str, corso, appello, pseudonimi: dict, includi_note: bool, storico: bool) -> dict:
    """Esercizi (testo, esiti, risposte sbagliate scelte, discriminazione) e compiti
    anonimi di una prova, con gli indicatori calcolati per la stima di copiature e LLM."""
    votomin = corsi_service.effective_votomin(corso, appello)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        segnaposto = ",".join("?" * len(_ESITI_SVOLTI))
        risultati = [dict(r) for r in conn.execute(
            f"SELECT r.*, c.soluzioni FROM risultati r JOIN compiti c ON c.id = r.compito_id "
            f"WHERE r.appello_id=? AND r.risposte IS NOT NULL AND r.esito IN ({segnaposto})",
            (appello.id, *_ESITI_SVOLTI),
        ).fetchall()]
        posizioni = defaultdict(list)
        for p in conn.execute(
            "SELECT ce.*, c.soluzioni FROM compito_esercizi ce JOIN compiti c ON c.id = ce.compito_id WHERE c.appello_id=? "
            "ORDER BY ce.compito_id, ce.posizione", (appello.id,),
        ).fetchall():
            posizioni[p["compito_id"]].append(dict(p))
        revisioni = {r["esercizio_id"]: r["revisione"] for r in conn.execute(
            "SELECT esercizio_id, revisione FROM appello_revisione_esercizi WHERE appello_id=?", (appello.id,))}
        precedenti = defaultdict(list)
        if storico and risultati:
            # prima per data; senza data (su una delle due prove) vale l'ordine di creazione
            data_prova = corsi_service.data_ordinabile(appello.data)
            for r in conn.execute(
                "SELECT r.matricola, r.appello_id, r.voto, r.voto_scritto, r.richiede_orale, r.esito, a.data FROM risultati r "
                "JOIN appelli a ON a.id = r.appello_id WHERE r.appello_id != ? AND r.esito IN ('voto', 'rifiutato')",
                (appello.id,),
            ).fetchall():
                data = corsi_service.data_ordinabile(r["data"])
                prima = data < data_prova if data and data_prova else r["appello_id"] < appello.id
                v = _voto_scritto(dict(r))
                if prima and v is not None:
                    precedenti[r["matricola"]].append((data, r["appello_id"], v))
    finally:
        conn.close()

    ordine = [e.id for e in esercizi_service.list_esercizi_appello(tag, appello.id)]
    for pos in posizioni.values():
        for p in pos:
            if p["esercizio_id"] not in ordine:
                ordine.append(p["esercizio_id"])
    esercizi = esercizi_service.esercizi_per_id(tag, ordine)
    ordine = [eid for eid in ordine if eid in esercizi]
    etichette = {eid: f"E{i}" for i, eid in enumerate(ordine, start=1)}
    indice = {eid: i for i, eid in enumerate(ordine)}
    varianti = {eid: {v.id: k for k, v in enumerate(esercizi[eid].varianti, start=1)} for eid in ordine}
    for pos in posizioni.values():
        for p in pos:
            if p["esercizio_id"] in esercizi:
                p["opzioni"] = json.loads(p["risposte_mischiate"]) if p["risposte_mischiate"] else None
                i = p["posizione"]
                p["nvar"] = (varianti[p["esercizio_id"]].get(p["variante_id"]) or _variante_da_opzioni(
                    esercizi[p["esercizio_id"]], p["opzioni"], p["soluzioni"][i] if i < len(p["soluzioni"] or "") else ""))

    per_es = {eid: {"giuste": 0, "sbagliate": 0, "non_date": 0, "scelte": Counter(), "punti": [], "note": [],
                    "obbligatorio": False, "x": [], "y": [],
                    # per variante: [giuste, totale] (domande chiuse) o i punteggi (aperte)
                    "per_variante": defaultdict(lambda: [0, 0]), "punti_variante": defaultdict(list)} for eid in ordine}
    compiti = []
    for r in risultati:
        soluzioni, risposte = r["soluzioni"] or "", r["risposte"] or ""
        punteggi = json.loads(r["punteggi_obbligatori"]) if r["punteggi_obbligatori"] else {}
        note = json.loads(r["note_svolgimenti"]) if r["note_svolgimenti"] else {}
        scritto = _voto_scritto(r)
        voci, sbagliate_note, esiti_mc = [], {}, {}
        for p in sorted(posizioni.get(r["compito_id"], []), key=lambda p: indice.get(p["esercizio_id"], len(ordine))):
            eid, i = p["esercizio_id"], p["posizione"]
            if eid not in esercizi:
                continue
            e, st = esercizi[eid], per_es[eid]
            st["obbligatorio"] = st["obbligatorio"] or bool(p["obbligatorio"])
            opzioni, nvar = p["opzioni"], p["nvar"]
            et = etichette[eid] + (f"v{nvar}" if nvar and len(e.varianti) > 1 else "")
            nota = note.get(str(i), "")
            if includi_note and nota and not _NOTA_ESCLUSA.search(nota):
                st["note"].append(_breve(nota, 200))
            if p["aperta"]:
                punti = punteggi.get(str(i))
                if punti is None:
                    voci.append(f"{et}=?")
                else:
                    st["punti"].append(punti)
                    if nvar:
                        st["punti_variante"][nvar].append(punti)
                    voci.append(f"{et}={punti}/{corso.punteggio_max_aperta}")
                    if scritto is not None:
                        st["x"].append(punti)
                        st["y"].append(scritto)
                continue
            data = risposte[i].upper() if i < len(risposte) else "X"
            giusta = data == (soluzioni[i].upper() if i < len(soluzioni) else "?")
            if nvar:
                st["per_variante"][nvar][1] += 1
                st["per_variante"][nvar][0] += 1 if giusta else 0
            if giusta:
                st["giuste"] += 1
                codice = "+"
                if p["obbligatorio"] and str(i) in punteggi and punteggi[str(i)] != corso.risposta_corretta:
                    codice += f"[svolg. {punteggi[str(i)]}/{corso.risposta_corretta}]"
            elif data == "X":
                st["non_date"] += 1
                codice = "."
            else:
                st["sbagliate"] += 1
                codice = "?"
                if opzioni and nvar:
                    k = ord(data) - ord("A")
                    if 0 <= k < len(opzioni):
                        canoniche = [_norm(t) for t in e.varianti[nvar - 1].risposte]
                        if _norm(opzioni[k]) in canoniche:
                            codice = chr(ord("a") + canoniche.index(_norm(opzioni[k])))
                            st["scelte"][(nvar, codice)] += 1
                            sbagliate_note[(eid, nvar)] = codice
            voci.append(f"{et}={codice}")
            esiti_mc[eid] = 1 if giusta else 0
            if scritto is not None:
                st["x"].append(1 if giusta else 0)
                st["y"].append(scritto)
        prec = [v for *_, v in sorted(precedenti.get(r["matricola"], []))]
        compiti.append({
            "pseudonimo": pseudonimi[r["matricola"]], "scritto": scritto,
            "orale": r["voto"] if r["orale_svolto"] else None, "esito_orale": r["esito_orale"] if r["orale_svolto"] else None,
            "rifiutato": r["esito"] == "rifiutato", "voci": voci, "sbagliate": sbagliate_note,
            "esiti_mc": esiti_mc, "precedenti": prec,
        })

    # indice di Guttman normalizzato: quanto spesso il compito sbaglia esercizi facili
    # (molto risolti dagli altri) e risolve quelli difficili
    facilita = {eid: st["giuste"] / (st["giuste"] + st["sbagliate"] + st["non_date"])
                for eid, st in per_es.items() if st["giuste"] + st["sbagliate"] + st["non_date"]}
    for c in compiti:
        voti = sorted(((facilita[eid], x) for eid, x in c["esiti_mc"].items() if eid in facilita), reverse=True)
        k, giusti = len(voti), sum(x for _, x in voti)
        if k >= 4 and 0 < giusti < k:
            errori = sum(1 for a in range(k) for b in range(a + 1, k) if voti[a][1] == 0 and voti[b][1] == 1)
            c["guttman"] = round(errori / (giusti * (k - giusti)), 2)
        else:
            c["guttman"] = None

    # coppie di compiti con molte risposte sbagliate identiche (stesso esercizio, stessa
    # variante, stessa opzione): tra gli esercizi sbagliati da entrambi, quante coincidono
    # e quante coinciderebbero per caso, data la frequenza di ciascuna risposta sbagliata
    prob_uguali = defaultdict(float)
    for eid, st in per_es.items():
        per_var = defaultdict(dict)
        for (nvar, codice), n in st["scelte"].items():
            per_var[nvar][codice] = n
        for nvar, cnt in per_var.items():
            tot = sum(cnt.values())
            prob_uguali[(eid, nvar)] = sum((n / tot) ** 2 for n in cnt.values())
    coppie = []
    for a in range(len(compiti)):
        sa = compiti[a]["sbagliate"]
        if len(sa) < 3:
            continue
        for b in range(a + 1, len(compiti)):
            sb = compiti[b]["sbagliate"]
            comuni = sa.keys() & sb.keys()
            identiche = sum(1 for k in comuni if sa[k] == sb[k])
            if identiche >= 3:
                attese = sum(prob_uguali[k] for k in comuni)
                coppie.append((identiche, len(comuni), attese, compiti[a]["pseudonimo"], compiti[b]["pseudonimo"]))
    coppie.sort(key=lambda c: (c[0] - c[2], c[0]), reverse=True)

    elenco_es = []
    for eid in ordine:
        st, e = per_es[eid], esercizi[eid]
        elenco_es.append({
            "etichetta": etichette[eid], "esercizio": e, "revisione": revisioni.get(eid), **st,
            "totale": st["giuste"] + st["sbagliate"] + st["non_date"],
            "discriminazione": _correlazione(st["x"], st["y"]),
        })
    voti_scritti = [c["scritto"] for c in compiti if c["scritto"] is not None]
    return {
        "appello": appello, "votomin": votomin, "esercizi": elenco_es, "compiti": compiti,
        "coppie": coppie[:MAX_COPPIE], "voti_scritti": voti_scritti,
        "stat": statistiche_service.calcola(tag, appello.id, includi_esercizi=False, corso=corso, appello=appello),
    }


def _testo_prova(d: dict, includi_compiti: bool, corso) -> str:
    a, s = d["appello"], d["stat"]
    righe = [
        f"### Prova «{a.nome}»" + (f" del {a.data}" if a.data else "")
        + (" (prova parziale di un raggruppamento)" if a.membro_raggruppamento else ""),
        f"Compiti corretti: {s['totale']}; promossi {s['promossi']} ({_pct(s['promossi'], s['totale'])}), "
        f"insufficienti {s['insufficienti']} ({_pct(s['insufficienti'], s['totale'])}), voto minimo {d['votomin']}. "
        f"Media scritto {s['media_scritto'] if s['media_scritto'] is not None else '-'}, "
        f"media orale {s['media_orale'] if s['media_orale'] is not None else '-'} (orali svolti: {s['orali_svolti']}), "
        f"media finale {s['media_finale'] if s['media_finale'] is not None else '-'}.",
        f"Distribuzione dei voti dello scritto (voto:numero): {_istogramma(d['voti_scritti'])}",
        "",
        "#### Esercizi",
    ]
    for es in d["esercizi"]:
        e = es["esercizio"]
        attributi = [x for x in (
            f"argomento: {e.argomento}" if e.argomento else "",
            f"difficoltà indicata dal docente: {e.difficolta}/3" if e.difficolta else "",
            "domanda aperta" if e.aperta else "",
            "obbligatorio" if es["obbligatorio"] else "",
            {"annullato": "ANNULLATO dopo la prova (testo rivelatosi sbagliato: punteggio pieno a tutti)",
             "facoltativo": "reso NON obbligatorio dopo la prova (troppo difficile)"}.get(es["revisione"] or "", ""),
        ) if x]
        righe += ["", f"**{es['etichetta']}** — {e.nome or 'senza nome'}" + (f" ({'; '.join(attributi)})" if attributi else "")]
        for k, v in enumerate(e.varianti, start=1):
            righe.append((f"Variante {k}: " if len(e.varianti) > 1 else "Testo: ") + _breve(v.testo, 900))
            if not e.aperta and v.risposte:
                righe.append("  Opzioni: " + " | ".join(
                    f"({chr(ord('a') + j)}){' [corretta]' if j == 0 else ''} {_breve(t, 160)}" for j, t in enumerate(v.risposte)))
        if e.soluzione:
            righe.append("Soluzione del docente: " + _breve(e.soluzione, 600))
        if e.aperta:
            righe.append(f"Esiti: {len(es['punti'])} valutati, punteggio medio {_media(es['punti'])} su {corso.punteggio_max_aperta}"
                         + (f"; distribuzione {_istogramma(es['punti'])}" if es["punti"] else ""))
        else:
            n = es["totale"]
            righe.append(f"Esiti su {n}: giuste {_pct(es['giuste'], n)}, sbagliate {_pct(es['sbagliate'], n)}, "
                         f"non date {_pct(es['non_date'], n)}")
            if es["scelte"]:
                righe.append("Risposte sbagliate scelte: " + ", ".join(
                    (f"v{nv}" if len(e.varianti) > 1 else "") + f"({c}) {n2} volte"
                    for (nv, c), n2 in es["scelte"].most_common()))
        if len(e.varianti) > 1 and (es["per_variante"] or es["punti_variante"]):
            if e.aperta:
                righe.append("Esiti per variante: " + "; ".join(
                    f"v{nv} punteggio medio {_media(p)} su {len(p)}" for nv, p in sorted(es["punti_variante"].items())))
            else:
                righe.append("Esiti per variante: " + "; ".join(
                    f"v{nv} giuste {_pct(g, n)} su {n}" for nv, (g, n) in sorted(es["per_variante"].items())))
        if es["discriminazione"] is not None:
            righe.append(f"Discriminazione (correlazione tra esito dell'esercizio e voto dello scritto): {es['discriminazione']}")
        if es["note"]:
            righe.append("Note del docente sugli errori negli svolgimenti (anonime): " + " / ".join(es["note"][:MAX_NOTE_PER_ESERCIZIO]))

    if includi_compiti and d["compiti"]:
        righe += [
            "", "#### Compiti (anonimi)",
            "Una riga per compito: pseudonimo | voto dello scritto | orale (se svolto) | indice di Guttman | "
            "risposte per esercizio" + (" | voti dello scritto in prove precedenti dello stesso corso" if any(c["precedenti"] for c in d["compiti"]) else "") + ".",
        ]
        for c in sorted(d["compiti"], key=lambda c: c["pseudonimo"]):
            parti = [c["pseudonimo"], f"scritto {c['scritto'] if c['scritto'] is not None else '?'}"]
            if c["orale"] is not None or c["esito_orale"]:
                parti.append(f"orale: {c['esito_orale'] if c['esito_orale'] != 'voto' else 'voto finale ' + str(c['orale'])}")
            if c["rifiutato"]:
                parti.append("voto rifiutato dallo studente")
            parti.append(f"G={c['guttman'] if c['guttman'] is not None else '-'}")
            parti.append(" ".join(c["voci"]))
            if c["precedenti"]:
                parti.append("prec: " + ", ".join(str(v) for v in c["precedenti"]))
            righe.append(" | ".join(parti))
        if d["coppie"]:
            righe += ["", "#### Coppie di compiti con più risposte sbagliate identiche",
                      "Per ogni coppia: tra gli esercizi sbagliati da entrambi, quante risposte sbagliate sono identiche "
                      "e quante lo sarebbero in media per caso (dalla frequenza di ciascuna risposta sbagliata)."]
            for identiche, comuni, attese, a, b in d["coppie"]:
                righe.append(f"{a} e {b}: {identiche} identiche su {comuni} sbagliate da entrambi (attese per caso {attese:.1f})")
    return "\n".join(righe)


# --- esiti aggregati di un corso (per lo storico delle edizioni) -------------------------

def _esiti_corso(tag: str) -> dict:
    """Per ogni esercizio usato nelle prove del corso: nome, argomento, inizio del testo ed
    esiti complessivi (una sola lettura del database, senza il dettaglio per compito)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        righe = conn.execute(
            "SELECT r.risposte, r.punteggi_obbligatori, c.soluzioni, ce.posizione, ce.esercizio_id, ce.aperta "
            "FROM risultati r JOIN compiti c ON c.id = r.compito_id JOIN compito_esercizi ce ON ce.compito_id = c.id "
            "WHERE r.risposte IS NOT NULL AND r.esito IN ('voto', 'rifiutato')"
        ).fetchall()
        per_es = defaultdict(lambda: {"giuste": 0, "sbagliate": 0, "non_date": 0, "punti": []})
        for r in righe:
            st, i = per_es[r["esercizio_id"]], r["posizione"]
            if r["aperta"]:
                punti = json.loads(r["punteggi_obbligatori"] or "{}").get(str(i))
                if punti is not None:
                    st["punti"].append(punti)
                continue
            data = r["risposte"][i].upper() if i < len(r["risposte"]) else "X"
            if data == (r["soluzioni"][i].upper() if i < len(r["soluzioni"]) else "?"):
                st["giuste"] += 1
            elif data == "X":
                st["non_date"] += 1
            else:
                st["sbagliate"] += 1
        info = {}
        if per_es:
            segnaposto = ",".join("?" * len(per_es))
            for e in conn.execute(f"SELECT id, nome, argomento, aperta FROM esercizi WHERE id IN ({segnaposto})", list(per_es)):
                v = conn.execute("SELECT testo FROM esercizio_varianti WHERE esercizio_id=? ORDER BY id LIMIT 1", (e["id"],)).fetchone()
                info[e["id"]] = {"nome": e["nome"], "argomento": e["argomento"], "aperta": bool(e["aperta"]),
                                 "testo": v["testo"] if v else ""}
        return {eid: {**info[eid], **st} for eid, st in per_es.items() if eid in info}
    finally:
        conn.close()


def _testo_edizione(corso, dettaglio_esercizi: bool) -> str:
    stat = statistiche_service.calcola_corso(corso.tag, corso=corso)
    righe = [f"### Edizione {corso.anno or '?'}" + (f" — {corso.nome}" if corso.nome else ""),
             f"Compiti corretti: {stat['totale']}; promossi {_pct(stat['promossi'], stat['totale'])}; "
             f"media dei voti finali {stat['media_finale'] if stat['media_finale'] is not None else '-'}."]
    if stat["totale"]:
        righe.append(f"Distribuzione dei voti finali (voto:numero): {_istogramma(stat['voti_finali'])}")
    for p in stat["per_appello"]:
        righe.append(f"- {p['appello_nome']}: {p['totale']} compiti, promossi {p['promossi_pct']}%, media {p['media_finale']}")
    esiti = _esiti_corso(corso.tag)
    if not esiti:
        return "\n".join(righe)
    per_arg = defaultdict(lambda: [0, 0])
    for e in esiti.values():
        if not e["aperta"]:
            per_arg[e["argomento"] or "(argomento non indicato)"][0] += e["giuste"]
            per_arg[e["argomento"] or "(argomento non indicato)"][1] += e["giuste"] + e["sbagliate"] + e["non_date"]
    if per_arg:
        righe.append("Risposte giuste per argomento: " + "; ".join(
            f"{arg} {_pct(g, n)} (su {n})" for arg, (g, n) in sorted(per_arg.items(), key=lambda x: x[1][0] / max(x[1][1], 1))))
    if dettaglio_esercizi:
        righe.append("Esercizi, dal più sbagliato (giuste/sbagliate/non date; inizio del testo):")
        ordinati = sorted(esiti.values(), key=lambda e: (e["aperta"], e["giuste"] / max(e["giuste"] + e["sbagliate"] + e["non_date"], 1)))
        for e in ordinati:
            n = e["giuste"] + e["sbagliate"] + e["non_date"]
            esito = (f"aperta, punteggio medio {_media(e['punti'])}/{corso.punteggio_max_aperta} su {len(e['punti'])}"
                     if e["aperta"] else f"{_pct(e['giuste'], n)}/{_pct(e['sbagliate'], n)}/{_pct(e['non_date'], n)} su {n}")
            righe.append(f"- {e['nome'] or 'senza nome'}" + (f" [{e['argomento']}]" if e["argomento"] else "")
                         + f": {esito} — {_breve(e['testo'], 120)}")
    return "\n".join(righe)


# --- prompt ------------------------------------------------------------------------------

SISTEMA = """Sei un esperto di didattica universitaria e di valutazione degli apprendimenti (psicometria, analisi degli item). Un docente ti affida i risultati, resi anonimi, delle prove scritte del suo corso, per capire quali sono le criticità emerse e come ripensare il corso. Gli studenti compaiono solo con uno pseudonimo (S001, S002, ...).

Come leggere i dati:
- Gli esercizi sono a risposta multipla, con alcune domande aperte valutate dal docente. Nelle righe dei compiti: "+" risposta giusta, "." risposta non data, una lettera minuscola è l'opzione sbagliata scelta (con la numerazione delle "Opzioni" dell'esercizio, dove (a) è sempre quella corretta), "?" risposta sbagliata non ricostruibile; "E3v2" indica la variante 2 dell'esercizio E3; per le domande aperte "E5=7/10" è il punteggio, "=?" non ancora valutato; "[svolg. 1/3]" su un esercizio obbligatorio è il punteggio dato allo svolgimento scritto.
- Ogni studente riceve gli esercizi in ordine e con opzioni mescolate: posizione e lettera stampate non contano.
- "G" è l'indice di Guttman normalizzato del compito (0 = risolve sempre gli esercizi più facili prima di quelli difficili, valori alti, oltre 0.4-0.5, = sbaglia esercizi facili e risolve quelli difficili: profilo anomalo, compatibile con aiuti esterni ma anche con preparazione a macchia di leopardo).
- La "discriminazione" di un esercizio è la correlazione tra il suo esito e il voto dello scritto: vicino a 0 o negativa = l'esercizio non distingue chi è preparato (testo ambiguo, risposta indicata come corretta sbagliata, o tranello).
- L'orale non è sostenuto da tutti: può essere richiesto per regole del corso o a discrezione del docente, quindi il campione degli orali non è casuale. Un orale molto più basso dello scritto resta comunque un indizio utile.
- Mancano di proposito tutte le indicazioni del docente sui singoli studenti (sospetti, annullamenti, motivazioni): le tue stime devono basarsi solo sui risultati.

Scrivi in italiano un rapporto in Markdown, con titoli di sezione, numeri a sostegno di ogni affermazione e senza ripetere i dati grezzi. Rispondi solo con il documento Markdown (niente testo prima o dopo, niente blocco di codice intorno): se puoi, rendilo scaricabile come file .md. La prima riga del documento deve essere esattamente la riga "Codice dell'analisi: ..." che trovi all'inizio dei dati. Sezioni:
1. **Sintesi** — le 5-8 osservazioni più importanti.
2. **Argomenti e concetti capiti meno** — globalmente e nella prova o nell'anno analizzato, confrontati con gli anni precedenti quando i dati lo permettono (peggiorati, migliorati, critici da sempre). Deduci l'argomento dal testo quando non è indicato. Per gli esercizi più sbagliati, interpreta le risposte sbagliate più scelte: quale errore concettuale o di calcolo rivelano.
3. **Esercizi problematici** — troppo facili o troppo difficili, poco discriminanti, con un'opzione sbagliata scelta più di quella giusta (possibile errore nel testo o nella soluzione: verifica tu risolvendo l'esercizio), molte risposte non date.
4. **Compiti probabilmente svolti con l'aiuto dell'LLM** — stima quanti compiti (un intervallo, con il grado di confidenza) e quali pseudonimi sono più sospetti, spiegando gli indizi di ciascuno. Ragiona così: risolvi tu gli esercizi come farebbe un chatbot interrogato in fretta e individua le risposte sbagliate tipiche di un modello linguistico; cerca compiti che le scelgono, compiti con esercizi difficili giusti e facili sbagliati (G alto), con quasi nessuna risposta lasciata in bianco, con uno scritto molto più alto delle prove precedenti dello stesso studente o dell'orale. Distingui gli indizi forti da quelli deboli e ricorda che sono indizi statistici, non prove.
5. **Possibili copiature tra studenti** — dalle coppie con molte risposte sbagliate identiche rispetto a quelle attese per caso.
6. **Calibrazione della prova** — distribuzione dei voti, soglia di sufficienza, rapporto tra scritto e orale, differenze tra prove o appelli e tra anni.
7. **Suggerimenti per il corso** — concreti e ordinati per priorità: argomenti da rinforzare e come, esercizi da riscrivere o sostituire, modifiche al formato della prova (anche per ridurre l'uso degli LLM).
8. **Indicazioni per i prossimi compiti** — (a) gli esercizi andati molto male: quale concetto rispiegare e come verificare nei compiti successivi che sia stato compreso (che tipo di esercizio proporre, con quali distrattori mirati agli errori osservati); (b) le varianti dello stesso esercizio con esiti molto diversi (vedi "Esiti per variante"): quanto differiscono, le cause probabili (numeri o calcoli più pesanti, un passaggio in più, distrattori più insidiosi, testo meno chiaro) e come correggerle per rendere le varianti il più possibile omogenee; (c) esercizi troppo facili o poco discriminanti da sostituire.
9. **Limiti dell'analisi** — dati mancanti o pochi casi che rendono incerte le conclusioni."""


def ambito_appello(tag: str, appello_id: int) -> dict:
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    if appello is None:
        raise ValueError("Appello non trovato")
    return {"tipo": "appello", "tag": tag, "appello_id": appello_id, "corso": corso, "appello": appello,
            "titolo": f"{appello.nome} — {corso.nome} ({corso.anno})",
            "url": f"/corsi/{tag}/appelli/{appello_id}/ia/analisi", "torna": f"/corsi/{tag}/appelli/{appello_id}#statistiche",
            "torna_etichetta": appello.nome}


def ambito_corso(tag: str) -> dict:
    corso = corsi_service.get_corso(tag)
    return {"tipo": "corso", "tag": tag, "corso": corso, "appello": None, "titolo": f"{corso.nome} ({corso.anno})",
            "url": f"/corsi/{tag}/ia/analisi", "torna": f"/corsi/{tag}/statistiche", "torna_etichetta": "Statistiche del corso"}


def ambito_generale(tags: list[str]) -> dict:
    tags = [t for t in dict.fromkeys(tags or []) if config.corso_exists(t)]
    if not tags:
        raise ValueError("Nessun corso selezionato")
    corsi = [corsi_service.get_corso(t) for t in tags]
    nomi = sorted({c.nome for c in corsi})
    query = "&".join(f"corsi={t}" for t in tags)
    return {"tipo": "generale", "tags": tags, "tag": "", "corsi": corsi, "corso": None, "appello": None,
            "titolo": f"{', '.join(nomi[:3])}{'…' if len(nomi) > 3 else ''} — {len(corsi)} {'corso' if len(corsi) == 1 else 'corsi'}",
            "url": "/ia/analisi", "query": query, "torna": f"/statistiche-generali?{query}", "torna_etichetta": "Statistiche generali"}


def _pseudonimi(matricole: set[str]) -> dict[str, str]:
    """Pseudonimi in un ordine casuale diverso a ogni prompt: dallo pseudonimo non si
    risale alla matricola, nemmeno confrontando prompt diversi."""
    sale = secrets.token_hex(16)
    ordinate = sorted(matricole, key=lambda m: hashlib.sha256((sale + m).encode()).hexdigest())
    cifre = max(3, len(str(len(ordinate))))
    return {m: f"S{str(i).zfill(cifre)}" for i, m in enumerate(ordinate, start=1)}


def _matricole(tag: str, appello_ids: list[int]) -> set[str]:
    if not appello_ids:
        return set()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        segnaposto = ",".join("?" * len(appello_ids))
        return {r["matricola"] for r in conn.execute(
            f"SELECT matricola FROM risultati WHERE appello_id IN ({segnaposto})", appello_ids)}
    finally:
        conn.close()


def _regole(corso) -> str:
    return (f"Punteggi: risposta giusta {corso.risposta_corretta}, sbagliata {corso.risposta_sbagliata}, "
            f"non data {corso.risposta_vuota}; domanda aperta fino a {corso.punteggio_max_aperta}. "
            f"Voto minimo per la sufficienza {corso.votomin} (prove parziali: {corso.votomin_raggruppamento}). "
            "Un esercizio obbligatorio non svolto rende insufficiente il compito.")


def _recenti(corsi: list, anni: int, riferimento: Optional[int] = None) -> list:
    """Solo i corsi degli ultimi `anni` anni accademici (0 = tutti), contando all'indietro
    dall'anno di `riferimento` (o dal più recente tra i corsi)."""
    if not anni or not corsi:
        return corsi
    ultimo = riferimento or max(statistiche_service._anno_key(c.anno) for c in corsi)
    return [c for c in corsi if not statistiche_service._anno_key(c.anno) or statistiche_service._anno_key(c.anno) > ultimo - anni]


def nuovo_codice() -> str:
    """Codice di un prompt: lega la risposta del modello alla mappa (locale) degli pseudonimi."""
    return datetime.now().strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(3)


def costruisci_prompt(ambito: dict, includi_compiti: bool = True, includi_note: bool = True, includi_storico: bool = True,
                      istruzioni: str = "", anni: int = 0) -> dict:
    """{"sistema", "richiesta", "n_compiti", "codice", "pseudonimi"}: il testo da inviare al
    modello per l'ambito; "pseudonimi" ({pseudonimo: matricola}) resta sul computer, per
    ritrovare dopo chi c'è dietro gli pseudonimi citati nella risposta."""
    codice = nuovo_codice()
    parti = [f"Codice dell'analisi: {codice}", ""]
    n_compiti = 0
    pseudonimi = {}
    if ambito["tipo"] in ("appello", "corso"):
        corso = ambito["corso"]
        if ambito["tipo"] == "appello":
            prove = _prove(corso.tag, ambito["appello"])
            oggetto = f"la prova «{ambito['appello'].nome}»" + (" (con le sue prove parziali)" if len(prove) > 1 else "")
        else:
            prove = corsi_service.list_appelli(corso.tag, includi_raggruppamenti=False)
            oggetto = f"tutte le prove dell'edizione {corso.anno} del corso"
        prove.sort(key=lambda a: corsi_service.data_ordinabile(a.data))
        pseudonimi = _pseudonimi(_matricole(corso.tag, [a.id for a in prove]))
        parti += [
            f"# Corso: {corso.nome}" + (f" — {corso.facolta}" if corso.facolta else "") + (f", {corso.universita}" if corso.universita else ""),
            f"Anno accademico: {corso.anno or '?'}. {_regole(corso)}",
            f"Oggetto dell'analisi: {oggetto}. Gli pseudonimi sono gli stessi in tutte le prove qui sotto.",
            "", "## Prove analizzate",
        ]
        for a in prove:
            d = _analizza_prova(corso.tag, corso, a, pseudonimi, includi_note, storico=ambito["tipo"] == "appello")
            if not d["compiti"] and not d["stat"]["totale"]:
                continue
            n_compiti += len(d["compiti"])
            parti += ["", _testo_prova(d, includi_compiti, corso)]
        altre = [c for c in _recenti(_edizioni(corso), anni, statistiche_service._anno_key(corso.anno)) if c.tag != corso.tag] if includi_storico else []
        if altre:
            parti += ["", "## Edizioni degli anni precedenti (o successivi) dello stesso corso, per confronto"]
            parti += ["\n\n".join(_testo_edizione(c, dettaglio_esercizi=True) for c in altre)]
        if ambito["tipo"] == "appello":
            parti += ["", "## Le altre prove della stessa edizione, per confronto",
                      _testo_edizione(corso, dettaglio_esercizi=False)]
    else:
        corsi = sorted(_recenti(ambito["corsi"], anni), key=lambda c: statistiche_service._anno_key(c.anno))
        parti += [
            "# Analisi di più edizioni o corsi",
            f"Corsi inclusi: {', '.join(f'{c.nome} ({c.anno})' for c in corsi)}.",
            "Non ci sono i dati dei singoli compiti: l'analisi riguarda andamento negli anni, argomenti ed esercizi. "
            "Nella sezione sui compiti svolti con un LLM ragiona sui segnali aggregati (es. cambiamenti della distribuzione "
            "dei voti o degli esercizi più sbagliati negli anni recenti) e indica cosa servirebbe per una stima per compito.",
            "", "## Edizioni",
        ]
        parti += ["\n\n".join(f"{_testo_edizione(c, dettaglio_esercizi=True)}\n{_regole(c)}" for c in corsi)]
    if istruzioni.strip():
        parti += ["", "## Indicazioni del docente per questa analisi", istruzioni.strip()]
    return {"sistema": SISTEMA, "richiesta": "\n".join(parti), "n_compiti": n_compiti, "codice": codice,
            "pseudonimi": {p: m for m, p in pseudonimi.items()}}


def prompt_come_testo(prompt: dict) -> str:
    return prompt["sistema"] + "\n\n" + "=" * 40 + "\n\n" + prompt["richiesta"]


def analizza(ambito: dict, includi_compiti: bool, includi_note: bool, includi_storico: bool, istruzioni: str = "",
             profilo_id: str = "", anni: int = 0) -> dict:
    conf = ia_service.fornitore(profilo_id)
    if not conf:
        raise ValueError("Nessuna configurazione LLM (Anthropic, OpenRouter o Ollama): vedi Impostazioni")
    prompt = costruisci_prompt(ambito, includi_compiti, includi_note, includi_storico, istruzioni, anni)
    salva_mappa(ambito, prompt)
    risposta = ia_service._chiama(conf, prompt["sistema"], [{"role": "user", "content": prompt["richiesta"]}])
    testo = ia_service._testo(risposta).strip()
    if not testo:
        raise ValueError("Il modello non ha risposto nulla: riprova")
    return {"testo": testo, "conf": {"nome": conf["nome"], "modello": conf["modello"]}}


# --- mappe degli pseudonimi (solo sul computer) ------------------------------------------

_CODICE_RE = re.compile(r"Codice dell.analisi:\s*\**\s*(\d{8}-\d{4}-[0-9a-f]{6})")
GIORNI_MAPPE = 90


def _cartella_mappe(ambito: dict) -> Path:
    return _cartella(ambito) / "mappe"


def salva_mappa(ambito: dict, prompt: dict) -> None:
    """Conserva sul computer, per qualche mese, quale matricola c'è dietro ogni pseudonimo
    di un prompt (identificato dal suo codice): la risposta del modello, salvata qui,
    potrà mostrare i nomi. Le mappe più vecchie si cancellano."""
    if not prompt.get("pseudonimi"):
        return
    cartella = _cartella_mappe(ambito)
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / f"{prompt['codice']}.json").write_text(json.dumps(
        {"codice": prompt["codice"], "quando": datetime.now().isoformat(timespec="seconds"),
         "pseudonimi": prompt["pseudonimi"]}, ensure_ascii=False), encoding="utf-8")
    limite = datetime.now().timestamp() - GIORNI_MAPPE * 86400
    for vecchia in cartella.glob("*.json"):
        if vecchia.stat().st_mtime < limite:
            vecchia.unlink(missing_ok=True)


def carica_mappa(ambito: dict, codice: str) -> Optional[dict]:
    if not re.fullmatch(r"\d{8}-\d{4}-[0-9a-f]{6}", codice or ""):
        return None
    try:
        return json.loads((_cartella_mappe(ambito) / f"{codice}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def nomi_pseudonimi(ambito: dict, pseudonimi: dict) -> dict:
    """{pseudonimo: "Cognome Nome (matricola)"} per gli pseudonimi di un'analisi salvata."""
    if not pseudonimi or not ambito.get("tag"):
        return {}
    from quizesame.services import studenti as studenti_service
    nomi = {}
    for p, m in pseudonimi.items():
        s = studenti_service.get_studente(ambito["tag"], m)
        nomi[p] = f"{s.cognome} {s.nome} ({m})" if s else m
    return nomi


def pulisci_markdown(testo: str) -> str:
    """Toglie il blocco di codice che a volte avvolge il documento (```markdown ... ```)."""
    t = (testo or "").strip()
    m = re.fullmatch(r"```[a-zA-Z]*\s*\n(.*)\n```", t, re.S)
    return (m.group(1) if m else t).strip()


# --- analisi salvate ---------------------------------------------------------------------

_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def _cartella(ambito: dict) -> Path:
    # le analisi di più corsi stanno fuori dalle cartelle dei corsi (list_corsi considera
    # corso solo una cartella con db.sqlite, quindi questa non viene scambiata per un corso)
    return config.corso_dir(ambito["tag"]) / "analisi_ia" if ambito["tag"] else config.DATA_ROOT / "_analisi_ia"


def salva(ambito: dict, testo: str, conf: dict, opzioni: dict) -> str:
    cartella = _cartella(ambito)
    cartella.mkdir(parents=True, exist_ok=True)
    adesso = datetime.now()
    base = adesso.strftime("%Y%m%d-%H%M%S")
    analisi_id, n = base, 1
    while (cartella / f"{analisi_id}.json").exists():
        n += 1
        analisi_id = f"{base}-{n}"
    testo = pulisci_markdown(testo)
    trovato = _CODICE_RE.search(testo)
    mappa = carica_mappa(ambito, trovato.group(1)) if trovato else None
    dati = {
        "id": analisi_id, "quando": adesso.isoformat(timespec="seconds"), "tipo": ambito["tipo"],
        "appello_id": ambito.get("appello_id"), "tags": ambito.get("tags"), "titolo": ambito["titolo"],
        "conf": conf, "opzioni": opzioni, "testo": testo,
        # solo sul computer: chi c'è dietro gli pseudonimi citati nel rapporto
        "pseudonimi": (mappa or {}).get("pseudonimi") or {},
    }
    (cartella / f"{analisi_id}.json").write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    return analisi_id


def _appartiene(ambito: dict, d: dict) -> bool:
    if d.get("tipo") != ambito["tipo"]:
        return False
    if ambito["tipo"] == "appello":
        return d.get("appello_id") == ambito["appello_id"]
    if ambito["tipo"] == "generale":
        return sorted(d.get("tags") or []) == sorted(ambito["tags"])
    return True


def elenco(ambito: dict) -> list[dict]:
    cartella = _cartella(ambito)
    if not cartella.exists():
        return []
    voci = []
    for p in sorted(cartella.glob("*.json"), reverse=True):
        if not _ID_RE.match(p.stem):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _appartiene(ambito, d):
            voci.append({k: d.get(k) for k in ("id", "quando", "titolo", "conf")})
    return voci


def carica(ambito: dict, analisi_id: str) -> Optional[dict]:
    if not _ID_RE.match(analisi_id or ""):
        return None
    p = _cartella(ambito) / f"{analisi_id}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if _appartiene(ambito, d) else None


def elimina(ambito: dict, analisi_id: str) -> bool:
    if carica(ambito, analisi_id) is None:
        return False
    (_cartella(ambito) / f"{analisi_id}.json").unlink()
    return True


# --- indicazioni per i prossimi compiti ---------------------------------------------------

_TITOLO_INDICAZIONI = re.compile(r"indicazioni per i prossimi compiti", re.I)
_TITOLO_SEZIONE = re.compile(r"^\s*(#{1,6}\s|\d+[.)]\s+\*\*|\*\*\d+[.)])")


def sezione_indicazioni(testo: str) -> str:
    """Dal rapporto di un'analisi, la sola sezione "Indicazioni per i prossimi compiti"
    (fino al titolo della sezione successiva); vuota se non c'è."""
    righe = (testo or "").splitlines()
    for i, riga in enumerate(righe):
        if _TITOLO_INDICAZIONI.search(riga) and _TITOLO_SEZIONE.match(riga):
            corpo = []
            for r in righe[i + 1:]:
                if _TITOLO_SEZIONE.match(r):
                    break
                corpo.append(r)
            # il titolo stesso può contenere già l'inizio del testo (es. "8. **Indicazioni...** — ...")
            resto = re.sub(r"^.*?indicazioni per i prossimi compiti\**\s*[—:-]*\s*", "", riga, flags=re.I).strip()
            return "\n".join(([resto] if resto else []) + corpo).strip()
    return ""


def indicazioni_recenti(tag: str, max_analisi: int = 3) -> list[dict]:
    """Le indicazioni per i prossimi compiti delle analisi salvate più recenti di questo
    corso (e, se non bastano, delle altre edizioni dello stesso insegnamento): una per
    appello, dalla più recente. [{"titolo", "quando", "testo"}]"""
    corso = corsi_service.get_corso(tag)
    corsi = [corso] + sorted(corsi_service.corsi_simili(corso, corsi_service.list_corsi()),
                             key=lambda c: statistiche_service._anno_key(c.anno), reverse=True)
    trovate, visti = [], set()
    for c in corsi:
        cartella = config.corso_dir(c.tag) / "analisi_ia"
        if not cartella.exists():
            continue
        analisi = []
        for p in cartella.glob("*.json"):
            try:
                analisi.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        for d in sorted(analisi, key=lambda d: d.get("quando") or "", reverse=True):
            chiave = (c.tag, d.get("tipo"), d.get("appello_id"))
            testo = sezione_indicazioni(d.get("testo", ""))
            if chiave in visti or not testo:
                continue
            visti.add(chiave)
            trovate.append({"titolo": d.get("titolo") or c.nome, "quando": (d.get("quando") or "")[:10], "testo": testo})
            if len(trovate) >= max_analisi:
                return trovate
    return trovate


# --- Markdown della risposta -------------------------------------------------------------

def _inline(t: str) -> str:
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"(?<![\w*$\\])\*(?!\s)([^*$]+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)


def con_nomi(html: Markup, nomi: dict) -> Markup:
    """Accanto a ogni pseudonimo del rapporto, il nome dello studente (nascondibile)."""
    if not nomi:
        return html
    def sost(m):
        nome = nomi.get(m.group(0))
        if not nome:
            return m.group(0)
        return (f'<span class="pseudonimo" data-info="{escape(nome)}">{m.group(0)}'
                f'<span class="nome-reale"> · {escape(nome)}</span></span>')
    return Markup(re.sub(r"\bS\d{3,}\b", sost, str(html)))


def markdown_html(testo: str) -> Markup:
    """Il Markdown essenziale delle risposte (titoli, elenchi, tabelle, grassetto), già
    sottoposto a escape: le formule restano tra $ per KaTeX."""
    out, paragrafo, lista, tabella = [], [], None, []

    def chiudi():
        nonlocal lista, tabella
        if paragrafo:
            out.append("<p>" + "<br>".join(_inline(r) for r in paragrafo) + "</p>")
            paragrafo.clear()
        if lista:
            out.append(f"</{lista}>")
            lista = None
        if tabella:
            righe = [r for r in tabella if not re.fullmatch(r"\|?[\s:|-]+\|?", r)]
            html = ["<table class=\"tabella-elenco\">"]
            for k, r in enumerate(righe):
                celle = [c.strip() for c in r.strip().strip("|").split("|")]
                tagc = "th" if k == 0 and len(righe) > 1 else "td"
                html.append("<tr>" + "".join(f"<{tagc}>{_inline(c)}</{tagc}>" for c in celle) + "</tr>")
            out.append("".join(html) + "</table>")
            tabella = []

    for riga in str(escape(testo)).splitlines():
        s = riga.strip()
        titolo = re.match(r"(#{1,6})\s+(.*)", s)
        voce = re.match(r"([-*•]|\d+[.)])\s+(.*)", s)
        if s.startswith("|"):
            if not tabella:
                chiudi()
            tabella.append(s)
            continue
        if tabella:
            chiudi()
        if not s:
            chiudi()
        elif titolo:
            chiudi()
            livello = min(len(titolo.group(1)) + 1, 5)
            out.append(f"<h{livello}>{_inline(titolo.group(2))}</h{livello}>")
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            chiudi()
            out.append("<hr>")
        elif voce:
            tipo = "ol" if voce.group(1)[0].isdigit() else "ul"
            if paragrafo or (lista and lista != tipo):
                chiudi()
            if not lista:
                lista = tipo
                out.append(f"<{tipo}>")
            out.append(f"<li>{_inline(voce.group(2))}</li>")
        elif lista and riga.startswith((" ", "\t")):
            out[-1] = out[-1][:-5] + " " + _inline(s) + "</li>"
        else:
            if lista:
                chiudi()
            paragrafo.append(s)
    chiudi()
    return Markup("\n".join(out))
