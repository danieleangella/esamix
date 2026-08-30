"""Generazione LaTeX dei testi d'esame e dei risultati.

Porting di SETTINGS2.py (legacy/SETTINGS2.py) senza dipendenza dal modulo SETTINGS
globale: tutti i parametri (corso, facoltà, date, soglie di valutazione) arrivano da un
LatexContext costruito a partire dai dati dell'appello/corso nel database.
"""
import copy
import random
from dataclasses import dataclass
from random import randint


@dataclass
class LatexContext:
    corso: str
    facolta: str
    universita: str
    anno: str
    tag: str
    appello_nome: str
    appello_data: str
    consegna: int
    votomin: int
    risposta_corretta: int
    risposta_sbagliata: int
    risposta_vuota: int
    frase_consegna: str
    frase_regole: str
    orale_data: str = ""
    orale_ora: str = ""
    orale_aula: str = ""


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _safe_format(template: str, **kwargs) -> str:
    """Applica un template con placeholder {nome} personalizzabile dall'utente senza mai
    sollevare eccezioni (un placeholder sbagliato non deve rompere la generazione del PDF)."""
    try:
        return template.format_map(_SafeDict(**kwargs))
    except Exception:
        return template


LETTERE = {0: "A", 1: "B", 2: "C", 3: "D", 4: "E", 5: "F", 6: "G"}

BEGIN_DOCUMENT = (
    "\\documentclass[a4paper,10pt]{amsart}\n\n"
    "\\usepackage{amsfonts,latexsym,rawfonts,amsmath,amssymb,amsthm, mathrsfs}\n"
    "\\usepackage[english]{babel}\n"
    "\\usepackage{amssymb}\n"
    "\\usepackage[utf8]{inputenc}\n"
    "\\usepackage{fullpage}\n"
    "\\usepackage{longtable}\n"
    "\\usepackage{framed}\n"
    "\\usepackage{lipsum}\n"
    "\\usepackage{multicol}\n"
    "\\usepackage{booktabs}\n\n"
    "\\renewcommand{\\familydefault}{\\sfdefault}\n\n"
    "\\theoremstyle{definition}\n"
    "\\newtheorem{ex}{Esercizio}\n\n"
    "\\renewcommand{\\Re}{\\mathsf{Re}}\n"
    "\\renewcommand{\\Im}{\\mathsf{Im}}\n\n"
    "\\begin{document}\n\n"
    "\\pagenumbering{gobble}\n\n"
)


def _text_consegna(ctx: LatexContext) -> str:
    if ctx.consegna and ctx.consegna > 0:
        return _safe_format(ctx.frase_consegna, consegna=ctx.consegna)
    return ""


def intestazione_breve(ctx: LatexContext) -> str:
    appello_label = ctx.appello_nome.replace("-", " ")
    # righe facoltative: un campo vuoto (facoltà/università/anno non impostati) non deve
    # produrre una riga vuota, altrimenti due \\ consecutivi generano un errore fatale
    # di compilazione LaTeX ("There's no line here to end").
    universita_anno = ", a.a. ".join(p for p in (ctx.universita, ctx.anno) if p)
    righe = [r for r in (ctx.corso, ctx.facolta, universita_anno) if r]
    intestazione_corso = "".join(f"{r}\\\\\n" for r in righe)
    return (
        "\\begin{center}\n"
        "\\fbox{\n"
        "\\begin{minipage}{0.6\\textwidth}\n"
        "\\bfseries\n"
        "\\small\n"
        "\\begin{center}\n"
        f"{intestazione_corso}"
        f"{appello_label} ({ctx.appello_data})\\\n"
        "\\medskip\n"
        "\\\\\n"
        "\\end{center}\n"
        "\\end{minipage}\n"
        "}\n"
        "\\end{center}\n"
        "\\vspace{1cm}\n\n"
    )


