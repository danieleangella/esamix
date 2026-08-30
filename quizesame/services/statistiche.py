"""Statistiche di un appello: promossi/insufficienti, medie scritto/orale, distribuzione
dei voti confrontata con una gaussiana teorica, ed esercizi più sbagliati con le risposte
più comuni. Calcolate a partire dai dati già salvati (risultati, compiti, compito_esercizi),
niente di nuovo da persistere."""
import json
import math
import re
from collections import Counter, defaultdict
from typing import Optional

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service


def _asimmetria_e_curtosi(voti: list[int], media: float, dev_std: float) -> tuple[Optional[float], Optional[float]]:
    """Skewness e curtosi in eccesso: per una gaussiana valgono entrambe 0, quindi la loro
    distanza da 0 è una misura diretta di quanto la distribuzione reale si discosti dalla
    forma teorica (a campana e simmetrica) attesa per un voto d'esame ben calibrato."""
    n = len(voti)
    if n < 3 or dev_std == 0:
        return None, None
    m2 = sum((v - media) ** 2 for v in voti) / n
    m3 = sum((v - media) ** 3 for v in voti) / n
    m4 = sum((v - media) ** 4 for v in voti) / n
    asimmetria = m3 / m2 ** 1.5
    curtosi = m4 / m2 ** 2 - 3
    return round(asimmetria, 2), round(curtosi, 2)


def _distribuzione_e_gaussiana(voti: list[int]) -> dict:
    vuoto = {
        "bins": [], "curva_points": "", "larghezza": 0, "altezza": 180, "media": None, "dev_std": None,
        "asimmetria": None, "curtosi": None, "distanza_gaussiana": None,
    }
    if not voti:
        return vuoto
    minimo, massimo = min(voti), max(voti)
    conteggio = Counter(voti)
    valori = [{"voto": v, "conteggio": conteggio.get(v, 0)} for v in range(minimo, massimo + 1)]

    n = len(voti)
    media = sum(voti) / n
    dev_std = math.sqrt(sum((v - media) ** 2 for v in voti) / n) if n > 1 else 0.0
    asimmetria, curtosi = _asimmetria_e_curtosi(voti, media, dev_std)
    distanza_gaussiana = round(math.sqrt(asimmetria ** 2 + curtosi ** 2), 2) if asimmetria is not None else None

    curva = [0.0] * len(valori)
    if dev_std > 0:
        for i, b in enumerate(valori):
            densita = (1 / (dev_std * math.sqrt(2 * math.pi))) * math.exp(-((b["voto"] - media) ** 2) / (2 * dev_std ** 2))
            curva[i] = densita * n

    max_valore = max([b["conteggio"] for b in valori] + curva) or 1

    larghezza_bin = 40
    altezza = 180
    bins = []
    punti_curva = []
    for i, b in enumerate(valori):
        bar_h = round(b["conteggio"] / max_valore * altezza, 1)
        x_centro = i * larghezza_bin + larghezza_bin / 2
        bins.append({
            "voto": b["voto"], "conteggio": b["conteggio"],
            "x": i * larghezza_bin + 4, "larghezza": larghezza_bin - 8,
            "y": round(altezza - bar_h, 1), "altezza_barra": bar_h, "x_centro": x_centro,
        })
        y_curva = round(altezza - (curva[i] / max_valore * altezza), 1)
        punti_curva.append(f"{x_centro},{y_curva}")

    return {
        "bins": bins, "curva_points": " ".join(punti_curva), "larghezza": len(valori) * larghezza_bin,
        "altezza": altezza, "media": round(media, 2), "dev_std": round(dev_std, 2),
        "asimmetria": asimmetria, "curtosi": curtosi, "distanza_gaussiana": distanza_gaussiana,
    }


