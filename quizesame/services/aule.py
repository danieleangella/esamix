"""Aule per una singola prova (membro di un raggruppamento) e assegnazione degli
studenti ammessi: le aule si definiscono da zero per ogni prova, e l'assegnazione
automatica distribuisce gli ammessi non ancora fissati a mano proporzionalmente alla
capienza residua di ciascuna aula, in ordine di cognome."""
from dataclasses import dataclass

from quizesame import config, db


@dataclass
class Aula:
    id: int
    appello_id: int
    nome: str
    capienza: int


def list_aule(tag: str, appello_id: int) -> list[Aula]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute(
            "SELECT * FROM appello_aule WHERE appello_id=? ORDER BY id", (appello_id,)
        ).fetchall()
        return [Aula(id=r["id"], appello_id=r["appello_id"], nome=r["nome"], capienza=r["capienza"]) for r in rows]
    finally:
        conn.close()


def crea_aula(tag: str, appello_id: int, nome: str, capienza: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO appello_aule (appello_id, nome, capienza) VALUES (?,?,?)",
            (appello_id, nome.strip(), capienza),
        )
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


def assegna_manuale(tag: str, appello_id: int, matricola: str, aula_id: int) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO aula_assegnazioni (matricola, appello_id, aula_id, manuale) VALUES (?,?,?,1) "
            "ON CONFLICT(matricola, appello_id) DO UPDATE SET aula_id=excluded.aula_id, manuale=1",
            (matricola, appello_id, aula_id),
        )
        conn.commit()
    finally:
        conn.close()


def _ripartizione_resti_piu_alti(quote: list[float], n: int) -> list[int]:
    """Arrotonda `quote` (una per aula, proporzionale alla capienza residua) a un elenco
    di interi che sommano esattamente a `n`, col metodo dei resti più alti: si parte dalla
    parte intera di ciascuna quota, poi si distribuisce l'eventuale differenza (n meno la
    somma delle parti intere) alle quote coi resti frazionari più alti."""
    basi = [int(q) for q in quote]
    resto = n - sum(basi)
    ordine_resti = sorted(range(len(quote)), key=lambda i: quote[i] - basi[i], reverse=True)
    for i in ordine_resti[:resto]:
        basi[i] += 1
    return basi


def assegna_automatica(tag: str, appello_id: int, matricole_ordinate: list[str]) -> None:
    """Distribuisce su `list_aule(tag, appello_id)` gli studenti di `matricole_ordinate`
    (già filtrati sugli ammessi e ordinati per cognome dal chiamante) che non hanno già
    un'assegnazione manuale: le assegnazioni manuali restano fisse, e la capienza usata per
    il calcolo proporzionale è quella residua (capienza totale meno i pin manuali già
    presenti in ciascuna aula). Chi eccede la capienza residua totale resta senza aula."""
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

        capienza_residua = [max(0, aula["capienza"] - conteggio_manuali[aula["id"]]) for aula in aule]
        pool = [m for m in matricole_ordinate if m not in manuali]

        totale_residuo = sum(capienza_residua)
        n = min(len(pool), totale_residuo)
        if totale_residuo > 0 and n > 0:
            quote = [c / totale_residuo * n for c in capienza_residua]
            quantita = _ripartizione_resti_piu_alti(quote, n)
        else:
            quantita = [0 for _ in aule]

        cursore = 0
        for aula, quantita_aula in zip(aule, quantita):
            for matricola in pool[cursore:cursore + quantita_aula]:
                conn.execute(
                    "INSERT INTO aula_assegnazioni (matricola, appello_id, aula_id, manuale) VALUES (?,?,?,0) "
                    "ON CONFLICT(matricola, appello_id) DO UPDATE SET aula_id=excluded.aula_id, manuale=0",
                    (matricola, appello_id, aula["id"]),
                )
            cursore += quantita_aula
        conn.commit()
    finally:
        conn.close()
