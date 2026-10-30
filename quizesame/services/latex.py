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
    # già pronta (LaTeX): per chi deve fare l'orale quando l'appello non ne ha data/ora/aula
    frase_orale: str = ""


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
    "\\usepackage[T1]{fontenc}\n"
    # Helvetica: un sans serif chiaro e regolare (tipo Arial), tra quelli consigliati per
    # la lettura di studenti con DSA; presente in ogni installazione LaTeX (psnfss)
    "\\usepackage[scaled=0.95]{helvet}\n"
    "\\usepackage{array}\n"
    "\\usepackage[table]{xcolor}\n"
    "\\usepackage{fullpage}\n"
    "\\usepackage{longtable}\n"
    "\\usepackage{framed}\n"
    "\\usepackage{lipsum}\n"
    "\\usepackage{multicol}\n"
    "\\usepackage{booktabs}\n\n"
    "\\renewcommand{\\familydefault}{\\sfdefault}\n"
    # un po' più di interlinea rispetto al minimo: righe meno fitte, più facili da seguire
    "\\linespread{1.08}\n"
    # riga verticale sottile tra le due colonne di esercizi
    "\\setlength{\\columnseprule}{0.4pt}\n\\setlength{\\columnsep}{0.8cm}\n"
    # nelle colonne strette: un po' di elasticità in più negli spazi e possibilità di andare
    # a capo dopo una virgola dentro una formula in linea (es. "$A=(1,0,2), B=(2,1,0)$"),
    # altrimenti le formule lunghe escono dal margine
    "\\setlength{\\emergencystretch}{1.5em}\n"
    "\\AtBeginDocument{\\mathchardef\\virgolamat=\\mathcode`\\,\\mathcode`\\,=\"8000"
    "\\mathchardef\\puntovirgolamat=\\mathcode`\\;\\mathcode`\\;=\"8000}\n"
    "{\\catcode`\\,=\\active\\gdef,{\\virgolamat\\penalty500\\relax}}\n"
    "{\\catcode`\\;=\\active\\gdef;{\\puntovirgolamat\\penalty500\\relax}}\n"
    # le formule in display non vanno mai a capo: nelle colonne strette i \qquad tra le parti
    # (es. "A=(1,0,2), \qquad B=...") diventano \quad, per stare nella larghezza della colonna
    "\\everydisplay\\expandafter{\\the\\everydisplay\\def\\qquad{\\quad}}\n\n"
    "\\theoremstyle{definition}\n"
    "\\newtheorem{ex}{Esercizio}\n\n"
    "\\renewcommand{\\Re}{\\mathsf{Re}}\n"
    "\\renewcommand{\\Im}{\\mathsf{Im}}\n\n"
    "\\begin{document}\n\n"
    "\\pagenumbering{gobble}\n\n"
)


_FRASE_CONSEGNA_PREDEFINITA = "Consegnate lo svolgimento dei {consegna} esercizi obbligatori."


def _text_consegna(ctx: LatexContext) -> str:
    if ctx.consegna and ctx.consegna > 0:
        # la frase predefinita, con un solo esercizio, diventerebbe "dei 1 esercizi"
        if ctx.consegna == 1 and ctx.frase_consegna.strip() == _FRASE_CONSEGNA_PREDEFINITA:
            return "Consegnate lo svolgimento dell'esercizio obbligatorio.\n"
        return _safe_format(ctx.frase_consegna, consegna=ctx.consegna)
    return ""


def _quanti(n: int, singolare: str, plurale: str) -> str:
    return f"{n} {singolare if n == 1 else plurale}"


def _titolo_documento(titolo: str, dettaglio: str = "") -> str:
    """Titolo di un documento sotto l'intestazione (es. "Risultati", "Studenti ammessi")."""
    return (
        f"\\noindent{{\\Large\\bfseries {titolo}}}"
        + (f"\\hfill{{\\normalsize {dettaglio}}}" if dettaglio else "")
        + "\\par\\vspace{0.5cm}\n\n"
    )


# righe alternate a fondo grigio chiarissimo: nelle tabelle lunghe aiutano a seguire la
# riga giusta (anche a chi ha difficoltà di lettura), e restano leggibili in bianco e nero
_RIGHE_ALTERNATE = "\\rowcolors{2}{black!4}{white}\n\\renewcommand{\\arraystretch}{1.5}\n"


