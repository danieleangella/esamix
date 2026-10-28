from collections import defaultdict
from dataclasses import dataclass
from math import ceil, floor

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services.latex import LatexContext, crea_tex_risultati, celavoto, celamatr


def ctx_for(tag: str, corso, appello) -> LatexContext:
    return LatexContext(
        corso=corso.nome, facolta=corso.facolta, universita=corso.universita, anno=corso.anno, tag=tag,
        appello_nome=appello.nome, appello_data=appello.data or "",
        consegna=corsi_service.effective_consegna(corso, appello), votomin=corsi_service.effective_votomin(corso, appello),
        risposta_corretta=corso.risposta_corretta, risposta_sbagliata=corso.risposta_sbagliata,
        risposta_vuota=corso.risposta_vuota, frase_consegna=corso.frase_consegna, frase_regole=corso.frase_regole,
        frase_obbligatorio=corso.frase_obbligatorio,
        orale_data=appello.orale_data or "", orale_ora=appello.orale_ora or "", orale_aula=appello.orale_aula or "",
    )


def _etichetta_esito(r, votomin: int, annullato_come: str = "insufficiente") -> str:
    if r["esito"] == "assente":
        return "assente"
    if r["esito"] == "ritirato":
        return "ritirato"
    if r["esito"] == "annullato":
        # nei risultati pubblicati non si dice che è stato annullato: conta come un
        # insufficiente qualsiasi (o un ritirato, secondo l'impostazione del corso)
        return "ritirato" if annullato_come == "ritirato" else celavoto(0, votomin)
    if r["esito"] == "rifiutato":
        return "rifiutato"
    if r["richiede_orale"] and not r["orale_svolto"]:
        return "orale"
    if r["esito_orale"] == "assente":
        return "assente (orale)"
    if r["esito_orale"] == "insufficiente":
        return "insufficiente (orale)"
    if r["voto"] is None:
        return "-"
    return celavoto(int(r["voto"]), votomin)


def _righe_risultati(tag: str, appello_id: int, votomin: int):
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? ORDER BY s.cognome COLLATE NOCASE, s.nome COLLATE NOCASE",
            (appello_id,),
        ).fetchall()
    finally:
        conn.close()


def media_per_eccesso(voti: list[int], votomin: int) -> int:
    """Voto finale da più prove: la sufficienza si giudica sulla media esatta, e solo se
    è sufficiente la si approssima per eccesso (17 e 18 fanno 17,5: insufficiente, e il
    voto resta 17, sotto la soglia; 18 e 19 fanno 18,5, cioè 19)."""
    media = sum(voti) / len(voti)
    return ceil(media) if media >= votomin else floor(media)


def voti_prove_raggruppamento(tag: str, raggruppamento) -> dict[str, list]:
    """Per ogni studente con un risultato in almeno una prova del raggruppamento, il voto
    dello scritto in ciascuna prova (nell'ordine delle prove; None se non l'ha svolta o
    non ha un voto) e se le ha superate tutte, ognuna con la sua soglia."""
    corso = corsi_service.get_corso(tag)
    membri = raggruppamento.membri
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        voti: dict[str, list] = {}
        for i, membro in enumerate(membri):
            for r in conn.execute(
                "SELECT matricola, esito, voto, voto_scritto, valutazione_sospesa FROM risultati WHERE appello_id=?",
                (membro.id,),
            ):
                voto = r["voto_scritto"] if r["voto_scritto"] is not None else r["voto"]
                if r["esito"] not in ("voto", "rifiutato") or r["valutazione_sospesa"]:
                    voto = None
                voti.setdefault(r["matricola"], [None] * len(membri))[i] = voto
    finally:
        conn.close()
    soglie = [corsi_service.effective_votomin(corso, m) for m in membri]
    return {
        matricola: {
            "voti": v,
            "superate": all(x is not None and x >= soglia for x, soglia in zip(v, soglie)),
        }
        for matricola, v in voti.items()
    }


def nomi_prove(tag: str, appello_id: int) -> list[str]:
    """Le prove di un raggruppamento (colonne in più nei risultati combinati); vuoto per
    un appello qualsiasi."""
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    return [m.nome for m in raggruppamento.membri] if raggruppamento else []


