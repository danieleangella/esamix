"""Migrazione da un db.sqlite legacy (schema a colonne fisse, vedi legacy/esami.py) al
nuovo schema normalizzato. Non modifica né cancella mai i file legacy: legge in
sola lettura e scrive esclusivamente nel nuovo corso di destinazione."""
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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

    corsi_service.create_corso(tag, nome, facolta, universita, anno, docente, eredita=False)
    corsi_service.update_meta(tag, importato_da_legacy="1")

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
                # prove già svolte, senza elenco iscritti: si ricostruisce chi poteva sostenerle
                fonte_ammessi="corso",
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
        matricole_valide: set[str] = set()
        for row in studenti_rows:
            matricola = _clean_matricola(row["matricola"])
            if not matricola:
                report.warnings.append(f"Riga studenti scartata: matricola vuota ({dict(row)})")
                continue
            matricole_valide.add(matricola)
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
                if not isinstance(voto, int):
                    report.warnings.append(
                        f"Studente {matricola}, colonna '{col}': voto non numerico ({voto!r}), scartato"
                    )
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
                if matricola not in matricole_valide:
                    report.warnings.append(
                        f"Riga esami per matricola {matricola} (appello '{col}'): nessuno studente con questa "
                        "matricola in 'studenti' (probabile prova/collaudo), saltata"
                    )
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
            # un corso migrato da legacy è per definizione concluso: ogni risultato importato
            # va considerato già verbalizzato (non deve comparire tra gli "idonei da
            # verbalizzare" né essere più modificabile). 'ufficiale' resta solo per capire a
            # quale riga associare la data di verbalizzazione storica (le altre non ne hanno una).
            ufficiale = verbalizzata_per_studente.get(matricola) == col
            compito_id = None
            if entry["codice"] is not None:
                crow = conn.execute(
                    "SELECT id FROM compiti WHERE appello_id=? AND codice=?", (appello_id, str(entry["codice"]))
                ).fetchone()
                compito_id = crow["id"] if crow else None
            conn.execute(
                "INSERT INTO risultati (matricola, appello_id, compito_id, risposte, voto, verbalizzato, data_verbalizzazione) "
                "VALUES (?,?,?,?,?,1,?) "
                "ON CONFLICT(matricola, appello_id) DO NOTHING",
                (
                    matricola, appello_id, compito_id, entry["risposte"], entry["voto"],
                    entry["datav"] if ufficiale else None,
                ),
            )
            n_risultati += 1
            n_verbalizzati += 1

        # studenti con 'totale' impostato ma nessuna colonna corrispondente trovata:
        # non si perde il voto verbalizzato, si registra in un appello 'storico' dedicato
        senza_match = [
            row for row in studenti_rows
            if ("totale" in row.keys() and row["totale"] is not None)
            and _clean_matricola(row["matricola"]) not in verbalizzata_per_studente
        ]
        if senza_match:
            # inserito con conn (non corsi_service.create_appello, che aprirebbe una seconda
            # connessione allo stesso file: con questa transazione ancora aperta finirebbe
            # in 'database is locked')
            storico_id = conn.execute(
                "INSERT INTO appelli (slug, nome, tipo, data) VALUES (?,?,?,?)",
                ("verbalizzazione-storica", "Verbalizzazioni storiche (da migrazione)", "storico", None),
            ).lastrowid
            for row in senza_match:
                matricola = _clean_matricola(row["matricola"])
                conn.execute(
                    "INSERT INTO risultati (matricola, appello_id, voto, verbalizzato, data_verbalizzazione) "
                    "VALUES (?,?,?,1,?) ON CONFLICT(matricola, appello_id) DO NOTHING",
                    (matricola, storico_id, row["totale"], row["datav"]),
                )
                n_risultati += 1
                n_verbalizzati += 1
            report.warnings.append(
                f"{len(senza_match)} studenti avevano un voto verbalizzato ('totale') non riconducibile "
                "a nessuna colonna appello: registrati nell'appello 'Verbalizzazioni storiche (da migrazione)'"
            )

        # prove già svolte: il loro testo è definitivo (riapribile dalla scheda Testo)
        conn.execute("UPDATE appelli SET testo_chiuso=1 WHERE id IN (SELECT appello_id FROM compiti)"
                     " OR id IN (SELECT appello_id FROM risultati)")
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

    # esercizi di ogni appello dalla sua cartella e dettaglio dei compiti stampati (quale
    # esercizio in quale posizione, con quale ordine delle risposte) dai file
    # testo-<appello>.tex: servono alle statistiche sugli esercizi e al controllo delle
    # correzioni. Gli esercizi entrano così come sono stati stampati, anche se oggi la
    # banca li rifiuterebbe (es. due risposte uguali in una variante).
    try:
        riparazione = ripara_da_cartella_legacy(tag, str(cartella))
        report.dopo["esercizi_importati"] = sum(a["esercizi_sostituiti"] for a in riparazione["appelli"])
        report.dopo["compiti_ricostruiti"] = sum(a["compiti_ricostruiti"] for a in riparazione["appelli"])
        for a in riparazione["appelli"]:
            report.warnings.extend(f"{a['appello']}: {x}" for x in a["avvisi"])
            if a["esercizi_non_riconosciuti"]:
                report.warnings.append(f"{a['appello']}: {a['esercizi_non_riconosciuti']} domande stampate non "
                                       "riconosciute tra gli esercizi della cartella (modificati dopo la stampa?)")
    except Exception as e:
        report.warnings.append(f"Esercizi e dettaglio dei compiti non importati: {e}")
    return report


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# --- Riparazione dei corsi già importati: esercizi e compiti delle vecchie prove ---------

