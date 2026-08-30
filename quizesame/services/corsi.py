import re
from dataclasses import dataclass, field
from typing import Optional

from quizesame import config, db

TAG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")

DEFAULT_VOTOMIN = 18
DEFAULT_VOTOMIN_RAGGRUPPAMENTO = 15
DEFAULT_CONSEGNA = 0
DEFAULT_RISPOSTA_CORRETTA = 3
DEFAULT_RISPOSTA_SBAGLIATA = -1
DEFAULT_RISPOSTA_VUOTA = 0
DEFAULT_PUNTEGGIO_MAX_APERTA = 3

# frasi del testo d'esame, personalizzabili dalla scheda Impostazioni del corso;
# {consegna}/{corretta}/{sbagliata}/{vuota}/{votomin} vengono sostituiti con i valori
# effettivi dell'appello al momento della generazione dei compiti.
DEFAULT_FRASE_CONSEGNA = "Consegnate lo svolgimento dei {consegna} esercizi obbligatori.\n"
DEFAULT_FRASE_REGOLE = (
    "Ogni risposta corretta viene valutata $+{corretta}$, "
    "ogni risposta errata ${sbagliata}$, ed ogni risposta non data ${vuota}$. "
    "La prova scritta \\`e superata se si raggiunge una votazione "
    "uguale o superiore a ${votomin}$.\n"
)


def _slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = _SLUG_INVALID_RE.sub("-", text).strip("-")
    return text or "appello"


@dataclass
class Corso:
    tag: str
    nome: str = ""
    facolta: str = ""
    universita: str = ""
    anno: str = ""
    docente: str = ""
    votomin: int = DEFAULT_VOTOMIN
    votomin_raggruppamento: int = DEFAULT_VOTOMIN_RAGGRUPPAMENTO
    consegna: int = DEFAULT_CONSEGNA
    risposta_corretta: int = DEFAULT_RISPOSTA_CORRETTA
    risposta_sbagliata: int = DEFAULT_RISPOSTA_SBAGLIATA
    risposta_vuota: int = DEFAULT_RISPOSTA_VUOTA
    punteggio_max_aperta: int = DEFAULT_PUNTEGGIO_MAX_APERTA
    frase_consegna: str = DEFAULT_FRASE_CONSEGNA
    frase_regole: str = DEFAULT_FRASE_REGOLE
    orale_dopo_richiesta: bool = True
    orale_soglia_attiva: bool = False
    orale_soglia_n: Optional[int] = None
    orale_soglia_voto: Optional[int] = None
    ritirato_conta_insufficiente: bool = False
    domande_esame: str = ""


@dataclass
class Appello:
    id: int
    slug: str
    nome: str
    tipo: str
    data: Optional[str]
    attivo: bool
    membro_raggruppamento: bool = False
    orale_data: Optional[str] = None
    orale_ora: Optional[str] = None
    orale_aula: Optional[str] = None


@dataclass
class Raggruppamento:
    id: int
    appello_id: int
    nome: str
    membri: list = field(default_factory=list)  # list[Appello]


def effective_votomin(corso: Corso, appello: Appello) -> int:
    """Le prove che fanno parte di un raggruppamento (es. prove parziali) usano la
    soglia dedicata del corso; tutti gli altri appelli usano quella standard."""
    return corso.votomin_raggruppamento if appello.membro_raggruppamento else corso.votomin


def effective_consegna(corso: Corso, appello: Appello) -> int:
    return corso.consegna


def list_corsi() -> list[Corso]:
    corsi = []
    if not config.DATA_ROOT.exists():
        return corsi
    for entry in sorted(config.DATA_ROOT.iterdir()):
        if entry.is_dir() and (entry / "db.sqlite").exists():
            corsi.append(get_corso(entry.name))
    return sorted(corsi, key=lambda c: c.anno, reverse=True)


def corsi_simili(tag: str) -> list[Corso]:
    """Altri corsi con stesso nome e stessa facoltà: tipicamente lo stesso corso ripetuto
    in un anno diverso, quindi il candidato più probabile da cui importare esercizi."""
    corrente = get_corso(tag)
    return [
        c for c in list_corsi()
        if c.tag != tag
        and c.nome.strip().lower() == corrente.nome.strip().lower()
        and c.facolta.strip().lower() == corrente.facolta.strip().lower()
    ]


def create_corso(
    tag: str, nome: str, facolta: str, universita: str, anno: str, docente: str,
    votomin: int = DEFAULT_VOTOMIN, consegna: int = DEFAULT_CONSEGNA,
    votomin_raggruppamento: int = DEFAULT_VOTOMIN_RAGGRUPPAMENTO,
) -> Corso:
    tag = (tag or "").strip()
    if not TAG_RE.match(tag):
        raise ValueError("Il tag può contenere solo lettere, numeri, trattini e underscore")
    if config.corso_exists(tag):
        raise ValueError(f"Esiste già un corso con tag '{tag}'")
    db.init_db(config.corso_db_path(tag))
    update_meta(
        tag, nome=nome, facolta=facolta, universita=universita, anno=anno, docente=docente,
        votomin=str(votomin), consegna=str(consegna), votomin_raggruppamento=str(votomin_raggruppamento),
    )
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO corsi_laurea (ctype, nome) VALUES (?, ?)", ("na", "Non specificato")
        )
        conn.commit()
    finally:
        conn.close()
    return get_corso(tag)


