# EsaMiX

App locale per la creazione e la correzione di compiti d'esame a risposta multipla.
Gira come un piccolo server sul proprio computer e si usa dal browser. I dati restano
sul computer su cui gira l'app; l'unica eccezione, facoltativa, è la generazione di
esercizi con l'IA (vedi sotto).

## Requisiti

- **Python 3.10 o superiore.**
- (Facoltativo) una distribuzione **LaTeX** con il comando `pdflatex`, solo se si vuole
  generare direttamente i PDF di compiti/griglie/risultati. Senza LaTeX l'app funziona
  comunque: resta disponibile il testo sorgente `.tex`, da compilare altrove.

### Installare Python 3

Se il comando `python3 --version` (Linux/macOS) o `python --version` (Windows) risponde
con una versione 3.10 o superiore, questo passaggio si può saltare.

**Linux (Debian/Ubuntu e derivate)**

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

Su altre distribuzioni usare il gestore pacchetti equivalente (`dnf`, `pacman`, ecc.):
il pacchetto da cercare si chiama `python3`.

**macOS**

Installare [Homebrew](https://brew.sh) se non già presente, poi:

```bash
brew install python
```

In alternativa, scaricare l'installer ufficiale da
[python.org/downloads](https://www.python.org/downloads/).

**Windows**

Scaricare l'installer da [python.org/downloads](https://www.python.org/downloads/) ed
eseguirlo. **Importante:** nella prima schermata dell'installer, spuntare la casella
**"Add python.exe to PATH"** prima di procedere, altrimenti i comandi `python` non
saranno riconosciuti dal terminale.

In alternativa è possibile installare Python dal Microsoft Store (cercare "Python 3.x").

## Installazione di EsaMiX

Scaricare il codice di questo repository (con `git clone`, oppure scaricando lo ZIP da
GitHub e estraendolo in una cartella), poi:

**Linux / macOS** — aprire un terminale nella cartella del progetto ed eseguire:

```bash
./installa.sh
```

**Windows** — fare doppio clic su `installa.bat` (oppure eseguirlo da un terminale
`cmd`/PowerShell aperto nella cartella del progetto).

Lo script crea un ambiente virtuale Python isolato (cartella `.venv/`) e vi installa
l'app: non modifica nulla al di fuori della cartella del progetto. Va eseguito una sola
volta (o di nuovo solo dopo aver scaricato un aggiornamento del codice). Su Windows
trova Python anche se non è stato aggiunto al PATH (tramite il lanciatore `py`), e se
un'installazione precedente si era interrotta basta rilanciarlo.

Se si preferisce non usare lo script, i passaggi equivalenti manuali sono:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Su Windows (sia nel Prompt dei comandi sia in PowerShell):

```
py -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Se poi Windows dice che `quizesame` non è riconosciuto come comando, l'app è stata
installata fuori dall'ambiente virtuale: rilanciare `installa.bat`.

## Aggiornamento

Per scaricare gli aggiornamenti più recenti dell'app (senza dover usare `git` a mano):

**Linux / macOS**

```bash
./aggiorna.sh
```

**Windows** — doppio clic su `aggiorna.bat`.

Lo script scarica il codice più recente da GitHub e reinstalla le eventuali dipendenze
cambiate; non tocca mai i corsi/dati già presenti nella cartella `corsi/` (non fanno
parte del codice scaricato con git). Se hai modificato a mano qualche file del programma,
l'aggiornamento si ferma per non farti perdere quelle modifiche: te lo segnala e ti dice
come procedere.

Se una versione precedente dello script si ferma dicendo che ci sono "modifiche non
salvate" e nell'elenco compare solo la cartella `corsi/` (o un file al suo interno), si
tratta dei tuoi dati, non di modifiche al programma: esegui una volta a mano
`git pull --ff-only` nella cartella del programma, poi di nuovo lo script (la versione
aggiornata dello script non ha più questo problema).

Dopo l'aggiornamento **riavvia EsaMiX** (Ctrl+C nel terminale, poi di nuovo l'avvio): finché
non lo fai, ogni pagina mostra un avviso, perché il programma in esecuzione è ancora quello
vecchio.

## Avvio

**Linux / macOS**

```bash
./esamix.sh
```

**Windows** — doppio clic su `esamix.bat`.

Si apre automaticamente il browser su `http://127.0.0.1:8000`. Se la porta 8000 è già
occupata da un altro programma, l'app usa automaticamente la prima porta libera successiva
(8001, 8002, …) e lo segnala nel terminale. Per partire da una porta diversa, impostare la
variabile d'ambiente `QUIZESAME_PORT` (es. `QUIZESAME_PORT=8010 ./esamix.sh`). Per chiudere l'app,
tornare al terminale e premere `Ctrl+C` (su Windows, premere un tasto quando richiesto
per chiudere la finestra).

Senza script, l'avvio manuale è:

```bash
source .venv/bin/activate        # se non è già attivo; su Windows: .venv\Scripts\activate
quizesame
```

## Uso

Dalla homepage si apre un corso, e da lì i suoi appelli (o un raggruppamento di prove
parziali). In cima alla pagina di un appello il **percorso** mostra le fasi nell'ordine —
Testo, Compiti d'esame, Presenze, Correzione, Verbalizzazione, Chiusura — con lo stato di
ciascuna (verde completata, arancione in corso, bordo blu prossimo passo) e porta alla
scheda in cui completarla; il riquadro "Prossimo passo" dice cosa fare adesso.

- **Testo**: esercizi del compito, aggiunti dalla banca del corso, da altri corsi, creati da
  zero, generati con l'IA o importati da un file; obbligatorio sì/no con un clic. Quando è
  pronto si **chiude il testo**: non si modifica più finché non lo si riapre, e si generano i
  fogli da pubblicare (senza e con soluzioni).
- **Compiti d'esame**: elenco iscritti (file della segreteria, prenotazioni Moodle, aggiunte
  una per una, cestino per togliere), generazione (a testo chiuso) e stampa dei compiti; per le prove
  parziali anche gli ammessi e l'assegnazione alle aule.
- **Presenze**: registro del giorno della prova; in alternativa, finite le correzioni,
  "Segna assenti tutti i non corretti".
- **Correzione**: inserimento delle risposte di ogni compito (solo le lettere delle domande
  chiuse: il "-" delle domande aperte lo aggiunge il campo da solo), con il voto in tempo
  reale. Gli obbligatori svolti con la risposta giusta partono dal punteggio massimo, da
  abbassare se lo svolgimento non va; le domande aperte ricevono un punteggio a mano (finché
  manca contano 0). Per ogni svolgimento una nota degli errori, con le note simili già date ad
  altri studenti come suggerimento, e una nota sul compito. Gli svolgimenti si possono
  rimandare e valutare poi esercizio per esercizio nella pagina **Svolgimenti e note**, che
  confronta punteggi e note per vedere se le correzioni sono coerenti. Bozze e compiti con
  svolgimenti da valutare stanno nell'elenco **Correzioni da completare**; cancellare una
  correzione riporta lo studente lì. In fondo "Chiudi la correzione", attivo quando tutti
  hanno un esito e non restano correzioni da completare; a correzione chiusa ci sono i
  risultati da pubblicare e i **voti per Moodle** (CSV con la struttura del file di
  prenotazioni Moodle caricato).
- **Orali**, **Verbalizzazione** (con l'esportazione dei voti nel file della segreteria) e
  **Chiusura**, ognuna nella sua scheda.
- **Impostazioni** dell'appello: nome, data, orale e, per le prove parziali, le aule (una può
  essere riservata agli studenti DSA/L104; un avviso segnala quando gli studenti superano la
  quota di posti impostata nel corso, 50% di default).

Le azioni più frequenti (iscritti, esercizi, aule) si salvano senza ricaricare la pagina. Le
spiegazioni meno usate sono nelle icone **i**, e i pulsanti a icona mostrano cosa fanno al
passaggio del mouse. La voce **Guida** del menu descrive tutto nel dettaglio.

Per le **prove parziali**, chiusa la correzione dell'ultima prova si generano i **risultati
complessivi**: solo chi ha superato tutte le prove, con il voto di ciascuna e il voto finale,
cioè la media arrotondata per eccesso solo se la media esatta è già sufficiente (con soglia 18,
17 e 18 danno 17,5: non superato), oppure "orale". La scheda **Risultati** raccoglie in cima
tutti i file da pubblicare, complessivi e di ogni prova.

Per provare l'app senza toccare i propri corsi, **Impostazioni → Corso di prova** (o la Guida)
installa un corso con dati inventati: un appello e due prove parziali, con correzioni già fatte
e altre da fare. Si elimina con un clic, senza lasciare tracce.

Dopo un aggiornamento del codice va riavviata l'app (l'app stessa lo segnala con un avviso).