def intestazione(ctx: LatexContext, codice, posizioni: list[dict]) -> str:
    """`posizioni` (vedi mischia()) determina quante caselle mette la griglia delle
    risposte in testa al foglio (una per esercizio del compito) e quali sono domande
    aperte (casella con un grande "-" invece del numero, dato che non c'è una lettera
    da scrivere)."""
    griglia = "".join(
        "\\fbox{\\parbox{20pt}{\\centering\\Large -}}" if p["aperta"]
        else f"\\fbox{{\\parbox{{20pt}}{{\\scriptsize {i + 1}}}}}"
        for i, p in enumerate(posizioni)
    )
    return (
        "\\begin{flushright}\n"
        "\\begin{minipage}{0.80\\textwidth}\n"
        "\\begin{framed}\n"
        "{\\it Nome:}\\enspace\\hrulefill\\quad \n"
        "{\\it Cognome:}\\enspace\\hrulefill\\newline\n"
        f"{{\\it Corso:}}\\enspace{{\\bf {ctx.corso}}}\\quad\n"
        "{\\it Matricola:}\\enspace\\hrulefill\\quad\\newline\n"
        f"{{\\it Data:}}\\enspace{{\\bf {ctx.appello_data}}}\\newline\n\n\\medskip\n\n"
        f"{{\\it Risposte:}}\\enspace{{\\Huge\\bf{griglia}}}\n"
        "\\bigskip\\newline\n"
        f"{{\\it Codice:}}\\enspace{{\\bf {codice}}}\\hfill\\fbox{{\\parbox{{3cm}}{{\\Large\\bf Voto:}}"
        "\\enspace\\hrulefill\\,{\\bf/30}}\n"
        "\\end{framed}\n"
        "\\end{minipage}\n"
        "\\end{flushright}\n\n"
        "\\vspace{1cm}\n\n"
        "\\begin{center}\n"
        f"{{\\bfseries {_text_consegna(ctx)}}}"
        "\\end{center}\n"
    )


def regole(ctx: LatexContext) -> str:
    return (
        "\\begin{footnotesize}\n"
        "\\begin{center}\n"
        "\\fbox{\\fbox{\\parbox{5.5in}{\\centering\n"
        "Compilate {\\it subito} con nome, cognome, numero di matricola. "
        "Lasciate sul banco il libretto universitario.\n"
        "Non \\`e consentito l'utilizzo di libri, appunti, "
        "telefoni cellulari, strumenti elettronici.\n"
        "Riportate le risposte nella griglia in cima al foglio. "
        f"{_text_consegna(ctx)}"
        f"{_safe_format(ctx.frase_regole, corretta=ctx.risposta_corretta, sbagliata=ctx.risposta_sbagliata, vuota=ctx.risposta_vuota, votomin=ctx.votomin)}"
        "La prova dura due ore.\n"
        "}}}\n"
        "\\end{center}\n"
        "\\end{footnotesize}\n\n"
    )


def tex_esercizio(exx) -> str:
    testo, risposte = exx[0], exx[1]
    out = "\n\\begin{ex}\n" + testo + "\n\\begin{description}\n"
    for sol in risposte:
        out += f"\\item[{LETTERE[risposte.index(sol)]}] {sol}\n"
    out += "\\end{description}\n\\end{ex}\n\n"
    return out


def tex_esercizio_aperto(testo: str) -> str:
    """Domanda aperta: nessuna lista di risposte a scelta multipla, solo spazio bianco in
    cui lo studente scrive la risposta a mano."""
    out = "\n\\begin{ex}\n" + testo + "\n\\end{ex}\n\n"
    out += "\\vspace{3cm}\n"
    return out


def _separatore_esercizio() -> str:
    return "\\begin{center}\n\\noindent\\rule{0.3\\textwidth}{0.4pt}\n\\end{center}\n"


def stampa_codici(ctx: LatexContext, studenti) -> str:
    tex = intestazione_breve(ctx)
    tex += (
        "\\begin{center}\n\\begin{longtable}{cc}\n\\toprule\n"
        "{\\bfseries Codice} & {\\bfseries Griglia} \\\\\n\\toprule\n"
    )
    for codice, griglia in studenti:
        tex += f"{codice} & {griglia}\\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\\end{center}\n\n"
    return tex


