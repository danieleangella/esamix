import sys
import SETTINGS
import SETTINGS2
import random
from random import randint
from random import shuffle
import datetime
import sqlite3
from time import gmtime, strftime
from math import ceil
import csv
import os

sys.path.insert(0, SETTINGS2.dir_ex)
import testi
esercizi=testi.esercizi



def crea_testo():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
#    c.execute('DROP TABLE IF EXISTS compiti')
    c.execute('CREATE TABLE IF NOT EXISTS compiti(\
              codice INT PRIMARY KEY,\
              soluzioni TEXT,\
              used TEXT)')
    [studenti, tex]=SETTINGS2.crea_file_tex(esercizi)
    try:
        fout = open(SETTINGS2.fout_testi, "w")
    except:
        print("[Error]")
        fout.close()
        pass
    fout.write(tex)
    fout.close()
    for stt in studenti:
        try:
            c.execute("INSERT INTO compiti (codice, soluzioni, used)\
                      VALUES (?, ?, ?)", (stt[0], stt[1], SETTINGS.APPELLO_IDX))
        except sqlite3.IntegrityError:
            print('[Error]')
    conn.commit()
    print("[saved]")
    conn.close()


def inserisci_studenti():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS CorsiType ( \
               ctype TEXT PRIMARY KEY,\
               corso_name TEXT)")
    try:
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("MEL", "Ing Meccanica"))
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("GEL", "Ing Gestionale"))
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("na", "na"))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    c.execute("CREATE TABLE IF NOT EXISTS studenti(\
              matricola TEXT PRIMARY KEY,\
              nome TEXT,\
              cognome TEXT,\
              laurea TEXT,\
              ofa BOOL,\
              primaprovaparziale INT,\
              secondaprovaparziale INT,\
              proveparziali INT,\
              primoappello INT,\
              secondoappello INT,\
              terzoappello INT,\
              quartoappello INT,\
              quintoappello INT,\
              sestoappello INT,\
              settimoappello INT,\
              primoappellostraordinario INT,\
              secondoappellostraordinario INT,\
              totale INT,\
              datav TEXT)")
    check=True
    jj=0
    while check:
        print("\n")
        matricola = input('# Matricola:  ')
        nome      = input('# Nome:       ')
        cognome   = input('# Cognome:    ')
        laurea    = input('# Corso:      ')
        print("\n", matricola, "\t", nome, cognome, "\t(", laurea, ")")
        confirm = input('\n# [correct? (y/n)]\t')
        if confirm.upper() != "NO" and confirm.upper() != "N":
            try:
                c.execute("INSERT INTO studenti (matricola, nome, cognome, laurea, ofa)\
                          VALUES (?,?,?,?,?)", (matricola, nome, cognome, laurea, True))
                conn.commit()
                print("[saved]")
                jj += 1
            except sqlite3.IntegrityError:
                print('[Error]')
                print('[No change]')
        contin = input('\n# [continue? (y/n)]\t')
        if contin.upper() != "NO" and contin.upper() != "N":
            check=True
        else:
            check=False
    print('[',jj,' changes]')
    print("\n")
    conn.commit()
    conn.close()



def inserisci_stud_dacsvfile():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS CorsiType ( \
               ctype TEXT PRIMARY KEY,\
               corso_name TEXT)")
    try:
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("MEL", "Ing Meccanica"))
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("GEL", "Ing Gestionale"))
        c.execute("INSERT INTO CorsiType (ctype, corso_name)\
                  VALUES (?,?)", ("na", "na"))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    try:
        c.execute("CREATE TABLE IF NOT EXISTS studenti(\
              matricola TEXT PRIMARY KEY,\
              nome TEXT,\
              cognome TEXT,\
              laurea TEXT,\
              ofa BOOL,\
              primaprovaparziale INT,\
              secondaprovaparziale INT,\
              proveparziali INT,\
              primoappello INT,\
              secondoappello INT,\
              terzoappello INT,\
              quartoappello INT,\
              quintoappello INT,\
              sestoappello INT,\
              settimoappello INT,\
              primoappellostraordinario INT,\
              secondoappellostraordinario INT,\
              totale INT,\
              datav TEXT)")

        cvsfilename = input('# nome file csv: (formato file: Matricola,Nome,Cognome)  ')
        filecsv = open(os.path.join(SETTINGS2.dir_path, SETTINGS.APPELLO_IDX, cvsfilename)) #opens the csv file
        csv_reader = csv.reader(filecsv) #legge il file
        for row in csv_reader:
            c.execute("SELECT * FROM studenti WHERE matricola=?", (row[0],))
            testex = c.fetchone()
            if testex is None:
                c.execute("INSERT INTO studenti (matricola, nome, cognome) VALUES(?, ?, ?)", row)
    except():
        print('[Error]')
    print("\n")
    conn.commit()
    conn.close()





