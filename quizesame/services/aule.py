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
    quota: Optional[int] = None  # % degli studenti scelta a mano; None = in proporzione ai posti


def list_aule(tag: str, appello_id: int) -> list[Aula]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT * FROM appello_aule WHERE appello_id=? ORDER BY id", (appello_id,)
        ).fetchall()
        return [
            Aula(id=r["id"], appello_id=r["appello_id"], nome=r["nome"], capienza=r["capienza"], dsa=bool(r["dsa"]),
                 quota=r["quota"] if "quota" in r.keys() else None)
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


def modifica_aula(tag: str, appello_id: int, aula_id: int, nome: str, capienza: int, quota: Optional[int] = None) -> None:
    """Nome, posti e quota (% degli studenti, None = in proporzione ai posti) di un'aula;
    gli studenti già assegnati restano dove sono."""
    if not nome.strip() or capienza < 1:
        raise ValueError("Servono il nome dell'aula e almeno un posto")
    if quota is not None and not 0 <= quota <= 100:
        raise ValueError("La quota è una percentuale, da 0 a 100")
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("UPDATE appello_aule SET nome=?, capienza=?, quota=? WHERE id=? AND appello_id=?",
                     (nome.strip(), capienza, quota, aula_id, appello_id))
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


def togli_assegnazioni(tag: str, appello_id: int) -> None:
    """Toglie l'aula a tutti gli studenti della prova (anche a quelli spostati a mano)."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute("DELETE FROM aula_assegnazioni WHERE appello_id=?", (appello_id,))
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


def pesi_aule(capienze: list[int], quote: list[Optional[int]]) -> list[float]:
    """Percentuale di studenti per ciascuna aula: quella scelta a mano (quota), e per le
    altre quanto resta, diviso in proporzione ai posti."""
    fissate = sum(q for q in quote if q is not None)
    libere = [c if q is None else 0 for c, q in zip(capienze, quote)]
    resto = max(0.0, 100.0 - fissate)
    return [float(q) if q is not None else (resto * c / sum(libere) if sum(libere) else 0.0)
            for c, q in zip(libere, quote)]


def _ripartizione_proporzionale(capienze: list[int], n: int, quote: Optional[list] = None) -> list[int]:
    """Quanti studenti in ciascuna aula: in proporzione ai posti (tutte piene alla stessa
    percentuale), o alle quote scelte a mano dove ci sono (vedi pesi_aule), arrotondando
    col metodo dei resti più alti perché la somma sia esattamente `n`. Un'aula non supera
    mai i suoi posti: chi non ci sta va nelle altre, sempre secondo i pesi."""
    pesi = pesi_aule(capienze, quote or [None] * len(capienze))
    quantita = [0] * len(capienze)
    restanti = min(n, sum(capienze))
    aperte = [i for i in range(len(capienze)) if capienze[i] > 0]
    while restanti > 0 and aperte:
        totale = sum(pesi[i] for i in aperte) or float(len(aperte))
        quote_i = {i: (pesi[i] if totale else 1.0) / totale * restanti for i in aperte}
        basi = {i: int(q) for i, q in quote_i.items()}
        avanzo = restanti - sum(basi.values())
        for i in sorted(aperte, key=lambda i: quote_i[i] - basi[i], reverse=True)[:avanzo]:
            basi[i] += 1
        piene = []
        for i in aperte:
            dato = min(basi[i], capienze[i] - quantita[i])
            quantita[i] += dato
            restanti -= dato
            if quantita[i] >= capienze[i]:
                piene.append(i)
        if not piene:
            break
        aperte = [i for i in aperte if i not in piene]
        if all(pesi[i] == 0 for i in aperte):
            # le aule rimaste avevano quota 0: chi non ci sta altrove va lì, nei posti liberi
            pesi = [float(c) for c in capienze]
    return quantita


def _distribuisci(conn, appello_id: int, aule: list, capienze: list[int], pool: list[str], dsa: set[str],
                  quote: Optional[list] = None) -> None:
    """Riempie `aule` (con le rispettive capienze residue) con gli studenti di `pool`
    (in ordine di cognome) in proporzione ai posti di ciascuna, aula DSA compresa: se
    c'è un'aula per i DSA/L104, quelli di `dsa` vanno tutti lì, qualunque sia il cognome,
    e i posti che restano della sua quota li occupano gli altri, come nelle aule normali."""
    n = min(len(pool), sum(capienze))
    quote = quote or [None] * len(aule)
    quantita = _ripartizione_proporzionale(capienze, n, quote)
    i_dsa = next((i for i, a in enumerate(aule) if a["dsa"]), None)
    da_dsa = [m for m in pool if m in dsa] if i_dsa is not None else []
    if i_dsa is not None:
        da_dsa = da_dsa[:capienze[i_dsa]]
        if quantita[i_dsa] < len(da_dsa):
            # più DSA della quota dell'aula: li prende tutti, le altre si dividono gli
            # altri studenti sempre in proporzione ai posti
            altre = [k for k in range(len(aule)) if k != i_dsa]
            ripartite = _ripartizione_proporzionale([capienze[k] for k in altre], n - len(da_dsa), [quote[k] for k in altre])
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
        quote = [a["quota"] if "quota" in a.keys() else None for a in aule]
        _distribuisci(conn, appello_id, list(aule), residua, pool, matricole_dsa, quote)
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


def aula_per_cognome(cognome: str, ammessi: list[dict], aule: list[Aula], dsa: bool = False) -> Optional[int]:
    """L'aula in cui mettere uno studente aggiunto dopo l'assegnazione, senza toccare gli
    altri: quella dei DSA/L104 se lo è, altrimenti quella dell'intervallo di cognomi in cui
    cade (fra due intervalli, la prima dopo); None se non si può dedurre."""
    if dsa:
        aula_dsa = next((a for a in aule if a.dsa), None)
        if aula_dsa:
            return aula_dsa.id
    aula_dsa_presente = any(a.dsa for a in aule)
    intervalli = []
    for aula in aule:
        cognomi = sorted((s["cognome"] or "").lower() for s in ammessi
                         if s.get("aula_id") == aula.id and not (aula_dsa_presente and s.get("dsa")))
        if cognomi:
            intervalli.append((cognomi[0], cognomi[-1], aula.id))
    if not intervalli:
        return None
    intervalli.sort()
    c = (cognome or "").lower()
    for primo, ultimo, aula_id in intervalli:
        if c <= ultimo:
            return aula_id
    return intervalli[-1][2]