def _normalizza_testo(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def leggi_compiti_tex(tex: str) -> list[tuple[str, list[tuple[str, list[str]]]]]:
    """Dal file testo-<appello>.tex della vecchia CLI, che contiene tutti i compiti
    stampati: per ogni compito il codice e, nell'ordine in cui erano stampati, il testo di
    ogni esercizio con le sue risposte nell'ordine delle lettere A, B, C, ..."""
    compiti = []
    for blocco in re.split(r"\{\\it Codice:\}\\enspace\{\\bf\s*", tex)[1:]:
        m = re.match(r"(\w+)\}", blocco)
        if not m:
            continue
        fine = blocco.find("\\clearpage")
        corpo = blocco[:fine] if fine >= 0 else blocco
        esercizi = []
        for e in re.finditer(r"\\begin\{ex\}(.*?)\\end\{ex\}", corpo, re.S):
            x = e.group(1)
            i = x.find("\\begin{description}")
            testo = _normalizza_testo(x[:i] if i >= 0 else x)
            risposte = [_normalizza_testo(r) for r in
                        re.findall(r"\\item\[[A-Z]\](.*?)(?=\\item\[|\\end\{description\})", x, re.S)]
            esercizi.append((testo, risposte))
        compiti.append((m.group(1), esercizi))
    return compiti


def _cartella_originale(conn) -> Optional[Path]:
    """La cartella principale del corso legacy da cui è stato importato, ricavata dalle
    note "Importato da <cartella>/<appello>/esercizi" lasciate sugli esercizi."""
    for (note,) in conn.execute("SELECT note FROM esercizi WHERE note LIKE 'Importato da %' ORDER BY id"):
        p = Path(note[len("Importato da "):].strip())
        if p.name == "esercizi" and p.parent.parent.is_dir():
            return p.parent.parent
    return None


def cartella_originale(tag: str) -> Optional[str]:
    conn = db.get_connection(config.corso_db_path(tag))
    try:
        p = _cartella_originale(conn)
        return str(p) if p else None
    finally:
        conn.close()


def ripara_da_cartella_legacy(tag: str, cartella_principale: str = "") -> dict:
    """Ripara un corso già importato dalla vecchia CLI, a partire dalla sua cartella:
    - riassegna a ogni appello gli esercizi della sua cartella (le versioni precedenti
      dell'importazione davano a tutti gli appelli quelli della prima cartella letta);
    - ricostruisce dal file testo-<appello>.tex quale esercizio, in quale variante e con
      quale ordine delle risposte c'era in ogni posizione di ogni compito stampato, così
      le statistiche mostrano gli esercizi più sbagliati e le risposte sbagliate scelte.
    Non tocca i compiti che hanno già questo dettaglio, né risultati e voti. Ritorna un
    riepilogo per appello."""
    import json

    conn = db.get_connection(config.corso_db_path(tag))
    try:
        cartella = Path(cartella_principale).resolve() if cartella_principale else _cartella_originale(conn)
        if cartella is None or not cartella.is_dir():
            raise ValueError("Cartella originale del corso non trovata: indicala a mano")
        appelli = conn.execute("SELECT id, slug, nome FROM appelli ORDER BY id").fetchall()
        riepilogo = []
        for a in appelli:
            cartella_es = cartella / a["slug"] / "esercizi"
            file_tex = cartella / a["slug"] / f"testo-{a['slug']}.tex"
            if not (cartella_es / "testi.py").exists():
                continue
            voce = {"appello": a["nome"], "esercizi_sostituiti": 0, "compiti_ricostruiti": 0,
                    "esercizi_non_riconosciuti": 0, "avvisi": []}
            try:
                originali = esercizi_service.carica_esercizi_da_cartella(str(cartella_es))
            except Exception as e:
                voce["avvisi"].append(f"esercizi non leggibili: {e}")
                riepilogo.append(voce)
                continue

            # 1. esercizi assegnati: se non coincidono con quelli della cartella, li sostituisce
            def firma(varianti):
                return sorted(_normalizza_testo(t) for t, _ in varianti)
            assegnati = conn.execute(
                "SELECT e.id, e.note FROM appello_esercizi ae JOIN esercizi e ON e.id = ae.esercizio_id "
                "WHERE ae.appello_id=? ORDER BY ae.ordine, ae.rowid", (a["id"],)
            ).fetchall()
            firme_assegnati = sorted(
                firma([(v["testo"], None) for v in conn.execute(
                    "SELECT testo FROM esercizio_varianti WHERE esercizio_id=?", (e["id"],))])
                for e in assegnati
            )
            if firme_assegnati != sorted(firma(v) for v in originali):
                for e in assegnati:
                    conn.execute("DELETE FROM appello_esercizi WHERE appello_id=? AND esercizio_id=?", (a["id"], e["id"]))
                    in_uso = conn.execute(
                        "SELECT (SELECT count(*) FROM appello_esercizi WHERE esercizio_id=?) + "
                        "(SELECT count(*) FROM compito_esercizi WHERE esercizio_id=?)", (e["id"], e["id"])
                    ).fetchone()[0]
                    # elimina solo le copie importate e mai usate altrove
                    if not in_uso and (e["note"] or "").startswith("Importato da "):
                        conn.execute("DELETE FROM esercizio_verifiche WHERE esercizio_id=?", (e["id"],))
                        conn.execute("DELETE FROM esercizio_sospetti WHERE esercizio_id=?", (e["id"],))
                        conn.execute("DELETE FROM esercizio_varianti WHERE esercizio_id=?", (e["id"],))
                        conn.execute("DELETE FROM esercizi WHERE id=?", (e["id"],))
                for ordine, varianti in enumerate(originali, start=1):
                    cur = conn.execute("INSERT INTO esercizi (nome, note) VALUES (?,?)",
                                       (None, f"Importato da {cartella_es}"))
                    for testo, risposte in varianti:
                        conn.execute("INSERT INTO esercizio_varianti (esercizio_id, testo, risposte_json) VALUES (?,?,?)",
                                     (cur.lastrowid, testo.strip(), json.dumps(list(risposte))))
                    conn.execute("INSERT INTO appello_esercizi (appello_id, esercizio_id, ordine) VALUES (?,?,?)",
                                 (a["id"], cur.lastrowid, ordine))
                voce["esercizi_sostituiti"] = len(originali)

            # 2. dettaglio dei compiti stampati, dal .tex
            if not file_tex.exists():
                voce["avvisi"].append(f"manca {file_tex.name}: compiti non ricostruiti")
                riepilogo.append(voce)
                continue
            varianti_per_testo = {}
            for r in conn.execute(
                "SELECT ev.id, ev.esercizio_id, ev.testo, ev.risposte_json FROM esercizio_varianti ev "
                "JOIN appello_esercizi ae ON ae.esercizio_id = ev.esercizio_id WHERE ae.appello_id=?", (a["id"],)
            ):
                # più varianti possono avere la stessa domanda e risposte diverse
                varianti_per_testo.setdefault(_normalizza_testo(r["testo"]), []).append(r)
            compiti_db = {r["codice"]: r for r in conn.execute(
                "SELECT c.id, c.codice, c.soluzioni FROM compiti c WHERE c.appello_id=? AND NOT EXISTS "
                "(SELECT 1 FROM compito_esercizi ce WHERE ce.compito_id = c.id)", (a["id"],))}
            for codice, stampati in leggi_compiti_tex(file_tex.read_text(encoding="utf-8", errors="replace")):
                compito = compiti_db.get(codice)
                if compito is None:
                    continue
                righe = []
                for posizione, (testo, risposte_stampate) in enumerate(stampati):
                    stampate = set(risposte_stampate)
                    v = max(varianti_per_testo.get(testo, []), default=None, key=lambda r: len(
                        stampate & {_normalizza_testo(x) for x in json.loads(r["risposte_json"])}))
                    if v is None:
                        voce["esercizi_non_riconosciuti"] += 1
                        continue
                    originali_v = json.loads(v["risposte_json"])
                    per_testo = {_normalizza_testo(r): r for r in originali_v}
                    mischiate = [per_testo.get(r, r) for r in risposte_stampate]
                    # la risposta corretta (la prima tra le originali) deve stare alla
                    # lettera indicata nelle soluzioni del compito: altrimenti l'abbinamento
                    # non è affidabile e la posizione resta senza dettaglio
                    lettera = compito["soluzioni"][posizione:posizione + 1].upper()
                    if (not lettera or not originali_v or originali_v[0] not in mischiate
                            or "ABCDEFGHIJ"[mischiate.index(originali_v[0])] != lettera):
                        voce["esercizi_non_riconosciuti"] += 1
                        continue
                    righe.append((compito["id"], posizione, v["esercizio_id"], json.dumps(mischiate)))
                # variante_id resta vuoto: i compiti della vecchia CLI non vanno rigenerati,
                # e così le varianti dell'esercizio restano modificabili
                conn.executemany(
                    "INSERT INTO compito_esercizi (compito_id, posizione, esercizio_id, risposte_mischiate) VALUES (?,?,?,?)",
                    righe,
                )
                if righe:
                    voce["compiti_ricostruiti"] += 1
            riepilogo.append(voce)
        conn.commit()
        return {"cartella": str(cartella), "appelli": riepilogo}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