def get_corso(tag: str) -> Corso:
    meta = get_meta(tag)
    return Corso(
        tag=tag,
        **{k: meta.get(k, "") for k in ("nome", "facolta", "universita", "anno", "docente")},
        votomin=int(meta.get("votomin", DEFAULT_VOTOMIN)),
        votomin_raggruppamento=int(meta.get("votomin_raggruppamento", DEFAULT_VOTOMIN_RAGGRUPPAMENTO)),
        consegna=int(meta.get("consegna", DEFAULT_CONSEGNA)),
        risposta_corretta=int(meta.get("risposta_corretta", DEFAULT_RISPOSTA_CORRETTA)),
        risposta_sbagliata=int(meta.get("risposta_sbagliata", DEFAULT_RISPOSTA_SBAGLIATA)),
        risposta_vuota=int(meta.get("risposta_vuota", DEFAULT_RISPOSTA_VUOTA)),
        punteggio_max_aperta=int(meta.get("punteggio_max_aperta", DEFAULT_PUNTEGGIO_MAX_APERTA)),
        frase_consegna=meta.get("frase_consegna") or DEFAULT_FRASE_CONSEGNA,
        frase_regole=meta.get("frase_regole") or DEFAULT_FRASE_REGOLE,
        orale_dopo_richiesta=meta.get("orale_dopo_richiesta", "1") == "1",
        orale_soglia_attiva=meta.get(
            "orale_soglia_attiva", "1" if meta.get("orale_soglia_n") and meta.get("orale_soglia_voto") else "0"
        ) == "1",
        orale_soglia_n=int(meta["orale_soglia_n"]) if meta.get("orale_soglia_n") else None,
        orale_soglia_voto=int(meta["orale_soglia_voto"]) if meta.get("orale_soglia_voto") else None,
        ritirato_conta_insufficiente=meta.get("ritirato_conta_insufficiente", "0") == "1",
        domande_esame=meta.get("domande_esame", ""),
    )


def get_meta(tag: str) -> dict:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def update_meta(tag: str, **fields) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for key, value in fields.items():
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _row_to_appello(conn, row) -> Appello:
    membro = conn.execute(
        "SELECT 1 FROM raggruppamento_membri WHERE appello_id=?", (row["id"],)
    ).fetchone() is not None
    chiavi = row.keys()
    return Appello(
        id=row["id"], slug=row["slug"], nome=row["nome"], tipo=row["tipo"], data=row["data"],
        attivo=bool(row["attivo"]), membro_raggruppamento=membro,
        orale_data=row["orale_data"] if "orale_data" in chiavi else None,
        orale_ora=row["orale_ora"] if "orale_ora" in chiavi else None,
        orale_aula=row["orale_aula"] if "orale_aula" in chiavi else None,
    )


def list_appelli(tag: str, includi_raggruppamenti: bool = True) -> list[Appello]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        query = "SELECT * FROM appelli"
        if not includi_raggruppamenti:
            query += " WHERE tipo != 'raggruppamento'"
        query += " ORDER BY data IS NULL, data, id"
        rows = conn.execute(query).fetchall()
        return [_row_to_appello(conn, r) for r in rows]
    finally:
        conn.close()


def get_appello(tag: str, appello_id: int) -> Optional[Appello]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute("SELECT * FROM appelli WHERE id=?", (appello_id,)).fetchone()
        return _row_to_appello(conn, row) if row else None
    finally:
        conn.close()


def _unique_appello_slug(conn, base_slug: str) -> str:
    existing = {r["slug"] for r in conn.execute("SELECT slug FROM appelli")}
    if base_slug not in existing:
        return base_slug
    i = 2
    while f"{base_slug}-{i}" in existing:
        i += 1
    return f"{base_slug}-{i}"


def create_appello(
    tag: str, nome: str, tipo: str = "appello", data: Optional[str] = None,
    slug: Optional[str] = None,
) -> Appello:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        slug = _unique_appello_slug(conn, slug if slug else _slugify(nome))
        cur = conn.execute(
            "INSERT INTO appelli (slug, nome, tipo, data) VALUES (?,?,?,?)",
            (slug, nome, tipo, data),
        )
        conn.commit()
        return get_appello(tag, cur.lastrowid)
    finally:
        conn.close()


