import os
import random
from random import randint
from random import shuffle
import SETTINGS
import copy


# nomi file

dir_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),SETTINGS.TAG)
sqlite_db = os.path.join(dir_path, "db.sqlite")
dir_ex = os.path.join(dir_path, SETTINGS.APPELLO_IDX, "esercizi")
if SETTINGS.NUMERO_STUDENTI == 0:
    fout_testi = os.path.join(dir_path, SETTINGS.APPELLO_IDX, "testo-"+SETTINGS.APPELLO_IDX+"-online.tex")
else:
    fout_testi = os.path.join(dir_path, SETTINGS.APPELLO_IDX, "testo-"+SETTINGS.APPELLO_IDX+".tex")
fout_risultati = os.path.join(dir_path, SETTINGS.APPELLO_IDX, "risultati-"+SETTINGS.APPELLO_IDX+".tex")
fout_risultati_parziali = os.path.join(dir_path, SETTINGS.APPELLO_IDX, "risultati-prove-parziali"+".tex")

# formattazione

begin_document = "\\documentclass[a4paper,10pt]{amsart}\n\n" \
                 + "\\usepackage{amsfonts,latexsym,rawfonts,amsmath,amssymb,amsthm, mathrsfs}\n" \
                 + "\\usepackage[english]{babel}\n" \
                 + "\\usepackage{amssymb}\n" \
                 + "\\usepackage[utf8]{inputenc}\n" \
                 + "\\usepackage{fullpage}\n" \
                 + "\\usepackage{longtable}\n" \
                 + "\\usepackage{framed}\n" \
                 + "\\usepackage{lipsum}\n" \
                 + "\\usepackage{multicol}\n" \
                 + "\\usepackage{booktabs}\n\n" \
                 + "\\renewcommand{\\familydefault}{\\sfdefault}\n\n" \
                 + "\\theoremstyle{definition}\n" \
                 + "\\newtheorem{ex}{Esercizio}\n\n" \
                 + "\\renewcommand{\\Re}{\\mathsf{Re}}\n" \
                 + "\\renewcommand{\\Im}{\\mathsf{Im}}\n\n" \
                 + "\\begin{document}\n\n" \
                 + "\\pagenumbering{gobble}\n\n"


ofa=""
if "parzial" in SETTINGS.APPELLO_IDX:
    ofa="{\\it OFA:}\\enspace superato $\\square$" + \
         "$\\qquad$ ; non superato $\\square$\\newline\n"

if SETTINGS.CONSEGNA>0:
    text_consegna = "Consegnate lo svolgimento dei " + str(SETTINGS.CONSEGNA) + " esercizi obbligatori.\n"
else:
    text_consegna = ""

def intestazione(codice):
    return "\\begin{flushright}\n" \
           + "\\begin{minipage}{0.80\\textwidth}\n" \
           + "\\begin{framed}\n" \
           + "{\\it Nome:}\\enspace\\hrulefill\\quad \n" \
           + "{\\it Cognome:}\\enspace\\hrulefill\\newline\n" \
           + "{\\it Corso:}\\enspace{\\hrulefill\\bf ({\\scriptsize " + SETTINGS.TAG + "})}\\quad\n" \
           + "{\\it Matricola:}\\enspace\\hrulefill\\quad\\newline\n" \
           + ofa \
           + "{\\it Data:}\\enspace{\\bf " + SETTINGS.APPELLO[SETTINGS.APPELLO_IDX] \
           + "}\\newline\n\n\\medskip\n\n" \
           + "{\\it Risposte:}\\enspace{\\Huge\\bf" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 1}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 2}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 3}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 4}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 5}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 6}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 7}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 8}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 9}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 10}}" \
           + "\\fbox{\\parbox{20pt}{\\scriptsize 11}}" \
           + "}\n" \
           + "\\bigskip\\newline\n" \
           + "{\\it Codice:}\\enspace{\\bf " + str(codice) \
           + "}\\hfill\\fbox{\\parbox{3cm}{\\Large\\bf Voto:}" \
           + "\\enspace\\hrulefill\\,{\\bf/30}}\n" \
           + "\\end{framed}\n" \
           + "\\end{minipage}\n" \
           + "\\end{flushright}\n\n" \
           + "\\vspace{1cm}\n\n" \
           + "\\begin{center}\n" \
           + "{\\bfseries " + text_consegna + "}" \
           + "\\end{center}\n"


def intestazione_breve(appello = SETTINGS.APPELLO_IDX + " (" + SETTINGS.APPELLO[SETTINGS.APPELLO_IDX] + ")"):
    tex = "\\begin{center}\n" \
          + "\\fbox{\n" \
          + "\\begin{minipage}{0.6\\textwidth}\n" \
          + "\\bfseries\n" \
          + "\\small\n" \
          + "\\begin{center}\n" \
          + SETTINGS.CORSO + "\\\\\n" \
          + SETTINGS.FACOLTA + "\\\\\n" \
          + SETTINGS.UNIVERSITA + ", a.a. " \
          + SETTINGS.ANNO + "\\\\\n" \
          + appello.replace("-", " ") + "\\\n" \
          + "\\medskip\n" \
          + "\\\\\n" \
          + "\\end{center}\n" \
          + "\\end{minipage}\n" \
          + "}\n" \
          + "\\end{center}\n" \
          + "\\vspace{1cm}\n\n"
    return tex

