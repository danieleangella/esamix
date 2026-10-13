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
    frase_obbligatorio: str = ""
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


def _prefisso_obbligatorio(ctx: LatexContext, obbligatorio: bool) -> str:
    """La frase sugli esercizi obbligatori (impostazione del corso) va anteposta, in
    grassetto, al testo di ogni esercizio marcato obbligatorio: non solo ricordata una
    volta nel riquadro delle regole generali."""
    if not obbligatorio:
        return ""
    frase = _safe_format(ctx.frase_obbligatorio, consegna=ctx.consegna).strip()
    return f"\\textbf{{{frase}}} " if frase else ""


def tex_esercizio(ctx: LatexContext, exx, obbligatorio: bool = False, evidenzia_corretta: bool = False) -> str:
    """`evidenzia_corretta` (fogli di riferimento, dove la corretta è sempre la prima):
    segna la risposta A con un segno di spunta e in grassetto. La lettera va dalla
    posizione e non dal testo, che potrebbe ripetersi tra due risposte."""
    testo, risposte = exx[0], exx[1]
    out = "\n\\begin{ex}\n" + _prefisso_obbligatorio(ctx, obbligatorio) + testo + "\n\\begin{description}\n"
    for k, sol in enumerate(risposte):
        lettera = LETTERE.get(k, str(k + 1))
        if evidenzia_corretta and k == 0:
            out += f"\\item[{lettera} $\\checkmark$] \\textbf{{{sol}}}\n"
        else:
            out += f"\\item[{lettera}] {sol}\n"
    out += "\\end{description}\n\\end{ex}\n\n"
    return out


def tex_esercizio_aperto(ctx: LatexContext, testo: str, obbligatorio: bool = False) -> str:
    """Domanda aperta: nessuna lista di risposte a scelta multipla, solo spazio bianco in
    cui lo studente scrive la risposta a mano."""
    out = "\n\\begin{ex}\n" + _prefisso_obbligatorio(ctx, obbligatorio) + testo + "\n\\end{ex}\n\n"
    out += "\\vspace{3cm}\n"
    return out


def _separatore_esercizio() -> str:
    return "\\begin{center}\n\\noindent\\rule{0.3\\textwidth}{0.4pt}\n\\end{center}\n"


