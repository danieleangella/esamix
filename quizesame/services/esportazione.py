"""Compilazione del file di export voti verso la segreteria (formato
ListaStudentiEsameExport): un CSV con righe di intestazione/metadati seguite
dall'elenco degli studenti iscritti. Si compilano solo le colonne "Esito" e
"Domande d'esame" per gli studenti già corretti, lasciando invariato il resto
del file (comprese le altre colonne e gli studenti senza un risultato)."""
import csv
import io
from typing import Optional

from quizesame.services import correzione as correzione_service
from quizesame.services import corsi as corsi_service


def _esito_per_export(risultato: Optional[dict], votomin: int) -> Optional[str]:
    """None significa "non scrivere nulla" (nessuna valutazione ancora completata o
    confermata): lo studente resta come nel file caricato."""
    if risultato is None:
        return None
    if risultato["esito"] == "assente":
        return "ASS"
    if risultato["esito"] == "ritirato":
        return "RIT"
    if risultato["esito"] != "voto":
        return None
    if risultato["valutazione_sospesa"]:
        return None
    if risultato["richiede_orale"] and not risultato["orale_svolto"]:
        return None
    if risultato["orale_svolto"] and risultato["esito_orale"] == "assente":
        return "ASS"
    if risultato["orale_svolto"] and risultato["esito_orale"] == "insufficiente":
        return "0"
    voto = risultato["voto"]
    if voto is None:
        return None
    if voto < votomin:
        return "0"
    if voto > 30:
        return "31"
    return str(voto)


def compila_export_voti(tag: str, appello_id: int, contenuto: bytes) -> tuple[str, str, list[dict]]:
    """`contenuto` sono i byte del file ListaStudentiEsameExport.csv scaricato dalla
    segreteria: cerca la riga di intestazione con le colonne "Matricola" ed "Esito",
    poi per ogni riga studente il cui matricola corrisponde a un risultato già inserito
    in questo appello compila "Esito" (voto, ASS, RIT o 0 per insufficiente) e "Domande
    d'esame" (testo salvato nelle impostazioni del corso).

    Ritorna (testo compilato, codifica rilevata nel file caricato, elenco degli studenti
    compilati [{"matricola","nome","cognome","voto_label"}] — il chiamante decide se
    scaricarlo subito o mostrare prima una conferma, es. per il raggruppamento)."""
    for codifica in ("utf-8-sig", "cp1252"):
        try:
            testo = contenuto.decode(codifica)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Non riconosco la codifica del file: attesi UTF-8 o Windows-1252")

    righe = list(csv.reader(io.StringIO(testo)))
    indice_intestazione = next(
        (i for i, r in enumerate(righe) if "Matricola" in r and "Esito" in r), None
    )
    if indice_intestazione is None:
        raise ValueError("Non trovo nel file la riga di intestazione con le colonne 'Matricola' ed 'Esito'")

    intestazione = righe[indice_intestazione]
    idx_matricola = intestazione.index("Matricola")
    idx_esito = intestazione.index("Esito")
    idx_domande = intestazione.index("Domande d'esame") if "Domande d'esame" in intestazione else None

    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    votomin = corsi_service.effective_votomin(corso, appello)
    risultati = {r["matricola"]: r for r in correzione_service.list_risultati(tag, appello_id)}

    compilati = []
    for riga in righe[indice_intestazione + 1:]:
        if len(riga) <= idx_matricola or not riga[idx_matricola].strip():
            continue
        matricola = riga[idx_matricola].strip()

        risultato = risultati.get(matricola)
        esito = _esito_per_export(risultato, votomin)
        if esito is None:
            continue
        riga[idx_esito] = esito
        if idx_domande is not None:
            riga[idx_domande] = corso.domande_esame
        compilati.append({
            "matricola": matricola, "nome": risultato["nome"], "cognome": risultato["cognome"],
            "voto_label": esito,
        })

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(righe)
    return buffer.getvalue(), codifica, compilati