def _riga_intestazione(*celle: str, altezza: str = "") -> str:
    """Riga d'intestazione di una tabella: tutte le celle in grassetto, fondo grigio e la
    stessa altezza delle righe del corpo (`altezza`, se queste hanno uno strut più alto)."""
    strut = f"\\rule{{0pt}}{{{altezza}}}" if altezza else ""
    return "\\rowcolor{black!10}" + strut + " & ".join(f"{{\\normalfont\\bfseries {c}}}" for c in celle) + " \\\\\n"


def _titolo_prova(ctx: LatexContext) -> str:
    return ctx.appello_nome.replace("-", " ")


def intestazione_breve(ctx: LatexContext) -> str:
    """Intestazione dei documenti per il docente o da pubblicare (fogli di riferimento,
    griglia, anteprima, risultati, elenchi): una fascia con il corso a sinistra (corso di
    studi, università e anno sotto) e la prova con la data a destra, chiusa da un filetto.
    Le righe facoltative vuote (facoltà/università/anno non impostati) vengono omesse."""
    universita_anno = ", a.a. ".join(p for p in (ctx.universita, ctx.anno) if p)
    sotto = " \\\\[1pt]\n".join(r for r in (ctx.facolta, universita_anno) if r)
    return (
        "\\noindent\\begin{minipage}[t]{0.64\\textwidth}\\raggedright\n"
        f"{{\\large\\bfseries {ctx.corso}}}"
        + (f"\\\\[2pt]\n{{\\small {sotto}}}" if sotto else "")
        + "\n\\end{minipage}\\hfill\n"
        "\\begin{minipage}[t]{0.34\\textwidth}\\raggedleft\n"
        f"{{\\large\\bfseries {_titolo_prova(ctx)}}}"
        + (f"\\\\[2pt]\n{{\\small {ctx.appello_data}}}" if ctx.appello_data else "")
        + "\n\\end{minipage}\\par\n"
        "\\vspace{4pt}\\noindent\\rule{\\textwidth}{0.8pt}\\par\n"
        "\\vspace{0.6cm}\n\n"
    )


# caselle della griglia delle risposte nell'intestazione del compito: al più tante per riga
# (oltre si va a capo), larghe abbastanza da scrivere una lettera grande e leggibile
_CASELLE_PER_RIGA = 12


def _griglia_risposte(posizioni: list[dict]) -> str:
    """Tabella con il numero dell'esercizio sopra e la casella vuota sotto, in cui lo
    studente scrive la lettera; per una domanda aperta la casella contiene un trattino
    (non c'è una lettera da scrivere). Con molti esercizi va a capo ogni _CASELLE_PER_RIGA."""
    righe = []
    for inizio in range(0, len(posizioni), _CASELLE_PER_RIGA):
        gruppo = posizioni[inizio:inizio + _CASELLE_PER_RIGA]
        colonne = "|" + "|".join(">{\\centering\\arraybackslash}p{0.62cm}" for _ in gruppo) + "|"
        numeri = " & ".join(f"{{\\footnotesize {inizio + k + 1}}}" for k in range(len(gruppo)))
        caselle = " & ".join("{\\Large --}" if p["aperta"] else "" for p in gruppo)
        # un unico fondo bianco dietro la tabella (sul grigio dell'intestazione): un fondo per
        # cella coprirebbe le righe verticali tra le caselle
        righe.append(
            f"{{\\setlength{{\\fboxsep}}{{0pt}}\\colorbox{{white}}{{\\begin{{tabular}}{{{colonne}}}\\hline\n{numeri} \\\\\\hline\n"
            f"\\rule{{0pt}}{{0.65cm}}{caselle} \\\\\\hline\n\\end{{tabular}}}}}}"
        )
    return "\\\\[4pt]\n".join(righe)