## Dati

Ogni corso è una cartella dentro `corsi/`, con il proprio database (`db.sqlite`) e i
testi generati (`corsi/<corso>/output/`). I dati non vengono mai inviati altrove: tutto
resta sul computer su cui gira l'app.

## Documenti

Compiti, griglie, fogli da pubblicare (con e senza soluzioni, generati quando si chiude il
testo), anteprima di controllo,
risultati ed elenchi di iscritti sono disponibili in PDF, HTML e TeX; i documenti da
pubblicare anche in Markdown. Ogni compito occupa sempre due pagine (un foglio
fronte-retro), in un carattere senza grazie leggibile anche per studenti con DSA. Dopo un
aggiornamento, "Rigenera tutti i documenti" (scheda Compiti d'esame) ricrea i PDF con la
grafica nuova mantenendo codici e ordine di esercizi e risposte.

## Registro

La voce **Registro** del menu elenca tutte le azioni svolte nell'app (moduli inviati con
l'esito, documenti aperti o scaricati, verifiche con l'IA) e gli aggiornamenti installati.
Il registro è nel file `corsi/registro.jsonl`.

## Generazione di esercizi con l'IA (facoltativa)

Nella scheda Testo di un appello, **"Genera con l'IA"** crea nuovi esercizi con Claude
(Anthropic) sul modello delle prove analoghe degli anni precedenti dello stesso corso:
per una prova parziale, le prove parziali corrispondenti; per un appello, gli appelli.
Gli esercizi proposti passano sempre da una pagina di conferma, dove si sceglie quali
importare.

