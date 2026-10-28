"""Aule per una singola prova (membro di un raggruppamento) e assegnazione degli
studenti ammessi: le aule si definiscono da zero per ogni prova, e l'assegnazione
automatica distribuisce gli ammessi non ancora fissati a mano in ordine di cognome, lo
stesso numero in ogni aula nei limiti della capienza residua. Un'aula può essere riservata agli
studenti DSA/L104: se ce n'è almeno una, tutti i DSA vanno lì e gli altri nelle restanti."""
from dataclasses import dataclass
from typing import Optional

from quizesame import config, db


@dataclass
class Aula:
    id: int
    appello_id: int
    nome: str
    capienza: int
    dsa: bool = False


def list_aule(tag: str, appello_id: int) -> list[Aula]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT * FROM appello_aule WHERE appello_id=? ORDER BY id", (appello_id,)
        ).fetchall()
        return [
            Aula(id=r["id"], appello_id=r["appello_id"], nome=r["nome"], capienza=r["capienza"], dsa=bool(r["dsa"]))
            for r in rows
        ]
    finally:
        conn.close()


def crea_aula(tag: str, appello_id: int, nome: str, capienza: int, dsa: bool = False) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO appello_aule (appello_id, nome, capienza, dsa) VALUES (?,?,?,?)",
            (appello_id, nome.strip(), capienza, dsa),
        )
        conn.commit()
    finally:
        conn.close()


def imposta_aula_dsa(tag: str, appello_id: int, aula_id: int, dsa: bool) -> None:
    """Riserva (o libera) un'aula per i DSA/L104: l'aula riservata è una sola per prova,
    quindi riservarne una toglie la riserva alle altre."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if dsa:
            conn.execute("UPDATE appello_aule SET dsa=0 WHERE appello_id=?", (appello_id,))
        conn.execute("UPDATE appello_aule SET dsa=? WHERE id=? AND appello_id=?", (dsa, aula_id, appello_id))
        conn.commit()
    finally:
        conn.close()


def modifica_aula(tag: str, appello_id: int, aula_id: int, nome: str, capienza: int) -> None:
    """Nome e posti di un'aula; gli studenti già assegnati restano dove sono."""
    if not nome.strip() or capienza < 1:
        raise ValueError("Servono il nome dell'aula e almeno un posto")
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("UPDATE appello_aule SET nome=?, capienza=? WHERE id=? AND appello_id=?",
                     (nome.strip(), capienza, aula_id, appello_id))
        conn.commit()
    finally:
        conn.close()


def elimina_aula(tag: str, appello_id: int, aula_id: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "UPDATE aula_assegnazioni SET aula_id=NULL, manuale=0 WHERE appello_id=? AND aula_id=?",
            (appello_id, aula_id),
        )
        conn.execute("DELETE FROM appello_aule WHERE id=? AND appello_id=?", (aula_id, appello_id))
        conn.commit()
    finally:
        conn.close()


def assegna_manuale(tag: str, appello_id: int, matricola: str, aula_id: Optional[int]) -> None:
    """Fissa a mano l'aula di uno studente; con `aula_id` None lo toglie dall'aula e lo
    restituisce all'assegnazione automatica."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        if aula_id is None:
            conn.execute("DELETE FROM aula_assegnazioni WHERE matricola=? AND appello_id=?", (matricola, appello_id))
            conn.commit()
            return
        conn.execute(
            "INSERT INTO aula_assegnazioni (matricola, appello_id, aula_id, manuale) VALUES (?,?,?,1) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET aula_id=excluded.aula_id, manuale=1",
            (matricola, appello_id, aula_id),
        )
        conn.commit()
    finally:
        conn.close()


def _ripartizione_uniforme(capienze: list[int], n: int) -> list[int]:
    """Quanti studenti in ciascuna aula: lo stesso numero in tutte (al più uno di
    differenza), senza superare i posti di nessuna; chi non ci sta in un'aula piccola va
    nelle altre. Si parte dalle aule più piccole, che si riempiono per prime."""
    quantita = [0] * len(capienze)
    restanti = n
    ordine = sorted(range(len(capienze)), key=lambda i: capienze[i])
    for k, i in enumerate(ordine):
        aule_rimaste = len(ordine) - k
        quantita[i] = min(capienze[i], -(-restanti // aule_rimaste))
        restanti -= quantita[i]
    return quantita


def _distribuisci(conn, appello_id: int, aule: list, capienze: list[int], pool: list[str]) -> None:
    """Riempie `aule` (con le rispettive capienze residue) con gli studenti di `pool`,
    nell'ordine dato (di cognome): lo stesso numero in ogni aula, nei limiti dei posti."""
    quantita = _ripartizione_uniforme(capienze, min(len(pool), sum(capienze)))
    cursore = 0
    for aula, quantita_aula in zip(aule, quantita):
        for matricola in pool[cursore:cursore + quantita_aula]:
            conn.execute(
                "INSERT INTO aula_assegnazioni (matricola, appello_id, aula_id, manuale) VALUES (?,?,?,0) "
                "ON CONFLICT(matricola, appello_id) DO UPDATE SET aula_id=excluded.aula_id, manuale=0",
                (matricola, appello_id, aula["id"]),
            )
        cursore += quantita_aula


