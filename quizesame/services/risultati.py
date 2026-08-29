from collections import defaultdict
from dataclasses import dataclass
from math import ceil

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services.latex import LatexContext, crea_tex_risultati, celavoto


def _ctx_for(tag: str, corso, appello) -> LatexContext:
    return LatexContext(
        corso=corso.nome, facolta=corso.facolta, universita=corso.universita, anno=corso.anno, tag=tag,
        appello_nome=appello.nome, appello_data=appello.data or "",
        consegna=corsi_service.effective_consegna(corso, appello), votomin=corsi_service.effective_votomin(corso, appello),
        risposta_corretta=corso.risposta_corretta, risposta_sbagliata=corso.risposta_sbagliata,
        risposta_vuota=corso.risposta_vuota, frase_consegna=corso.frase_consegna, frase_regole=corso.frase_regole,
        orale_data=appello.orale_data or "", orale_ora=appello.orale_ora or "", orale_aula=appello.orale_aula or "",
    )


def _etichetta_esito(r, votomin: int) -> str:
    if r["esito"] == "assente":
        return "assente"
    if r["esito"] == "ritirato":
        return "ritirato"
    if r["richiede_orale"] and not r["orale_svolto"]:
        return "orale"
    if r["esito_orale"] == "assente":
        return "assente (orale)"
    if r["esito_orale"] == "insufficiente":
        return "insufficiente (orale)"
    if r["voto"] is None:
        return "-"
    return celavoto(int(r["voto"]), votomin)


def stampa_risultati(tag: str, appello_id: int) -> str:
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT r.*, s.nome, s.cognome FROM risultati r "
            "JOIN studenti s ON s.matricola = r.matricola "
            "WHERE r.appello_id=? ORDER BY r.matricola",
            (appello_id,),
        ).fetchall()
    finally:
        conn.close()
    lista = [(r["matricola"], r["nome"], r["cognome"], _etichetta_esito(r, votomin)) for r in rows]
    return crea_tex_risultati(_ctx_for(tag, corso, appello), lista)


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
            voti = voti_per_matricola[matricola][-len(membri):]
            media = sum(voti) / len(voti)
            voto = ceil(media) if media >= 18 else int(media)
            conn.execute(
                "INSERT INTO risultati (matricola, appello_id, voto) VALUES (?,?,?) "
                "ON CONFLICT(matricola, appello_id) DO UPDATE SET voto=excluded.voto",
                (matricola, raggruppamento.appello_id, voto),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()

    tex = stampa_risultati(tag, raggruppamento.appello_id)
    return RaggruppamentoResult(n_calcolati=n, tex=tex)
