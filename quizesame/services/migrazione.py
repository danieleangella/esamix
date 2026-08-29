"""Migrazione da un db.sqlite legacy (schema a colonne fisse, vedi legacy/esami.py) al
nuovo schema normalizzato. Non modifica né cancella mai i file legacy: legge in
sola lettura e scrive esclusivamente nel nuovo corso di destinazione."""
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from quizesame import config, db
from quizesame.services import corsi as corsi_service
from quizesame.services import esercizi as esercizi_service

COLONNE_NON_APPELLO = {"matricola", "nome", "cognome", "laurea", "ofa", "totale", "datav"}

# (colonna senza trattini, slug con trattini, nome visualizzato, tipo)
# Ordine = ordine cronologico noto (usato come tie-break per capire quale riga è stata
# verbalizzata, quando più colonne hanno lo stesso voto di 'totale'). Il voto minimo non
# è più per-appello: le prove parziali diventano membri del raggruppamento
# 'prove-parziali' creato più sotto, e da lì ereditano automaticamente la soglia
# dedicata del corso (votomin_raggruppamento).
APPELLO_MAP = [
    ("primaprovaparziale", "prima-prova-parziale", "Prima prova parziale", "appello"),
    ("secondaprovaparziale", "seconda-prova-parziale", "Seconda prova parziale", "appello"),
    ("secondaprovaparzialevariante", "seconda-prova-parziale-variante", "Seconda prova parziale (variante)", "appello"),
    ("proveparziali", "prove-parziali", "Prove parziali (media)", "raggruppamento"),
    ("primoappello", "primo-appello", "Primo appello", "appello"),
    ("secondoappello", "secondo-appello", "Secondo appello", "appello"),
    ("terzoappello", "terzo-appello", "Terzo appello", "appello"),
    ("quartoappello", "quarto-appello", "Quarto appello", "appello"),
    ("quintoappello", "quinto-appello", "Quinto appello", "appello"),
    ("sestoappello", "sesto-appello", "Sesto appello", "appello"),
    ("settimoappello", "settimo-appello", "Settimo appello", "appello"),
    ("primoappellostraordinario", "primo-appello-straordinario", "Primo appello straordinario", "straordinario"),
    ("secondoappellostraordinario", "secondo-appello-straordinario", "Secondo appello straordinario", "straordinario"),
]
APPELLO_MAP_BY_COL = {c[0]: c for c in APPELLO_MAP}

# membri noti del raggruppamento "prove-parziali" (solo queste due prove venivano
# combinate dalla vecchia CLI: legacy/esami.py::stampa_risultati_compitini)
RAGGRUPPAMENTO_PROVE_PARZIALI_MEMBRI = ["prima-prova-parziale", "seconda-prova-parziale"]