def intestazione(ctx: LatexContext, codice, posizioni: list[dict]) -> str:
    """Intestazione del compito dello studente: corso, prova e data con il codice del
    compito ben visibile; poi i dati dello studente su righe ampie da scrivere a mano, la
    griglia delle risposte (vedi _griglia_risposte) e il riquadro del voto. Etichette in
    tondo o grassetto, mai in corsivo, per la leggibilità (anche per studenti con DSA)."""
    linea = "\\rule[-2pt]{{{}}}{{0.4pt}}"
    return (
        # tutta l'intestazione su un fondo grigio chiarissimo (le caselle da scrivere restano bianche)
        "\\noindent\\colorbox{black!5}{\\begin{minipage}{\\dimexpr\\textwidth-2\\fboxsep}\\vspace{2pt}\n"
        # corso e prova a sinistra, codice del compito a destra
        "\\noindent\\begin{minipage}[b]{0.70\\textwidth}\\raggedright\n"
        f"{{\\large\\bfseries {ctx.corso}}}\\\\[2pt]\n"
        f"{{{_titolo_prova(ctx)}" + (f" \\quad {ctx.appello_data}" if ctx.appello_data else "") + "}\n"
        "\\end{minipage}\\hfill\n"
        "\\fcolorbox{black}{white}{\\begin{minipage}[b]{0.22\\textwidth}\\centering\n"
        "{\\scriptsize Codice del compito}\\\\[1pt]\n"
        f"{{\\Large\\bfseries {codice}}}\n"
        "\\end{minipage}}\\par\n"
        "\\vspace{0.35cm}\n\n"
        # dati dello studente
        "\\noindent{\\bfseries Cognome}\\enspace" + linea.format("0.36\\textwidth")
        + "\\hfill{\\bfseries Nome}\\enspace" + linea.format("0.36\\textwidth") + "\\par\n"
        "\\vspace{0.4cm}\n"
        "\\noindent{\\bfseries Matricola}\\enspace" + linea.format("0.30\\textwidth") + "\\par\n"
        "\\vspace{0.3cm}\n\n"
        # griglia delle risposte e voto
        "\\noindent\\begin{minipage}[t]{0.76\\textwidth}\\raggedright\n"
        "{\\bfseries Risposte}\\\\[2pt]\n"
        f"{_griglia_risposte(posizioni)}\n"
        "\\end{minipage}\\hfill\n"
        "\\begin{minipage}[t]{0.20\\textwidth}\\raggedleft\n"
        "\\vspace{0.4cm}\n"
        "\\fcolorbox{black}{white}{\\begin{minipage}{0.92\\linewidth}\\centering\n"
        "{\\bfseries Voto}\\\\[0.35cm]\n"
        "\\rule{0.5\\linewidth}{0.4pt}\\,/30\\\\[2pt]\n"
        "\\end{minipage}}\n"
        "\\end{minipage}\\par\\vspace{3pt}\n"
        "\\end{minipage}}\\par\n"
        "\\vspace{0.2cm}\n\n"
        "\\begin{center}\n"
        f"{{\\bfseries {_text_consegna(ctx)}}}"
        "\\end{center}\n"
    )


