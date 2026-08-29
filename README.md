# Gestione compiti d'esame

App locale per la creazione e la correzione di compiti d'esame a risposta multipla.
Gira come un piccolo server sul proprio computer e si usa dal browser.

## Installazione

Serve Python 3.10 o superiore, già installato sulla maggior parte dei computer.

Apri un terminale nella cartella del progetto ed esegui:

```bash
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate
pip install -e .
```

(l'operazione va ripetuta solo se questi passaggi non sono già stati fatti in precedenza).

## Avvio

```bash
source .venv/bin/activate        # se non è già attivo
quizesame
```

Si apre automaticamente il browser su `http://127.0.0.1:8000`. Per chiudere l'app,
tornare al terminale e premere `Ctrl+C`.

## Dati

Ogni corso è una cartella dentro `corsi/`, con il proprio database (`db.sqlite`) e i
testi generati (`corsi/<corso>/output/`). I dati non vengono mai inviati altrove: tutto
resta sul computer su cui gira l'app.

## Importare i dati di un corso già gestito con la vecchia versione a riga di comando

Dalla pagina "Migrazione dati legacy" si può cercare e importare un `db.sqlite`
generato dalla vecchia CLI (cartella `legacy/`). L'importazione **non modifica né
cancella** i file originali: scrive un nuovo corso separato.

## Vecchia versione a riga di comando

Il vecchio strumento a riga di comando (`legacy/esami.py`) resta disponibile invariato
come riferimento/fallback e continua a funzionare sulle cartelle corso esistenti.
