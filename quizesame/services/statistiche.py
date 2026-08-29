"""Statistiche di un appello: promossi/insufficienti, medie scritto/orale, distribuzione
dei voti confrontata con una gaussiana teorica, ed esercizi più sbagliati con le risposte
più comuni. Calcolate a partire dai dati già salvati (risultati, compiti, compito_esercizi),
niente di nuovo da persistere."""
import json
import math
from collections import Counter, defaultdict

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service


def _distribuzione_e_gaussiana(voti: list[int]) -> dict:
    vuoto = {"bins": [], "curva_points": "", "larghezza": 0, "altezza": 180, "media": None, "dev_std": None}
    if not voti:
        return vuoto
    minimo, massimo = min(voti), max(voti)
    conteggio = Counter(voti)
    valori = [{"voto": v, "conteggio": conteggio.get(v, 0)} for v in range(minimo, massimo + 1)]

    n = len(voti)
    media = sum(voti) / n
    dev_std = math.sqrt(sum((v - media) ** 2 for v in voti) / n) if n > 1 else 0.0

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
            "SELECT ce.posizione, ce.esercizio_id, ce.risposte_mischiate, e.nome FROM compito_esercizi ce "
            "JOIN esercizi e ON e.id = ce.esercizio_id WHERE ce.compito_id=?",
            (r["compito_id"],),
        ).fetchall()
        for pos in posizioni:
            i = pos["posizione"]
            if i >= len(risposte) or i >= len(soluzioni):
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
        valutati = [r for r in risultati if not (r["richiede_orale"] and not r["orale_svolto"])]
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

        return {
            "totale": totale, "promossi": len(promossi), "insufficienti": len(insufficienti),
            "in_attesa_orale": len(in_attesa_orale), "orali_svolti": len(voti_orali),
            "media_scritto": media(voti_scritti), "media_orale": media(voti_orali), "media_finale": media(voti_finali),
            "votomin": votomin, "distribuzione": _distribuzione_e_gaussiana(voti_finali),
            "esercizi": _statistiche_esercizi(tag, conn, risultati),
        }
    finally:
        conn.close()