def regole(ctx: LatexContext) -> str:
    return (
        "\\begin{footnotesize}\n"
        "\\begin{center}\n"
        "\\fbox{\\fbox{\\parbox{5.5in}{\\centering\n"
        "Compilate {\\bfseries subito} con nome, cognome, numero di matricola. "
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
    tex += _titolo_documento("Griglia delle risposte corrette", _quanti(len(studenti), "compito", "compiti"))
    tex += (
        "\\noindent{\\small Per ogni codice di compito, la lettera corretta di ogni esercizio, nell'ordine in cui "
        "compare su quel foglio; il trattino indica una domanda aperta.}\\par\\medskip\n\n"
        + _RIGHE_ALTERNATE +
        "\\begin{longtable}{>{\\bfseries}l l}\n\\toprule\n"
        + _riga_intestazione("Codice", "Risposte corrette") + "\\midrule\n\\endhead\n"
    )
    for codice, griglia in studenti:
        # lettere distanziate e a gruppi di cinque, come nella casella della correzione
        gruppi = [griglia[i:i + 5] for i in range(0, len(griglia), 5)]
        lettere = "\\quad ".join("\\,".join(g) for g in gruppi)
        tex += f"{codice} & {{\\ttfamily {lettere}}}\\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\n"
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
    sinistra, largo quanto la colonna. Non si usa leftbar (framed): dentro multicols non sa
    quanto spazio resta nella colonna, così una soluzione che non ci stava finiva nella
    pagina dopo, lasciando la colonna accanto vuota e quella dell'esercizio stirata a tutta
    altezza (un enorme spazio bianco tra testo e risposte). Il blocco non si spezza: se non
    sta nella colonna passa alla successiva, che con \\raggedcolumns non stira nulla."""
    return (
        "\\par\\smallskip\\noindent\\hbox{\\vrule width 3pt\\hspace{10pt}"
        "\\begin{minipage}[t]{\\dimexpr\\linewidth-13pt\\relax}\\small\\raggedright"
        "{\\bfseries Soluzione / suggerimento.} "
        f"{soluzione}\n\\end{{minipage}}}}\\par\\medskip\n\n"
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
    for indice_compito in range(n_varianti_max):
        if indice_compito > 0:
            tex += "\\clearpage\n\n"
        tex += (
            "\\noindent\\fbox{\\parbox{\\dimexpr\\linewidth-2\\fboxsep-2\\fboxrule}{\\centering\\strut"
            f"\\large\\bfseries Compito {LETTERE.get(indice_compito, indice_compito + 1)}"
            "}}\n\\vspace{0.4cm}\n\n"
        )
        # \\raggedcolumns: a fine colonna resta spazio libero in fondo, invece di
        # distribuirlo tra le righe (che aprirebbe buchi dentro un esercizio)
        tex += "\\begin{multicols}{2}\n\\raggedcolumns\n\\raggedright\n\\setcounter{ex}{0}\n"
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
        "\\usepackage[a4paper,top=2cm,bottom=2cm,left=2.5cm,right=4.5cm]{geometry}\n",
    ).replace(
        "\\begin{document}\n",
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\raggedbottom\n"
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
            tex += "{\\small (nessuna soluzione/suggerimento inserito)}\\par\\medskip\n\n"
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
        "\\vspace{3pt}{\\bfseries Revisione dell'esercizio " + str(numero) + "}"
        + (f" \\quad({_escape_testo_semplice(es.get('nome') or '')})" if es.get("nome") else "")
        + "\\par\\smallskip\n"
        "\\begin{tabular}{@{}l@{\\qquad}l@{\\qquad}l@{}}\n"
        + "".join(righe)
        + "\\end{tabular}\\par\\smallskip\n"
        "{\\small Note per le correzioni:}\\par\\smallskip\n"
        f"\\TextField[name=note{numero},multiline=true,width=\\linewidth,height=3.2cm,"
        "bordercolor={0.6 0.6 0.6},backgroundcolor={1 1 1},charsize=10pt]{}\\par\\vspace{3pt}}}\\par\n\n"
    )


def pagina_compito(ctx: LatexContext, codice, posizioni: list[dict]) -> str:
    """Il foglio di un singolo studente: intestazione con codice e griglia, esercizi su due
    colonne nell'ordine di `posizioni` (ciascuna con testo, risposte nell'ordine stampato,
    obbligatorio, aperta) e regole in fondo. Usata sia per generare un blocco nuovo sia per
    ricostruire, con la grafica attuale, i compiti già generati (stessi codici e ordine)."""
    tex = intestazione(ctx, codice, posizioni)
    # testo allineato a sinistra: giustificato, nelle colonne strette, lascia spazi enormi
    tex += "\\begin{small}\n\n\\begin{multicols}{2}\n\\raggedright\n\\setcounter{ex}{0}\n"
    for p in posizioni:
        if p["aperta"]:
            tex += tex_esercizio_aperto(ctx, p["testo"], p["obbligatorio"])
        else:
            tex += tex_esercizio(ctx, [p["testo"], p["risposte"]], p["obbligatorio"])
        tex += _separatore_esercizio()
    tex += "\\end{multicols}\n\\end{small}\n\n\\vfill\n"
    tex += regole(ctx)
    # ogni compito occupa esattamente due pagine (un foglio fronte-retro): se il testo sta
    # in una pagina, la seconda resta bianca, così nella stampa fronte-retro nessun compito
    # comincia sul retro di quello prima. Più di due pagine: lo segnala la generazione.
    tex += "\\clearpage\n\\ifodd\\value{page}\\else\\null\\clearpage\\fi\n\n"
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
        tex += pagina_compito(ctx, code, posizioni)
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

    tex += "\n\n\\end{document}"
    return studenti, tex


def _copertina_griglia(ctx: LatexContext, numero_blocco: int, n_blocco: int, totale: int, n_blocchi: int) -> str:
    """Prima pagina del fascicolo con le griglie: il corso e la prova nella metà superiore;
    sotto, quanti compiti sono stati stampati (in questo blocco e in tutto) e gli spazi per
    scrivere a mano iscritti, presenti e assenti. Le griglie cominciano su un nuovo fronte."""
    universita_anno = ", a.a. ".join(p for p in (ctx.universita, ctx.anno) if p)
    sotto = "".join(f"{{\\large {r}}}\\par\\smallskip\n" for r in (ctx.facolta, universita_anno) if r)
    prova = _escape_testo_semplice(ctx.appello_nome) + (f" --- {_escape_testo_semplice(ctx.appello_data)}" if ctx.appello_data else "")

    def campo(etichetta: str, valore: str = "") -> str:
        contenuto = f"{{\\Large\\bfseries {valore}}}" if valore else "\\rule{4cm}{0.4pt}"
        return f"{{\\large {etichetta}}} & {contenuto} \\\\[0.9cm]\n"
    return (
        "\\thispagestyle{empty}\n"
        "\\noindent\\begin{minipage}[t][0.45\\textheight][c]{\\textwidth}\\centering\n"
        f"{{\\Huge\\bfseries {ctx.corso}}}\\par\\bigskip\n"
        + sotto +
        "\\vspace{1cm}\n"
        f"{{\\LARGE {prova}}}\\par\\bigskip\n"
        "{\\large Griglie delle risposte corrette}\\par\n"
        "\\end{minipage}\\par\n"
        "\\noindent\\rule{\\textwidth}{0.6pt}\\par\\vspace{1cm}\n"
        "\\begin{center}\n\\renewcommand{\\arraystretch}{1}\n\\begin{tabular}{l@{\\qquad}l}\n"
        + campo(f"Compiti stampati in questo blocco (n.~{numero_blocco})", str(n_blocco))
        + campo(f"Compiti stampati in totale ({_quanti(n_blocchi, 'blocco', 'blocchi')})", str(totale))
        + campo("Studenti iscritti")
        + campo("Studenti presenti")
        + campo("Studenti assenti")
        + "\\end{tabular}\n\\end{center}\n"
        + NUOVA_PAGINA_DISPARI
    )


def crea_file_griglia(ctx: LatexContext, studenti_codici_griglie, numero_blocco: int | None = None,
                      totale_compiti: int | None = None, n_blocchi: int | None = None) -> str:
    """Griglia delle soluzioni per un singolo blocco: studenti_codici_griglie è
    [(codice, griglia), ...]. Con `numero_blocco` si apre con una copertina (vedi
    _copertina_griglia) che riporta anche i compiti stampati in tutto (`totale_compiti`,
    in `n_blocchi` blocchi)."""
    tex = BEGIN_DOCUMENT
    if numero_blocco is not None:
        tex += _copertina_griglia(ctx, numero_blocco, len(studenti_codici_griglie),
                                  totale_compiti if totale_compiti is not None else len(studenti_codici_griglie),
                                  n_blocchi or 1)
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
    """Riquadro con data, ora e aula dell'orale, o in mancanza la frase del corso (es.
    "contattare il docente"): solo se nell'elenco c'è qualcuno che deve fare l'orale."""
    dettagli = ", ".join(_escape_testo_semplice(p) for p in (ctx.orale_data, ctx.orale_ora, ctx.orale_aula) if p)
    testo = f"{{\\bfseries Orale:}} {dettagli}" if dettagli else ctx.frase_orale
    if not testo:
        return ""
    return (
        "\\noindent\\fbox{\\parbox{\\dimexpr\\textwidth-2\\fboxsep-2\\fboxrule}{\\strut "
        f"{testo}}}}}\\par\\vspace{{0.4cm}}\n\n"
    )


