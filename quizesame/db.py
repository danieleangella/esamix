import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS corsi_laurea (
    ctype TEXT PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS studenti (
    matricola TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    cognome TEXT NOT NULL,
    laurea_ctype TEXT REFERENCES corsi_laurea(ctype)
);

CREATE TABLE IF NOT EXISTS appelli (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'appello',
    data TEXT,
    attivo BOOLEAN NOT NULL DEFAULT 1,
    orale_data TEXT,
    orale_ora TEXT,
    orale_aula TEXT
);

-- Una riga significa "questo studente deve sempre fare l'orale, in tutti gli appelli
-- successivi": creata la prima volta che gli viene richiesto l'orale in un compito, e
-- controllata a ogni correzione successiva (anche se lo studente arriva da un altro
-- corso/anno tramite import). 'origine' è testo libero (non una FK cross-database) dato
-- che ogni corso è un file SQLite indipendente.
CREATE TABLE IF NOT EXISTS orale_obbligatorio (
    matricola TEXT PRIMARY KEY REFERENCES studenti(matricola),
    motivazione TEXT,
    origine TEXT,
    data TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raggruppamenti (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appello_id INTEGER NOT NULL UNIQUE REFERENCES appelli(id),
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raggruppamento_membri (
    raggruppamento_id INTEGER NOT NULL REFERENCES raggruppamenti(id),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    PRIMARY KEY (raggruppamento_id, appello_id)
);

CREATE TABLE IF NOT EXISTS esercizi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT,
    argomento TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS esercizio_varianti (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    esercizio_id INTEGER NOT NULL REFERENCES esercizi(id),
    testo TEXT NOT NULL,
    risposte_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appello_esercizi (
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    esercizio_id INTEGER NOT NULL REFERENCES esercizi(id),
    ordine INTEGER,
    obbligatorio BOOLEAN NOT NULL DEFAULT 0,
    data_assegnazione TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (appello_id, esercizio_id)
);

-- Ogni chiamata a "genera blocco" crea una riga qui: un gruppo di compiti stampati
-- insieme, con un proprio file PDF del blocco e della griglia soluzioni. Il file di
-- riferimento (testo + risposte corrette) resta invece unico per appello e comune a
-- tutti i blocchi.
CREATE TABLE IF NOT EXISTS blocchi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    numero INTEGER NOT NULL,
    numero_studenti INTEGER NOT NULL,
    creato_il TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(appello_id, numero)
);

CREATE TABLE IF NOT EXISTS compiti (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    blocco_id INTEGER REFERENCES blocchi(id),
    codice TEXT NOT NULL,
    soluzioni TEXT NOT NULL,
    UNIQUE(appello_id, codice)
);

-- Per ogni compito, quale esercizio (e quale variante) è finito in ciascuna posizione
-- della griglia di risposte: la mischia degli esercizi è diversa per ogni compito, quindi
-- serve per sapere, in fase di correzione, a quale esercizio corrisponde ogni lettera
-- della stringa di risposte (e se quell'esercizio era 'obbligatorio' per quel compito).
CREATE TABLE IF NOT EXISTS compito_esercizi (
    compito_id INTEGER NOT NULL REFERENCES compiti(id),
    posizione INTEGER NOT NULL,
    esercizio_id INTEGER NOT NULL REFERENCES esercizi(id),
    variante_id INTEGER REFERENCES esercizio_varianti(id),
    obbligatorio BOOLEAN NOT NULL DEFAULT 0,
    risposte_mischiate TEXT,
    PRIMARY KEY (compito_id, posizione)
);

CREATE TABLE IF NOT EXISTS risultati (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    matricola TEXT NOT NULL REFERENCES studenti(matricola),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    compito_id INTEGER REFERENCES compiti(id),
    risposte TEXT,
    voto INTEGER,
    voto_scritto INTEGER,
    esito TEXT NOT NULL DEFAULT 'voto',
    insufficiente_per_obbligatorio BOOLEAN NOT NULL DEFAULT 0,
    svolgimenti_rifiutati TEXT,
    punteggi_obbligatori TEXT,
    richiede_orale BOOLEAN NOT NULL DEFAULT 0,
    orale_svolto BOOLEAN NOT NULL DEFAULT 0,
    orale_motivazione TEXT,
    esito_orale TEXT,
    verbalizzato BOOLEAN NOT NULL DEFAULT 0,
    data_verbalizzazione TEXT,
    data_inserimento TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(matricola, appello_id)
);
"""


# Colonne aggiunte a tabelle già esistenti nel corso dello sviluppo: un corso creato con
# uno schema più vecchio va "aggiornato" aggiungendo solo quelle mancanti (le nuove
# tabelle, invece, le crea già lo script sopra grazie a CREATE TABLE IF NOT EXISTS).
ADDITIVE_COLUMNS = {
    "appello_esercizi": [
        ("obbligatorio", "BOOLEAN NOT NULL DEFAULT 0"),
        ("data_assegnazione", "TEXT DEFAULT CURRENT_TIMESTAMP"),
    ],
    "risultati": [
        ("insufficiente_per_obbligatorio", "BOOLEAN NOT NULL DEFAULT 0"),
        ("svolgimenti_rifiutati", "TEXT"),
        ("punteggi_obbligatori", "TEXT"),
        ("richiede_orale", "BOOLEAN NOT NULL DEFAULT 0"),
        ("orale_svolto", "BOOLEAN NOT NULL DEFAULT 0"),
        ("orale_motivazione", "TEXT"),
        ("voto_scritto", "INTEGER"),
        ("esito", "TEXT NOT NULL DEFAULT 'voto'"),
        ("esito_orale", "TEXT"),
    ],
    "esercizi": [
        ("argomento", "TEXT"),
        ("difficolta", "INTEGER"),
        ("soluzione", "TEXT"),
        ("aperta", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "compiti": [
        ("blocco_id", "INTEGER REFERENCES blocchi(id)"),
    ],
    "compito_esercizi": [
        ("risposte_mischiate", "TEXT"),
        ("aperta", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "appelli": [
        ("orale_data", "TEXT"),
        ("orale_ora", "TEXT"),
        ("orale_aula", "TEXT"),
    ],
}


def _ensure_schema_current(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)  # crea le tabelle mancanti (idempotente)
    tabelle_esistenti = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for tabella, colonne in ADDITIVE_COLUMNS.items():
        if tabella not in tabelle_esistenti:
            continue  # tabella nuova, creata già completa dallo script sopra
        colonne_presenti = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabella})")}
        for nome, dichiarazione in colonne:
            if nome not in colonne_presenti:
                conn.execute(f"ALTER TABLE {tabella} ADD COLUMN {nome} {dichiarazione}")
    conn.commit()


_schema_verificati: set[str] = set()


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # ogni servizio apre/chiude una propria connessione per ogni chiamata (una pagina può
    # arrivare a farne decine): rieseguire lo script di creazione tabelle e i PRAGMA
    # table_info di _ensure_schema_current su ognuna di esse è un lavoro ripetuto inutile,
    # dato che lo schema non cambia sotto i piedi del processo se non passando da qui.
    # Va verificato una sola volta per file di database per ogni esecuzione del programma.
    chiave = str(db_path.resolve())
    if chiave not in _schema_verificati:
        _ensure_schema_current(conn)
        _schema_verificati.add(chiave)
    return conn


def init_db(db_path: Path) -> None:
    get_connection(db_path).close()
