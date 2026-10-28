"""Aule per una singola prova (membro di un raggruppamento) e assegnazione degli
studenti ammessi: le aule si definiscono da zero per ogni prova, e l'assegnazione
automatica distribuisce gli ammessi non ancora fissati a mano in ordine di cognome, in
proporzione alla capienza residua di ciascuna aula (aula DSA compresa). Un'aula può essere riservata agli
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


def _ripartizione_proporzionale(capienze: list[int], n: int) -> list[int]:
    """Quanti studenti in ciascuna aula, in proporzione ai posti (tutte le aule piene
    alla stessa percentuale), arrotondando col metodo dei resti più alti perché la somma
    sia esattamente `n`. Con n non oltre i posti totali, nessuna aula supera i suoi."""
    totale = sum(capienze)
    if totale <= 0 or n <= 0:
        return [0] * len(capienze)
    quote = [c / totale * n for c in capienze]
    basi = [int(q) for q in quote]
    ordine_resti = sorted(range(len(quote)), key=lambda i: quote[i] - basi[i], reverse=True)
    for i in ordine_resti[:n - sum(basi)]:
        basi[i] += 1
    return basi


def _distribuisci(conn, appello_id: int, aule: list, capienze: list[int], pool: list[str], dsa: set[str]) -> None:
    """Riempie `aule` (con le rispettive capienze residue) con gli studenti di `pool`
    (in ordine di cognome) in proporzione ai posti di ciascuna, aula DSA compresa: se
    c'è un'aula per i DSA/L104, quelli di `dsa` vanno tutti lì, qualunque sia il cognome,
    e i posti che restano della sua quota li occupano gli altri, come nelle aule normali."""
    n = min(len(pool), sum(capienze))
    quantita = _ripartizione_proporzionale(capienze, n)
    i_dsa = next((i for i, a in enumerate(aule) if a["dsa"]), None)
    da_dsa = [m for m in pool if m in dsa] if i_dsa is not None else []
    if i_dsa is not None:
        da_dsa = da_dsa[:capienze[i_dsa]]
        if quantita[i_dsa] < len(da_dsa):
            # più DSA della quota dell'aula: li prende tutti, le altre si dividono gli
            # altri studenti sempre in proporzione ai posti
            altre = [k for k in range(len(aule)) if k != i_dsa]
            ripartite = _ripartizione_proporzionale([capienze[k] for k in altre], n - len(da_dsa))
            quantita = [0] * len(aule)
            quantita[i_dsa] = len(da_dsa)
            for k, q in zip(altre, ripartite):
                quantita[k] = q

    def assegna(matricola, aula):
        conn.execute(
            "INSERT INTO aula_assegnazioni (matricola, appello_id, aula_id, manuale) VALUES (?,?,?,0) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET aula_id=excluded.aula_id, manuale=0",
            (matricola, appello_id, aula["id"]),
        )

    for m in da_dsa:
        assegna(m, aule[i_dsa])
    gia = set(da_dsa)
    altri = [m for m in pool if m not in gia]
    cursore = 0
    for k, aula in enumerate(aule):
        posti = quantita[k] - (len(da_dsa) if k == i_dsa else 0)
        for m in altri[cursore:cursore + posti]:
            assegna(m, aula)
        cursore += posti


def assegna_automatica(
    tag: str, appello_id: int, matricole_ordinate: list[str], matricole_dsa: Optional[set[str]] = None,
) -> None:
    """Distribuisce su `list_aule(tag, appello_id)` gli studenti di `matricole_ordinate`
    (già filtrati sugli ammessi e ordinati per cognome dal chiamante) che non hanno già
    un'assegnazione manuale: le assegnazioni manuali restano fisse, e la capienza usata per
    il calcolo è quella residua (capienza totale meno le assegnazioni manuali già presenti
    in ciascuna aula). Tutte le aule si riempiono in proporzione ai posti (vedi
    _ripartizione_proporzionale); gli studenti di `matricole_dsa` vanno nell'aula riservata ai
    DSA/L104, se c'è, insieme ad altri studenti nei posti che restano. Chi eccede la
    capienza residua resta senza aula."""
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
        residua = [max(0, aula["capienza"] - conteggio_manuali[aula["id"]]) for aula in aule]
        pool = [m for m in matricole_ordinate if m not in manuali]
        conn.execute("DELETE FROM aula_assegnazioni WHERE appello_id=? AND manuale=0", (appello_id,))
        _distribuisci(conn, appello_id, list(aule), residua, pool, matricole_dsa)
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


def riassunto_assegnazione(titolo: str, ammessi: list[dict], aule: list[Aula]) -> dict:
    """Come sono distribuiti gli studenti nelle aule, da comunicare: {"testo", "avvisi"}.
    Il testo dà per ogni aula l'intervallo di cognomi (iniziali quanto basta per
    distinguere il confine con l'aula successiva, dalla A alla Z agli estremi) e quale
    aula accoglie i DSA/L104, senza numeri né nomi. Gli avvisi (studenti senza aula, o in
    un'aula fuori dall'ordine di cognome) sono per il docente, fuori dal testo."""
    aula_dsa_presente = any(a.dsa for a in aule)
    def in_ordine(s):  # i DSA nell'aula riservata non seguono l'ordine dei cognomi
        return not (aula_dsa_presente and s.get("dsa"))
    gruppi = []
    for aula in aule:
        studenti = sorted(
            (s for s in ammessi if s.get("aula_id") == aula.id and in_ordine(s)),
            key=lambda s: ((s["cognome"] or "").lower(), (s["nome"] or "").lower()),
        )
        gruppi.append((aula, studenti))
    con_cognomi = sorted([g for g in gruppi if g[1]], key=lambda g: (g[1][0]["cognome"] or "").lower())
    intervalli = {}
    for i, (aula, studenti) in enumerate(con_cognomi):
        primo, ultimo = studenti[0]["cognome"] or "", studenti[-1]["cognome"] or ""
        da = "A" if i == 0 else primo[:_prefisso_distintivo(con_cognomi[i - 1][1][-1]["cognome"] or "", primo)]
        a = "Z" if i == len(con_cognomi) - 1 else ultimo[:_prefisso_distintivo(ultimo, con_cognomi[i + 1][1][0]["cognome"] or "")]
        intervalli[aula.id] = f"cognomi da {da.capitalize()} a {a.capitalize()}"
    righe = [titolo, ""] if titolo else []
    for aula, _ in sorted(gruppi, key=lambda g: (g[0].id not in intervalli, list(intervalli).index(g[0].id) if g[0].id in intervalli else 0)):
        parti = [intervalli[aula.id]] if aula.id in intervalli else []
        if aula.dsa:
            parti.append("più gli studenti DSA e L104" if parti else "studenti DSA e L104")
        if parti:
            righe.append(f"Aula {aula.nome}: " + ", ".join(parti))

    avvisi = []
    senza = sum(1 for s in ammessi if not s.get("aula_id"))
    if senza:
        avvisi.append(f"{senza} student{'e' if senza == 1 else 'i'} ancora senza aula")
    fuori_ordine = 0
    for i, (aula, studenti) in enumerate(con_cognomi):
        for j, (_, altri) in enumerate(con_cognomi):
            if i != j:
                lo, hi = (altri[0]["cognome"] or "").lower(), (altri[-1]["cognome"] or "").lower()
                fuori_ordine += sum(1 for s in studenti if lo < (s["cognome"] or "").lower() < hi)
    if fuori_ordine:
        avvisi.append(f"{fuori_ordine} student{'e è' if fuori_ordine == 1 else 'i sono'} in un'aula fuori "
                      "dall'ordine di cognome (assegnati a mano): il riassunto non lo dice")
    return {"testo": "\n".join(righe), "avvisi": avvisi}