def _statistiche_esercizi(tag: str, conn, risultati: list[dict]) -> list[dict]:
    per_esercizio = defaultdict(lambda: {"nome": "", "corrette": 0, "sbagliate": 0, "risposte_sbagliate": Counter()})
    for r in risultati:
        if not r["compito_id"] or not r["risposte"]:
            continue
        compito = conn.execute("SELECT soluzioni FROM compiti WHERE id=?", (r["compito_id"],)).fetchone()
        if not compito:
            continue
        soluzioni, risposte = compito["soluzioni"], r["risposte"]
        posizioni = conn.execute(
            "SELECT ce.posizione, ce.esercizio_id, ce.aperta, ce.risposte_mischiate, e.nome FROM compito_esercizi ce "
            "JOIN esercizi e ON e.id = ce.esercizio_id WHERE ce.compito_id=?",
            (r["compito_id"],),
        ).fetchall()
        for pos in posizioni:
            i = pos["posizione"]
            # una domanda aperta non ha una lettera "corretta" con cui confrontare la
            # risposta: non ha senso conteggiarla come esercizio sbagliato/corretto qui.
            if pos["aperta"] or i >= len(risposte) or i >= len(soluzioni):
                continue
            lettera_data = risposte[i].upper()
            lettera_corretta = soluzioni[i].upper()
            stat = per_esercizio[pos["esercizio_id"]]
            stat["nome"] = pos["nome"] or f"Esercizio #{pos['esercizio_id']}"
            if lettera_data == lettera_corretta:
                stat["corrette"] += 1
            else:
                stat["sbagliate"] += 1
                if lettera_data != "X":
                    testo_scelto = None
                    if pos["risposte_mischiate"]:
                        opzioni = json.loads(pos["risposte_mischiate"])
                        indice = ord(lettera_data) - ord("A")
                        if 0 <= indice < len(opzioni):
                            testo_scelto = opzioni[indice]
                    # senza risposte_mischiate (compiti generati prima di questa
                    # funzionalità) non c'è modo di risalire al testo: la lettera da sola
                    # cambia significato a ogni compito, quindi si raggruppa come "ignota".
                    stat["risposte_sbagliate"][testo_scelto or "(testo non disponibile)"] += 1

    elenco = []
    for esercizio_id, stat in per_esercizio.items():
        totale = stat["corrette"] + stat["sbagliate"]
        risposte_sbagliate = [
            {"testo": testo, "conteggio": n, "percentuale": round(100 * n / totale) if totale else 0}
            for testo, n in stat["risposte_sbagliate"].most_common()
        ]
        esercizio = esercizi_service.get_esercizio(tag, esercizio_id)
        varianti_testo = [v.testo for v in esercizio.varianti] if esercizio else []
        elenco.append({
            "nome": stat["nome"], "corrette": stat["corrette"], "sbagliate": stat["sbagliate"],
            "percentuale_sbagliate": round(100 * stat["sbagliate"] / totale) if totale else 0,
            "risposte_sbagliate": risposte_sbagliate, "varianti_testo": varianti_testo,
        })
    elenco.sort(key=lambda s: s["percentuale_sbagliate"], reverse=True)
    return elenco


def calcola(tag: str, appello_id: int) -> dict:
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        risultati = [dict(r) for r in conn.execute(
            "SELECT * FROM risultati WHERE appello_id=?", (appello_id,)
        ).fetchall()]

        totale = len(risultati)
        in_attesa_orale = [r for r in risultati if r["richiede_orale"] and not r["orale_svolto"]]
        # un voto rifiutato dallo studente non è un esito definitivo (dovrà ripresentarsi):
        # non va contato né tra i promossi né tra gli insufficienti.
        valutati = [
            r for r in risultati
            if not (r["richiede_orale"] and not r["orale_svolto"]) and r["esito"] != "rifiutato"
        ]
        promossi = [r for r in valutati if r["voto"] is not None and r["voto"] >= votomin]
        insufficienti = [r for r in valutati if r["voto"] is not None and r["voto"] < votomin]

        voti_scritti = []
        for r in risultati:
            v = r["voto_scritto"] if r["voto_scritto"] is not None else (r["voto"] if not r["richiede_orale"] else None)
            if v is not None:
                voti_scritti.append(v)
        voti_orali = [r["voto"] for r in risultati if r["orale_svolto"] and r["voto"] is not None]
        voti_finali = [r["voto"] for r in valutati if r["voto"] is not None]

        media = lambda lista: round(sum(lista) / len(lista), 2) if lista else None  # noqa: E731
        pct = lambda n: round(100 * n / totale) if totale else None  # noqa: E731

        return {
            "totale": totale, "promossi": len(promossi), "insufficienti": len(insufficienti),
            "in_attesa_orale": len(in_attesa_orale), "orali_svolti": len(voti_orali),
            "promossi_pct": pct(len(promossi)), "insufficienti_pct": pct(len(insufficienti)),
            "in_attesa_orale_pct": pct(len(in_attesa_orale)), "orali_svolti_pct": pct(len(voti_orali)),
            "media_scritto": media(voti_scritti), "media_orale": media(voti_orali), "media_finale": media(voti_finali),
            "votomin": votomin, "distribuzione": _distribuzione_e_gaussiana(voti_finali),
            "esercizi": _statistiche_esercizi(tag, conn, risultati), "voti_finali": voti_finali,
        }
    finally:
        conn.close()