def elimina_appello(tag: str, appello_id: int) -> None:
    """Elimina un appello e tutto ciò che gli è assegnato (esercizi assegnati, blocchi,
    compiti, eventuale raggruppamento). Rifiuta se ha già risultati registrati: in quel
    caso andrebbero persi voti reali senza modo di recuperarli."""
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        appello = conn.execute("SELECT slug FROM appelli WHERE id=?", (appello_id,)).fetchone()
        if appello is None:
            raise ValueError(f"Appello #{appello_id} non trovato")
        n_risultati = conn.execute(
            "SELECT COUNT(*) c FROM risultati WHERE appello_id=?", (appello_id,)
        ).fetchone()["c"]
        if n_risultati:
            raise ValueError(
                f"Questo appello ha già {n_risultati} risultati registrati: non può essere eliminato."
            )
        compiti_ids = [r["id"] for r in conn.execute("SELECT id FROM compiti WHERE appello_id=?", (appello_id,))]
        for cid in compiti_ids:
            conn.execute("DELETE FROM compito_esercizi WHERE compito_id=?", (cid,))
        conn.execute("DELETE FROM compiti WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM blocchi WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM appello_esercizi WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM raggruppamento_membri WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM raggruppamenti WHERE appello_id=?", (appello_id,))
        conn.execute("DELETE FROM appelli WHERE id=?", (appello_id,))
        slug = appello["slug"]
        conn.commit()
    finally:
        conn.close()

    out_dir = config.corso_dir(tag) / "output"
    if out_dir.exists():
        for p in out_dir.glob(f"*{slug}*"):
            p.unlink(missing_ok=True)


def update_appello(tag: str, appello_id: int, **fields) -> None:
    if not fields:
        return
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE appelli SET {set_clause} WHERE id=?", (*fields.values(), appello_id))
        conn.commit()
    finally:
        conn.close()


def list_corsi_laurea(tag: str) -> list[dict]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute("SELECT ctype, nome FROM corsi_laurea ORDER BY nome").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_corso_laurea(tag: str, ctype: str, nome: str) -> None:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        conn.execute(
            "INSERT INTO corsi_laurea (ctype, nome) VALUES (?, ?) "
            "ON CONFLICT(ctype) DO UPDATE SET nome=excluded.nome",
            (ctype, nome),
        )
        conn.commit()
    finally:
        conn.close()


# --- Raggruppamenti (es. media di due prove parziali) ---------------------------------

def _row_to_raggruppamento(conn, row) -> Raggruppamento:
    membri_rows = conn.execute(
        "SELECT a.* FROM raggruppamento_membri rm JOIN appelli a ON a.id = rm.appello_id "
        "WHERE rm.raggruppamento_id=? ORDER BY a.data IS NULL, a.data, a.id",
        (row["id"],),
    ).fetchall()
    return Raggruppamento(
        id=row["id"], appello_id=row["appello_id"], nome=row["nome"],
        membri=[_row_to_appello(conn, r) for r in membri_rows],
    )


def list_raggruppamenti(tag: str) -> list[Raggruppamento]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        rows = conn.execute("SELECT * FROM raggruppamenti ORDER BY id").fetchall()
        return [_row_to_raggruppamento(conn, r) for r in rows]
    finally:
        conn.close()


def get_raggruppamento(tag: str, raggruppamento_id: int) -> Optional[Raggruppamento]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute("SELECT * FROM raggruppamenti WHERE id=?", (raggruppamento_id,)).fetchone()
        return _row_to_raggruppamento(conn, row) if row else None
    finally:
        conn.close()


def get_raggruppamento_by_appello(tag: str, appello_id: int) -> Optional[Raggruppamento]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        row = conn.execute("SELECT * FROM raggruppamenti WHERE appello_id=?", (appello_id,)).fetchone()
        return _row_to_raggruppamento(conn, row) if row else None
    finally:
        conn.close()


def create_raggruppamento(
    tag: str, nome: str, membro_ids: list[int], appello_id: Optional[int] = None
) -> Raggruppamento:
    """Crea un raggruppamento (es. media di due prove parziali). Se appello_id non è
    indicato, crea automaticamente l'appello 'virtuale' che ospiterà il voto combinato
    (usato dalla migrazione, che invece riusa un appello già esistente)."""
    if len(membro_ids) < 2:
        raise ValueError("Un raggruppamento deve avere almeno due appelli membri")
    if appello_id is None:
        appello = create_appello(tag, nome=nome, tipo="raggruppamento")
        appello_id = appello.id
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cur = conn.execute(
            "INSERT INTO raggruppamenti (appello_id, nome) VALUES (?, ?)", (appello_id, nome)
        )
        raggruppamento_id = cur.lastrowid
        for membro_id in membro_ids:
            conn.execute(
                "INSERT INTO raggruppamento_membri (raggruppamento_id, appello_id) VALUES (?, ?)",
                (raggruppamento_id, membro_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get_raggruppamento(tag, raggruppamento_id)