def inserisci_risultati():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    jj=0
    check=True
    while check:
        print("\n")
        c.execute("CREATE TABLE IF NOT EXISTS esami (\
                  codice TEXT,\
                  risposte TEXT,\
                  matricola TEXT,\
                  appello TEXT)")
        try:
            matricola  = input("# Matricola:       \t")
            c.execute("SELECT nome, cognome, laurea, ofa FROM studenti WHERE matricola=? AND datav IS NULL", (matricola,))
            stud=c.fetchone()
        except():
            print('[Error]')
        codice        = input("# Codice compito:   \t")
        stringa       = input("# Stringa soluzione:\t")
        ofaR = str(stud[3])
        if "parzial" in SETTINGS.APPELLO_IDX:
            ofaR       = input("# Ofa (["+str(stud[3])+"]):\t") or str(stud[3])
        if ofaR.upper() != "NO" and ofaR.upper() != "N" and str(ofaR).upper() != "F" \
           and str(ofaR).upper() != "FALSE" and str(ofaR).upper() != "0":
            ofa = True
        else:
            ofa = False
        try:
            c.execute("SELECT soluzioni FROM compiti WHERE codice=? AND used=?", (codice,SETTINGS.APPELLO_IDX,))
            stringagiusta=c.fetchone()[0]
        except():
            print('[Error]')
        punteggio=valuta(stringagiusta, stringa)
        print("\n", matricola, " - ", stud[0], stud[1], "(", stud[2], ")\n", stringa, "\t[", stringagiusta, "]\n VOTO: ",punteggio)
        if not(ofa):
            print("\n OFA unsuccessfull")
        confirm = input('\n# [correct? (y/n)]\t')
        if confirm.upper() != "NO" and confirm.upper() != "N":
            try:
                c.execute("UPDATE studenti SET "+SETTINGS.APPELLO_IDX.replace("-", "")+" =? WHERE matricola=?", (punteggio,matricola,))
                c.execute("UPDATE studenti SET ofa=? WHERE matricola=?", (ofa,matricola,))
                conn.commit()
                print("[saved]")
                jj += 1
            except sqlite3.IntegrityError:
                print('[Error]')
                print('[No change]')
            try:
                c.execute("INSERT INTO esami (codice, risposte, matricola, appello)\
                          VALUES (?,?,?,?)", (codice, stringa, matricola, SETTINGS.APPELLO_IDX.replace("-", "")))
                conn.commit()
                print("[saved]")
            except:
                print('[Error]')
        contin = input('\n# [continue? (y/n)]\t')
        if contin.upper() != "NO" and contin.upper() != "N":
            check=True
        else:
            check=False
    print('[',jj,' changes]')
    print("\n")
    conn.commit()
    conn.close()
    return





def stampa_risultati():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    c.execute("SELECT matricola, " + SETTINGS.APPELLO_IDX.replace("-", "") + ", nome, cognome FROM studenti\
                    WHERE " + SETTINGS.APPELLO_IDX.replace("-", "") + " IS NOT NULL" + " ORDER BY matricola")
    lista = c.fetchall()
    tex = SETTINGS2.crea_tex_risultati(lista)
    try:
        fout = open(SETTINGS2.fout_risultati, "w")
    except:
        print("[Error]")
        fout.close()
        pass
    fout.write(tex)
    fout.close()
    conn.commit()
    conn.close()
    return



def fix_issue():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    c.execute("SELECT matricola, laurea FROM studenti WHERE laurea == ?", ("",))
    lista1 = c.fetchall()
    for stud in lista1:
        try:
            c.execute("UPDATE studenti SET laurea=? WHERE matricola=?", ("na",stud[0],))
            conn.commit()
            print("[saved]")
        except:
            print("[Error]")
            print("[no change]")
    print("[done]")
    conn.commit()
    print("[saved]")
    conn.close()
    return