def crea_tex_risultati(ctx: LatexContext, righe: list[dict], prove: list[str] = ()) -> str:
    """righe: dict con matricola/nome/cognome/etichetta (e voti_prove, uno per ciascuna
    delle `prove` di un raggruppamento) — etichetta è già il testo da stampare al posto del
    voto (un numero, "30lode", "non superato", "orale", "assente", "ritirato", ...): la
    logica su quale etichetta usare vive in risultati.py."""
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    titolo = "Risultati complessivi delle prove" if prove else "Risultati della prova scritta"
    tex += _titolo_documento(titolo, _quanti(len(righe), "studente", "studenti"))
    if any(r.get("orale_in_attesa") for r in righe):
        tex += _intestazione_orale(ctx)
    colonne_prove = " c" * len(prove)
    tex += (
        _RIGHE_ALTERNATE +
        f"\\begin{{center}}\n\\begin{{longtable}}{{>{{\\ttfamily}}c c{colonne_prove} >{{\\bfseries}}c}}\n\\toprule\n"
        + _riga_intestazione("Matricola", "Iniziali", *(_escape_testo_semplice(p) for p in prove),
                             "Voto finale" if prove else "Esito")
        + "\\midrule\n\\endhead\n"
    )
    for r in righe:
        iniziali = f"{(r['nome'] or ' ')[0]}{(r['cognome'] or ' ')[0]}".strip()
        voti = "".join(f" & {v}" for v in r.get("voti_prove") or [])
        tex += f"{celamatr(r['matricola'])} & {iniziali}{voti} & {r['etichetta']}\\\\\n"
    tex += "\\bottomrule\n\\end{longtable}\n\\end{center}\n\n"
    tex += "\\vfill\n\\end{document}"
    return tex