def _andamento_appelli(per_appello: list[dict]) -> dict:
    """Geometria di un grafico a barre appaiate (esami svolti / promossi) per appello, in
    ordine cronologico: stesso approccio di _distribuzione_e_gaussiana, coordinate già
    calcolate qui per tenere il template semplice."""
    altezza = 160
    margine_top = 14  # spazio per l'etichetta numerica sopra la barra più alta
    if not per_appello:
        return {"gruppi": [], "larghezza": 0, "altezza": altezza, "margine_top": margine_top}
    larghezza_gruppo = 100
    max_valore = max(p["totale"] for p in per_appello) or 1
    gruppi = []
    for i, p in enumerate(per_appello):
        x0 = i * larghezza_gruppo
        h_totale = round(p["totale"] / max_valore * altezza, 1)
        h_promossi = round(p["promossi"] / max_valore * altezza, 1)
        gruppi.append({
            "nome": p["appello_nome"], "totale": p["totale"], "promossi": p["promossi"],
            "x_totale": x0 + 15, "y_totale": round(margine_top + altezza - h_totale, 1), "h_totale": h_totale,
            "x_promossi": x0 + 45, "y_promossi": round(margine_top + altezza - h_promossi, 1), "h_promossi": h_promossi,
            "x_centro": x0 + larghezza_gruppo / 2,
        })
    return {
        "gruppi": gruppi, "larghezza": len(per_appello) * larghezza_gruppo,
        "altezza": altezza, "margine_top": margine_top,
    }


def calcola_corso(tag: str) -> dict:
    """Le stesse statistiche di `calcola`, ma aggregate su tutti gli appelli del corso
    (esclusi i raggruppamenti, che non hanno propri risultati ma calcolano una media di
    altri appelli): utile per farsi un'idea d'insieme sull'andamento dell'intero corso,
    non solo di un singolo appello."""
    appelli = corsi_service.list_appelli(tag, includi_raggruppamenti=False)
    per_appello = []
    totale = promossi = insufficienti = in_attesa_orale = orali_svolti = 0
    voti_finali_tutti: list[int] = []
    voti_scritti_tutti: list[int] = []
    voti_orali_tutti: list[int] = []

    for appello in appelli:
        stat = calcola(tag, appello.id)
        if stat["totale"] == 0:
            continue
        per_appello.append({
            "appello_id": appello.id, "appello_nome": appello.nome,
            "totale": stat["totale"], "promossi": stat["promossi"], "insufficienti": stat["insufficienti"],
            "promossi_pct": stat["promossi_pct"], "insufficienti_pct": stat["insufficienti_pct"],
            "media_finale": stat["media_finale"],
        })
        totale += stat["totale"]
        promossi += stat["promossi"]
        insufficienti += stat["insufficienti"]
        in_attesa_orale += stat["in_attesa_orale"]
        orali_svolti += stat["orali_svolti"]
        voti_finali_tutti.extend(stat["voti_finali"])
        if stat["media_scritto"] is not None:
            voti_scritti_tutti.append(stat["media_scritto"])
        if stat["media_orale"] is not None:
            voti_orali_tutti.append(stat["media_orale"])

    media = lambda lista: round(sum(lista) / len(lista), 2) if lista else None  # noqa: E731
    pct = lambda n: round(100 * n / totale) if totale else None  # noqa: E731

    return {
        "totale": totale, "promossi": promossi, "insufficienti": insufficienti,
        "in_attesa_orale": in_attesa_orale, "orali_svolti": orali_svolti,
        "promossi_pct": pct(promossi), "insufficienti_pct": pct(insufficienti),
        "in_attesa_orale_pct": pct(in_attesa_orale), "orali_svolti_pct": pct(orali_svolti),
        "media_scritto": media(voti_scritti_tutti), "media_orale": media(voti_orali_tutti),
        "media_finale": media(voti_finali_tutti),
        "distribuzione": _distribuzione_e_gaussiana(voti_finali_tutti),
        "per_appello": per_appello, "andamento": _andamento_appelli(per_appello),
    }


def calcola_globale() -> dict:
    """Riepilogo su tutti i corsi dell'installazione, per il riquadro in homepage:
    quanti esami risultano svolti (voto finale registrato) e quante sufficienze, in
    quanti corsi e a partire da quale anno accademico."""
    corsi = corsi_service.list_corsi()
    esami_svolti = 0
    sufficienze = 0
    anni_iniziali = []
    for corso in corsi:
        stat = calcola_corso(corso.tag)
        esami_svolti += stat["promossi"] + stat["insufficienti"]
        sufficienze += stat["promossi"]
        match = re.search(r"\d{4}", corso.anno or "")
        if match:
            anni_iniziali.append(int(match.group()))

    return {
        "esami_svolti": esami_svolti, "sufficienze": sufficienze,
        "sufficienze_pct": round(100 * sufficienze / esami_svolti) if esami_svolti else None,
        "numero_corsi": len(corsi), "anno_iniziale": min(anni_iniziali) if anni_iniziali else None,
    }