def stampa_risultati_compitini():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    c.execute("SELECT matricola, primaprovaparziale, secondaprovaparziale FROM studenti\
                    WHERE primaprovaparziale IS NOT NULL AND secondaprovaparziale IS NOT NULL\
                    AND primaprovaparziale >= ? AND secondaprovaparziale >= ?", (SETTINGS.VOTOMIN_PARZIALI, SETTINGS.VOTOMIN_PARZIALI))
    lista1 = c.fetchall()
    jj=0
    for stud in lista1:
        try:
            if ((stud[1]+stud[2])/2>=18):
                voto = ceil((stud[1]+stud[2])/2)
            else:
                voto = int((stud[1]+stud[2])/2)
            c.execute("UPDATE studenti SET proveparziali=? WHERE matricola=?", (voto, stud[0],))
            conn.commit()
            print("[saved]")
            jj+=1
        except:
            print("[Error]")
            print("[no change]")
    c.execute("SELECT matricola, proveparziali, nome, cognome FROM studenti\
                    WHERE proveparziali IS NOT NULL")
    lista = c.fetchall()
    tex = SETTINGS2.crea_tex_risultati(lista, SETTINGS.VOTOMIN_APPELLO)
    try:
        fout = open(SETTINGS2.fout_risultati_parziali, "w")
    except:
        print("[Error]")
        fout.close()
        pass
    fout.write(tex)
    fout.close()
    conn.commit()
    conn.close()
    print("[",jj," changes]")
    print("\n")
    return



def elimina_compito():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    check=True
    jj=0
    while check:
        print("\n")
        codice = input('Codice:  ')
        print("\n", codice)
        confirm = input('\n# [correct? (y/n)]\t')
        if confirm.upper() != "NO" and confirm.upper() != "N":
            try:
                c.execute("UPDATE compiti SET used = '' WHERE codice=?", (codice,))
                conn.commit()
                print("[saved]")
                jj += 1
            except sqlite3.IntegrityError:
                print('[Error]')
                print('[No change]')
        contin = input('\n# [continue? (y/n)]\t')
        if contin.upper() != "NO" and contin.upper() != "N":
            check=True
        else:
            check=False
    print('[',jj,' changes]')
    print("\n")
    conn.commit()
    conn.close()
    return




def verbalizza():
    conn = sqlite3.connect(SETTINGS2.sqlite_db)
    c = conn.cursor()
    check=True
    jj=0
    while check:
        print("\n")
        try:
            matricola  = input("# Matricola:\t")
            appello = SETTINGS.APPELLO_IDX.replace("-", "")
            c.execute("SELECT nome, cognome, laurea, ofa, " + appello + \
                      " FROM studenti WHERE matricola=? AND datav IS NULL AND " + appello + " IS NOT NULL", (matricola,))
            record=c.fetchall()
            c.execute("SELECT nome, cognome, laurea, ofa, " + "proveparziali" + \
                      " FROM studenti WHERE matricola=? AND datav IS NULL AND " + "proveparziali" + " IS NOT NULL", (matricola,))
            record+=c.fetchall()
            stud=record[0][0:4]
            vv=record[0][4]
        except():
            print('[Error]')
        voto = input("# Voto: ["+str(vv)+"]\t") or vv
        data = input("# Data: ["+strftime("%Y-%m-%d", gmtime())+"]\t") or strftime("%Y-%m-%d", gmtime())
        ofaR = input("# Ofa: ["+str(stud[3])+"]\t") or str(stud[3])
        if ofaR.upper() != "NO" and ofaR.upper() != "N" and ofaR.upper() != "FALSE"\
           and ofaR.upper() != "F" and str(ofaR).upper() != "0":
            ofaN = True
        else:
            ofaN = False
        try:
            if int(voto) >= SETTINGS.VOTOMIN_VERB and ofaN: 
                print("\n(", data, ")\n", matricola, " - ", stud[0], stud[1], "(", stud[2], ")\n VOTO: ", voto)
                confirm = input('\n# [correct? (y/n)]\t')
                if confirm.upper() != "NO" and confirm.upper() != "N":
                    try:
                        c.execute("UPDATE studenti SET totale = ?, datav = ?, ofa = ? WHERE matricola=?",\
                                  (voto, data, ofaN, matricola,))
                        conn.commit()
                        print("[saved]")
                        jj += 1
                    except:
                        print('[Error]')
                        print('[no change]')
            else:
                print('[Error]')
                print('[no change]')
        except:
            print("[Error]")
            pass
        contin = input('\n# [continue? (y/n)]\t')
        if contin.upper() != "NO" and contin.upper() != "N":
            check=True
        else:
            check=False
    print('[',jj,' changes]')
    print("\n")
    conn.commit()
    conn.close()
    return