def _tabella_ammessi(righe: list[dict], con_aula: bool = False, etichetta: str = "", prove: list[str] = ()) -> str:
    """Righe ben distanziate (spazio a sufficienza per segnare a penna presenza/assenza
    durante l'appello), con una riga di separazione tra uno studente e l'altro. La
    casella "Presente" è la prima colonna (senza intestazione, da spuntare a penna); la
    colonna "Note" segnala DSA e/o ammissione in deroga (aggiunto a mano pur non
    soddisfacendo i requisiti automatici). Lo spazio verticale è dato da uno strut
    invisibile dentro ogni riga (non da un salto riga finale tipo '\\\\[0.9cm]'): messo
    dopo l'ultima riga, quest'ultimo lascerebbe la prima riga incollata all'intestazione,
    con uno spazio diverso (più stretto) da quello, uniforme, fra tutte le altre righe.
    `con_aula`: colonna in più con l'aula assegnata (nell'elenco generale, se ci sono aule).
    `etichetta`: a quale aula si riferisce il foglio (o "elenco completo"), ripetuta in un
    riquadro in cima a ogni pagina della tabella: i fogli stampati restano riconoscibili
    anche una volta separati e distribuiti nelle aule.
    `prove`: le prove precedenti dello stesso raggruppamento, con una colonna ciascuna per
    il voto preso ('voti_precedenti' di ogni riga)."""
    # con i voti delle prove precedenti, cognome e nome si stringono per fare posto
    stretto = 0.03 * min(len(prove), 3)
    voti_col = _colonne_voti(len(prove))
    if con_aula:
        colonne = f"c >{{\\ttfamily}}l >{{\\bfseries}}p{{{0.24 - stretto:.2f}\\textwidth}} p{{{0.2 - stretto:.2f}\\textwidth}}{voti_col} l l"
        intestazione = ("Presente", "Matricola", "Cognome", "Nome", *map(_testo_piccolo, prove), "Aula", "Note")
    else:
        colonne = f"c >{{\\ttfamily}}l >{{\\bfseries}}p{{{0.3 - stretto:.2f}\\textwidth}} p{{{0.25 - stretto:.2f}\\textwidth}}{voti_col} l"
        intestazione = ("Presente", "Matricola", "Cognome", "Nome", *map(_testo_piccolo, prove), "Note")
    riga_etichetta = (
        f"\\rowcolor{{white}}\\multicolumn{{{len(intestazione)}}}{{r}}"
        f"{{\\setlength{{\\fboxsep}}{{5pt}}\\fbox{{\\Large\\bfseries {etichetta}}}}} \\\\[4pt]\n"
    ) if etichetta else ""
    tex = _RIGHE_ALTERNATE + (
        f"\\begin{{longtable}}{{{colonne}}}\n{riga_etichetta}\\toprule\n"
        + _riga_intestazione(*intestazione, altezza="0.75cm") + "\\midrule\n\\endhead\n"
    )
    for i, r in enumerate(righe):
        note_parti = []
        if r.get("dsa"):
            note_parti.append(r.get("dsa_tipo") if r.get("dsa_tipo") in ("DSA", "L104") else "DSA")
        if r.get("in_deroga"):
            note_parti.append("deroga")
        note = ", ".join(note_parti)
        aula = f" & {_escape_testo_semplice(r.get('aula_nome') or '--')}" if con_aula else ""
        voti = list(r.get("voti_precedenti") or [])
        voti = "".join(f" & {_escape_testo_semplice(str(v))}" for v in (voti + ["--"] * len(prove))[:len(prove)])
        tex += (f"\\rule{{0pt}}{{0.75cm}}{{\\Large $\\square$}} & {r['matricola']} & {_escape_testo_semplice(r['cognome'])}"
                f" & {_escape_testo_semplice(r['nome'])}{voti}{aula} & {note} \\\\\n")
    tex += "\\bottomrule\n\\end{longtable}\n\n"
    return tex