regole = "\\begin{footnotesize}\n" \
         + "\\begin{center}\n" \
         + "\\fbox{\\fbox{\\parbox{5.5in}{\\centering\n" \
         + "Compilate {\\it subito} con nome, cognome, numero di matricola. " \
         + "Lasciate sul banco il libretto universitario.\n" \
         + "Non \\`e consentito l'utilizzo di libri, appunti, " \
         + "telefoni cellulari, strumenti elettronici.\n" \
         + "Riportate le risposte nella griglia in cima al foglio. " \
         + text_consegna \
         + "Ogni risposta corretta viene valutata $+3$, " \
         + "ogni risposta errata $-1$, ed ogni risposta non data $0$. " \
         + "La prova scritta \\`e superata se si raggiunge una votazione " \
         + "uguale o superiore a $"+str(SETTINGS.VOTOMIN)+"$.\n" \
         + "La prova dura due ore.\n" \
         + "}}}\n" \
         + "\\end{center}\n" \
         + "\\end{footnotesize}\n\n"


lettere = {0: "A",\
           1: "B",\
           2: "C",\
           3: "D",\
           4: "E",\
           5: "F",\
           6: "G",}

def tex_esercizio(exx):
    str = "\n\\begin{ex}\n" \
           + exx[0] + "\n" \
           + "\\begin{description}\n"
    for soll in exx[1]:
        str += "\\item[" + lettere[exx[1].index(soll)] + "] " + soll + "\n"
    str += "\\end{description}\n" \
           + "\\end{ex}\n\n"
    str += "\\begin{center}\n" \
           + "\\noindent\\rule{0.3\\textwidth}{0.4pt}\n" \
           + "\\end{center}\n"
    return str


def stampa_codici(studenti):
    tex = intestazione_breve()
    tex += "\\begin{center}\n" \
           + "\\begin{longtable}{cc}\n" \
           + "\\toprule\n" \
           + "{\\bfseries Codice} & {\\bfseries Griglia} \\\\\n" \
           + "\\toprule\n"
    for stud in studenti:
        tex += str(stud[0]) + " & "
        tex += str(stud[1])
        tex += "\\\\\n"
    tex += "\\bottomrule\n" \
           + "\\end{longtable}\n" \
           + "\\end{center}\n\n"
    return tex


def crea_file_tex(esercizi):
    studenti=[]
    tex = begin_document

    for ii in range(0,SETTINGS.NUMFILE):
        tex += intestazione_breve()
        tex += "\\begin{center}\n" \
                   + "{\\small\\it (la risposta corretta \\`e sempre ""A"")}\n" \
                   + "\\end{center}\n" \
                   + "\\vspace{1cm}\n\n"
        tex += "\n\\begin{multicols}{2}\n" \
               + "\\setcounter{ex}{0}\n"
        for exx in [esercizi[jj][ii] for jj in range(0,SETTINGS.NUMEX)]:
            tex += tex_esercizio(exx)
        tex += "\\end{multicols}\n\n" \
               + "\\clearpage\n\n"
    for jj in range(SETTINGS.NUMERO_STUDENTI):
        code = randint(0,999999)
        tex += intestazione(code)
        tex += "\\begin{small}\n" \
                   + "\n\\begin{multicols}{2}\n" \
                   + "\\setcounter{ex}{0}\n"
        [esser, soll] = mischia(esercizi)
        griglia=""
        for sss in soll:
            griglia+=lettere[sss]
        studenti.append([code, griglia])
        for exx in esser:
            tex += tex_esercizio(exx)
        tex += "\\end{multicols}\n" \
                   + "\\end{small}\n\n" \
                   + "\\vfill\n"
        tex += regole
        tex += "\\clearpage\n\n"
    if SETTINGS.NUMERO_STUDENTI > 0:
        tex += stampa_codici(studenti)
    tex += "\n\n\\end{document}"
    return [studenti, tex]


def mischia(esercizi):
    esercizi_tmp = []
    soluzioni=[]
    for j in range(0,SETTINGS.NUMEX):
        rand = randint(0,SETTINGS.NUMFILE-1)
        esercizi_tmp.append(copy.deepcopy(esercizi[j][rand]))
        random.shuffle(esercizi_tmp[-1][1])
        esercizi_tmp[-1].append(esercizi_tmp[-1][1].index(esercizi[j][rand][1][0]))
    random.shuffle(esercizi_tmp)
    return [[[bb[0], bb[1]] for bb in esercizi_tmp], [bb[2] for bb in esercizi_tmp]]










def celamatr(matrchiara):
    return (matrchiara[0:3]+"xx"+matrchiara[5:7])

def celavoto(votochiaro,votomin):
    if votochiaro>30:
        return "30lode"
    if votochiaro<votomin:
        return "non superato"
    return str(votochiaro)


def crea_tex_risultati(lista, votomin=SETTINGS.VOTOMIN):
    tex = begin_document
    tex += intestazione_breve()
    tex += "\\begin{center}\n" \
           + "\\begin{longtable}{cc}\n" \
           + "\\toprule\n" \
           + "{\\bfseries Matricola} & {\\bfseries Voto} \\\\\n" \
           + "\\toprule\n"
    for stud in lista:
        tex += celamatr(stud[0]) + "(" + stud[2][0] + stud[3][0] + ")" + " & "
        tex += celavoto(int(stud[1]), votomin)
        tex += "\\\\\n"
    tex += "\\bottomrule\n" \
           + "\\end{longtable}\n" \
           + "\\end{center}\n\n"
    tex += "\\vfill\n" \
           + "\\end{document}"
    return tex