def valuta(str_corretta, str_grade):
    punteggio = 0
    for jj in range(0,len(str_grade)):
        if str_grade[jj].upper()=="X":
            punteggio += SETTINGS.RISPOSTA_VUOTA
        else:
            if str_grade[jj].upper()==str_corretta[jj]:
                punteggio += SETTINGS.RISPOSTA_CORRETTA
            else:
                punteggio += SETTINGS.RISPOSTA_SBAGLIATA
    return punteggio






def carica_tutto():
    """
    [0] nome
    [1] cognome
    [2] matricola
    [3] primo appello
    [4] secondo appello
    [5] terzo appello
    [6] quarto appello
    [7] quinto appello
    [8] sesto appello
    [9] settimo appello
    [10] FINALE
    """
    tmp=[]
    try:
        fin = open(name_risultati_globali_txt_fout, "r")
        tmp = fin.readlines()
        fin.close()
    except:
        pass
    lista = []
    jj = 0
    Q=11
    while jj<len(tmp)+Q+1:
        lista.append([tmp[jj+hh] for hh in range(0,Q)])
        jj += Q
    return lista

def update(ris_appello, ris_totali, indice):
    backup()
    lista = []
    for studente in ris_appello:
        studente_old = [studente[0], studente[1], studente[2], \
                        "x", "x", "x", "x", "x", "x", "x", \
                        "x"]
        for rr in ris_totali:
            if rr[2]==studente[2]:
                studente_old=rr
        studente_old[2+indice]=studente[6]
        lista.append(studente_old)
    return lista

def crea_tex_totale(lista):
    tex = begin_document
    tex += intestazione_breve
    tex += "\\begin{center}\n" \
           + "\\begin{tabular}{ccc|ccccccc|c}\n" \
           + "\\toprule\n" \
           + "{\\bfseries Matricola} & {\\bfseries Nome} & {\\bfseries Cognome} & " \
           + "{\\bfseries appello 1} & {\\bfseries appello 2} & {\\bfseries appello 3} & " \
           + "{\\bfseries appello 4} & {\\bfseries appello 5} & {\\bfseries appello 6} & " \
           + "{\\bfseries appello 7} & {\\bfseries voto finale} " \
           + "\\\\\n" \
           + "\\toprule\n"
    for stud in lista:
        tex += str(stud[0]) + " & "
        tex += str(stud[1]) + " & "
        tex += str(stud[2]) + " & "
        tex += str(stud[3]) + " & "
        tex += str(stud[4]) + " & "
        tex += str(stud[5]) + " & "
        tex += str(stud[6]) + " & "
        tex += str(stud[7]) + " & "
        tex += str(stud[8]) + " & "
        tex += str(stud[9]) + " & "
        tex += str(stud[10]) + "\\\\\n"
    tex += "\\bottomrule\n" \
           + "\\end{tabular}\n" \
           + "\\end{center}\n\n"
    tex += "\\vfill\n" \
           + "\\end{document}"
    try:
        fout = open(name_risultati_globali_tex_fout, "w")
    except:
        print("[Error]")
        fout.close()
        pass
    fout.write(tex)
    fout.close()
    return tex
















if __name__ == "__main__":
    x = input(" [0] Crea testo\n [1] Inserisci studenti\n [2] Valuta compiti\
              \n [3] Stampa risultati\n [4] Stampa risultati compitini\
              \n [5] Elimina compito\n [6] Verbalizza\
              \n [7] Inserisci studenti da file \n [X] Esci\n\n# Opzione: ")
    if x=="0":
        crea_testo()
    elif x=="1":
        inserisci_studenti()
    elif x=="2":
        inserisci_risultati()
    elif x=="3":
        stampa_risultati()
    elif x=="4":
        stampa_risultati_compitini()
    elif x=="5":
        elimina_compito()
    elif x=="6":
        verbalizza()
    elif x=="7":
        inserisci_stud_dacsvfile()
    elif x=="F":
        fix_issue()
    else:
        print("No choice")
    print("\n\n[OK]")