def _piede_elenco(etichetta: str) -> str:
    """Stile di pagina con `etichetta` a piè di ogni pagina (da qui in poi: dopo un
    \\clearpage vale per le pagine successive)."""
    return (
        "\\makeatletter\\def\\ps@elenco{\\let\\@oddhead\\@empty\\let\\@evenhead\\@empty"
        f"\\def\\@oddfoot{{\\hfil{{\\bfseries {etichetta}}}\\hfil}}\\let\\@evenfoot\\@oddfoot}}"
        "\\makeatother\\pagestyle{elenco}\n"
    )


def _testo_piccolo(testo: str) -> str:
    """Intestazione di una colonna stretta (es. il nome di una prova), in corpo piccolo:
    va in una colonna a larghezza fissa (vedi _colonne_voti), dove va a capo."""
    return f"\\small {_escape_testo_semplice(testo)}"


def _colonne_voti(n: int) -> str:
    """Colonne dei voti delle prove: larghezza fissa, il nome della prova va a capo."""
    return " >{\\centering\\arraybackslash}p{1.7cm}" * n


# nuova pagina che sia sempre dispari (un fronte nella stampa fronte-retro), lasciando
# bianco il retro dell'ultima pagina se serve
NUOVA_PAGINA_DISPARI = "\\clearpage\n\\ifodd\\value{page}\\else\\null\\thispagestyle{empty}\\clearpage\\fi\n"


def crea_tex_ammessi(ctx: LatexContext, titolo: str, lista: list[dict], aule: list, aula_singola: str = "",
                     prove: list[str] = ()) -> str:
    """Elenco degli studenti ammessi a sostenere una prova (matricola per intero, non
    oscurata: serve a fare l'appello in aula, non va pubblicata come i risultati). `lista`:
    dict con matricola/nome/cognome/dsa/aula_nome. `aule`: le aule di questa prova (oggetti
    con .id/.nome/.capienza); se presenti, la tabella generale indica l'aula di ciascuno e
    dopo segue una tabella per aula con solo gli studenti assegnati a quella specifica aula.
    `aula_singola`: nome dell'aula se l'elenco è già quello di un'aula sola.
    Ogni pagina dice, in cima alla tabella e a piè di pagina, a quale aula si riferisce o se
    è l'elenco completo. L'elenco di ciascuna aula comincia sempre su una pagina dispari,
    così stampando fronte-retro i fogli di un'aula non stanno sul retro di quelli di un'altra.
    `prove`: vedi _tabella_ammessi."""
    etichetta = f"Aula {_escape_testo_semplice(aula_singola)}" if aula_singola else (
        "Elenco completo (tutte le aule)" if aule else "Elenco completo")
    tex = BEGIN_DOCUMENT
    tex += _piede_elenco(etichetta)
    tex += intestazione_breve(ctx)
    tex += _titolo_documento(titolo, _quanti(len(lista), "studente", "studenti"))
    tex += _tabella_ammessi(lista, con_aula=bool(aule), etichetta=etichetta, prove=prove)
    for aula in aule:
        righe_aula = [r for r in lista if r.get("aula_id") == aula.id]
        etichetta_aula = f"Aula {_escape_testo_semplice(aula.nome)}"
        tex += NUOVA_PAGINA_DISPARI + _piede_elenco(etichetta_aula)
        tex += _titolo_documento(etichetta_aula, _quanti(len(righe_aula), "studente", "studenti") + f" (capienza {aula.capienza})")
        tex += _tabella_ammessi(righe_aula, etichetta=etichetta_aula, prove=prove)
    tex += "\\end{document}"
    return tex


