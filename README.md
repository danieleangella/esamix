# EsaMiX

App locale per la creazione e la correzione di compiti d'esame a risposta multipla.
Gira come un piccolo server sul proprio computer e si usa dal browser. Nessun dato
viene inviato altrove: tutto resta sul computer su cui gira l'app.

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
volta (o di nuovo solo dopo aver scaricato un aggiornamento del codice).

Se si preferisce non usare lo script, i passaggi equivalenti manuali sono:

```bash
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate
pip install -e .
```

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

## Avvio

**Linux / macOS**

```bash
./esamix.sh
```

**Windows** — doppio clic su `esamix.bat`.

Si apre automaticamente il browser su `http://127.0.0.1:8000`. Per chiudere l'app,
tornare al terminale e premere `Ctrl+C` (su Windows, premere un tasto quando richiesto
per chiudere la finestra).

Senza script, l'avvio manuale è:

```bash
source .venv/bin/activate        # se non è già attivo; su Windows: .venv\Scripts\activate
quizesame
```

## Dati

Ogni corso è una cartella dentro `corsi/`, con il proprio database (`db.sqlite`) e i
testi generati (`corsi/<corso>/output/`). I dati non vengono mai inviati altrove: tutto
resta sul computer su cui gira l'app.

Dalla pagina Impostazioni di un corso (o dalle Impostazioni generali dell'app, per tutti
i corsi insieme) si può scaricare in qualsiasi momento un **backup** in formato zip.
Sempre dalle Impostazioni generali dell'app è possibile eliminare definitivamente tutti
i corsi presenti (azione irreversibile, protetta da una frase di conferma da scrivere):
conviene farne un backup prima.

## Importare i dati di un corso già gestito con la vecchia versione a riga di comando

Dalla pagina "Migrazione dati legacy" si può cercare e importare un `db.sqlite`
generato dalla vecchia CLI (cartella `legacy/`). L'importazione **non modifica né
cancella** i file originali: scrive un nuovo corso separato.

## Vecchia versione a riga di comando

Il vecchio strumento a riga di comando (`legacy/esami.py`) resta disponibile invariato
come riferimento/fallback e continua a funzionare sulle cartelle corso esistenti.