def stampa_codici(ctx: LatexContext, studenti) -> str:
    tex = intestazione_breve(ctx)
    tex += (
        "\\begin{center}\n{\\large\\bfseries Griglia delle risposte corrette}\\\\[2pt]\n"
        "{\\small per ogni codice di compito, le lettere corrette nell'ordine; ``-'' indica una domanda aperta}\n"
        "\\end{center}\n\\medskip\n\n"
        "\\begin{center}\n\\begin{longtable}{cc}\n\\toprule\n"
        "{\\bfseries Codice} & {\\bfseries Griglia} \\\\\n\\toprule\n\\endhead\n"
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


def _box_soluzione(soluzione: str) -> str:
    """Soluzione sotto un esercizio dei fogli di riferimento: blocco con un filetto a
    sinistra (ambiente leftbar di framed), che a differenza di un riquadro a larghezza
    fissa segue la colonna e si spezza tra due pagine se serve."""
    return (
        "\\begin{leftbar}\n\\small{\\bfseries Soluzione / suggerimento.} "
        f"{soluzione}\n\\end{{leftbar}}\n\n"
    )


def _escape_testo_semplice(testo: str) -> str:
    """Per testi brevi non pensati come LaTeX (es. il nome di un esercizio), che possono
    contenere caratteri speciali come & _ # %: senza escape farebbero fallire la
    compilazione dell'intero documento."""
    sostituzioni = {
        "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#",
        "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
    }
    return "".join(sostituzioni.get(c, c) for c in testo)


def crea_file_riferimento(ctx: LatexContext, esercizi_struct: list[dict], con_soluzioni: bool = False) -> str:
    """Foglio di riferimento (risposta corretta sempre "A"): pensato per essere
    pubblicato online, comune a tutti i blocchi di compiti prodotti per questo appello, e
    generato una sola volta. Non elenca le varianti esercizio per esercizio, ma le
    raggruppa in "compiti" completi (Compito A con la prima variante di ognuno, Compito B
    con la seconda, ...): tanti quante il numero massimo di varianti tra gli esercizi
    assegnati, ripetendo ciclicamente le varianti di chi ne ha di meno.
    Ne esistono due versioni: senza soluzioni/suggerimenti (quella da pubblicare) e, con
    `con_soluzioni`, con il riquadro della soluzione sotto ogni esercizio che ne ha una."""
    n_varianti_max = max((len(es["varianti"]) for es in esercizi_struct), default=0)
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    tex += (
        "\\begin{center}\n"
        "{\\small\\it (foglio di riferimento"
        + (" con soluzioni e suggerimenti" if con_soluzioni else "")
        + ": un compito completo per ogni combinazione "
        "di varianti degli esercizi; la risposta corretta \\`e sempre la A, segnata con $\\checkmark$)}\n"
        "\\end{center}\n\\vspace{1cm}\n\n"
    )
    for indice_compito in range(n_varianti_max):
        if indice_compito > 0:
            tex += "\\clearpage\n\n"
        tex += (
            "\\noindent\\fbox{\\parbox{\\dimexpr\\linewidth-2\\fboxsep-2\\fboxrule}{\\centering\\strut"
            f"\\large\\bfseries Compito {LETTERE.get(indice_compito, indice_compito + 1)}"
            + (f"\\quad{{\\normalsize\\mdseries (variante {indice_compito + 1} di ogni esercizio)}}" if n_varianti_max > 1 else "")
            + "}}\n\\vspace{0.4cm}\n\n"
        )
        tex += "\\begin{multicols}{2}\n\\raggedright\n\\setcounter{ex}{0}\n"
        for es in esercizi_struct:
            varianti = es["varianti"]
            variante = varianti[indice_compito % len(varianti)]
            if es.get("aperta"):
                tex += tex_esercizio_aperto(ctx, variante["testo"], es.get("obbligatorio", False))
            else:
                tex += tex_esercizio(ctx, [variante["testo"], variante["risposte"]], es.get("obbligatorio", False),
                                     evidenzia_corretta=True)
            if con_soluzioni and es.get("soluzione"):
                tex += _box_soluzione(es["soluzione"])
            tex += _separatore_esercizio()
        tex += "\\end{multicols}\n\n"
    tex += "\n\n\\end{document}"
    return tex


def crea_file_anteprima(ctx: LatexContext, esercizi_struct: list[dict]) -> str:
    """Anteprima di controllo del testo di un compito, per il docente: ogni esercizio
    assegnato (nell'ordine della scheda Testo) con nome, tutte le sue varianti, la
    risposta corretta evidenziata e la soluzione/suggerimento. Non è un compito da
    stampare per gli studenti e non crea codici: si può generare in qualunque momento,
    anche prima dei blocchi, per verificare che testo e soluzioni siano corretti.
    esercizi_struct: come per mischia(), con in più "nome" (facoltativo).
    Ogni esercizio ha una barra d'intestazione, le varianti in blocchi separati e, sotto,
    un riquadro di revisione per chi controlla: caselle "corretta"/"da correggere" per
    ogni variante e per la soluzione, più uno spazio per le note. Il PDF è compilabile
    (caselle e note si riempiono e salvano nel lettore PDF); stampato, restano caselle e
    riquadri vuoti da riempire a mano. Usa solo pacchetti presenti in ogni installazione
    LaTeX di base (xcolor, framed, hyperref)."""
    n_varianti = sum(len(es["varianti"]) for es in esercizi_struct)
    tex = BEGIN_DOCUMENT.replace(
        "\\usepackage{fullpage}\n",
        "\\usepackage[a4paper,top=2cm,bottom=2cm,left=2.5cm,right=4.5cm]{geometry}\n"
        "\\usepackage[table]{xcolor}\n",
    ).replace(
        "\\begin{document}\n",
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\setlength{\\parindent}{0pt}\n"
        # colore e spessore del filetto a sinistra di ogni variante (ambiente leftbar di framed)
        "\\renewenvironment{leftbar}{\\def\\FrameCommand{{\\color{black!35}\\vrule width 2.5pt}\\hspace{8pt}}"
        "\\MakeFramed{\\advance\\hsize-\\width\\FrameRestore}}{\\endMakeFramed}\n\n"
        "\\begin{document}\n\\begin{Form}\n",
    )
    tex += intestazione_breve(ctx)
    tex += (
        "\\begin{center}\n{\\large\\bfseries ANTEPRIMA DI CONTROLLO}\\\\[2pt]\n"
        "{\\small con soluzioni -- non distribuire agli studenti}\\\\[6pt]\n"
        f"{{\\small {len(esercizi_struct)} esercizi, {n_varianti} varianti in tutto; "
        "la risposta corretta \\`e segnata con $\\checkmark$.\\\\\n"
        "Sotto ogni esercizio, nel riquadro \\emph{Revisione}, segna se ciascuna variante e la "
        "soluzione sono corrette e annota le correzioni da fare.}\n"
        "\\end{center}\n\\vspace{0.3cm}\n\n"
    )
    for i, es in enumerate(esercizi_struct, start=1):
        if i > 1:
            tex += "\\pagebreak[3]\\bigskip\n\n"
        tex += _intestazione_esercizio_anteprima(i, len(esercizi_struct), es)
        varianti = es["varianti"]
        for j, v in enumerate(varianti, start=1):
            tex += "\\begin{leftbar}\n"
            if len(varianti) > 1:
                tex += f"{{\\small\\bfseries\\color{{black!60}}VARIANTE {j} DI {len(varianti)}}}\\par\\smallskip\n"
            tex += _prefisso_obbligatorio(ctx, es.get("obbligatorio", False)) + v["testo"] + "\n"
            if not es.get("aperta") and v["risposte"]:
                tex += "\\begin{description}\n"
                for k, r in enumerate(v["risposte"]):
                    if k == 0:
                        tex += f"\\item[{LETTERE[k]} $\\checkmark$] \\textbf{{{r}}}\n"
                    else:
                        tex += f"\\item[{LETTERE.get(k, k + 1)}] {r}\n"
                tex += "\\end{description}\n"
            tex += "\\end{leftbar}\n\\medskip\n\n"
        if es.get("soluzione"):
            tex += (
                "\\begin{leftbar}\n{\\small\\bfseries\\color{black!60}SOLUZIONE / SUGGERIMENTO}\\par\\smallskip\n"
                f"{es['soluzione']}\n\\end{{leftbar}}\n\\medskip\n\n"
            )
        else:
            tex += "{\\small\\it (nessuna soluzione/suggerimento inserito)}\\par\\medskip\n\n"
        tex += _riquadro_revisione(i, es)
    tex += (
        "\\clearpage\n"
        "\\noindent\\colorbox{black!12}{\\parbox{\\dimexpr\\linewidth-2\\fboxsep}{\\large\\bfseries "
        "Note generali sul compito}}\\par\\medskip\n"
        "\\noindent\\TextField[name=notegenerali,multiline=true,width=\\linewidth,height=0.8\\textheight,"
        "bordercolor={0.6 0.6 0.6},backgroundcolor={1 1 1},charsize=10pt]{}\n"
    )
    tex += "\n\n\\end{Form}\n\\end{document}"
    return tex


def _intestazione_esercizio_anteprima(numero: int, totale: int, es: dict) -> str:
    """Barra grigia a tutta larghezza con numero e nome dell'esercizio, così l'inizio di
    ogni esercizio si riconosce a colpo d'occhio anche sfogliando il foglio stampato."""
    nome = _escape_testo_semplice(es.get("nome") or f"Esercizio #{es['esercizio_id']}")
    etichette = [f"{len(es['varianti'])} variant{'i' if len(es['varianti']) != 1 else 'e'}"]
    if es.get("obbligatorio"):
        etichette.append("obbligatorio")
    if es.get("aperta"):
        etichette.append("domanda aperta")
    return (
        "\\noindent\\colorbox{black!12}{\\parbox{\\dimexpr\\linewidth-2\\fboxsep}{\\strut"
        f"{{\\large\\bfseries Esercizio {numero}/{totale}}}\\quad {{\\large {nome}}}"
        f"\\hfill{{\\small {', '.join(etichette)}}}}}}}\\par\\medskip\n\n"
    )


def _caselle_esito(nome_campo: str) -> str:
    # il quadratino è disegnato in LaTeX e il campo, senza bordo, ci sta sopra: il bordo
    # di hyperref includerebbe anche la profondità della riga, risultando un rettangolo alto
    def casella(nome: str) -> str:
        return (
            "\\rlap{\\setlength{\\fboxsep}{0pt}\\fbox{\\rule{0pt}{9pt}\\hspace{9pt}}}"
            f"\\CheckBox[name={nome},width=9pt,height=9pt,bordercolor={{}},backgroundcolor={{}}]{{}}"
        )
    return (
        f"{casella(nome_campo + 'ok')}\\enspace corretta"
        f" & {casella(nome_campo + 'ko')}\\enspace da correggere"
    )


def _riquadro_revisione(numero: int, es: dict) -> str:
    """Riquadro per chi controlla il testo: per ogni variante (e per la soluzione, se
    c'è) due caselle "corretta" / "da correggere", poi uno spazio per le note sulle
    correzioni. Non si spezza tra due pagine."""
    varianti = es["varianti"]
    righe = []
    for j in range(1, len(varianti) + 1):
        etichetta = f"Variante {j}" if len(varianti) > 1 else ("Testo" if es.get("aperta") else "Testo e risposte")
        righe.append(f"{etichetta} & {_caselle_esito(f'es{numero}v{j}')} \\\\[3pt]\n")
    if es.get("soluzione"):
        righe.append(f"Soluzione & {_caselle_esito(f'es{numero}sol')} \\\\[3pt]\n")
    return (
        "\\noindent\\fcolorbox{black!45}{yellow!7}{\\parbox{\\dimexpr\\linewidth-2\\fboxsep-2\\fboxrule}{"
        "\\vspace{3pt}{\\bfseries Revisione}\\par\\smallskip\n"
        "\\begin{tabular}{@{}l@{\\qquad}l@{\\qquad}l@{}}\n"
        + "".join(righe)
        + "\\end{tabular}\\par\\smallskip\n"
        "{\\small Note per le correzioni:}\\par\\smallskip\n"
        f"\\TextField[name=note{numero},multiline=true,width=\\linewidth,height=3.2cm,"
        "bordercolor={0.6 0.6 0.6},backgroundcolor={1 1 1},charsize=10pt]{}\\par\\vspace{3pt}}}\\par\n\n"
    )


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
        # testo allineato a sinistra: giustificato, nelle colonne strette, lascia spazi enormi
        tex += "\\begin{small}\n\n\\begin{multicols}{2}\n\\raggedright\n\\setcounter{ex}{0}\n"
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
                tex += tex_esercizio_aperto(ctx, p["testo"], p["obbligatorio"])
            else:
                tex += tex_esercizio(ctx, [p["testo"], p["risposte"]], p["obbligatorio"])
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
        "{\\bfseries Matricola} & {\\bfseries Voto} \\\\\n\\toprule\n\\endhead\n"
    )
    for matricola, nome, cognome, etichetta in lista:
        tex += f"{celamatr(matricola)}({nome[0]}{cognome[0]}) & {etichetta}\\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\\end{center}\n\n"
    tex += "\\vfill\n\\end{document}"
    return tex