def crea_tex_orali(ctx: LatexContext, dati: dict, note_studente) -> str:
    """Foglio per gli orali (vedi orali.foglio_orali): prima l'elenco di chi deve sostenerlo,
    con la casella della presenza, il voto dello scritto (per un raggruppamento i voti
    delle prove e la media) e lo spazio per il voto dell'orale (o l'esito, se già
    registrato); poi, da una nuova pagina dispari, una scheda per studente con gli errori
    fatti in ogni prova, le note e uno spazio per gli appunti. `note_studente(s,
    raggruppamento)`: le note da elencare per uno studente (testo semplice)."""
    prove = dati["prove"]
    studenti = dati["studenti"]
    e = _escape_testo_semplice
    tex = BEGIN_DOCUMENT
    tex += intestazione_breve(ctx)
    tex += _titolo_documento("Orali", _quanti(len(studenti), "studente", "studenti"))
    tex += _intestazione_orale(ctx)
    voti_col = _colonne_voti(len(prove))
    stretto = 0.03 * min(len(prove), 3)
    intestazione = ("Presente", "Matricola", "Cognome", "Nome", *map(_testo_piccolo, prove),
                    "Media" if prove else "Scritto", "Voto orale")
    tex += _RIGHE_ALTERNATE + (
        f"\\begin{{longtable}}{{c >{{\\ttfamily}}l >{{\\bfseries}}p{{{0.22 - stretto:.2f}\\textwidth}} "
        f"p{{{0.18 - stretto:.2f}\\textwidth}}{voti_col} c c}}\n\\toprule\n"
        + _riga_intestazione(*intestazione, altezza="0.85cm") + "\\midrule\n\\endhead\n"
    )
    for s in studenti:
        voti = "".join(f" & {e(str(v))}" for v in s["voti_prove"])
        scritto = str(s["scritto"]) if s["scritto"] is not None else "--"
        orale = (f"\\textit{{{e(s['esito_orale'])}}}" if s["esito_orale"]
                 else "\\setlength{\\fboxsep}{0pt}\\fbox{\\rule{0pt}{0.6cm}\\hspace{1.6cm}}")
        tex += (f"\\rule{{0pt}}{{0.85cm}}{{\\Large $\\square$}} & {e(s['matricola'])} & {e(s['cognome'])} & "
                f"{e(s['nome'])}{voti} & {scritto} & {orale} \\\\\n")
    tex += "\\bottomrule\n\\end{longtable}\n\n"

    if studenti:
        tex += NUOVA_PAGINA_DISPARI
        tex += _titolo_documento("Gli scritti, studente per studente")
    for s in studenti:
        tex += "\\noindent\\begin{minipage}{\\textwidth}\n"
        voto = f"scritto {s['scritto']}" if s["scritto"] is not None else "scritto --"
        if prove and s["media_esatta"] is not None:
            voto = f"media {str(s['media_esatta']).replace('.', ',')} (voto {s['scritto']})"
        tex += (
            "\\noindent\\colorbox{black!10}{\\parbox{\\dimexpr\\linewidth-2\\fboxsep}{\\strut"
            f"{{\\large\\bfseries {e(s['cognome'])} {e(s['nome'])}}}\\quad{{\\ttfamily {e(s['matricola'])}}}"
            f"\\hfill{{\\bfseries {voto}}}}}}}\\par\\smallskip\n"
        )
        for sc in s["scritti"]:
            dettagli = [f"voto {sc['voto']}" if sc["voto"] is not None else e(sc["esito"])]
            if sc["codice"]:
                dettagli.append(f"compito {e(str(sc['codice']))}")
            if sc["n_esercizi"]:
                dettagli.append(f"{len(sc['errori'])} errori su {sc['n_esercizi']} esercizi"
                                if len(sc["errori"]) != 1 else f"1 errore su {sc['n_esercizi']} esercizi")
            titolo = f"{{\\bfseries {e(sc['prova'])}}}: " if prove else ""
            tex += f"\\noindent {titolo}{', '.join(dettagli)}\\par\n"
            if sc["errori"]:
                tex += "\\begin{itemize}\\setlength{\\itemsep}{0pt}\n"
                for err in sc["errori"]:
                    etichetta = e(err["esercizio_nome"]) + (" (obbligatorio)" if err["obbligatorio"] else "")
                    nota = f" --- \\textit{{{e(err['nota'])}}}" if err["nota"] else ""
                    tex += f"\\item {etichetta}: {e(err['descrizione'])}{nota}\n"
                tex += "\\end{itemize}\n"
        note = note_studente(s, bool(prove))
        if note:
            tex += "\\noindent{\\small\\bfseries Note:}\\begin{itemize}\\setlength{\\itemsep}{0pt}\n"
            tex += "".join(f"\\item {{\\small {e(n)}}}\n" for n in note)
            tex += "\\end{itemize}\n"
        tex += ("\\smallskip\\noindent{\\small Appunti dell'orale:}\\par\\vspace{0.7cm}"
                "\\noindent\\rule{\\textwidth}{0.3pt}\\par\\vspace{0.7cm}\\noindent\\rule{\\textwidth}{0.3pt}\\par\n")
        tex += "\\end{minipage}\\par\\bigskip\\bigskip\n\n"
    tex += "\\end{document}"
    return tex
