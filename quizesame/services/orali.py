"""Foglio per gli orali: chi deve sostenere l'orale di un appello (o di un raggruppamento),
con le caselle per la presenza e per il voto, e per ciascuno il riepilogo dello scritto (o
degli scritti di ogni prova): voto (e media), errori fatti, note e motivazioni. Le stesse
informazioni alimentano il PDF/LaTeX, la pagina HTML, il Markdown e il CSV."""
import csv
import io

from quizesame import config, db
from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service
from quizesame.services import risultati as risultati_service
from quizesame.services.studenti import tipo_dsa


def _errore(riga: dict, risposta_corretta: int) -> bool:
    """Un esercizio non preso in pieno: punteggio sotto quello di una risposta corretta (per
    gli svolgimenti valutati a mano anche un punteggio parziale), o risposta sbagliata."""
    if riga["punteggio_assegnato"] is not None:
        return riga["punteggio_assegnato"] < risposta_corretta
    return not riga["corretta"]


def descrizione_errore(e: dict, risposta_corretta: int) -> str:
    """Cosa è andato storto in un esercizio, in breve (testo semplice)."""
    if e["lettera_corretta"] in ("-", "?"):
        # domanda aperta (o posizione senza griglia): conta solo il punteggio dato
        parti = []
    elif e["lettera_data"] == "X":
        parti = ["non risposto"]
    elif e["lettera_data"] != e["lettera_corretta"]:
        parti = [f"risposta {e['lettera_data']} invece di {e['lettera_corretta']}"]
    else:
        parti = ["risposta giusta"]
    if e["punteggio_assegnato"] is not None:
        svolgimento = "svolgimento " if parti == ["risposta giusta"] else ""
        parti.append(f"{svolgimento}{e['punteggio_assegnato']}/{risposta_corretta} punti")
    return ", ".join(parti)


def foglio_orali(tag: str, appello_id: int) -> dict:
    """{corso, appello, prove (nomi, solo per un raggruppamento), studenti}: per ogni
    studente che deve fare (o ha fatto) l'orale, l'anagrafica, il voto dello scritto di
    riferimento (la media per un raggruppamento, con i voti delle prove), l'esito
    dell'orale se già registrato, le motivazioni dell'orale e, prova per prova, voto,
    codice del compito, errori ed eventuale nota sul compito."""
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    prove = raggruppamento.membri if raggruppamento else [appello]
    votomin = corsi_service.effective_votomin(corso, appello)
    voti_prove = risultati_service.voti_prove_raggruppamento(tag, raggruppamento) if raggruppamento else {}
    righe = (correzione_service.list_orali_da_svolgere(tag, appello_id)
             + correzione_service.list_orali_svolti(tag, appello_id))
    righe.sort(key=lambda r: ((r["cognome"] or "").lower(), (r["nome"] or "").lower()))

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        anagrafica = {r["matricola"]: dict(r) for r in conn.execute("SELECT * FROM studenti")}
        obbligatori = {r["matricola"]: dict(r) for r in conn.execute("SELECT * FROM orale_obbligatorio")}
    finally:
        conn.close()

    studenti = []
    for r in righe:
        matricola = r["matricola"]
        scritti = []
        for p in prove:
            try:
                d = correzione_service.dettaglio_risultato(tag, p.id, matricola)
            except ValueError:
                scritti.append({"prova": p.nome, "esito": "nessun risultato", "voto": None, "codice": None,
                                "errori": [], "n_esercizi": 0, "nota": "", "motivazione_orale": ""})
                continue
            ris = d["risultato"]
            voto = ris["voto_scritto"] if ris["voto_scritto"] is not None else ris["voto"]
            errori = [
                {**e, "descrizione": descrizione_errore(e, corso.risposta_corretta)}
                for e in d["righe"] if _errore(e, corso.risposta_corretta)
            ]
            scritti.append({
                "prova": p.nome, "esito": ris["esito"], "voto": voto, "codice": ris["codice"],
                "errori": errori, "n_esercizi": len(d["righe"]),
                "nota": ris["nota_correzione"] or "",
                "motivazione_orale": ris["orale_motivazione"] or "" if raggruppamento else "",
                "insufficiente_per_obbligatorio": bool(ris["insufficiente_per_obbligatorio"]),
            })
        voti = voti_prove.get(matricola, {}).get("voti") or []
        scritto = r["voto_scritto"] if r["voto_scritto"] is not None else r["voto"]
        if raggruppamento and scritto is None and voti and None not in voti:
            scritto = risultati_service.media_per_eccesso(voti, votomin)
        media_esatta = round(sum(voti) / len(voti), 2) if raggruppamento and voti and None not in voti else None
        if r["orale_svolto"]:
            esito_orale = {"assente": "assente", "insufficiente": "insufficiente"}.get(
                r["esito_orale"], f"voto {r['voto']}" if r["voto"] is not None else "svolto")
        else:
            esito_orale = ""
        s = anagrafica.get(matricola, {})
        obbligatorio = obbligatori.get(matricola)
        studenti.append({
            "matricola": matricola, "cognome": r["cognome"], "nome": r["nome"],
            "dsa": tipo_dsa(s.get("dsa"), s.get("dsa_tipo")) if s.get("dsa") else "",
            "dsa_note": s.get("dsa_note") or "",
            "voti_prove": [v if v is not None else "—" for v in voti] if raggruppamento else [],
            "scritto": scritto, "media_esatta": media_esatta,
            "esito_orale": esito_orale,
            "motivazione_orale": r["orale_motivazione"] or "",
            "orale_obbligatorio": obbligatorio["motivazione"] or "sì" if obbligatorio else "",
            "scritti": scritti,
        })
    return {
        "corso": corso, "appello": appello, "prove": [p.nome for p in prove] if raggruppamento else [],
        "studenti": studenti,
    }