def assegna_automatica(
    tag: str, appello_id: int, matricole_ordinate: list[str], matricole_dsa: Optional[set[str]] = None,
) -> None:
    """Distribuisce su `list_aule(tag, appello_id)` gli studenti di `matricole_ordinate`
    (già filtrati sugli ammessi e ordinati per cognome dal chiamante) che non hanno già
    un'assegnazione manuale: le assegnazioni manuali restano fisse, e la capienza usata per
    il calcolo è quella residua (capienza totale meno le assegnazioni manuali già presenti
    in ciascuna aula). Le aule si riempiono in modo uniforme (vedi _ripartizione_uniforme). Se almeno un'aula è riservata ai DSA/L104, gli studenti di
    `matricole_dsa` vanno tutti nelle aule riservate e gli altri nelle restanti; senza aule
    riservate si distribuiscono tutti insieme come prima. Chi eccede la capienza residua
    resta senza aula (anche se in precedenza ne aveva una assegnata automaticamente)."""
    matricole_dsa = matricole_dsa or set()
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        aule = conn.execute(
            "SELECT * FROM appello_aule WHERE appello_id=? ORDER BY id", (appello_id,)
        ).fetchall()
        if not aule:
            return
        manuali = {
            r["matricola"]: r["aula_id"] for r in conn.execute(
                "SELECT matricola, aula_id FROM aula_assegnazioni WHERE appello_id=? AND manuale=1", (appello_id,)
            ).fetchall()
        }
        conteggio_manuali = {aula["id"]: 0 for aula in aule}
        for aula_id in manuali.values():
            if aula_id in conteggio_manuali:
                conteggio_manuali[aula_id] += 1
        residua = {aula["id"]: max(0, aula["capienza"] - conteggio_manuali[aula["id"]]) for aula in aule}
        pool = [m for m in matricole_ordinate if m not in manuali]
        conn.execute("DELETE FROM aula_assegnazioni WHERE appello_id=? AND manuale=0", (appello_id,))

        aule_dsa = [a for a in aule if a["dsa"]]
        if aule_dsa:
            gruppi = [
                (aule_dsa, [m for m in pool if m in matricole_dsa]),
                ([a for a in aule if not a["dsa"]], [m for m in pool if m not in matricole_dsa]),
            ]
        else:
            gruppi = [(list(aule), pool)]
        for aule_gruppo, pool_gruppo in gruppi:
            _distribuisci(conn, appello_id, aule_gruppo, [residua[a["id"]] for a in aule_gruppo], pool_gruppo)
        conn.commit()
    finally:
        conn.close()


def _prefisso_distintivo(a: str, b: str) -> int:
    """Quante lettere servono per distinguere due cognomi consecutivi (es. Gallo/Gamba: 3)."""
    a, b = a.lower(), b.lower()
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n + 1


def riassunto_assegnazione(titolo: str, ammessi: list[dict], aule: list[Aula]) -> str:
    """Testo da comunicare agli studenti su come sono distribuiti nelle aule: per ogni
    aula l'intervallo di cognomi (iniziali quanto basta per distinguere il confine con
    l'aula successiva, dalla A alla Z agli estremi), e l'aula riservata ai DSA/L104 (senza
    nomi). Segnala gli studenti fuori dall'ordine di cognome (spostati a mano)."""
    righe = [titolo, ""] if titolo else []
    normali = []
    for aula in aule:
        if aula.dsa:
            continue
        studenti = sorted(
            (s for s in ammessi if s.get("aula_id") == aula.id and not (s.get("dsa") and any(a.dsa for a in aule))),
            key=lambda s: ((s["cognome"] or "").lower(), (s["nome"] or "").lower()),
        )
        if studenti:
            normali.append((aula, studenti))
    normali.sort(key=lambda x: (x[1][0]["cognome"] or "").lower())
    for i, (aula, studenti) in enumerate(normali):
        primo, ultimo = studenti[0]["cognome"] or "", studenti[-1]["cognome"] or ""
        if i == 0:
            da = "A"
        else:
            precedente = normali[i - 1][1][-1]["cognome"] or ""
            da = primo[:_prefisso_distintivo(precedente, primo)]
        if i == len(normali) - 1:
            a = "Z"
        else:
            successivo = normali[i + 1][1][0]["cognome"] or ""
            a = ultimo[:_prefisso_distintivo(ultimo, successivo)]
        righe.append(f"Aula {aula.nome}: cognomi da {da.capitalize()} a {a.capitalize()}")
    # chi è in un'aula ma con un cognome dentro l'intervallo di un'altra (spostato a mano)
    fuori_ordine = 0
    for i, (aula, studenti) in enumerate(normali):
        for j, (altra, altri) in enumerate(normali):
            if i == j:
                continue
            lo, hi = altri[0]["cognome"].lower(), altri[-1]["cognome"].lower()
            fuori_ordine += sum(1 for s in studenti if lo < (s["cognome"] or "").lower() < hi)
    for aula in aule:
        if aula.dsa:
            n = sum(1 for s in ammessi if s.get("aula_id") == aula.id)
            righe.append(f"Aula {aula.nome}: studenti DSA e L104" + (f" ({n})" if n else ""))
    senza = sum(1 for s in ammessi if not s.get("aula_id"))
    if senza:
        righe.append(f"Ancora senza aula: {senza} student{'e' if senza == 1 else 'i'}")
    if fuori_ordine:
        righe.append(f"Attenzione: {fuori_ordine} student{'e è' if fuori_ordine == 1 else 'i sono'} in un'aula "
                     "fuori dall'ordine di cognome (assegnati a mano): controlla prima di comunicarlo.")
    return "\n".join(righe)