def mischia(esercizi_struct: list[dict]) -> list[dict]:
    """esercizi_struct: [{esercizio_id, obbligatorio, aperta, varianti: [{variante_id, testo, risposte}]}, ...]
    Sceglie una variante a caso (probabilità uniforme tra le sue) per ciascun esercizio —
    esercizi diversi possono avere un numero diverso di varianti, non serve che sia lo
    stesso per tutti — mischia l'ordine delle risposte e l'ordine degli esercizi nel
    compito. Ritorna, per ciascuna posizione nel compito risultante:
    {esercizio_id, variante_id, obbligatorio, aperta, testo, risposte, indice_corretta}.
    Per una domanda aperta (senza risposte a scelta multipla) risposte è vuota e
    indice_corretta è None: non c'è nulla da mischiare né una lettera "corretta"."""
    posizioni = []
    for es in esercizi_struct:
        variante = copy.deepcopy(es["varianti"][randint(0, len(es["varianti"]) - 1)])
        if es.get("aperta"):
            posizioni.append({
                "esercizio_id": es["esercizio_id"], "variante_id": variante["variante_id"],
                "obbligatorio": es["obbligatorio"], "aperta": True, "testo": variante["testo"],
                "risposte": [], "indice_corretta": None,
            })
            continue
        risposte = variante["risposte"]
        corretta = risposte[0]
        random.shuffle(risposte)
        posizioni.append({
            "esercizio_id": es["esercizio_id"], "variante_id": variante["variante_id"],
            "obbligatorio": es["obbligatorio"], "aperta": False, "testo": variante["testo"],
            "risposte": risposte, "indice_corretta": risposte.index(corretta),
        })
    random.shuffle(posizioni)
    return posizioni


def crea_file_riferimento(ctx: LatexContext, esercizi_struct: list[dict]) -> str:
    """Foglio di riferimento (testo + risposta corretta sempre "A" per ogni variante di
    ogni esercizio): pensato per essere pubblicato online, comune a tutti i blocchi di
    compiti prodotti per questo appello, e generato una sola volta."""
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    tex += (
        "\\begin{center}\n"
        "{\\small\\it (foglio di riferimento: tutte le varianti di ogni esercizio, la risposta corretta \\`e sempre \"A\")}\n"
        "\\end{center}\n\\vspace{1cm}\n\n"
    )
    tex += "\n\\begin{multicols}{2}\n\\setcounter{ex}{0}\n"
    for es in esercizi_struct:
        varianti = es["varianti"]
        for i, variante in enumerate(varianti):
            if es.get("aperta"):
                tex += tex_esercizio_aperto(variante["testo"])
            else:
                tex += tex_esercizio([variante["testo"], variante["risposte"]])
            # la soluzione (se presente) va subito dopo il testo dell'ultima variante
            # stampata di questo esercizio, prima della riga che lo separa dal successivo.
            if i == len(varianti) - 1 and es.get("soluzione"):
                tex += (
                    "\\begin{center}\\fbox{\\parbox{0.9\\linewidth}{\\small "
                    f"{{\\bfseries Soluzione/suggerimento:}} {es['soluzione']}"
                    "}}\\end{center}\n\n"
                )
            if i < len(varianti) - 1:
                tex += _separatore_esercizio()
        tex += _separatore_esercizio()
    tex += "\\end{multicols}\n\n"
    tex += "\n\n\\end{document}"
    return tex