def _clean_matricola(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("﻿")


@dataclass
class MigrationReport:
    prima: dict = field(default_factory=dict)
    dopo: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def migra(
    tag: str, legacy_db_path: str,
    nome: str, facolta: str, universita: str, anno: str, docente: str,
) -> MigrationReport:
    if config.corso_exists(tag):
        raise ValueError(f"Esiste già un corso con tag '{tag}': scegli un tag diverso")

    src = sqlite3.connect(f"file:{legacy_db_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    report = MigrationReport()
    report.prima = {
        t: src.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("studenti", "compiti", "esami")
        if _table_exists(src, t)
    }

    cols = [r["name"] for r in src.execute("PRAGMA table_info(studenti)")]
    colonne_appello = [c for c in cols if c not in COLONNE_NON_APPELLO]

    corsi_service.create_corso(tag, nome, facolta, universita, anno, docente)

    # corsi di laurea
    ctype_validi = set()
    if _table_exists(src, "CorsiType"):
        for row in src.execute("SELECT ctype, corso_name FROM CorsiType"):
            corsi_service.upsert_corso_laurea(tag, row["ctype"], row["corso_name"])
            ctype_validi.add(row["ctype"])

    # appelli: una riga per colonna riconosciuta (o non riconosciuta, con slug=colonna)
    appello_id_by_col = {}
    appello_id_by_slug = {}
    for col in colonne_appello:
        if col in APPELLO_MAP_BY_COL:
            _, slug, nome_appello, tipo = APPELLO_MAP_BY_COL[col]
        else:
            slug, nome_appello, tipo = col, col, "appello"
            report.warnings.append(f"Colonna '{col}' non riconosciuta: creato appello '{slug}' da rinominare a mano")
        appello = corsi_service.create_appello(tag, slug=slug, nome=nome_appello, tipo=tipo, data=None)
        appello_id_by_col[col] = appello.id
        appello_id_by_slug[slug] = appello.id

    # raggruppamento 'prove-parziali': combina, se presenti, le due prove parziali note.
    # Diventarne membri è anche ciò che dà a queste due prove la soglia dedicata
    # 'votomin_raggruppamento' del corso invece di quella standard.
    if "proveparziali" in appello_id_by_col:
        membri_ids = [appello_id_by_slug[s] for s in RAGGRUPPAMENTO_PROVE_PARZIALI_MEMBRI if s in appello_id_by_slug]
        if len(membri_ids) == len(RAGGRUPPAMENTO_PROVE_PARZIALI_MEMBRI):
            corsi_service.create_raggruppamento(
                tag, nome="Prove parziali (media)", membro_ids=membri_ids,
                appello_id=appello_id_by_col["proveparziali"],
            )
        else:
            report.warnings.append(
                "Non ho trovato entrambe le prove parziali da collegare al raggruppamento 'prove-parziali': "
                "i voti già calcolati sono stati comunque migrati, ma andrà ricreato manualmente il "
                "raggruppamento per ricalcolarlo in futuro"
            )
        if "secondaprovaparzialevariante" in appello_id_by_col:
            report.warnings.append(
                "La 'seconda prova parziale (variante)' non fa parte del raggruppamento 'prove-parziali' "
                "(come già nella vecchia CLI) e quindi userà il voto minimo standard del corso, non quello "
                "dedicato alle prove parziali: aggiungila a un raggruppamento a mano se serve"
            )

    # matricola -> dati studente + risultati per appello (uniti da studenti+esami prima di inserire)
    studenti_rows = list(src.execute("SELECT * FROM studenti"))
    risultati_map: dict[tuple[str, str], dict] = {}  # (matricola, col) -> {voto, totale, datav}

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        for row in studenti_rows:
            matricola = _clean_matricola(row["matricola"])
            if not matricola:
                report.warnings.append(f"Riga studenti scartata: matricola vuota ({dict(row)})")
                continue
            laurea = row["laurea"] if "laurea" in row.keys() else None
            laurea_ctype = laurea if laurea in ctype_validi else None
            if laurea and laurea not in ctype_validi:
                report.warnings.append(f"Studente {matricola}: corso di laurea '{laurea}' non riconosciuto, lasciato non impostato")
            conn.execute(
                "INSERT INTO studenti (matricola, nome, cognome, laurea_ctype) VALUES (?,?,?,?) "
                "ON CONFLICT(matricola) DO NOTHING",
                (matricola, row["nome"] or "", row["cognome"] or "", laurea_ctype),
            )
            totale = row["totale"] if "totale" in row.keys() else None
            datav = row["datav"] if "datav" in row.keys() else None
            for col in colonne_appello:
                voto = row[col]
                if voto is None:
                    continue
                risultati_map[(matricola, col)] = {
                    "voto": voto, "totale": totale, "datav": datav, "risposte": None, "codice": None,
                }

        # compiti + risposte da 'esami' (arricchiscono i risultati già raccolti sopra)
        n_compiti = 0
        if _table_exists(src, "compiti"):
            for row in src.execute("SELECT * FROM compiti"):
                slug = row["used"]
                appello_id = appello_id_by_slug.get(slug)
                if appello_id is None:
                    report.warnings.append(f"Compito con codice {row['codice']}: appello '{slug}' sconosciuto, saltato")
                    continue
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO compiti (appello_id, codice, soluzioni) VALUES (?,?,?)",
                        (appello_id, str(row["codice"]), row["soluzioni"]),
                    )
                    n_compiti += 1
                except Exception:
                    pass

        if _table_exists(src, "esami"):
            for row in src.execute("SELECT * FROM esami"):
                matricola = _clean_matricola(row["matricola"])
                col = row["appello"]
                if col not in appello_id_by_col:
                    report.warnings.append(f"Riga esami per matricola {matricola}: appello '{col}' sconosciuto, saltata")
                    continue
                key = (matricola, col)
                entry = risultati_map.setdefault(key, {"voto": None, "totale": None, "datav": None, "risposte": None, "codice": None})
                entry["risposte"] = row["risposte"]
                entry["codice"] = row["codice"]

        n_risultati = 0
        n_verbalizzati = 0
        # per capire quale riga tra più colonne è "quella verbalizzata" (stesso voto di
        # 'totale'), scegliamo, tra le corrispondenze, quella più recente secondo APPELLO_MAP
        ordine_slug = {c[0]: i for i, c in enumerate(APPELLO_MAP)}
        per_studente_match: dict[str, list[str]] = {}
        for (matricola, col), entry in risultati_map.items():
            if entry["totale"] is not None and entry["voto"] == entry["totale"]:
                per_studente_match.setdefault(matricola, []).append(col)

        verbalizzata_per_studente = {}
        for matricola, cols_match in per_studente_match.items():
            cols_match.sort(key=lambda c: ordine_slug.get(c, -1))
            verbalizzata_per_studente[matricola] = cols_match[-1]

        for (matricola, col), entry in risultati_map.items():
            appello_id = appello_id_by_col[col]
            verbalizzato = verbalizzata_per_studente.get(matricola) == col
            compito_id = None
            if entry["codice"] is not None:
                crow = conn.execute(
                    "SELECT id FROM compiti WHERE appello_id=? AND codice=?", (appello_id, str(entry["codice"]))
                ).fetchone()
                compito_id = crow["id"] if crow else None
            conn.execute(
                "INSERT INTO risultati (matricola, appello_id, compito_id, risposte, voto, verbalizzato, data_verbalizzazione) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(matricola, appello_id) DO NOTHING",
                (
                    matricola, appello_id, compito_id, entry["risposte"], entry["voto"],
                    verbalizzato, entry["datav"] if verbalizzato else None,
                ),
            )
            n_risultati += 1
            if verbalizzato:
                n_verbalizzati += 1

        # studenti con 'totale' impostato ma nessuna colonna corrispondente trovata:
        # non si perde il voto verbalizzato, si registra in un appello 'storico' dedicato
        senza_match = [
            row for row in studenti_rows
            if ("totale" in row.keys() and row["totale"] is not None)
            and _clean_matricola(row["matricola"]) not in verbalizzata_per_studente
        ]
        if senza_match:
            storico = corsi_service.create_appello(tag, slug="verbalizzazione-storica", nome="Verbalizzazioni storiche (da migrazione)", tipo="storico", data=None)
            for row in senza_match:
                matricola = _clean_matricola(row["matricola"])
                conn.execute(
                    "INSERT INTO risultati (matricola, appello_id, voto, verbalizzato, data_verbalizzazione) "
                    "VALUES (?,?,?,1,?) ON CONFLICT(matricola, appello_id) DO NOTHING",
                    (matricola, storico.id, row["totale"], row["datav"]),
                )
                n_risultati += 1
                n_verbalizzati += 1
            report.warnings.append(
                f"{len(senza_match)} studenti avevano un voto verbalizzato ('totale') non riconducibile "
                "a nessuna colonna appello: registrati nell'appello 'Verbalizzazioni storiche (da migrazione)'"
            )

        conn.commit()
    finally:
        conn.close()
        src.close()

    report.dopo = {
        "studenti": len(studenti_rows), "appelli": len(appello_id_by_col),
        "compiti": n_compiti, "risultati": n_risultati, "verbalizzati": n_verbalizzati,
    }
    return report


def migra_da_cartella(
    tag: str, cartella_principale: str,
    nome: str, facolta: str, universita: str, anno: str, docente: str,
) -> MigrationReport:
    """Migrazione completa a partire dalla cartella principale di un corso legacy
    (es. 'GeoOZ-IngMEL-UniFi25'), organizzata come si faceva con la vecchia CLI:
    - il database si trova in '<cartella_principale>/db.sqlite'
    - gli esercizi di ogni appello sono in '<cartella_principale>/<slug-appello>/esercizi/'
      (testi.py + testoN.py)
    Importa studenti/appelli/compiti/risultati dal database, poi assegna automaticamente
    a ciascun appello gli esercizi trovati nella sua cartella, se presente."""
    cartella = Path(cartella_principale)
    legacy_db_path = cartella / "db.sqlite"
    if not legacy_db_path.exists():
        raise ValueError(
            f"Non ho trovato un file 'db.sqlite' in '{cartella_principale}'. "
            "Assicurati che il database più aggiornato sia lì, nella cartella principale "
            "(non in una sottocartella di un singolo appello)."
        )

    report = migra(tag, str(legacy_db_path), nome, facolta, universita, anno, docente)

    n_esercizi_totale = 0
    for appello in corsi_service.list_appelli(tag):
        esercizi_dir = cartella / appello.slug / "esercizi"
        if not (esercizi_dir / "testi.py").exists():
            continue
        try:
            n = esercizi_service.importa_da_cartella_legacy(tag, str(esercizi_dir), appello.id)
            n_esercizi_totale += n
        except Exception as e:
            report.warnings.append(f"Esercizi di '{appello.nome}' ({esercizi_dir}): errore nell'importazione — {e}")
    report.dopo["esercizi_importati"] = n_esercizi_totale
    return report


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
