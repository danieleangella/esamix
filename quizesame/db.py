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
    laurea_ctype TEXT REFERENCES corsi_laurea(ctype),
    dsa BOOLEAN NOT NULL DEFAULT 0
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
    nome TEXT NOT NULL,
    matricola_minima_prima_prova TEXT
);

CREATE TABLE IF NOT EXISTS raggruppamento_membri (
    raggruppamento_id INTEGER NOT NULL REFERENCES raggruppamenti(id),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    PRIMARY KEY (raggruppamento_id, appello_id)
);

-- Aule usate per una singola prova (membro di un raggruppamento): definite da zero per
-- ogni prova, dato che le aule assegnate cambiano da una sessione d'esame all'altra.
CREATE TABLE IF NOT EXISTS appello_aule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    nome TEXT NOT NULL,
    capienza INTEGER NOT NULL
);

-- Assegnazione di uno studente ammesso a un'aula per una data prova: 'manuale' distingue
-- un'assegnazione scelta a mano dal docente (che l'assegnazione automatica non deve mai
-- sovrascrivere) da una calcolata proporzionalmente alla capienza residua.
CREATE TABLE IF NOT EXISTS aula_assegnazioni (
    matricola TEXT NOT NULL REFERENCES studenti(matricola),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    aula_id INTEGER REFERENCES appello_aule(id),
    manuale BOOLEAN NOT NULL DEFAULT 0,
    PRIMARY KEY (matricola, appello_id)
);

-- Studente ammesso a una prova membro "in deroga", cioè aggiunto a mano dal docente pur
-- non soddisfacendo i requisiti automatici (soglia di matricola sulla prima prova, aver
-- superato la prova precedente sulle successive): la sua sola presenza qui basta a farlo
-- comparire nell'elenco ammessi di quella prova, con un avviso.
CREATE TABLE IF NOT EXISTS ammissioni_manuali (
    matricola TEXT NOT NULL REFERENCES studenti(matricola),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    PRIMARY KEY (matricola, appello_id)
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

-- Ultimo file "Lista Studenti Esame" caricato dalla segreteria per un appello: da qui si
-- ricavano sia l'elenco degli iscritti (per l'appello di presenza e il controllo in fase
-- di correzione) sia, su richiesta, il file compilato da riproporre alla segreteria. Il
-- testo è già decodificato (non i byte grezzi) così da poterlo ripubblicare con la stessa
-- codifica rilevata al momento del caricamento.
CREATE TABLE IF NOT EXISTS appello_segreteria_csv (
    appello_id INTEGER PRIMARY KEY REFERENCES appelli(id),
    nome_file TEXT NOT NULL,
    contenuto TEXT NOT NULL,
    codifica TEXT NOT NULL,
    caricato_il TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Iscritti aggiunti a mano uno per uno (alternativa al file della segreteria, quando non
-- ancora caricato): a differenza del semplice numero manuale (appelli.iscritti_manuale),
-- qui si tratta di un vero elenco nominale, usato anche per il controllo in correzione e
-- per l'elenco stampabile, esattamente come l'elenco derivato dal file della segreteria
-- (che però, quando presente, ha sempre la precedenza).
CREATE TABLE IF NOT EXISTS appello_iscritti_manuali (
    matricola TEXT NOT NULL REFERENCES studenti(matricola),
    appello_id INTEGER NOT NULL REFERENCES appelli(id),
    PRIMARY KEY (matricola, appello_id)
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
    "studenti": [
        ("dsa", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "raggruppamenti": [
        ("matricola_minima_prima_prova", "TEXT"),
    ],
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
        ("valutazione_sospesa", "BOOLEAN NOT NULL DEFAULT 0"),
        ("raggruppamento_da_confermare", "BOOLEAN NOT NULL DEFAULT 0"),
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
        ("chiuso", "BOOLEAN NOT NULL DEFAULT 0"),
        ("iscritti_manuale", "INTEGER"),
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
    """Va chiamata solo su un database già esistente (o dentro init_db, che crea la
    cartella apposta): NON crea più la cartella del corso al volo, perché farlo qui
    significava che anche un semplice GET su un corso già eliminato (es. un link vecchio,
    o una scheda del browser rimasta aperta) lo faceva silenziosamente resuscitare vuoto,
    con conseguente conferma della cancellazione e ricomparsa fantasma del corso."""
    db_path = Path(db_path)
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


def dimentica_schema(db_path: Path) -> None:
    """Da chiamare quando un database viene eliminato dal disco (es. elimina_corso): se
    poi si ricrea un corso con lo stesso tag nella stessa esecuzione del programma, senza
    questa il file nuovo (vuoto) verrebbe scambiato per uno già verificato in passato, e
    get_connection salterebbe la creazione delle tabelle lasciando un database senza
    schema (errore "no such table" alla prima query)."""
    _schema_verificati.discard(str(Path(db_path).resolve()))


def init_db(db_path: Path) -> None:
    """Unico punto che crea davvero un nuovo corso da zero: qui (e solo qui) è corretto
    creare anche la cartella, perché chi chiama sa di voler creare un corso nuovo."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    get_connection(db_path).close()