Dalla stessa scheda, **"✓ Verifica con l'IA"** fa ricontrollare al modello tutti gli
esercizi del compito (risposte indicate come corrette, unicità della risposta, testi e
soluzioni) e mostra per ciascuno se è corretto, da controllare o sbagliato. La verifica si
può fare anche su tutta la banca di un corso, su un singolo esercizio o su tutti i corsi
("Tutti gli esercizi"); l'esito resta salvato per ogni esercizio, così quelli corretti non
vanno ricontrollati finché non vengono modificati. Per gli esercizi con problemi, l'IA può
proporre una correzione: si vede prima e dopo, la si può ritoccare e la si salva solo
confermando.

In Impostazioni → "Intelligenza artificiale" si possono salvare più configurazioni (chiavi
o server diversi) e scegliere ogni volta quale usare. I fornitori possibili sono:

- **Anthropic** (Claude Opus 5): chiave API `sk-ant-…`, oppure la variabile d'ambiente
  `ANTHROPIC_API_KEY`. A pagamento sul proprio account Anthropic.
- **OpenRouter** (Claude Opus 5 tramite OpenRouter): chiave `sk-or-…`, oppure la
  variabile d'ambiente `OPENROUTER_API_KEY`. A pagamento sul credito OpenRouter.
- **Ollama su un proprio server**: indirizzo del server (es. `http://192.168.1.10:11434`),
  modello installato e, se il server è dietro un proxy con login, un token. I testi non
  escono dalla propria rete. Serve un modello abbastanza grande e un contesto ampio: sul
  server impostare `OLLAMA_CONTEXT_LENGTH` ad almeno 32768.

Le chiavi restano solo in `corsi/app_settings.json` e non vengono mai rimostrate per
intero nelle pagine. Le chiavi salvate con le versioni precedenti diventano automaticamente
una configurazione ciascuna. Senza nessun fornitore si può copiare il prompt, incollarlo in
claude.ai e importare la risposta con "Importa un compito da un unico file".

Vengono inviati solo i testi degli esercizi e le impostazioni del corso (mai dati degli
studenti), e solo quando si preme il pulsante.

## Importare i dati di un corso già gestito con la vecchia versione a riga di comando

Dalla pagina "Migrazione dati legacy" si può cercare e importare un `db.sqlite`
generato dalla vecchia CLI (cartella `legacy/`). L'importazione **non modifica né
cancella** i file originali: scrive un nuovo corso separato.

## Vecchia versione a riga di comando

Il vecchio strumento a riga di comando (`legacy/esami.py`) resta disponibile invariato
come riferimento/fallback e continua a funzionare sulle cartelle corso esistenti.