def foglio_orali_csv(dati: dict) -> bytes:
    """Una riga per studente: anagrafica, voti dello scritto, numero di errori e note, e le
    colonne Presente e Voto orale da compilare."""
    prove = dati["prove"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([
        "Matricola", "Cognome", "Nome", "DSA/L104", *prove, "Media" if prove else "Voto scritto",
        "Errori", "Note", "Presente", "Voto orale",
    ])
    for s in dati["studenti"]:
        errori = "; ".join(
            (f"{sc['prova']}: " if prove else "")
            + ", ".join(f"{e['esercizio_nome']} ({e['descrizione']})" for e in sc["errori"])
            for sc in s["scritti"] if sc["errori"]
        )
        writer.writerow([
            s["matricola"], s["cognome"], s["nome"], s["dsa"], *s["voti_prove"],
            s["scritto"] if s["scritto"] is not None else "", errori, " | ".join(note_studente(s, bool(prove))),
            "", s["esito_orale"],
        ])
    return buffer.getvalue().encode("utf-8-sig")


def note_studente(s: dict, raggruppamento: bool) -> list[str]:
    """Le note da mostrare per uno studente (testo semplice): DSA, perché fa l'orale, note
    sui compiti."""
    note = []
    if s["dsa"]:
        note.append(s["dsa"] + (f": {s['dsa_note']}" if s["dsa_note"] else ""))
    if s["orale_obbligatorio"]:
        note.append(f"Orale obbligatorio: {s['orale_obbligatorio']}")
    if s["motivazione_orale"]:
        note.append(f"Motivo dell'orale: {s['motivazione_orale']}")
    for sc in s["scritti"]:
        prefisso = f"{sc['prova']}: " if raggruppamento else ""
        if sc.get("motivazione_orale") and sc["motivazione_orale"] != s["motivazione_orale"]:
            note.append(f"{prefisso}motivo dell'orale: {sc['motivazione_orale']}")
        if sc.get("insufficiente_per_obbligatorio"):
            note.append(f"{prefisso}insufficiente per un obbligatorio non svolto")
        if sc["nota"]:
            note.append(f"{prefisso}nota sul compito: {sc['nota']}")
    return note