def crea_file_blocco(ctx: LatexContext, esercizi_struct: list[dict], numero_studenti: int, codici_esistenti=None):
    """esercizi_struct: vedi mischia(). Ritorna (studenti, tex) dove
    studenti = [(codice, griglia_soluzioni, posizioni), ...] e posizioni è la lista, in
    ordine, di {esercizio_id, variante_id, obbligatorio, risposte} per quel compito
    specifico — risposte è l'ordine (mischiato) delle risposte effettivamente stampate su
    quel foglio, da conservare per poter poi risalire al testo scelto in fase di
    statistiche (la lettera A/B/C da sola non basta: cambia ordine a ogni compito).
    Un blocco è l'insieme dei compiti stampati insieme in un'unica generazione.
    `codici_esistenti` (facoltativo) sono i codici già usati in questo appello (altri
    blocchi): un nuovo codice, oltre a non ripetersi all'interno di questo stesso blocco,
    non deve nemmeno ripetere uno di quelli, altrimenti il compito stampato su carta
    risulterebbe impossibile da salvare (violazione dell'unicità del codice) mentre lo
    studente crede comunque di avere in mano un compito valido."""
    codici_usati = set(codici_esistenti or ())
    studenti = []
    tex = BEGIN_DOCUMENT

    for _ in range(numero_studenti):
        code = randint(0, 999999)
        while code in codici_usati:
            code = randint(0, 999999)
        codici_usati.add(code)
        posizioni = mischia(esercizi_struct)
        tex += intestazione(ctx, code, posizioni)
        tex += "\\begin{small}\n\n\\begin{multicols}{2}\n\\setcounter{ex}{0}\n"
        # "-" segnala una domanda aperta: nessuna lettera "corretta" da confrontare, il
        # punteggio per quella posizione lo assegna sempre il docente in correzione.
        griglia = "".join("-" if p["aperta"] else LETTERE[p["indice_corretta"]] for p in posizioni)
        studenti.append((code, griglia, [
            {
                "esercizio_id": p["esercizio_id"], "variante_id": p["variante_id"], "obbligatorio": p["obbligatorio"],
                "aperta": p["aperta"], "risposte": p["risposte"],
            }
            for p in posizioni
        ]))
        for p in posizioni:
            if p["aperta"]:
                tex += tex_esercizio_aperto(p["testo"])
            else:
                tex += tex_esercizio([p["testo"], p["risposte"]])
            tex += _separatore_esercizio()
        tex += "\\end{multicols}\n\\end{small}\n\n\\vfill\n"
        tex += regole(ctx)
        tex += "\\clearpage\n\n"

    tex += "\n\n\\end{document}"
    return studenti, tex


def crea_file_griglia(ctx: LatexContext, studenti_codici_griglie) -> str:
    """Griglia delle soluzioni per un singolo blocco: studenti_codici_griglie è
    [(codice, griglia), ...]."""
    tex = BEGIN_DOCUMENT
    tex += stampa_codici(ctx, studenti_codici_griglie)
    tex += "\n\n\\end{document}"
    return tex


def celamatr(matricola: str) -> str:
    return matricola[0:3] + "xx" + matricola[5:7]


def celavoto(voto: int, votomin: int) -> str:
    if voto > 30:
        return "30lode"
    if voto < votomin:
        return "non superato"
    return str(voto)


def _intestazione_orale(ctx: LatexContext) -> str:
    if not (ctx.orale_data or ctx.orale_ora or ctx.orale_aula):
        return ""
    dettagli = ", ".join(p for p in (ctx.orale_data, ctx.orale_ora, ctx.orale_aula) if p)
    return (
        "\\begin{center}\n"
        f"{{\\bfseries Orale: {dettagli}}}\n"
        "\\end{center}\n\\vspace{0.5cm}\n\n"
    )


def crea_tex_risultati(ctx: LatexContext, lista) -> str:
    """lista: [(matricola, nome, cognome, etichetta), ...] — etichetta è già il testo da
    stampare al posto del voto (un numero, "30lode", "non superato", "orale", "assente",
    "ritirato", ...): la logica su quale etichetta usare vive in risultati.py."""
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    tex += _intestazione_orale(ctx)
    tex += (
        "\\begin{center}\n\\begin{longtable}{cc}\n\\toprule\n"
        "{\\bfseries Matricola} & {\\bfseries Voto} \\\\\n\\toprule\n"
    )
    for matricola, nome, cognome, etichetta in lista:
        tex += f"{celamatr(matricola)}({nome[0]}{cognome[0]}) & {etichetta}\\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\\end{center}\n\n"
    tex += "\\vfill\n\\end{document}"
    return tex