def _tabella_ammessi(righe: list[dict]) -> str:
    """Righe ben distanziate (spazio a sufficienza per segnare a penna presenza/assenza
    durante l'appello), con una riga di separazione tra uno studente e l'altro. La
    casella "Presente" è la prima colonna (senza intestazione, da spuntare a penna); la
    colonna "Note" segnala DSA e/o ammissione in deroga (aggiunto a mano pur non
    soddisfacendo i requisiti automatici). Lo spazio verticale è dato da uno strut
    invisibile dentro ogni riga (non da un salto riga finale tipo '\\\\[0.9cm]'): messo
    dopo l'ultima riga, quest'ultimo lascerebbe la prima riga incollata all'intestazione,
    con uno spazio diverso (più stretto) da quello, uniforme, fra tutte le altre righe."""
    tex = (
        "\\begin{center}\n\\begin{longtable}{clllc}\n\\toprule\n"
        "{} & {\\bfseries Matricola} & {\\bfseries Cognome} & {\\bfseries Nome} & {\\bfseries Note} \\\\\n\\toprule\n\\endhead\n"
    )
    for i, r in enumerate(righe):
        note_parti = []
        if r.get("dsa"):
            note_parti.append(r.get("dsa_tipo") if r.get("dsa_tipo") in ("DSA", "L104") else "DSA")
        if r.get("in_deroga"):
            note_parti.append("deroga")
        note = ", ".join(note_parti)
        if i > 0:
            tex += "\\midrule\n"
        tex += f"\\rule{{0pt}}{{0.9cm}}{{\\Large $\\square$}} & {r['matricola']} & {r['cognome']} & {r['nome']} & {note} \\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\\end{center}\n\n"
    return tex


def crea_tex_ammessi(ctx: LatexContext, titolo: str, lista: list[dict], aule: list) -> str:
    """Elenco degli studenti ammessi a sostenere una prova (matricola per intero, non
    oscurata: serve a fare l'appello in aula, non va pubblicata come i risultati). `lista`:
    dict con matricola/nome/cognome/dsa/aula_nome. `aule`: le aule di questa prova (oggetti
    con .id/.nome/.capienza); se presenti, dopo la tabella generale segue una tabella per
    aula con solo gli studenti assegnati a quella specifica aula."""
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    tex += f"\\begin{{center}}{{\\bfseries {titolo}}} — {len(lista)} ammessi\\end{{center}}\\vspace{{0.5cm}}\n\n"
    tex += _tabella_ammessi(lista)
    for aula in aule:
        righe_aula = [r for r in lista if r.get("aula_id") == aula.id]
        tex += "\\newpage\n"
        tex += f"\\begin{{center}}{{\\bfseries Aula: {aula.nome} (capienza {aula.capienza})}} — {len(righe_aula)} studenti\\end{{center}}\\vspace{{0.5cm}}\n\n"
        tex += _tabella_ammessi(righe_aula)
    tex += "\\end{document}"
    return tex