def _righe_pubblicabili(tag: str, appello_id: int) -> list[dict]:
    """Le righe dell'elenco da pubblicare (matricola in chiaro: la oscura chi stampa).
    Per un raggruppamento compaiono solo gli studenti che hanno superato tutte le prove,
    con i voti di ciascuna e la media approssimata per eccesso ("non superato" sotto il
    voto minimo dell'esame, "orale" per chi lo deve sostenere)."""
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    rows = _righe_risultati(tag, appello_id, votomin)
    raggruppamento = corsi_service.get_raggruppamento_by_appello(tag, appello_id)
    if raggruppamento is None:
        return [
            {"matricola": r["matricola"], "nome": r["nome"], "cognome": r["cognome"], "voti_prove": [],
             "etichetta": _etichetta_esito(r, votomin, corso.annullato_come)}
            for r in rows
        ]
    combinati = {r["matricola"]: r for r in rows}
    anagrafica = _anagrafica(tag)
    righe = []
    for matricola, dati in voti_prove_raggruppamento(tag, raggruppamento).items():
        if not dati["superate"]:
            continue
        r = combinati.get(matricola)
        if r is not None and r["richiede_orale"] and not r["orale_svolto"]:
            etichetta = "orale"
        else:
            etichetta = celavoto(media_per_eccesso(dati["voti"], votomin), votomin)
        nome, cognome = anagrafica.get(matricola, ("", ""))
        righe.append({"matricola": matricola, "nome": nome, "cognome": cognome,
                      "voti_prove": [str(v) for v in dati["voti"]], "etichetta": etichetta})
    righe.sort(key=lambda x: (x["cognome"].lower(), x["nome"].lower()))
    return righe


def _anagrafica(tag: str) -> dict[str, tuple[str, str]]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        return {r["matricola"]: (r["nome"], r["cognome"]) for r in conn.execute("SELECT matricola, nome, cognome FROM studenti")}
    finally:
        conn.close()


def stampa_risultati(tag: str, appello_id: int) -> str:
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    # celamatr() è applicata da crea_tex_risultati stessa: qui la matricola resta in chiaro.
    righe = _righe_pubblicabili(tag, appello_id)
    return crea_tex_risultati(ctx_for(tag, corso, appello), righe, nomi_prove(tag, appello_id))


def lista_risultati_html(tag: str, appello_id: int) -> list[dict]:
    """Stessi dati di stampa_risultati (matricola oscurata, stessa etichetta d'esito, voti
    delle prove per un raggruppamento), per l'anteprima HTML e il Markdown."""
    return [{**r, "matricola": celamatr(r["matricola"])} for r in _righe_pubblicabili(tag, appello_id)]


@dataclass
class RaggruppamentoResult:
    n_calcolati: int
    tex: str


def calcola_raggruppamento(tag: str, raggruppamento_id: int) -> RaggruppamentoResult:
    """Calcola, per ogni studente che ha superato la soglia di *ciascuna* prova membro
    del raggruppamento, la media dei voti come voto combinato. Il voto minimo di ogni
    prova membro può essere diverso da quello standard del corso (tipicamente più basso
    per le prove parziali); il voto combinato risultante viene poi confrontato, in stampa
    e in fase di verbalizzazione, con il voto minimo standard del corso (o quello
    dell'appello 'virtuale' del raggruppamento, se impostato esplicitamente)."""
    corso = corsi_service.get_corso(tag)
    raggruppamento = corsi_service.get_raggruppamento(tag, raggruppamento_id)
    if raggruppamento is None:
        raise ValueError("Raggruppamento non trovato")
    membri = raggruppamento.membri
    votomin_esame = corsi_service.effective_votomin(corso, corsi_service.get_appello(tag, raggruppamento.appello_id))

    conn = db.get_connection(config.corso_db_path(tag))
    n = 0
    try:
        passanti_per_membro = []
        voti_per_matricola: dict[str, list[int]] = defaultdict(list)
        for membro in membri:
            soglia = corsi_service.effective_votomin(corso, membro)
            rows = conn.execute(
                "SELECT matricola, voto FROM risultati WHERE appello_id=? AND voto >= ?",
                (membro.id, soglia),
            ).fetchall()
            matricole = set()
            for r in rows:
                matricole.add(r["matricola"])
                voti_per_matricola[r["matricola"]].append(r["voto"])
            passanti_per_membro.append(matricole)

        passanti = set.intersection(*passanti_per_membro) if passanti_per_membro else set()

        for matricola in passanti:
            voto = media_per_eccesso(voti_per_matricola[matricola][-len(membri):], votomin_esame)
            # Se lo studente ha già completato l'orale (unico per il raggruppamento, può
            # essere stato richiesto su una qualunque prova membro), il suo voto finale non
            # va sovrascritto da un ricalcolo della media: voto_scritto tiene comunque
            # traccia della media appena calcolata come riferimento.
            conn.execute(
                "INSERT INTO risultati (matricola, appello_id, voto, voto_scritto) VALUES (?,?,?,?) "
                "ON CONFLICT(matricola, appello_id) DO UPDATE SET "
                "voto=CASE WHEN risultati.orale_svolto=1 THEN risultati.voto ELSE excluded.voto END, "
                "voto_scritto=excluded.voto_scritto",
                (matricola, raggruppamento.appello_id, voto, voto),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()

    tex = stampa_risultati(tag, raggruppamento.appello_id)
    return RaggruppamentoResult(n_calcolati=n, tex=tex)
